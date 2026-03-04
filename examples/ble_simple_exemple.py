from lib.esp32c3_at import ESP32C3_AT
import time

BLE_NAME = "Pico2-BLE"

# UART1, GP4=TX, GP5=RX, GP6=Reset (EN)
esp = ESP32C3_AT(uart_id=1, tx=4, rx=5, reset_pin=6)

print("\n=== Starting BLE Peripheral ===")

# 1. Initialize BLE in peripheral mode
resp = esp.ble_init()
print("[BLEINIT]", resp.strip())
if "ERROR" in resp:
    print("ERROR: failed to initialize BLE. Check the ESP32 AT firmware.")
    raise SystemExit

time.sleep_ms(500)

# 2. Set the device name
resp = esp.ble_set_name(BLE_NAME)
print("[BLENAME]", resp.strip())

# 3. Configure advertising parameters (connectable, 100 ms, all channels)
resp = esp.ble_set_adv_param()
print("[BLEADVPARAM]", resp.strip())

# 4. *** ESSENTIAL: include the name in the advertising packet data ***
#    Uses AT+BLEADVDATA with AD type 0x09 (Complete Local Name).
#    Without this step the device advertises without a name and doesn't appear to scanners.
resp = esp.ble_set_adv_data(BLE_NAME)
print("[BLEADVDATA]", resp.strip())
if "ERROR" in resp:
    print("ERROR: could not set advertising data.")

# 5. Display BLE MAC address for debugging
resp = esp.ble_get_addr()
print("[BLEADDR]", resp.strip())

# 6. Start advertising
resp = esp.ble_start_advertising()
print("[BLEADVSTART]", resp.strip())

if "OK" in resp:
    print(f"\nAdvertising BLE as '{BLE_NAME}'... waiting for connection.")
else:
    print("ERROR starting advertising:", resp.strip())

# Wait for connections and data
while True:
    if esp.uart.any():
        data = esp.uart.read(esp.uart.any())
        if data:
            print("Received:", data.decode("utf-8", "replace"))
    time.sleep_ms(100)