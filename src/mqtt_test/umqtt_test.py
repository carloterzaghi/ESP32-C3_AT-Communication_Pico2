"""
umqtt_test.py — Teste de transmissao MQTT via ESP32-C3 AT Commands

Conecta ao Wi-Fi, configura o cliente MQTT no firmware do ESP32-C3 e
publica um payload binario num topico MQTT.

Suporta dois modos:
  1. MQTT simples (TCP)       — para brokers publicos (ex: test.mosquitto.org)
  2. MQTT sobre TLS mutual    — para AWS IoT Core (certificados pre-gravados no ESP32)

Antes de usar com AWS IoT Core:
  - Grave os certificados na particao PKI do ESP32 com AT+SYSFLASH ou
    pre-flashe no mfg_nvs.bin conforme doc Espressif.
  - Ajuste MQTT_SCHEME=5 e as credenciais abaixo.
"""

from esp32_at import ESP32AT
import time
import load_certs

# ══════════════════════════════════════════════════════════════════════
#                         CONFIGURACOES
# ══════════════════════════════════════════════════════════════════════

# ── Wi-Fi ──
WIFI_SSID     = "SEU_SSID"
WIFI_PASSWORD = "SUA_SENHA"

# ── MQTT Broker ──
# AWS IoT Core — TLS mutual (certificados pre-gravados no ESP32):
MQTT_SCHEME    = 5                        # 1=TCP, 2=TLS sem cert, 5=TLS mutual (AWS IoT)
MQTT_HOST      = "a3p8fp5lk2prw7-ats.iot.us-west-2.amazonaws.com"
MQTT_PORT      = 8883                     # 1883=TCP, 8883=TLS
MQTT_CLIENT_ID = "pico2_esp32_test"
MQTT_USERNAME  = ""                       # Vazio se nao precisar
MQTT_PASSWORD  = ""
MQTT_TOPIC     = "$aws/rules/Prd_IoT_EF_BasicIngestToFirehose"
MQTT_QOS       = 1
MQTT_KEEPALIVE = 120

# ── Payload binario de teste (original do projeto) ──
payload = (
    b'\x80\x1e\x00G\xf4\x10\x00\x00\x00\x00\x00@a\x19\xfa@a\x19\xfa'
    b'\x03\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
    b'\x00\x00\x00\x00\x00\x00\x00\x00\x004v\x00\x00\x00\x00\x00\x00'
    b'\x00\x00 \x03\xa9\xa9\xa0i\x00\x80'
)

# ══════════════════════════════════════════════════════════════════════
#                           EXECUCAO
# ══════════════════════════════════════════════════════════════════════

def main():
    # ── Inicializa ESP32-C3 ──
    esp = ESP32AT(uart_id=1, tx=4, rx=5, reset_pin=6)

    # ── Conecta Wi-Fi ──
    print("\n=== Conectando Wi-Fi ===")
    resp = esp.connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    print("[WIFI]", resp.strip())
    if "ERROR" in resp and "GOT IP" not in resp:
        print("ERRO: falha ao conectar Wi-Fi.")
        return

    resp = esp.get_ip()
    print("[IP]", resp.strip())

    time.sleep_ms(500)

    # ── Configura cliente MQTT ──
    print("\n=== Configurando MQTT ===")
    resp = esp.mqtt_user_cfg(
        client_id=MQTT_CLIENT_ID,
        scheme=MQTT_SCHEME,
        username=MQTT_USERNAME,
        password=MQTT_PASSWORD
    )
    print("[MQTTUSERCFG]", resp.strip())
    if "ERROR" in resp:
        print("ERRO: falha ao configurar MQTT. Verifique se o firmware suporta MQTT.")
        esp.disconnect_wifi()
        return

    # ── Configura parametros de conexao ──
    resp = esp.mqtt_conn_cfg(keepalive=MQTT_KEEPALIVE)
    print("[MQTTCONNCFG]", resp.strip())

    # ── Conecta ao broker ──
    print(f"\n=== Conectando ao broker {MQTT_HOST}:{MQTT_PORT} ===")
    resp = esp.mqtt_connect(MQTT_HOST, MQTT_PORT, reconnect=0)
    print("[MQTTCONN]", resp.strip())
    if "ERROR" in resp:
        print("ERRO: falha ao conectar ao broker MQTT.")
        esp.disconnect_wifi()
        return

    time.sleep(1)

    # ── Publica payload binario ──
    print(f"\n=== Publicando {len(payload)} bytes em '{MQTT_TOPIC}' ===")
    resp = esp.mqtt_pub_raw(MQTT_TOPIC, payload, qos=MQTT_QOS)
    print("[MQTTPUBRAW]", resp.strip())

    if "ERROR" not in resp:
        print("Payload publicado com sucesso!")
    else:
        print("ERRO ao publicar payload.")

    # ── Publica tambem como hex (debug — facilita leitura no broker) ──
    hex_str = payload.hex()
    print(f"\n=== Publicando hex em '{MQTT_TOPIC}/hex' (debug) ===")
    resp = esp.mqtt_pub(f"{MQTT_TOPIC}/hex", hex_str, qos=0)
    print("[MQTTPUB hex]", resp.strip())

    # ── Limpeza ──
    time.sleep(1)
    print("\n=== Desconectando ===")
    resp = esp.mqtt_clean()
    print("[MQTTCLEAN]", resp.strip())

    resp = esp.disconnect_wifi()
    print("[WIFI OFF]", resp.strip())

    print("\n=== Teste MQTT finalizado ===")


# ── Executa ao importar ou rodar diretamente ──
print("\n" + "=" * 60)
print("  PASSO 1: Gravando certificados no ESP32-C3")
print("=" * 60)
load_certs.main()

print("\n" + "=" * 60)
print("  PASSO 2: Teste MQTT")
print("=" * 60)
main()