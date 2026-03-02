"""
umqtt_test.py — Teste MQTT via ESP32-C3 AT Commands para AWS IoT Core

Arquitetura
-----------
O Pico 2 controla o ESP32-C3 por UART via comandos AT. O cliente MQTT roda
inteiramente no ESP32-C3 (firmware ESP-AT v4.x). O Pico apenas envia comandos
e recebe confirmacoes — nenhuma biblioteca umqtt ou ssl eh necessaria no Pico.

  Pico 2 (MicroPython)
    └─ UART1 (AT commands) ──▶ ESP32-C3 (ESP-AT firmware)
                                   └─ TLS mutual auth ──▶ AWS IoT Core :8883

Fluxo de execucao
-----------------
  Passo 0 — Diagnostico: versao do firmware e estado atual dos namespaces NVS
  Passo 1 — Wi-Fi + SNTP: obtem IP e sincroniza relogio (exigido pelo TLS)
  Passo 2 — Certificados: converte DER→PEM e grava na mfg_nvs via AT+SYSMFG
  Passo 3 — Conectividade: confirma IP e hora antes de tentar o MQTT
  Passo 4 — MQTT: configura cliente TLS mutual (scheme=5) e conecta ao broker
  Passo 5 — Publicacao: envia payload binario via AT+MQTTPUBRAW

Referencia oficial Espressif:
  https://docs.espressif.com/projects/esp-at/en/latest/esp32c3/AT_Command_Examples/mqtt-at-examples-for-cloud.html

Como os certificados sao armazenados
------------------------------------
O ESP-AT le certificados MQTT da particao 'mfg_nvs' em tres namespaces
dedicados. As chaves NVS seguem o padrao do build system do ESP-AT (mfg_nvs.py):
quando o namespace contem apenas um arquivo de certificado, a chave recebe o
mesmo nome do namespace — SEM sufixo de indice:

  namespace  chave NVS   conteudo
  ---------  ----------  --------------------------------
  mqtt_ca    mqtt_ca     CA raiz do servidor (Amazon)
  mqtt_cert  mqtt_cert   Certificado do dispositivo
  mqtt_key   mqtt_key    Chave privada do dispositivo

ATENCAO: Os namespaces SSL client usam sufixo (.0, .1, ...) porque o firmware
suporta multiplos conjuntos de certs. Os namespaces MQTT nao usam sufixo.
Gravar em 'mqtt_ca.0' causa AT_MQTT_CA_LENGTH_ERROR (0x6021) na conexao.

Pre-requisitos
--------------
  1. Arquivo certs/AmazonRootCA1.der  — CA raiz da Amazon (DER)
  2. Arquivo certs/device.der         — Certificado do dispositivo (DER)
  3. Arquivo certs/privace.key.der    — Chave privada RSA ou EC (DER, PKCS#1 ou PKCS#8)
  4. Certificado ativo e com policy anexada no console AWS IoT Core
  5. Ajuste as constantes WIFI_SSID, WIFI_PASSWORD, AWS_HOST, MQTT_CLIENT_ID
"""

from esp32_at import ESP32AT
import ubinascii
import time

# ══════════════════════════════════════════════════════════════════════
#                         CONFIGURACOES
# ══════════════════════════════════════════════════════════════════════

# ── Wi-Fi ──
WIFI_SSID     = "SEU_SSID"
WIFI_PASSWORD = "SUA_SENHA"

# ── AWS IoT Core ──
AWS_HOST       = "SEU_HOST.iot.REGIAO.amazonaws.com"  # Ex: "abc123def456-ats.iot.us-east-1.amazonaws.com"
AWS_PORT       = 8883                                 # Porta padrao MQTT/TLS da AWS
MQTT_CLIENT_ID = "pico2_esp32_test"                   # Deve corresponder ao Thing Name ou policy
MQTT_TOPIC     = "SEU_TOPICO"                         # Ex: "test/pico2" ou "$aws/rules/..."
MQTT_QOS       = 1                                    # QoS 0=fire-and-forget, 1=acknowledged
MQTT_KEEPALIVE = 120                                  # Intervalo keepalive em segundos

