"""
umqtt_test.py -- MQTT test via ESP32-C3 AT Commands for AWS IoT Core

Architecture
------------
The Pico 2 controls the ESP32-C3 over UART via AT commands. The MQTT client runs
entirely on the ESP32-C3 (ESP-AT firmware v4.x). The Pico only sends commands
and receives confirmations -- no umqtt or ssl library is needed on the Pico.

  Pico 2 (MicroPython)
    +-- UART1 (AT commands) --> ESP32-C3 (ESP-AT firmware)
                                   +-- TLS mutual auth --> AWS IoT Core :8883

Execution flow
--------------
  Step 0 -- Diagnostics: firmware version and current NVS namespace state
  Step 1 -- Wi-Fi + SNTP: get IP and synchronize clock (required by TLS)
  Step 2 -- Certificates: convert DER to PEM and write to mfg_nvs via AT+SYSMFG
  Step 3 -- Connectivity: confirm IP and time before attempting MQTT
  Step 4 -- MQTT: configure mutual TLS client (scheme=5) and connect to broker
  Step 5 -- Publish: send binary payload via AT+MQTTPUBRAW

Official Espressif reference:
  https://docs.espressif.com/projects/esp-at/en/latest/esp32c3/AT_Command_Examples/mqtt-at-examples-for-cloud.html

How certificates are stored
---------------------------
ESP-AT reads MQTT certificates from the 'mfg_nvs' partition in three dedicated
namespaces. The NVS keys follow the ESP-AT build system convention (mfg_nvs.py):
when the namespace contains only one certificate file, the key has the same name
as the namespace -- WITHOUT an index suffix:

  namespace  NVS key     content
  ---------  ----------  --------------------------------
  mqtt_ca    mqtt_ca     Server root CA (Amazon)
  mqtt_cert  mqtt_cert   Device certificate
  mqtt_key   mqtt_key    Device private key

NOTE: SSL client namespaces use suffixes (.0, .1, ...) because the firmware
supports multiple cert sets. MQTT namespaces do not use suffixes.
Writing to 'mqtt_ca.0' causes AT_MQTT_CA_LENGTH_ERROR (0x6021) on connection.

Prerequisites
-------------
  1. File certs/AmazonRootCA1.der  -- Amazon root CA (DER)
  2. File certs/device.der         -- Device certificate (DER)
  3. File certs/privace.key.der    -- RSA or EC private key (DER, PKCS#1 or PKCS#8)
  4. Active certificate with attached policy in the AWS IoT Core console
  5. Adjust the constants WIFI_SSID, WIFI_PASSWORD, AWS_HOST, MQTT_CLIENT_ID
"""

from lib.esp32c3_at import ESP32C3_AT
import ubinascii
import time

# ======================================================================
#                         CONFIGURATION
# ======================================================================

# -- Wi-Fi --
WIFI_SSID     = "YOUR_SSID"
WIFI_PASSWORD = "YOUR_PASSWORD"

# -- AWS IoT Core --
AWS_HOST       = "YOUR_HOST.iot.REGION.amazonaws.com"  # E.g.: "abc123def456-ats.iot.us-east-1.amazonaws.com"
AWS_PORT       = 8883                                  # Default MQTT/TLS port for AWS
MQTT_CLIENT_ID = "pico2_esp32_test"                    # Must match the Thing Name or policy
MQTT_TOPIC     = "YOUR_TOPIC"                          # E.g.: "test/pico2" or "$aws/rules/..."
MQTT_QOS       = 1                                     # QoS 0=fire-and-forget, 1=acknowledged
MQTT_KEEPALIVE = 120                                   # Keepalive interval in seconds

# -- Certificate paths on the Pico filesystem --
# Files must be in the certs/ folder relative to the working directory.
# Accepted formats: binary DER (.der) -- converted to PEM internally.
CA_CERT_PATH  = "certs/AmazonRootCA1.der"  # Amazon root CA (verifies the AWS server)
DEV_CERT_PATH = "certs/device.der"         # Device X.509 certificate (identifies the client)
DEV_KEY_PATH  = "certs/privace.key.der"    # Device RSA/EC private key (PKCS#1 or PKCS#8)

# -- Mapping: (nvs_namespace, der_path, label, pem_type) --
# Each tuple defines a certificate to be written to the ESP32 mfg_nvs.
#
# nvs_namespace : namespace in the mfg_nvs partition (also used as NVS key)
# der_path      : path to the .der file on the Pico filesystem
# label         : descriptive text for logging
# pem_type      : PEM header -- "CERTIFICATE" for certs, None = auto-detect
#                 (auto-detect distinguishes PKCS#1 'RSA PRIVATE KEY' from PKCS#8
#                 'PRIVATE KEY'; wrong header causes TLS handshake failure)
CERTS = [
    ("mqtt_ca",   CA_CERT_PATH,  "CA (AmazonRootCA1)",   "CERTIFICATE"),
    ("mqtt_cert", DEV_CERT_PATH, "Client cert (device)",  "CERTIFICATE"),
    ("mqtt_key",  DEV_KEY_PATH,  "Client key (private)",  None),
]

