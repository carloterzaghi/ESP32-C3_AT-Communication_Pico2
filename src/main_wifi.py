from esp32c3_at import ESP32C3_AT

# UART1, GP4=TX, GP5=RX, GP6=Reset (EN)
esp = ESP32C3_AT(uart_id=1, tx=4, rx=5, reset_pin=6)

print("\n=== Testando módulo ===")
print(esp.send_cmd("AT"))

print("\n=== Versão ===")
print(esp.send_cmd("AT+GMR", timeout=3000))

print("\n=== Conectando ao WiFi ===")
resp = esp.connect_wifi("SeuSSID", "SuaSenhaWiFi")
print(resp)

print("\n=== IP atual ===")
print(esp.get_ip())

print("\n=== HTTP GET (meu IP público) ===")
resp = esp.http_get("api.ipify.org", "/?format=text")
print(resp)

print("\n=== Desconectando WiFi ===")
print(esp.disconnect_wifi())