# ── Caminhos dos certificados no filesystem do Pico ──
# Os arquivos devem estar na pasta certs/ relativa ao diretorio de trabalho.
# Formatos aceitos: DER binario (.der) — sera convertido para PEM internamente.
CA_CERT_PATH  = "certs/AmazonRootCA1.der"  # CA raiz da Amazon (verifica o servidor AWS)
DEV_CERT_PATH = "certs/device.der"         # Certificado X.509 do dispositivo (identifica o cliente)
DEV_KEY_PATH  = "certs/privace.key.der"    # Chave privada RSA/EC do dispositivo (PKCS#1 ou PKCS#8)

# ── Mapeamento: (namespace_nvs, caminho_der, rotulo, tipo_pem) ──
# Cada tupla define um certificado a ser gravado na mfg_nvs do ESP32.
#
# namespace_nvs : namespace na particao mfg_nvs (tambem usado como chave NVS)
# caminho_der   : caminho do arquivo .der no filesystem do Pico
# rotulo        : texto descritivo para log
# tipo_pem      : header PEM — "CERTIFICATE" para certs, None = auto-detecta
#                 (auto-detect distingue PKCS#1 'RSA PRIVATE KEY' de PKCS#8
#                 'PRIVATE KEY'; header errado causa falha no handshake TLS)
CERTS = [
    ("mqtt_ca",   CA_CERT_PATH,  "CA (AmazonRootCA1)",   "CERTIFICATE"),
    ("mqtt_cert", DEV_CERT_PATH, "Client cert (device)",  "CERTIFICATE"),
    ("mqtt_key",  DEV_KEY_PATH,  "Client key (private)",  None),
]

# ── Payload binario de teste ──
# Substitua pelo payload real da sua aplicacao.
payload = b'\x80\x05\x00\xbfV\x13\x9e\xa2\x88\x00\x00@a\x19\xfa@a\x19\xfa\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00v3.0.13\x00\x00\x00\x00\x00"\xb4|i\x1e\x80'
# ══════════════════════════════════════════════════════════════════════
#                           FUNCOES AUXILIARES
# ══════════════════════════════════════════════════════════════════════

def detect_der_key_type(der_data):
    """Detecta se uma chave privada DER eh PKCS#1 ou PKCS#8.

    O mbedTLS (usado internamente pelo ESP-AT) escolhe o algoritmo de
    parsing com base no header PEM, por isso o tipo precisa ser correto:

      Header PEM              Parser mbedTLS
      ----------------------  ------------------------------------
      'RSA PRIVATE KEY'  →    pk_parse_key_pkcs1_der()             (PKCS#1)
      'PRIVATE KEY'      →    pk_parse_key_pkcs8_unencrypted_der() (PKCS#8)

    O tipo eh inferido inspecionando o terceiro elemento da estrutura ASN.1
    (apos o SEQUENCE externo e o INTEGER version=0):
      0x02 (INTEGER)  — modulus RSA  → PKCS#1
      0x30 (SEQUENCE) — AlgorithmIdentifier → PKCS#8

    Args:
        der_data (bytes): conteudo binario da chave privada em formato DER.

    Returns:
        str: 'PRIVATE KEY' (PKCS#8) ou 'RSA PRIVATE KEY' (PKCS#1).
             Retorna 'RSA PRIVATE KEY' como fallback em caso de dados invalidos.
    """
    if len(der_data) < 10:
        return "RSA PRIVATE KEY"  # fallback

    if der_data[0] != 0x30:
        return "RSA PRIVATE KEY"

    if der_data[1] & 0x80:
        num_len_bytes = der_data[1] & 0x7F
        offset = 2 + num_len_bytes
    else:
        offset = 2

    if (offset + 3 <= len(der_data)
            and der_data[offset] == 0x02
            and der_data[offset + 1] == 0x01
            and der_data[offset + 2] == 0x00):
        tag_after_version = der_data[offset + 3]
        if tag_after_version == 0x30:
            return "PRIVATE KEY"       # PKCS#8
        elif tag_after_version == 0x02:
            return "RSA PRIVATE KEY"   # PKCS#1

    return "RSA PRIVATE KEY"


