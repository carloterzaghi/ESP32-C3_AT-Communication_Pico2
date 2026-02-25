from esp32_at import ESP32AT
import time

# UART1, GP4=TX, GP5=RX, GP6=Reset (EN)
esp = ESP32AT(uart_id=1, tx=4, rx=5, reset_pin=6)

print("\n=== Iniciando BLE Peripheral ===")
resp = esp.ble_init()
print(resp)

resp = esp.ble_set_name("Pico2-BLE")
print(resp)

resp = esp.ble_start_advertising()
print(resp)

print("Anunciando BLE como 'Pico2-BLE'... esperando conexão.")

# Aguarda conexões e dados
while True:
    if esp.uart.any():
        data = esp.uart.read(esp.uart.any())
        if data:
            print("Recebido:", data.decode("utf-8", "replace"))
    time.sleep_ms(100)