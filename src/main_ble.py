from esp32_at import ESP32AT
import time

BLE_NAME = "Pico2-BLE"

# UART1, GP4=TX, GP5=RX, GP6=Reset (EN)
esp = ESP32AT(uart_id=1, tx=4, rx=5, reset_pin=6)

print("\n=== Iniciando BLE Peripheral ===")

# 1. Inicializa BLE no modo peripheral
resp = esp.ble_init()
print("[BLEINIT]", resp.strip())
if "ERROR" in resp:
    print("ERRO: falha ao inicializar BLE. Verifique o firmware AT do ESP32.")
    raise SystemExit

time.sleep_ms(500)

# 2. Define o nome do dispositivo
resp = esp.ble_set_name(BLE_NAME)
print("[BLENAME]", resp.strip())

# 3. Configura parametros de advertising (connectable, 100 ms, todos os canais)
resp = esp.ble_set_adv_param()
print("[BLEADVPARAM]", resp.strip())

# 4. *** ESSENCIAL: inclui o nome nos dados do pacote de advertising ***
#    Usa AT+BLEADVDATA com AD type 0x09 (Complete Local Name).
#    Sem este passo o dispositivo anuncia sem nome e nao aparece para scanners.
resp = esp.ble_set_adv_data(BLE_NAME)
print("[BLEADVDATA]", resp.strip())
if "ERROR" in resp:
    print("ERRO: nao foi possivel definir os dados de advertising.")

# 5. Exibe endereco MAC BLE para debug
resp = esp.ble_get_addr()
print("[BLEADDR]", resp.strip())

# 6. Inicia advertising
resp = esp.ble_start_advertising()
print("[BLEADVSTART]", resp.strip())

if "OK" in resp:
    print(f"\nAnunciando BLE como '{BLE_NAME}'... esperando conexao.")
else:
    print("ERRO ao iniciar advertising:", resp.strip())

# Aguarda conexoes e dados
while True:
    if esp.uart.any():
        data = esp.uart.read(esp.uart.any())
        if data:
            print("Recebido:", data.decode("utf-8", "replace"))
    time.sleep_ms(100)