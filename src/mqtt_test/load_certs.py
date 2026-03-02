"""
load_certs.py — Carrega certificados AWS IoT Core no ESP32-C3 via AT+SYSMFG

O firmware ESP-AT v4.x armazena os certificados PKI na particao mfg_nvs.
Este script le os arquivos .der do filesystem do Pico e os grava no ESP32
usando o comando AT+SYSMFG (tipo 4 = blob binario).

IMPORTANTE: Copie a pasta certs/ para o filesystem do Pico antes de executar!
  - certs/AmazonRootCA1.der   → CA do servidor (Amazon Root CA)
  - certs/device.der          → Certificado do dispositivo (client cert)
  - certs/privace.key.der     → Chave privada do dispositivo (client key)

Apos gravar, reinicie o ESP32 (ou desligue/ligue) antes de testar o MQTT.
"""

from esp32_at import ESP32AT
import time

# ── Caminhos dos certificados no filesystem do Pico ──
CA_CERT_PATH  = "certs/AmazonRootCA1.der"
DEV_CERT_PATH = "certs/device.der"
DEV_KEY_PATH  = "certs/privace.key.der"

# ── Mapeamento: (namespace, key) usados pelo ESP-AT para MQTT PKI ──
# cert_key_ID=0 e CA_ID=0 no AT+MQTTUSERCFG referem a estes slots.
PKI_ITEMS = [
    ("mqtt_ca",   "mqtt_ca",   CA_CERT_PATH,  "CA cert (AmazonRootCA1)"),
    ("mqtt_cert", "mqtt_cert", DEV_CERT_PATH, "Client cert (device)"),
    ("mqtt_key",  "mqtt_key",  DEV_KEY_PATH,  "Client key (private key)"),
]


def write_pki_item(esp, namespace, key, file_path, label):
    """Grava um arquivo binario no manufacturing NVS via AT+SYSMFG.
    type=8 indica 'blob' (dados binarios de tamanho variavel)."""
    try:
        with open(file_path, "rb") as f:
            data = f.read()
    except OSError:
        print(f"  ERRO: arquivo '{file_path}' nao encontrado no Pico!")
        print(f"         Copie a pasta certs/ para o filesystem do Pico.")
        return False

    length = len(data)
    print(f"\n  [{label}]")
    print(f"  namespace={namespace}, key={key}, {length} bytes")

    # Limpa buffer UART agressivamente
    time.sleep_ms(300)
    while esp.uart.any():
        esp.uart.read()
    time.sleep_ms(200)
    while esp.uart.any():
        esp.uart.read()

    # Envia comando AT+SYSMFG: operation=2(write), type=8(blob)
    cmd = f'AT+SYSMFG=2,"{namespace}","{key}",8,{length}'
    print(f"  CMD: {cmd}")
    esp.uart.write((cmd + "\r\n").encode())

    # Coleta TODA a resposta por 8 segundos para debug
    buf = b""
    t0 = time.ticks_ms()
    got_prompt = False
    while time.ticks_diff(time.ticks_ms(), t0) < 8000:
        if esp.uart.any():
            chunk = esp.uart.read()
            if chunk:
                buf += chunk
            if b">" in buf and not got_prompt:
                got_prompt = True
                break
            if b"ERROR" in buf:
                break
        time.sleep_ms(10)

    # Debug: mostra exatamente o que recebeu
    print(f"  RAW ({len(buf)} bytes): {buf}")
    print(f"  DECODED: [{buf.decode('utf-8', 'ignore').strip()}]")
    print(f"  got_prompt={got_prompt}")

    if not got_prompt:
        print(f"  ABORT: nao recebeu '>'")
        return False

    # Pequena pausa antes de enviar os dados
    time.sleep_ms(100)

    # Envia dados binarios em chunks (UART buffer pode ser pequeno)
    print(f"  Enviando {length} bytes de dados...")
    CHUNK = 64
    for i in range(0, length, CHUNK):
        esp.uart.write(data[i:i+CHUNK])
        time.sleep_ms(20)  # Dá tempo ao ESP32 processar cada chunk

    # Aguarda a gravação na NVS (pode ser lento)
    time.sleep_ms(500)

    # Espera confirmacao
    resp_buf = b""
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 15000:
        if esp.uart.any():
            chunk = esp.uart.read()
            if chunk:
                resp_buf += chunk
            if b"OK" in resp_buf:
                break
            if b"ERROR" in resp_buf:
                break
        time.sleep_ms(10)

    resp = resp_buf.decode("utf-8", "ignore")
    print(f"  RESP RAW ({len(resp_buf)} bytes): {resp_buf}")
    print(f"  RESP: [{resp.strip()}]")

    if "OK" in resp:
        print(f"  OK!")
        return True
    else:
        print(f"  FALHOU")
        # Tenta recuperar o ESP32 enviando AT
        esp.uart.write(b"\r\n")
        time.sleep_ms(500)
        while esp.uart.any():
            esp.uart.read()
        esp.send_cmd("AT", timeout=2000)
        return False


