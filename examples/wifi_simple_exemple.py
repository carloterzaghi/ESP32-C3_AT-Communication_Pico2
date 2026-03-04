from lib.esp32c3_at import ESP32C3_AT

# UART1, GP4=TX, GP5=RX, GP6=Reset (EN)
esp = ESP32C3_AT(uart_id=1, tx=4, rx=5, reset_pin=6)

print("\n=== Testing module ===")
print(esp.send_cmd("AT"))

print("\n=== Version ===")
print(esp.send_cmd("AT+GMR", timeout=3000))

print("\n=== Connecting to WiFi ===")
resp = esp.connect_wifi("YourSSID", "YourWiFiPassword")
print(resp)

print("\n=== Current IP ===")
print(esp.get_ip())

print("\n=== HTTP GET (my public IP) ===")
resp = esp.http_get("api.ipify.org", "/?format=text")
print(resp)

print("\n=== Disconnecting WiFi ===")
print(esp.disconnect_wifi())