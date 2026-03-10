# Usage Examples

All examples import the driver from `lib/esp32c3_at.py` via `from lib.esp32c3_at import ESP32C3_AT`.
Make sure the `lib/` folder with the driver is on the Pico 2 filesystem.

---

### `wifi_simple_exemple.py` -- Wi-Fi + HTTP GET

Minimal Wi-Fi connection and HTTP request example.

**What it does:**
1. Initializes the driver and tests communication (`AT`)
2. Displays the AT firmware version (`AT+GMR`)
3. Connects to a Wi-Fi network with `connect_wifi(ssid, password)`
4. Retrieves the local IP with `get_ip()`
5. Performs an HTTP GET to `api.ipify.org/?format=text` (returns the public IP)
6. Disconnects from Wi-Fi with `disconnect_wifi()`

**Usage:**
```python
# Edit "YourSSID" and "YourWiFiPassword" in the file before running
import wifi_simple_exemple
```

---

### `ble_simple_exemple.py` -- BLE Peripheral (Advertising)

Configures the ESP32-C3 as a BLE Peripheral device visible to scanners.

**What it does:**
1. Initializes BLE in Peripheral mode (`ble_init()` -> `AT+BLEINIT=2`)
2. Sets the device name to `"Pico2-BLE"` (`ble_set_name()`)
3. Configures advertising (connectable, 100 ms, all channels) (`ble_set_adv_param()`)
4. Includes the name in the advertising packet via AD Type `0x09` (Complete Local Name) using `ble_set_adv_data()` -- **without this step the device advertises without a name**
5. Displays the BLE MAC address for debugging (`ble_get_addr()`)
6. Starts advertising (`ble_start_advertising()`)
7. Monitors the UART in a loop, printing connection events and received data

**Usage:**
```python
import ble_simple_exemple
# The device will appear as "Pico2-BLE" in the phone's BLE scanner
```

---

### `ble_web_exemple/` -- LED Control via BLE + Web Bluetooth

Complete example integrating the Pico 2 (MicroPython) with a web interface to
control the onboard LED (GP25) via BLE.

#### `main_ble_led.py` -- MicroPython Script (runs on the Pico 2)

**What it does:**
1. Initializes BLE + GATT Server (`ble_init()` + `ble_gatt_init()`)
2. Displays the actual GATT service and characteristic UUIDs
3. Configures advertising with name `"Pico2-BLE"` and starts it
4. In the main loop, processes UART events line by line:
   - **`+BLECONN:`** -- client connected, sends Notify `"CONNECTED"`
   - **`+BLEDISCONN:`** -- client disconnected, restarts advertising
   - **`+WRITE:`** -- command received via GATT Write (char `0xC302`):
     - `"1"` -> turns LED on, responds Notify `"OK:ON"`
     - `"0"` -> turns LED off, responds Notify `"OK:OFF"`
     - Other -> responds Notify `"ERR:<cmd>"`
5. Supports two `+WRITE` styles from ESP-AT v4.x firmware:
   - **Style A:** inline data on the same line (`+WRITE:0,1,3,0,1,1`)
   - **Style B:** data on the next line (`+WRITE:0,1,3,0,1` + next line)

**GATT indices used:**

| Constant | Value | UUID | Function |
|---|---|---|---|
| `SRV_IDX` | 1 | `0xA002` | Custom service |
| `WRITE_CHAR_IDX` | 3 | `0xC302` | Write characteristic (receives commands) |
| `NTFY_CHAR_IDX` | 6 | `0xC305` | Notify characteristic (sends confirmations) |

#### `index.html` -- Web Bluetooth Interface (runs in the browser)

Modern web page (dark mode) that:
- Connects to the `"Pico2-BLE"` device via Web Bluetooth
- Automatically discovers GATT service and characteristic UUIDs
- Displays discovered UUIDs in read-only fields
- Allows toggling the LED with buttons (sends `"1"` or `"0"` via GATT Write)
- Visualizes the LED state with a glow animation
- Shows a real-time event log (connection, commands, Notify responses)
- Receives confirmations via GATT Notify (`OK:ON`, `OK:OFF`, `CONNECTED`)

> The Web Bluetooth API requires **HTTPS** or **localhost**. To test locally:
> `python -m http.server 8080` and open `http://localhost:8080`

---

### `mqtt_aws_exemple/` -- Mutual TLS MQTT with AWS IoT Core

Complete MQTT publish test with mutual authentication (TLS) using native AT
commands -- **no `umqtt` or `ssl` library on the Pico**.

#### Architecture

```
Pico 2 (MicroPython)
  └─ UART1 (AT commands) ──▶ ESP32-C3 (ESP-AT firmware)
                                 └─ TLS mutual auth ──▶ AWS IoT Core :8883
```

#### `umqtt_test.py` -- Main Script

**Execution flow (6 steps):**

| Step | Description | Details |
|---|---|---|
| **0** | Diagnostics | Displays firmware version and NVS namespace state (`mqtt_ca`, `mqtt_cert`, `mqtt_key`) |
| **1** | Wi-Fi + SNTP | Connects to the network, configures SNTP (UTC) and waits up to 15s for clock synchronization |
| **2** | Certificates | Erases old namespaces, converts DER to PEM with `der_to_pem()`, writes to `mfg_nvs` via `sysmfg_write()` and verifies |
| **3** | Connectivity | Confirms valid IP and synchronized time before TLS |
| **4** | MQTT | Configures `mqtt_user_cfg(scheme=5)` + `mqtt_sni()` + `mqtt_conn_cfg()`, connects with up to 3 retries |
| **5** | Publish | Publishes binary payload via `mqtt_pub_raw()` (`AT+MQTTPUBRAW`) |

