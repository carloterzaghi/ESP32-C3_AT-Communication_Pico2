"""
ota_update.py -- OTA Update for Pico 2 via ESP32-C3 AT Commands

Downloads updated MicroPython files from an HTTP server and applies
them to the Pico 2 filesystem.

Architecture
------------
    Pico 2 (MicroPython)
      └─ UART1 (AT commands) ──▶ ESP32-C3 (ESP-AT firmware)
                                     └─ HTTP GET ──▶ OTA Server (LAN/Internet)

The OTA server hosts a version manifest (``version.json``) and the updated
Python files.  Any HTTP server works -- even Python's built-in::

    cd server_files
    python -m http.server 8080

version.json format
-------------------
::

    {
        "version": "1.0.1",
        "files": [
            {"path": "main.py",           "url": "/ota/main.py"},
            {"path": "lib/my_module.py",   "url": "/ota/lib/my_module.py"}
        ]
    }

- ``version``  -- arbitrary version string; update triggers when it differs
                  from the local version stored on the Pico.
- ``files``    -- list of objects with:
    - ``path`` -- destination path on the Pico filesystem.
    - ``url``  -- URL path on the OTA server.

Execution flow
--------------
  Step 1 -- Module check: verifies AT communication with the ESP32-C3.
  Step 2 -- WiFi: connects to the configured network.
  Step 3 -- Version check: downloads ``version.json`` and compares with local.
  Step 4 -- Apply update: downloads each file, writes safely (tmp + rename),
            updates the local version marker, and reboots.

Usage
-----
Edit the configuration constants below, then::

    import ota_update

To integrate with your application, call ``ota_update.main()`` at boot
(e.g., from ``main.py`` or ``boot.py``).
"""

from lib.esp32c3_at import ESP32C3_AT
import machine
import json
import time
import os

# ======================================================================
#                         CONFIGURATION
# ======================================================================

# -- Wi-Fi credentials --
WIFI_SSID     = "YOUR_SSID"
WIFI_PASSWORD = "YOUR_PASSWORD"

# -- OTA server --
OTA_HOST         = "192.168.1.100"         # HTTP server IP or hostname
OTA_PORT         = 8080                    # HTTP server port
OTA_VERSION_PATH = "/ota/version.json"     # URL path to version manifest

# -- Local version tracking --
LOCAL_VERSION_FILE = "ota_version"          # File on the Pico that stores the
                                            # current version string.

# ======================================================================
#                     INITIALIZE DRIVER
# ======================================================================

esp = ESP32C3_AT(uart_id=1, tx=4, rx=5, reset_pin=6)

# ======================================================================
#                     HTTP DOWNLOAD (via TCP + +IPD parsing)
# ======================================================================

def _tcp_download(host, path, port=80, timeout=30000):
    """
    Perform an HTTP GET via raw TCP through the ESP32-C3 and return
    the response body as **bytes**.

    Uses normal transmission mode (``AT+CIPMODE=0``).  The raw UART
    stream is parsed to extract ``+IPD,<len>:<data>`` segments, which
    are concatenated to reconstruct the clean HTTP response.

    Args:
        host    (str): Server hostname or IP.
        path    (str): URL path (e.g., ``'/ota/version.json'``).
        port    (int): Server TCP port. Default: ``80``.
        timeout (int): Max wait for the full response in ms.
                       Default: ``30000``.

    Returns:
        bytes | None: HTTP response body, or ``None`` on error.
    """
    esp.send_cmd("AT+CIPMODE=0")
    time.sleep_ms(200)

    # --- Open TCP connection ---
    resp = esp.send_cmd(
        f'AT+CIPSTART="TCP","{host}",{port}',
        timeout=10000, expected="CONNECT"
    )
    if "ERROR" in resp:
        print("[OTA] TCP connection failed")
        return None

    # --- Build HTTP request ---
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Connection: close\r\n"
        "User-Agent: Pico2-OTA/1.0\r\n"
        "\r\n"
    )

    # --- Clear UART and request send prompt ---
    while esp.uart.any():
        esp.uart.read()

    esp.uart.write(f"AT+CIPSEND={len(req)}\r\n".encode())

    buf = b""
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 3000:
        if esp.uart.any():
            chunk = esp.uart.read()
            if chunk:
                buf += chunk
        if b">" in buf:
            break
        time.sleep_ms(10)

    if b">" not in buf:
        print("[OTA] CIPSEND prompt not received")
        esp.send_cmd("AT+CIPCLOSE", timeout=2000)
        return None

    time.sleep_ms(100)

    # --- Send the HTTP request ---
    esp.uart.write(req.encode())

    # --- Accumulate UART data until the server closes ---
    response = b""
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < timeout:
        if esp.uart.any():
            data = esp.uart.read()
            if data:
                response += data
        if b"CLOSED" in response:
            break
        time.sleep_ms(10)

    # --- Extract TCP payload from +IPD messages ---
    # Handles:
    #   +IPD,<len>:<data>                       (standard)
    #   +IPD,<len>,<remote_ip>,<remote_port>:<data>  (AT+CIPDINFO enabled)
    tcp_data = b""
    i = 0
    while i < len(response):
        marker = response.find(b"+IPD,", i)
        if marker < 0:
            break
        colon = response.find(b":", marker + 5)
        if colon < 0:
            break
        length_field = response[marker + 5 : colon]
        comma = length_field.find(b",")
        try:
            length = int(length_field[:comma] if comma >= 0 else length_field)
        except ValueError:
            break
        data_start = colon + 1
        data_end = data_start + length
        tcp_data += response[data_start : min(data_end, len(response))]
        i = data_end

    if not tcp_data:
        return None

    # --- Separate HTTP headers from body ---
    sep = b"\r\n\r\n"
    idx = tcp_data.find(sep)
    if idx < 0:
        return tcp_data

    headers = tcp_data[:idx].decode("utf-8", "replace")
    status_line = headers.split("\r\n")[0] if headers else ""
    if "200" not in status_line:
        print(f"[OTA] HTTP error: {status_line}")
        return None

    return tcp_data[idx + len(sep):]