def der_to_pem(der_data, pem_type="CERTIFICATE"):
    """Converte um certificado ou chave de DER binario para PEM (Base64).

    O ESP-AT/mbedTLS identifica o formato PEM pela presenca do header
    '-----BEGIN ...-----'. O terminador nulo (\x00) ao final e obrigatorio:
    o mbedTLS usa strlen() internamente e so reconhece o bloco PEM se houver
    um byte nulo apos a ultima linha '-----END ...-----\n'.

    Args:
        der_data (bytes): conteudo binario do certificado ou chave em DER.
        pem_type (str | None): tipo a usar no header/footer PEM.
            'CERTIFICATE'    — para CA e certificados de dispositivo.
            'RSA PRIVATE KEY'— para chaves PKCS#1.
            'PRIVATE KEY'    — para chaves PKCS#8.
            None             — auto-detecta o tipo via detect_der_key_type().

    Returns:
        bytes: dados PEM codificados em Base64 com header, footer e \x00 final.
    """
    if pem_type is None:
        pem_type = detect_der_key_type(der_data)
        print(f"  [Auto-detect] Formato da chave: {pem_type}")

    b64 = ubinascii.b2a_base64(der_data).decode().strip()
    lines = [b64[i:i+64] for i in range(0, len(b64), 64)]
    pem  = f"-----BEGIN {pem_type}-----\n"
    pem += "\n".join(lines)
    pem += f"\n-----END {pem_type}-----\n"
    return pem.encode() + b'\x00'


def step_header(n, title):
    """Imprime um cabecalho de secao numerado para facilitar leitura do log."""
    print(f"\n{'=' * 60}")
    print(f"  PASSO {n}: {title}")
    print(f"{'=' * 60}")


# ══════════════════════════════════════════════════════════════════════
#                           EXECUCAO
# ══════════════════════════════════════════════════════════════════════