**Helper functions:**

| Function | Description |
|---|---|
| `detect_der_key_type(der_data)` | Detects whether the DER private key is PKCS#1 (`RSA PRIVATE KEY`) or PKCS#8 (`PRIVATE KEY`) via ASN.1 inspection |
| `der_to_pem(der_data, pem_type)` | Converts binary DER to PEM (Base64 with headers). Appends trailing `\x00` required by mbedTLS |
| `step_header(n, title)` | Prints a numbered section header for easier log reading |

**How certificates are stored:**

The ESP-AT firmware reads MQTT certificates from the `mfg_nvs` partition in dedicated
namespaces. The key naming convention follows the ESP-AT build system standard
([`mfg_nvs.py`](https://github.com/espressif/esp-at/blob/master/components/customized_partitions/generation_tools/mfg_nvs.py)):

| NVS Namespace | NVS Key | Content |
|---|---|---|
| `mqtt_ca` | `mqtt_ca` | Server CA (AmazonRootCA1) |
| `mqtt_cert` | `mqtt_cert` | Client certificate |
| `mqtt_key` | `mqtt_key` | Client private key |

> The NVS key is the same as the namespace name (no `.0` suffix). This is different
> from SSL namespaces (`client_cert.0`, `client_cert.1`) which support multiple
> sets. Writing to `mqtt_ca.0` causes `AT_MQTT_CA_LENGTH_ERROR` (0x6021).

#### `certs/` -- Certificates (not versioned)

Place here the `.der` certificates exported from the AWS IoT Core console:
- `AmazonRootCA1.der` -- Amazon root CA (verifies the server)
- `device.der` -- Device X.509 certificate (identifies the client)
- `privace.key.der` -- Device RSA/EC private key (PKCS#1 or PKCS#8)

**AWS Prerequisites:**
1. Active certificate in the AWS IoT Core console
2. Policy attached to the certificate with `iot:Connect` and `iot:Publish` permissions
3. Correct endpoint (format: `xxxxxxxx-ats.iot.<region>.amazonaws.com`)

**Usage:**
```python
# Edit the constants at the top of the file:
WIFI_SSID      = "your_network"
WIFI_PASSWORD  = "your_password"
AWS_HOST       = "abc123-ats.iot.us-east-1.amazonaws.com"
MQTT_CLIENT_ID = "pico2_device"
MQTT_TOPIC     = "test/pico2"

# Run:
import umqtt_test
```

---

### `OTA_exemple/` -- Over-the-Air Update via HTTP

Downloads updated MicroPython files from an HTTP server and applies them to
the Pico 2 filesystem, using the ESP32-C3 as the network interface.

#### Architecture

```
Pico 2 (MicroPython)
  └─ UART1 (AT commands) ──▶ ESP32-C3 (ESP-AT firmware)
                                 └─ HTTP GET ──▶ OTA Server (LAN / Internet)
```

#### `ota_update.py` -- Main Script

**Execution flow (4 steps):**

| Step | Description | Details |
|---|---|---|
| **1** | Module check | Verifies AT communication with the ESP32-C3 |
| **2** | Wi-Fi | Connects to the configured network |
| **3** | Version check | Downloads `version.json` from the server and compares with the local version stored in `ota_version` |
| **4** | Apply update | Downloads each file listed in the manifest, saves safely (tmp + rename), updates the version marker, reboots |

**Key features:**
- **Safe writes:** each file is written to a `.ota_tmp` temporary file first, then renamed -- a failed download never corrupts an existing file.
- **Atomic version update:** the local version marker is only updated **after** all files are successfully written. If the device reboots mid-update, the next run retries from scratch.
- **Proper `+IPD` parsing:** extracts TCP data from the ESP-AT `+IPD,<len>:<data>` framing, supporting both standard and `CIPDINFO`-extended formats.

#### `version.json` -- Version Manifest (example)

Place this file on your HTTP server. Format:

```json
{
    "version": "1.0.1",
    "files": [
        {"path": "main.py",           "url": "/ota/main.py"},
        {"path": "lib/my_module.py",  "url": "/ota/lib/my_module.py"}
    ]
}
```

- `version` -- arbitrary version string; an update is triggered whenever it differs from the local value.
- `files[].path` -- destination path on the Pico filesystem.
- `files[].url` -- URL path on the OTA server.

#### Quick server setup (on your PC)

```bash
# Create a folder structure matching the URL paths:
mkdir -p ota_server/ota
cp version.json ota_server/ota/
cp main.py      ota_server/ota/

# Start a simple HTTP server:
cd ota_server
python -m http.server 8080
```

Then set `OTA_HOST` to your PC's local IP (e.g., `192.168.1.100`) and
`OTA_PORT` to `8080`.

**Usage:**
```python
# Edit the configuration constants at the top of ota_update.py:
WIFI_SSID        = "your_network"
WIFI_PASSWORD    = "your_password"
OTA_HOST         = "192.168.1.100"
OTA_PORT         = 8080
OTA_VERSION_PATH = "/ota/version.json"

# Run:
import ota_update
```