def _download_text(host, path, port=80, timeout=30000):
    """Download a text resource via HTTP and return it as a string."""
    data = _tcp_download(host, path, port, timeout)
    if data is None:
        return None
    return data.decode("utf-8", "replace")


# ======================================================================
#                     VERSION MANAGEMENT
# ======================================================================

def _get_local_version():
    """Read the stored OTA version from the Pico filesystem."""
    try:
        with open(LOCAL_VERSION_FILE, "r") as f:
            return f.read().strip()
    except OSError:
        return "0.0.0"


def _save_local_version(version):
    """Persist the current OTA version on the Pico filesystem."""
    with open(LOCAL_VERSION_FILE, "w") as f:
        f.write(version)


def _ensure_dir(filepath):
    """Create parent directories for *filepath* if they don't exist."""
    parts = filepath.replace("\\", "/").split("/")
    if len(parts) <= 1:
        return
    accumulated = ""
    for part in parts[:-1]:
        accumulated = f"{accumulated}/{part}" if accumulated else part
        try:
            os.mkdir(accumulated)
        except OSError:
            pass  # already exists


# ======================================================================
#                     OTA UPDATE LOGIC
# ======================================================================

def check_for_update():
    """
    Download the remote version manifest and compare with local version.

    Returns:
        dict | None: The parsed manifest if an update is available,
                     ``None`` if already up-to-date or on error.
    """
    print("[OTA] Checking for updates...")
    body = _download_text(OTA_HOST, OTA_VERSION_PATH, OTA_PORT)
    if body is None:
        print("[OTA] Could not retrieve version manifest")
        return None

    try:
        manifest = json.loads(body)
    except (ValueError, KeyError):
        print("[OTA] Invalid version.json")
        return None

    remote_ver = manifest.get("version", "0.0.0")
    local_ver  = _get_local_version()
    print(f"[OTA] Local version : {local_ver}")
    print(f"[OTA] Remote version: {remote_ver}")

    if remote_ver != local_ver:
        return manifest

    print("[OTA] Already up to date.")
    return None


def apply_update(manifest):
    """
    Download every file listed in the manifest and write to the
    Pico filesystem.  Uses a *write-to-tmp-then-rename* strategy
    so that a failed download never leaves a corrupt file behind.

    The local version marker is updated **only** after all files
    have been written successfully.  If the device reboots mid-update
    the next run will retry the whole update.

    Returns:
        bool: ``True`` on success, ``False`` on any failure.
    """
    files = manifest.get("files", [])
    if not files:
        print("[OTA] Manifest contains no files")
        return False

    total = len(files)
    for idx, entry in enumerate(files):
        url_path  = entry.get("url", "")
        dest_path = entry.get("path", "")
        if not url_path or not dest_path:
            print(f"[OTA] Skipping invalid entry: {entry}")
            continue

        print(f"[OTA] [{idx + 1}/{total}] {url_path} -> {dest_path}")

        content = _download_text(OTA_HOST, url_path, OTA_PORT)
        if content is None:
            print(f"[OTA] Download failed: {url_path}")
            return False

        # Write to temporary file, then rename
        _ensure_dir(dest_path)
        tmp_path = dest_path + ".ota_tmp"
        try:
            with open(tmp_path, "w") as f:
                f.write(content)
            try:
                os.remove(dest_path)
            except OSError:
                pass
            os.rename(tmp_path, dest_path)
            print(f"[OTA] Saved {dest_path} ({len(content)} bytes)")
        except OSError as e:
            print(f"[OTA] Write error {dest_path}: {e}")
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return False

        # Small delay between consecutive downloads
        time.sleep_ms(500)

    # All files written -- persist the new version
    _save_local_version(manifest["version"])
    print(f"[OTA] Version updated to {manifest['version']}")
    return True


# ======================================================================
#                           MAIN
# ======================================================================

def main():
    """Run the full OTA check-and-update flow."""
    print("\n" + "=" * 44)
    print("       OTA Update via ESP32-C3")
    print("=" * 44)

    # ── Step 1: Module check ──
    print("\n--- Step 1: Module check ---")
    resp = esp.send_cmd("AT")
    if "OK" not in resp:
        print("[OTA] ESP32-C3 not responding. Aborting.")
        return
    print("[OTA] Module OK")

    # ── Step 2: WiFi ──
    print("\n--- Step 2: WiFi ---")
    resp = esp.connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    if "GOT IP" not in resp:
        print("[OTA] WiFi connection failed:")
        print(resp.strip())
        return
    print("[OTA] WiFi connected")
    print("[OTA]", esp.get_ip().strip())

    # ── Step 3: Check for update ──
    print("\n--- Step 3: Version check ---")
    manifest = check_for_update()
    if manifest is None:
        print("[OTA] Nothing to do.")
        esp.disconnect_wifi()
        return

    # ── Step 4: Apply update ──
    version = manifest.get("version", "?")
    files   = manifest.get("files", [])
    print(f"\n--- Step 4: Applying v{version} ({len(files)} file(s)) ---")

    if apply_update(manifest):
        print("\n[OTA] Update completed successfully!")
        esp.disconnect_wifi()
        print("[OTA] Rebooting in 3 seconds...")
        time.sleep(3)
        machine.reset()
    else:
        print("\n[OTA] Update failed -- no version change recorded.")
        esp.disconnect_wifi()


# Auto-run when imported
main()