def main():
    esp = ESP32AT(uart_id=1, tx=4, rx=5, reset_pin=6)

    # ── Desabilita auto-conexao WiFi (so vale apos reboot) ──
    print("\n=== Desabilitando WiFi auto-connect ===")
    esp.send_cmd("AT+CWAUTOCONN=0", timeout=2000)
    time.sleep_ms(200)

    # ── Desconecta WiFi atual ──
    esp.send_cmd("AT+CWQAP", timeout=3000)
    time.sleep_ms(500)

    # ── Reset para aplicar CWAUTOCONN=0 ──
    print("=== Reset para desativar WiFi auto-connect ===")
    esp._hw_reset()
    time.sleep(2)

    # Limpa qualquer lixo residual
    while esp.uart.any():
        esp.uart.read()
    time.sleep_ms(500)
    while esp.uart.any():
        esp.uart.read()

    # Confirma que WiFi nao conectou
    print("=== Verificando estado WiFi ===")
    resp = esp.send_cmd("AT+CWJAP?", timeout=2000)
    print(resp.strip())

    # ── Diagnostico ──
    print("\n=== Diagnostico: verificando suporte AT+SYSMFG ===")
    resp = esp.send_cmd("AT+SYSMFG?", timeout=2000)
    print(resp.strip())

    print("\n=== Diagnostico: particoes (AT+SYSFLASH?) ===")
    resp = esp.send_cmd("AT+SYSFLASH?", timeout=2000)
    print(resp.strip())

    # ── Grava certificados ──
    print("\n" + "=" * 50)
    print("  Gravando certificados no ESP32-C3 (mfg_nvs)")
    print("=" * 50)

    results = []
    for namespace, key, path, label in PKI_ITEMS:
        ok = write_pki_item(esp, namespace, key, path, label)
        results.append((label, ok))
        time.sleep_ms(500)  # Pausa entre gravacoes

    # ── Resultado ──
    print("\n" + "=" * 50)
    print("  RESULTADO")
    print("=" * 50)
    all_ok = True
    for label, ok in results:
        status = "OK" if ok else "FALHOU"
        print(f"  {label}: {status}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\nCertificados gravados com sucesso!")
        print("Reabilitando WiFi auto-connect...")
        esp.send_cmd("AT+CWAUTOCONN=1", timeout=2000)
        print("Reinicie o ESP32 e execute umqtt_test.py.")
    else:
        print("\nAlguns certificados falharam.")
        print("Reabilitando WiFi auto-connect...")
        esp.send_cmd("AT+CWAUTOCONN=1", timeout=2000)
        print("Possiveis causas:")
        print("  - Firmware nao suporta AT+SYSMFG (verifique versao)")
        print("  - Namespaces mqtt_ca/client_cert/client_key nao existem")
        print("  - Arquivos .der nao estao no filesystem do Pico")
        print("")
        print("Alternativa: use esptool.py no PC para flashar os certs")
        print("  na particao mfg_nvs do ESP32-C3.")