def main():
    """Executa o fluxo completo de conexao MQTT ao AWS IoT Core.

    Sequencia:
      0. Diagnostico — exibe versao do firmware e estado dos namespaces NVS
      1. Wi-Fi + SNTP — conecta a rede e sincroniza o relogio
      2. Certificados — grava CA, cert e chave na mfg_nvs via AT+SYSMFG
      3. Conectividade — confirma IP e hora valida pre-conexao
      4. MQTT — configura TLS mutual, conecta ao broker e verifica estado
      5. Publicacao — publica o payload binario no topico configurado
    """
    # Inicializa o driver AT — executa reset hardware e aguarda 'ready'
    esp = ESP32AT(uart_id=1, tx=4, rx=5, reset_pin=6)
    # Habilita log detalhado de erros AT (inclui codigos TLS/MQTT como 0x6021)
    esp.enable_syslog()

    # ═══════════════════ PASSO 0 ═══════════════════
    step_header(0, "Diagnostico do firmware")

    print("Versao:")
    resp = esp.send_cmd("AT+GMR", timeout=3000)
    print(resp.strip())

    print("\nNamespaces NVS (mqtt_*):")
    for ns in ["mqtt_ca", "mqtt_cert", "mqtt_key"]:
        resp = esp.send_cmd(f'AT+SYSMFG=1,"{ns}"', timeout=2000)
        if "ERROR" not in resp:
            print(f"  {ns}: {resp.strip()}")
        else:
            print(f"  {ns}: (vazio/nao existe)")
    print()

    # ═══════════════════ PASSO 1 ═══════════════════
    step_header(1, "Sincronizando relogio via SNTP")

    # O SNTP precisa de Wi-Fi, mas o relogio precisa estar certo antes do TLS:
    # certificados tem periodo de validade e o mbedTLS rejeita conexoes se o
    # horario do dispositivo estiver fora desse intervalo.
    print("Conectando Wi-Fi...")
    resp = esp.connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    print("[WIFI]", resp.strip())
    if "ERROR" in resp and "GOT IP" not in resp:
        print("ERRO: Wi-Fi falhou.")
        return

    # timezone=0 = UTC; ajuste se necessario (ex: -3 para BRT)
    resp = esp.sntp_config(timezone=0)
    print("[SNTP CFG]", resp.strip())

    # Aguarda ate 15 s para o SNTP sincronizar (resposta sai de 1970 para ano atual)
    time_synced = False
    for _ in range(15):
        time.sleep(1)
        resp = esp.sntp_time()
        t = resp.strip()
        print(f"  {t}")
        if "202" in t and "1970" not in t:  # Ano >= 2020 = sincronizado
            time_synced = True
            break

    if not time_synced:
        print("ERRO: relogio nao sincronizou.")
        esp.disconnect_wifi()
        return

    print("Relogio sincronizado!")

    # ═══════════════════ PASSO 2 ═══════════════════
    step_header(2, "Carregando certificados")

    # 2a) Apaga os namespaces MQTT antes de gravar
    # Necessario para evitar que dados antigos ou corrompidos de execucoes
    # anteriores interfiram. AT+SYSMFG=0,"ns" apaga todas as chaves do namespace.
    print("Limpando namespaces MQTT antigos...")
    for namespace, _, _, _ in CERTS:
        esp.sysmfg_erase(namespace)
        time.sleep_ms(100)
    print("  Namespaces limpos.")

    # 2b) Converte DER → PEM e grava cada certificado na mfg_nvs
    # O mbedTLS do ESP-AT exige formato PEM (Base64 com headers). Os arquivos
    # .der fornecidos pelo AWS IoT Core sao convertidos em tempo de execucao.
    all_ok = True
    for namespace, path, label, pem_type in CERTS:
        try:
            with open(path, "rb") as f:
                der_data = f.read()
        except OSError:
            print(f"  ERRO: '{path}' nao encontrado no Pico!")
            all_ok = False
            continue

        pem_data = der_to_pem(der_data, pem_type)
        print(f"  {label} (DER:{len(der_data)} -> PEM:{len(pem_data)} bytes)...", end=" ")

        # IMPORTANTE: namespace e chave NVS tem o mesmo nome (ex: 'mqtt_ca')
        # O build system do ESP-AT (mfg_nvs.py) nao adiciona sufixo '.0' para
        # namespaces MQTT, pois cada um contem apenas um arquivo de certificado.
        # Gravar em 'mqtt_ca.0' faria o MQTT retornar AT_MQTT_CA_LENGTH_ERROR.
        ok = esp.sysmfg_write(namespace, namespace, pem_data)

        print("OK" if ok else "FALHOU")
        if not ok:
            all_ok = False
        time.sleep_ms(300)

    if not all_ok:
        print("ERRO: falha ao gravar certificados.")
        esp.disconnect_wifi()
        return

    # 2c) Confirma que as chaves foram gravadas listando o namespace
    # AT+SYSMFG=1,"ns" retorna as chaves existentes e seus tipos (type=8 = blob)
    print("\nVerificando certificados gravados:")
    for namespace, _, label, _ in CERTS:
        resp = esp.send_cmd(f'AT+SYSMFG=1,"{namespace}"', timeout=2000)
        if "ERROR" not in resp:
            print(f"  {namespace}: {resp.strip()}")
        else:
            print(f"  {namespace}: ERRO na verificacao!")
            all_ok = False

    if not all_ok:
        print("ERRO: verificacao dos certificados falhou.")
        esp.disconnect_wifi()
        return

    print("Certificados gravados e verificados com sucesso!")

    # ═══════════════════ PASSO 3 ═══════════════════
    step_header(3, "Verificando conectividade")

    resp = esp.get_ip()
    print(resp.strip())
    if "ERROR" in resp or "0.0.0.0" in resp:
        print("ERRO: sem conectividade Wi-Fi.")
        return

    resp = esp.sntp_time()
    print("[Hora]", resp.strip())
    print("Conectividade OK!")

    # ═══════════════════ PASSO 4 ═══════════════════
    step_header(4, "Conectando MQTT ao AWS IoT Core")

    # Descarta qualquer sessao MQTT aberta de execucao anterior
    esp.mqtt_clean()
    time.sleep_ms(500)

    # 4a) AT+MQTTUSERCFG — define identidade e esquema TLS do cliente MQTT
    #   scheme=5  : MQTT sobre TLS com autenticacao mutua (cliente e servidor
    #               se autenticam). Exige que mqtt_ca, mqtt_cert e mqtt_key
    #               estejam gravados na mfg_nvs.
    #   cert_key_ID=0, CA_ID=0 : slot 0 dos namespaces mqtt_cert/mqtt_key/mqtt_ca
    print("Configurando MQTT user (scheme=5, mutual TLS)...")
    resp = esp.mqtt_user_cfg(MQTT_CLIENT_ID, scheme=5)
    print("[MQTTUSERCFG]", resp.strip())
    if "ERROR" in resp:
        print("ERRO: AT+MQTTUSERCFG falhou.")
        return

    # 4b) AT+MQTTSNI — define o Server Name Indication para o handshake TLS
    #   Obrigatorio para AWS IoT Core: o servidor usa SNI para selecionar
    #   o certificado correto quando multiplos dominios compartilham o mesmo IP.
    print(f"Configurando MQTT SNI: {AWS_HOST}")
    resp = esp.mqtt_sni(AWS_HOST)
    print("[MQTTSNI]", resp.strip())
    if "ERROR" in resp:
        print("AVISO: AT+MQTTSNI falhou (firmware pode nao suportar).")

    # 4c) AT+MQTTCONNCFG — parametros de sessao MQTT (keepalive, LWT, etc)
    print("Configurando MQTT connection params...")
    resp = esp.mqtt_conn_cfg(keepalive=MQTT_KEEPALIVE)
    print("[MQTTCONNCFG]", resp.strip())

    # 4d) AT+MQTTCONN — estabelece a conexao TCP+TLS+MQTT com o broker
    # reconnect=1 habilita reconexao automatica apos queda de rede.
    # Estado esperado apos conexao bem-sucedida: +MQTTCONN:0,4,5,...
    #   campo 2 = estado: 4=conectado, 5=desconectando, 6=desconectado
    MAX_RETRIES = 3
    connected = False
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\nTentativa {attempt}/{MAX_RETRIES}: Conectando a {AWS_HOST}:{AWS_PORT}...")
        resp = esp.mqtt_connect(AWS_HOST, AWS_PORT, reconnect=1)
        print("[MQTTCONN]", resp.strip())

        if "MQTTCONNECTED" in resp or ("OK" in resp and "ERROR" not in resp):
            time.sleep(1)  # Aguarda estabilizacao antes de consultar estado
            state_resp = esp.mqtt_state()
            print("[STATE]", state_resp.strip())
            if any(f",{s}," in state_resp for s in ["4", "5", "6"]):
                connected = True
                break

        print(f"  Falhou. Resposta: {resp.strip()}")
        if attempt < MAX_RETRIES:
            # Reinicia a configuracao MQTT completa antes de tentar novamente
            print("  Limpando e re-tentando em 3s...")
            esp.mqtt_clean()
            time.sleep(3)
            esp.mqtt_user_cfg(MQTT_CLIENT_ID, scheme=5)
            time.sleep_ms(200)
            esp.mqtt_sni(AWS_HOST)
            time.sleep_ms(200)
            esp.mqtt_conn_cfg(keepalive=MQTT_KEEPALIVE)
            time.sleep_ms(200)

    if not connected:
        print("\nERRO: Nao foi possivel conectar ao AWS IoT Core.")
        print("Verifique:")
        print("  1. Certificados .der corretos na pasta certs/")
        print("  2. Certificados ativos no console AWS IoT Core")
        print("  3. Policy anexada aos certificados")
        print("  4. Endpoint correto:", AWS_HOST)
        print("  5. Relogio sincronizado (TLS valida periodo)")
        print("\nEstado MQTT final:")
        print(esp.mqtt_state().strip())
        esp.mqtt_clean()
        esp.disconnect_wifi()
        return

    print("\nMQTT conectado com sucesso ao AWS IoT Core!")

    # ═══════════════════ PASSO 5 ═══════════════════
    step_header(5, "Publicando MQTT")

    print(f"  Topico: {MQTT_TOPIC}")
    print(f"  Payload: {len(payload)} bytes (binario)")
    print(f"  QoS: {MQTT_QOS}")

    # AT+MQTTPUBRAW aceita dados binarios arbitrarios (ao contrario de
    # AT+MQTTPUB que trata o payload como string e rejeita bytes nulos).
    # O ESP-AT envia '>' como prompt e aguarda exatamente <length> bytes.
    resp = esp.mqtt_pub_raw(MQTT_TOPIC, payload, qos=MQTT_QOS)
    print("[MQTTPUBRAW]", resp.strip())

    if "MQTTPUB:OK" in resp:
        print("Publicacao confirmada pelo broker!")  # QoS 1: PUBACK recebido
    elif "OK" in resp and "FAIL" not in resp:
        print("Publicacao enviada.")                 # QoS 0: sem confirmacao
    else:
        print("AVISO: publicacao pode ter falhado.")

    # ── Limpeza ──
    print(f"\n{'=' * 60}")
    print("  Desconectando...")
    print(f"{'=' * 60}")
    esp.mqtt_clean()
    esp.disconnect_wifi()
    print("\nTeste MQTT finalizado com sucesso!")


# ── Executa ──
main()
