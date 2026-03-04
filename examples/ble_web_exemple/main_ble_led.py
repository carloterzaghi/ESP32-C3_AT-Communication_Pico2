from lib.esp32c3_at import ESP32C3_AT
import machine
import time

# ─────────── Configuration ───────────
BLE_NAME    = "Pico2-BLE"
LED_PIN     = 25    # Pico 2 onboard LED (GP25)

# GATT service indices -- confirmed via AT+BLEGATTSSRV? and AT+BLEGATTSCHAR?
# +BLEGATTSSRV:1,1,0xA002,1
# +BLEGATTSCHAR:"char",1,3,0xC302,0x08  -> WRITE (char_idx=3)
# +BLEGATTSCHAR:"char",1,6,0xC305,0x10  -> NOTIFY (char_idx=6)
SRV_IDX        = 1   # Custom service UUID 0xA002
WRITE_CHAR_IDX = 3   # WRITE characteristic UUID 0xC302
NTFY_CHAR_IDX  = 6   # NOTIFY characteristic UUID 0xC305

# Short commands (1 byte) to fit in the ESP-AT firmware GATT attribute
# The firmware defines characteristic 0xC302 with a maximum size of 1 byte
CMD_ON  = "1"   # Turn LED on
CMD_OFF = "0"   # Turn LED off

# ─────────── Hardware ───────────
led = machine.Pin(LED_PIN, machine.Pin.OUT)

# ─────────── Initialize ESP32 ───────────
esp = ESP32C3_AT(uart_id=1, tx=4, rx=5, reset_pin=6)

print("\n=== BLE LED Control ===")

resp = esp.ble_init()
print("[BLEINIT]", resp.strip())
if "ERROR" in resp:
    print("ERROR: failed to initialize BLE. Check the AT firmware.")
    raise SystemExit

time.sleep_ms(500)

resp = esp.ble_set_name(BLE_NAME)
print("[BLENAME]", resp.strip())

# -- GATT Server --
resp = esp.ble_gatt_init()
print("[GATT INIT]", resp.strip())
if "ERROR" in resp:
    print("ERROR: AT+BLEGATTSSRVCRE or AT+BLEGATTSSRVSTART failed.")
    print("Check whether the AT firmware supports GATT server.")
    raise SystemExit

# Print the actual UUIDs to copy to the web page
print("\n--- Available GATT services (copy to the web page) ---")
print(esp.send_cmd("AT+BLEGATTSSRV?", timeout=3000).strip())
print("--- GATT Characteristics ---")
print(esp.send_cmd("AT+BLEGATTSCHAR?", timeout=3000).strip())
print("----------------------------------------------------\n")

# Read the actual srv_idx and char_idx from the response
resp = esp.ble_set_adv_param()
print("[BLEADVPARAM]", resp.strip())

resp = esp.ble_set_adv_data(BLE_NAME)
print("[BLEADVDATA]", resp.strip())

resp = esp.ble_start_advertising()
print("[BLEADVSTART]", resp.strip())

print(f"\nWaiting for BLE connection as '{BLE_NAME}'...")

# ─────────── Main loop ───────────
conn_idx     = None
buf          = b""
pending_write = None   # stores +WRITE metadata while waiting for the data line

while True:
    if esp.uart.any():
        chunk = esp.uart.read(esp.uart.any())
        if chunk:
            buf += chunk

    # Processa linha por linha
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        text = line.strip().decode("utf-8", "replace")
        if not text:
            continue

        # -- Pending data line from a previous +WRITE --
        if pending_write is not None:
            cmd = text.strip()
            pending_write = None

            print(f"[CMD] Recebido: '{cmd}'")

            if cmd == CMD_ON:
                led.value(1)
                confirmation = "OK:ON"
                print("[LED] On")
            elif cmd == CMD_OFF:
                led.value(0)
                confirmation = "OK:OFF"
                print("[LED] Off")
            else:
                confirmation = f"ERR:{cmd}"
                print(f"[LED] Unknown command: '{cmd}'")

            if conn_idx is not None:
                resp = esp.ble_notify(conn_idx, SRV_IDX, NTFY_CHAR_IDX, confirmation)
                print(f"[NOTIFY] {confirmation} ->", resp.strip())
            else:
                print("[NOTIFY] No client connected.")
            continue

        # -- Client connected --
        if text.startswith("+BLECONN:"):
            try:
                conn_idx = int(text.split(":")[1].split(",")[0])
            except Exception:
                conn_idx = 0
            print(f"[+] Client connected (conn_idx={conn_idx})")
            # Notify the web page that the connection was established
            resp = esp.ble_notify(conn_idx, SRV_IDX, NTFY_CHAR_IDX, "CONNECTED")
            print("[NOTIFY] CONNECTED ->", resp.strip())

        # -- Client disconnected --
        elif text.startswith("+BLEDISCONN:"):
            print("[-] Client disconnected. Restarting advertising...")
            conn_idx = None
            esp.ble_start_advertising()

        # -- GATT WRITE header received --
        # v4.x format (two possible styles):
        #   Style A (inline data):     +WRITE:0,1,3,0,1,1
        #   Style B (data on next line): +WRITE:0,1,3,0,1  -> next line = 1
        # Ignore writes to CCCD (desc_idx>0 or char_idx != WRITE_CHAR_IDX)
        elif text.startswith("+WRITE:"):
            parts = text[7:].split(",", 5)
            # char_idx esta em parts[2]
            try:
                char_idx_recv = int(parts[2]) if len(parts) > 2 else -1
            except ValueError:
                char_idx_recv = -1

            if char_idx_recv != WRITE_CHAR_IDX:
                # Write to CCCD or another characteristic -- ignore
                continue

            if len(parts) >= 6 and parts[5].strip():
                # Style A -- data already on the same line
                cmd = parts[5].strip()
                print(f"[CMD] Recebido: '{cmd}'")
                if cmd == CMD_ON:
                    led.value(1)
                    confirmation = "OK:ON"
                    print("[LED] On")
                elif cmd == CMD_OFF:
                    led.value(0)
                    confirmation = "OK:OFF"
                    print("[LED] Off")
                else:
                    confirmation = f"ERR:{cmd}"
                    print(f"[LED] Unknown command: '{cmd}'")
                if conn_idx is not None:
                    resp = esp.ble_notify(conn_idx, SRV_IDX, NTFY_CHAR_IDX, confirmation)
                    print(f"[NOTIFY] {confirmation} ->", resp.strip())
            else:
                # Style B -- data comes on the next line
                pending_write = parts

    time.sleep_ms(10)