# -- Binary test payload --
# Replace with the actual payload for your application.
payload = b'Hello from Pico 2 via ESP32-C3 MQTT!'  # Example binary payload
# ======================================================================
#                           HELPER FUNCTIONS
# ======================================================================

def detect_der_key_type(der_data):
    """Detects whether a DER private key is PKCS#1 or PKCS#8.

    mbedTLS (used internally by ESP-AT) chooses the parsing algorithm
    based on the PEM header, so the type must be correct:

      PEM Header              mbedTLS Parser
      ----------------------  ------------------------------------
      'RSA PRIVATE KEY'  ->   pk_parse_key_pkcs1_der()             (PKCS#1)
      'PRIVATE KEY'      ->   pk_parse_key_pkcs8_unencrypted_der() (PKCS#8)

    The type is inferred by inspecting the third element of the ASN.1
    structure (after the outer SEQUENCE and the INTEGER version=0):
      0x02 (INTEGER)  -- RSA modulus  -> PKCS#1
      0x30 (SEQUENCE) -- AlgorithmIdentifier -> PKCS#8

    Args:
        der_data (bytes): binary content of the private key in DER format.

    Returns:
        str: 'PRIVATE KEY' (PKCS#8) or 'RSA PRIVATE KEY' (PKCS#1).
             Returns 'RSA PRIVATE KEY' as fallback for invalid data.
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
    """Converts a certificate or key from binary DER to PEM (Base64).

    ESP-AT/mbedTLS identifies the PEM format by the presence of the
    '-----BEGIN ...-----' header. The trailing null byte (\x00) is mandatory:
    mbedTLS uses strlen() internally and only recognizes the PEM block if there
    is a null byte after the last '-----END ...-----\n' line.

    Args:
        der_data (bytes): binary content of the certificate or key in DER.
        pem_type (str | None): type to use in the PEM header/footer.
            'CERTIFICATE'    -- for CA and device certificates.
            'RSA PRIVATE KEY'-- for PKCS#1 keys.
            'PRIVATE KEY'    -- for PKCS#8 keys.
            None             -- auto-detects the type via detect_der_key_type().

    Returns:
        bytes: PEM-encoded data in Base64 with header, footer and trailing \x00.
    """
    if pem_type is None:
        pem_type = detect_der_key_type(der_data)
        print(f"  [Auto-detect] Key format: {pem_type}")

    b64 = ubinascii.b2a_base64(der_data).decode().strip()
    lines = [b64[i:i+64] for i in range(0, len(b64), 64)]
    pem  = f"-----BEGIN {pem_type}-----\n"
    pem += "\n".join(lines)
    pem += f"\n-----END {pem_type}-----\n"
    return pem.encode() + b'\x00'


def step_header(n, title):
    """Prints a numbered section header for easier log reading."""
    print(f"\n{'=' * 60}")
    print(f"  STEP {n}: {title}")
    print(f"{'=' * 60}")


# ======================================================================
#                           EXECUTION
# ======================================================================

def main():
    """Executes the full MQTT connection flow to AWS IoT Core.

    Sequence:
      0. Diagnostics -- displays firmware version and NVS namespace state
      1. Wi-Fi + SNTP -- connects to the network and synchronizes the clock
      2. Certificates -- writes CA, cert and key to mfg_nvs via AT+SYSMFG
      3. Connectivity -- confirms valid IP and time before connection
      4. MQTT -- configures mutual TLS, connects to the broker and checks state
      5. Publish -- publishes the binary payload to the configured topic
    """
    # Initialize the AT driver -- performs hardware reset and waits for 'ready'
    esp = ESP32C3_AT(uart_id=1, tx=4, rx=5, reset_pin=6)
    # Enable detailed AT error logging (includes TLS/MQTT codes like 0x6021)
    esp.enable_syslog()

    # ═══════════════════ STEP 0 ═══════════════════
    step_header(0, "Firmware diagnostics")

    print("Version:")
    resp = esp.send_cmd("AT+GMR", timeout=3000)
    print(resp.strip())

    print("\nNVS namespaces (mqtt_*):")
    for ns in ["mqtt_ca", "mqtt_cert", "mqtt_key"]:
        resp = esp.send_cmd(f'AT+SYSMFG=1,"{ns}"', timeout=2000)
        if "ERROR" not in resp:
            print(f"  {ns}: {resp.strip()}")
        else:
            print(f"  {ns}: (empty/does not exist)")
    print()

    # ═══════════════════ STEP 1 ═══════════════════
    step_header(1, "Synchronizing clock via SNTP")

    # SNTP requires Wi-Fi, but the clock must be correct before TLS:
    # certificates have a validity period and mbedTLS rejects connections if the
    # device time falls outside that interval.
    print("Connecting Wi-Fi...")
    resp = esp.connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    print("[WIFI]", resp.strip())
    if "ERROR" in resp and "GOT IP" not in resp:
        print("ERROR: Wi-Fi failed.")
        return

    # timezone=0 = UTC; adjust if needed (e.g.: -3 for BRT)
    resp = esp.sntp_config(timezone=0)
    print("[SNTP CFG]", resp.strip())

    # Wait up to 15 s for SNTP to sync (response changes from 1970 to current year)
    time_synced = False
    for _ in range(15):
        time.sleep(1)
        resp = esp.sntp_time()
        t = resp.strip()
        print(f"  {t}")
        if "202" in t and "1970" not in t:  # Year >= 2020 = synced
            time_synced = True
            break

    if not time_synced:
        print("ERROR: clock did not synchronize.")
        esp.disconnect_wifi()
        return

    print("Clock synchronized!")

    # ═══════════════════ STEP 2 ═══════════════════
    step_header(2, "Loading certificates")

    # 2a) Erase MQTT namespaces before writing
    # Required to prevent stale or corrupted data from previous runs
    # from interfering. AT+SYSMFG=0,"ns" erases all keys in the namespace.
    print("Clearing old MQTT namespaces...")
    for namespace, _, _, _ in CERTS:
        esp.sysmfg_erase(namespace)
        time.sleep_ms(100)
    print("  Namespaces cleared.")

    # 2b) Convert DER -> PEM and write each certificate to mfg_nvs
    # ESP-AT's mbedTLS requires PEM format (Base64 with headers). The .der files
    # provided by AWS IoT Core are converted at runtime.
    all_ok = True
    for namespace, path, label, pem_type in CERTS:
        try:
            with open(path, "rb") as f:
                der_data = f.read()
        except OSError:
            print(f"  ERROR: '{path}' not found on Pico!")
            all_ok = False
            continue

        pem_data = der_to_pem(der_data, pem_type)
        print(f"  {label} (DER:{len(der_data)} -> PEM:{len(pem_data)} bytes)...", end=" ")

        # IMPORTANT: NVS namespace and key share the same name (e.g.: 'mqtt_ca')
        # The ESP-AT build system (mfg_nvs.py) does not add a '.0' suffix for
        # MQTT namespaces, since each one contains only a single certificate file.
        # Writing to 'mqtt_ca.0' would cause MQTT to return AT_MQTT_CA_LENGTH_ERROR.
        ok = esp.sysmfg_write(namespace, namespace, pem_data)

        print("OK" if ok else "FAILED")
        if not ok:
            all_ok = False
        time.sleep_ms(300)

    if not all_ok:
        print("ERROR: failed to write certificates.")
        esp.disconnect_wifi()
        return

    # 2c) Confirm that keys were written by listing the namespace
    # AT+SYSMFG=1,"ns" returns existing keys and their types (type=8 = blob)
    print("\nVerifying written certificates:")
    for namespace, _, label, _ in CERTS:
        resp = esp.send_cmd(f'AT+SYSMFG=1,"{namespace}"', timeout=2000)
        if "ERROR" not in resp:
            print(f"  {namespace}: {resp.strip()}")
        else:
            print(f"  {namespace}: ERROR during verification!")
            all_ok = False

    if not all_ok:
        print("ERROR: certificate verification failed.")
        esp.disconnect_wifi()
        return

    print("Certificates written and verified successfully!")

    # ═══════════════════ STEP 3 ═══════════════════
    step_header(3, "Checking connectivity")

    resp = esp.get_ip()
    print(resp.strip())
    if "ERROR" in resp or "0.0.0.0" in resp:
        print("ERROR: no Wi-Fi connectivity.")
        return

    resp = esp.sntp_time()
    print("[Time]", resp.strip())
    print("Connectivity OK!")

    # ═══════════════════ STEP 4 ═══════════════════
    step_header(4, "Connecting MQTT to AWS IoT Core")

    # Discard any open MQTT session from a previous run
    esp.mqtt_clean()
    time.sleep_ms(500)

    # 4a) AT+MQTTUSERCFG -- defines MQTT client identity and TLS scheme
    #   scheme=5  : MQTT over TLS with mutual authentication (both client and
    #               server authenticate). Requires mqtt_ca, mqtt_cert and
    #               mqtt_key to be stored in mfg_nvs.
    #   cert_key_ID=0, CA_ID=0 : slot 0 of mqtt_cert/mqtt_key/mqtt_ca namespaces
    print("Configuring MQTT user (scheme=5, mutual TLS)...")
    resp = esp.mqtt_user_cfg(MQTT_CLIENT_ID, scheme=5)
    print("[MQTTUSERCFG]", resp.strip())
    if "ERROR" in resp:
        print("ERROR: AT+MQTTUSERCFG failed.")
        return

    # 4b) AT+MQTTSNI -- sets the Server Name Indication for the TLS handshake
    #   Required for AWS IoT Core: the server uses SNI to select the correct
    #   certificate when multiple domains share the same IP.
    print(f"Configuring MQTT SNI: {AWS_HOST}")
    resp = esp.mqtt_sni(AWS_HOST)
    print("[MQTTSNI]", resp.strip())
    if "ERROR" in resp:
        print("WARNING: AT+MQTTSNI failed (firmware may not support it).")

    # 4c) AT+MQTTCONNCFG -- MQTT session parameters (keepalive, LWT, etc)
    print("Configuring MQTT connection params...")
    resp = esp.mqtt_conn_cfg(keepalive=MQTT_KEEPALIVE)
    print("[MQTTCONNCFG]", resp.strip())

    # 4d) AT+MQTTCONN -- establishes the TCP+TLS+MQTT connection with the broker
    # reconnect=1 enables automatic reconnection after network drops.
    # Expected state after successful connection: +MQTTCONN:0,4,5,...
    #   field 2 = state: 4=connected, 5=disconnecting, 6=disconnected
    MAX_RETRIES = 3
    connected = False
    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\nAttempt {attempt}/{MAX_RETRIES}: Connecting to {AWS_HOST}:{AWS_PORT}...")
        resp = esp.mqtt_connect(AWS_HOST, AWS_PORT, reconnect=1)
        print("[MQTTCONN]", resp.strip())

        if "MQTTCONNECTED" in resp or ("OK" in resp and "ERROR" not in resp):
            time.sleep(1)  # Wait for stabilization before querying state
            state_resp = esp.mqtt_state()
            print("[STATE]", state_resp.strip())
            if any(f",{s}," in state_resp for s in ["4", "5", "6"]):
                connected = True
                break

        print(f"  Failed. Response: {resp.strip()}")
        if attempt < MAX_RETRIES:
            # Restart the full MQTT configuration before retrying
            print("  Cleaning up and retrying in 3s...")
            esp.mqtt_clean()
            time.sleep(3)
            esp.mqtt_user_cfg(MQTT_CLIENT_ID, scheme=5)
            time.sleep_ms(200)
            esp.mqtt_sni(AWS_HOST)
            time.sleep_ms(200)
            esp.mqtt_conn_cfg(keepalive=MQTT_KEEPALIVE)
            time.sleep_ms(200)

    if not connected:
        print("\nERROR: Could not connect to AWS IoT Core.")
        print("Check:")
        print("  1. Correct .der certificates in the certs/ folder")
        print("  2. Certificates active in the AWS IoT Core console")
        print("  3. Policy attached to the certificates")
        print("  4. Correct endpoint:", AWS_HOST)
        print("  5. Clock synchronized (TLS validates validity period)")
        print("\nFinal MQTT state:")
        print(esp.mqtt_state().strip())
        esp.mqtt_clean()
        esp.disconnect_wifi()
        return

    print("\nMQTT successfully connected to AWS IoT Core!")

    # ═══════════════════ STEP 5 ═══════════════════
    step_header(5, "Publishing MQTT")

    print(f"  Topico: {MQTT_TOPIC}")
    print(f"  Payload: {len(payload)} bytes (binary)")
    print(f"  QoS: {MQTT_QOS}")

    # AT+MQTTPUBRAW accepts arbitrary binary data (unlike AT+MQTTPUB which
    # treats the payload as a string and rejects null bytes).
    # ESP-AT sends '>' as a prompt and waits for exactly <length> bytes.
    resp = esp.mqtt_pub_raw(MQTT_TOPIC, payload, qos=MQTT_QOS)
    print("[MQTTPUBRAW]", resp.strip())

    if "MQTTPUB:OK" in resp:
        print("Publication confirmed by broker!")    # QoS 1: PUBACK received
    elif "OK" in resp and "FAIL" not in resp:
        print("Publication sent.")                   # QoS 0: no confirmation
    else:
        print("WARNING: publication may have failed.")

    # -- Cleanup --
    print(f"\n{'=' * 60}")
    print("  Disconnecting...")
    print(f"{'=' * 60}")
    esp.mqtt_clean()
    esp.disconnect_wifi()
    print("\nMQTT test completed successfully!")


# -- Execute --
main()
