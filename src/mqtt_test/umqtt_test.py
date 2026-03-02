"""
umqtt_test.py — Teste MQTT via ESP32-C3 AT Commands para AWS IoT Core

Abordagem baseada na documentacao oficial Espressif:
  https://docs.espressif.com/projects/esp-at/en/latest/esp32c3/AT_Command_Examples/mqtt-at-examples-for-cloud.html

Usa o cliente MQTT embarcado do ESP32-C3 com TLS mutual auth (scheme=5):
  AT+MQTTUSERCFG → AT+MQTTSNI → AT+MQTTCONNCFG → AT+MQTTCONN → AT+MQTTPUBRAW

Certificados sao gravados na particao mfg_nvs via AT+SYSMFG nos namespaces:
  mqtt_ca   → CA do servidor (AmazonRootCA1)
  mqtt_cert → certificado do cliente (device)
  mqtt_key  → chave privada do cliente

IMPORTANTE: O build system do ESP-AT (mfg_nvs.py) cria as chaves NVS
SEM o sufixo '.0' para namespaces MQTT (que tem uma unica cert):
  namespace='mqtt_ca',   key='mqtt_ca'   (NAO 'mqtt_ca.0')
  namespace='mqtt_cert', key='mqtt_cert' (NAO 'mqtt_cert.0')
  namespace='mqtt_key',  key='mqtt_key'  (NAO 'mqtt_key.0')

Isto difere dos namespaces SSL client (client_cert.0, client_cert.1, etc)
que usam '.N' porque tem multiplos conjuntos de certs no firmware.

Antes de usar:
  - Copie os .der para a pasta certs/ no filesystem do Pico
  - Ajuste WIFI_SSID, WIFI_PASSWORD, AWS_HOST e caminhos dos certs
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
AWS_PORT       = 8883
MQTT_CLIENT_ID = "pico2_esp32_test"
MQTT_TOPIC     = "SEU_TOPICO"  # Ex: "test/pico2"
MQTT_QOS       = 1
MQTT_KEEPALIVE = 120

# ── Certificados (.der no filesystem do Pico) ──
CA_CERT_PATH  = "certs/AmazonRootCA1.der"
DEV_CERT_PATH = "certs/device.der"
DEV_KEY_PATH  = "certs/privace.key.der"

# ── Mapeamento certificados → namespaces NVS no mfg_nvs ──
# O ESP-AT (scheme=5, cert_key_ID=0, CA_ID=0) le certs dos namespaces:
#   mqtt_ca   → CA do servidor
#   mqtt_cert → certificado do cliente
#   mqtt_key  → chave privada do cliente
# CHAVE NVS = nome do namespace (SEM '.0') conforme mfg_nvs.py do ESP-AT
# pem_type=None → auto-detecta PKCS#1 vs PKCS#8 para chaves privadas
CERTS = [
    ("mqtt_ca",   CA_CERT_PATH,  "CA (AmazonRootCA1)",   "CERTIFICATE"),
    ("mqtt_cert", DEV_CERT_PATH, "Client cert (device)",  "CERTIFICATE"),
    ("mqtt_key",  DEV_KEY_PATH,  "Client key (private)",  None),
]

# ── Payload binario de teste ──
payload = b'\x80\x05\x00\xbfV\x13\x9e\xa2\x88\x00\x00@a\x19\xfa@a\x19\xfa\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00v3.0.13\x00\x00\x00\x00\x00"\xb4|i\x1e\x80'
# ══════════════════════════════════════════════════════════════════════
#                           FUNCOES AUXILIARES
# ══════════════════════════════════════════════════════════════════════

def detect_der_key_type(der_data):
    """Detecta se a chave privada DER eh PKCS#1 ou PKCS#8.

    Estrutura ASN.1 apos SEQUENCE + version(INTEGER 0):
      PKCS#1: proximo elemento eh INTEGER (0x02) = modulus n
      PKCS#8: proximo elemento eh SEQUENCE (0x30) = AlgorithmIdentifier

    mbedTLS usa o header PEM para escolher o parser:
      'RSA PRIVATE KEY' → pk_parse_key_pkcs1_der()
      'PRIVATE KEY'     → pk_parse_key_pkcs8_unencrypted_der()
    Header errado = parser errado = falha no TLS handshake.
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
    """Converte dados DER binarios para formato PEM (Base64 com headers).
    Se pem_type=None, auto-detecta PKCS#1 vs PKCS#8 para chaves privadas.
    Adiciona null terminator para mbedTLS detectar PEM.
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
    print(f"\n{'=' * 60}")
    print(f"  PASSO {n}: {title}")
    print(f"{'=' * 60}")


# ══════════════════════════════════════════════════════════════════════
#                           EXECUCAO
# ══════════════════════════════════════════════════════════════════════

def main():
    esp = ESP32AT(uart_id=1, tx=4, rx=5, reset_pin=6)
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

    print("Conectando Wi-Fi...")
    resp = esp.connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    print("[WIFI]", resp.strip())
    if "ERROR" in resp and "GOT IP" not in resp:
        print("ERRO: Wi-Fi falhou.")
        return

    resp = esp.sntp_config(timezone=0)
    print("[SNTP CFG]", resp.strip())

    time_synced = False
    for _ in range(15):
        time.sleep(1)
        resp = esp.sntp_time()
        t = resp.strip()
        print(f"  {t}")
        if "202" in t and "1970" not in t:
            time_synced = True
            break

    if not time_synced:
        print("ERRO: relogio nao sincronizou.")
        esp.disconnect_wifi()
        return

    print("Relogio sincronizado!")

    # ═══════════════════ PASSO 2 ═══════════════════
    step_header(2, "Carregando certificados")

    # 2a) Limpa namespaces MQTT para remover dados antigos/corruptos
    print("Limpando namespaces MQTT antigos...")
    for namespace, _, _, _ in CERTS:
        esp.sysmfg_erase(namespace)
        time.sleep_ms(100)
    print("  Namespaces limpos.")

    # 2b) Converte DER→PEM e grava cada certificado
    all_ok = True
    for namespace, path, label, pem_type in CERTS:
        try:
            with open(path, "rb") as f:
                der_data = f.read()
        except OSError:
            print(f"  ERRO: '{path}' nao encontrado no Pico!")
            all_ok = False
            continue

        # Converte DER → PEM (formato esperado pelo mbedTLS do ESP-AT)
        pem_data = der_to_pem(der_data, pem_type)
        print(f"  {label} (DER:{len(der_data)} -> PEM:{len(pem_data)} bytes)...", end=" ")

        # Grava no mfg_nvs: namespace e key tem o MESMO nome (sem '.0')
        # Conforme mfg_nvs.py do ESP-AT para namespaces com cert unica
        ok = esp.sysmfg_write(namespace, namespace, pem_data)

        print("OK" if ok else "FALHOU")
        if not ok:
            all_ok = False
        time.sleep_ms(300)

    if not all_ok:
        print("ERRO: falha ao gravar certificados.")
        esp.disconnect_wifi()
        return

    # 2c) Verifica o que foi gravado
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

    # Limpa sessao MQTT anterior (ignora erro se nao havia)
    esp.mqtt_clean()
    time.sleep_ms(500)

    # 4a) Configurar usuario MQTT
    # scheme=5: MQTT over TLS (verify server cert + provide client cert)
    # cert_key_ID=0, CA_ID=0 -> usa mqtt_cert/mqtt_key/mqtt_ca slot 0
    print("Configurando MQTT user (scheme=5, mutual TLS)...")
    resp = esp.mqtt_user_cfg(MQTT_CLIENT_ID, scheme=5)
    print("[MQTTUSERCFG]", resp.strip())
    if "ERROR" in resp:
        print("ERRO: AT+MQTTUSERCFG falhou.")
        return

    # 4b) Configurar SNI (obrigatorio para AWS IoT Core)
    print(f"Configurando MQTT SNI: {AWS_HOST}")
    resp = esp.mqtt_sni(AWS_HOST)
    print("[MQTTSNI]", resp.strip())
    if "ERROR" in resp:
        print("AVISO: AT+MQTTSNI falhou (firmware pode nao suportar).")

    # 4c) Configurar parametros de conexao
    print("Configurando MQTT connection params...")
    resp = esp.mqtt_conn_cfg(keepalive=MQTT_KEEPALIVE)
    print("[MQTTCONNCFG]", resp.strip())

    # 4d) Conectar ao broker
    MAX_RETRIES = 3
    connected = False
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\nTentativa {attempt}/{MAX_RETRIES}: Conectando a {AWS_HOST}:{AWS_PORT}...")
        resp = esp.mqtt_connect(AWS_HOST, AWS_PORT, reconnect=1)
        print("[MQTTCONN]", resp.strip())

        if "MQTTCONNECTED" in resp or ("OK" in resp and "ERROR" not in resp):
            time.sleep(1)
            state_resp = esp.mqtt_state()
            print("[STATE]", state_resp.strip())
            if any(f",{s}," in state_resp for s in ["4", "5", "6"]):
                connected = True
                break

        print(f"  Falhou. Resposta: {resp.strip()}")
        if attempt < MAX_RETRIES:
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

    resp = esp.mqtt_pub_raw(MQTT_TOPIC, payload, qos=MQTT_QOS)
    print("[MQTTPUBRAW]", resp.strip())

    if "MQTTPUB:OK" in resp:
        print("Publicacao confirmada pelo broker!")
    elif "OK" in resp and "FAIL" not in resp:
        print("Publicacao enviada.")
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
