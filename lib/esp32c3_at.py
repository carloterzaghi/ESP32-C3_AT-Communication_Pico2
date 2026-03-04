"""
esp32c3_at.py - MicroPython driver for the ESP32-C3-Mini-1 module via AT commands.

This module provides a high-level interface to control the ESP32-C3-Mini-1
from a Raspberry Pi Pico 2 (or any compatible MicroPython board),
using UART communication with the ESP-AT firmware.

Supported features:
  - WiFi (STA): connection, IP retrieval, HTTP GET, disconnection.
  - MQTT: configuration, TLS/TCP connection, publish, subscribe, LWT, SNI, ALPN.
  - BLE: advertising, GATT server, notifications.
  - SNTP: time synchronization (required for TLS certificate validation).
  - PKI / Flash Partitions: writing certificates to flash partitions.
  - Manufacturing NVS (mfg_nvs): writing certificates and data to NVS.
  - Utilities: hardware reset, AT log (SYSLOG).

Typical connection (Pico 2 -> ESP32-C3-Mini-1):
  Pico GP4  (TX)  -> ESP32-C3 RX (GPIO20)
  Pico GP5  (RX)  -> ESP32-C3 TX (GPIO19)
  Pico GP6  (OUT) -> ESP32-C3 EN  (hardware reset)
  GND             -> GND
  3.3 V           -> 3.3 V

Basic usage example (WiFi + MQTT):

    from esp32c3_at import ESP32C3_AT

    esp = ESP32C3_AT(uart_id=1, tx=4, rx=5, reset_pin=6)
    esp.connect_wifi("MyNetwork", "MyPassword")
    esp.sntp_config()
    esp.mqtt_user_cfg("pico-client", scheme=5)
    esp.mqtt_connect("xxxxxxxx.iot.us-east-1.amazonaws.com", port=8883)
    esp.mqtt_pub("topic/test", "hello")
    esp.mqtt_clean()

References:
  - ESP-AT Command Set: https://docs.espressif.com/projects/esp-at/
  - ESP32-C3-Mini-1 Datasheet: https://www.espressif.com/
"""

import machine
import time


class ESP32C3_AT:
    """
    Driver for the ESP32-C3-Mini-1 module using the ESP-AT firmware.

    All communication is done via UART, sending AT commands and receiving
    the corresponding responses. The module is hardware-reset during
    initialization to ensure a clean and known state.

    Attributes:
        uart (machine.UART): UART instance configured for AT communication.
        reset (machine.Pin): ESP32-C3 reset pin (EN) (active low).
    """

    def __init__(self, uart_id: int = 1, tx: int = 4, rx: int = 5,
                 reset_pin: int = 6, baudrate: int = 115200):
        """
        Initializes the UART and applies a hardware reset to the ESP32-C3.

        Args:
            uart_id   (int): Pico UART peripheral ID (0 or 1). Default: 1.
            tx        (int): GP pin number used as TX. Default: GP4.
            rx        (int): GP pin number used as RX. Default: GP5.
            reset_pin (int): GP pin number connected to the module's EN pin.
                             Default: GP6.
            baudrate  (int): UART communication rate in bps. Default: 115200.

        Raises:
            Exception: Propagated by MicroPython if the pins are invalid
                       for the chosen UART.
        """
        self.uart = machine.UART(uart_id, baudrate=baudrate,
                                 tx=machine.Pin(tx), rx=machine.Pin(rx))
        self.reset = machine.Pin(reset_pin, machine.Pin.OUT, value=1)
        time.sleep(0.5)
        self._hw_reset()

    def _hw_reset(self):
        """
        Resets the ESP32-C3 via hardware through the EN pin and waits for the 'ready' message.

        Drives the EN pin low for 100 ms then high again,
        waiting up to 5 seconds for the ESP-AT firmware to fully boot.
        Called automatically by the constructor.
        """
        print("Resetting ESP32-C3 via hardware...")
        self.reset.value(0)
        time.sleep_ms(100)
        self.reset.value(1)
        # Wait for full boot (wait for "ready")
        resp = self._wait_response(5000, "ready")
        print("Boot:", resp.strip())

    def send_cmd(self, cmd: str, timeout: int = 2000, expected: str = "OK") -> str:
        """
        Sends an AT command and returns the full module response.

        Clears the UART buffer before sending to avoid contamination from
        residual data of previous commands.

        Args:
            cmd      (str): AT command without terminator (e.g., ``'AT+GMR'``).
            timeout  (int): Maximum wait time for the response in ms.
                            Default: 2000 ms.
            expected (str): Substring indicating the successful end of the response.
                            The wait also ends if ``'ERROR'`` is received.
                            Default: ``'OK'``.

        Returns:
            str: Full response received from the module (may contain multiple
                 lines). Decoded as UTF-8 with error replacement.
        """
        # Clear buffer before sending
        while self.uart.any():
            self.uart.read()
        self.uart.write((cmd + "\r\n").encode())
        return self._wait_response(timeout, expected)

    def _wait_response(self, timeout: int, expected: str) -> str:
        """
        Waits and accumulates the UART response until the expected token is received or timeout.

        Polls every 10 ms consuming all available bytes from the UART buffer.
        Reading is terminated early if ``expected`` or ``'ERROR'`` are found
        in the accumulated response.

        Args:
            timeout  (int): Maximum wait time in milliseconds.
            expected (str): Substring signaling the end of a valid response.

        Returns:
            str: Accumulated response decoded as UTF-8 (errors replaced
                 with ``U+FFFD``). May be incomplete if timeout occurs.
        """
        start = time.ticks_ms()
        response = b""
        while True:
            elapsed = time.ticks_diff(time.ticks_ms(), start)
            if elapsed >= timeout:
                break
            if self.uart.any():
                data = self.uart.read(self.uart.any())
                if data is not None:
                    response += data
            if expected.encode() in response or b"ERROR" in response:
                break
            time.sleep_ms(10)
        return response.decode("utf-8", "replace")

    # ─────────── WiFi ───────────

    def connect_wifi(self, ssid: str, password: str) -> str:
        """
        Connects the ESP32-C3 to a WiFi network in Station (STA) mode.

        Sets the WiFi mode to Station (``AT+CWMODE=1``) and then initiates
        association with the specified AP (``AT+CWJAP``). Waits up to 20 seconds
        for ``WIFI GOT IP`` confirmation.

        Args:
            ssid     (str): WiFi network name (SSID).
            password (str): WiFi network password.

        Returns:
            str: Full AT response. Contains ``'WIFI GOT IP'`` on success
                 or ``'ERROR'`` / timeout on failure.
        """
        self.send_cmd("AT+CWMODE=1")
        time.sleep_ms(500)
        resp = self.send_cmd(f'AT+CWJAP="{ssid}","{password}"',
                             timeout=20000, expected="WIFI GOT IP")
        return resp

    def get_ip(self) -> str:
        """
        Returns the IP information of the module's WiFi interface.

        Uses the ``AT+CIFSR`` command which returns the local IP (STAIP)
        and the MAC address (STAMAC).

        Returns:
            str: AT response with the module's IP and MAC.
        """
        return self.send_cmd("AT+CIFSR")

    def http_get(self, host: str, path: str = "/", port: int = 80) -> str:
        """
        Performs a simple HTTP GET request over TCP.

        Opens a TCP connection to the server, sends the HTTP 1.1 request
        and waits for the server to close the connection (``CLOSED``).
        Uses normal transmission mode (``AT+CIPMODE=0``).

        Args:
            host (str): Server hostname or IP address (e.g., ``'example.com'``).
            path (str): HTTP resource path (e.g., ``'/api/data'``). Default: ``'/'``.
            port (int): Server TCP port. Default: ``80``.

        Returns:
            str: Full response received, including HTTP headers and body,
                 or the AT error response if the connection fails.
        """
        self.send_cmd("AT+CIPMODE=0")
        time.sleep_ms(200)
        resp = self.send_cmd(f'AT+CIPSTART="TCP","{host}",{port}',
                             timeout=10000, expected="CONNECT")
        if "ERROR" in resp:
            print("Error connecting TCP:", resp)
            return resp

        req = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
        self.send_cmd(f"AT+CIPSEND={len(req)}", timeout=3000, expected=">")
        time.sleep_ms(200)
        # Send the request body (no extra \r\n)
        self.uart.write(req.encode())
        return self._wait_response(10000, "CLOSED")

    def disconnect_wifi(self) -> str:
        """
        Disconnects the ESP32-C3 from the current WiFi network.

        Sends ``AT+CWQAP`` to terminate the AP association.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd("AT+CWQAP")

    # ─────────── MQTT ───────────

    def mqtt_user_cfg(self, client_id: str, scheme: int = 1, username: str = "",
                      password: str = "", cert_key_id: int = 0,
                      ca_id: int = 0, path: str = "") -> str:
        """
        Configures the MQTT client credentials and security scheme (``AT+MQTTUSERCFG``).

        Must be called before :meth:`mqtt_connect`. For TLS with ALPN/SNI
        (AWS IoT Core), call :meth:`mqtt_sni` and :meth:`mqtt_alpn` **after** this method.

        Args:
            client_id   (str): Unique MQTT client identifier.
            scheme      (int): Security / transport scheme:

                * ``1`` -- TCP without encryption.
                * ``2`` -- TLS without certificate verification.
                * ``3`` -- TLS with server certificate verification.
                * ``4`` -- TLS with client certificate.
                * ``5`` -- Mutual TLS (client + server). Use for AWS IoT Core.
                * ``6`` -- WebSocket.
                * ``7`` -- WebSocket Secure (WSS).

                Default: ``1``.
            username    (str): MQTT username. Default: ``''``.
            password    (str): MQTT password. Default: ``''``.
            cert_key_id (int): PKI client certificate pair index
                               stored in flash. Default: ``0``.
            ca_id       (int): CA certificate index stored in flash.
                               Default: ``0``.
            path        (str): Custom authentication path (rarely used).
                               Default: ``''``.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd(
            f'AT+MQTTUSERCFG=0,{scheme},"{client_id}","{username}","{password}",{cert_key_id},{ca_id},"{path}"',
            timeout=5000
        )

    def mqtt_conn_cfg(self, keepalive: int = 120, disable_clean: int = 0,
                      lwt_topic: str = "", lwt_msg: str = "",
                      lwt_qos: int = 0, lwt_retain: int = 0) -> str:
        """
        Configures MQTT connection parameters: keepalive, clean session and LWT (``AT+MQTTCONNCFG``).

        Must be called before :meth:`mqtt_connect`.

        Args:
            keepalive     (int): Keepalive interval in seconds (0 disables it).
                                 Default: ``120``.
            disable_clean (int): ``0`` = clean session on each connection.
                                 ``1`` = persistent session.
                                 Default: ``0``.
            lwt_topic     (str): LWT (Last Will and Testament) message topic.
                                 Leave empty to disable. Default: ``''``.
            lwt_msg       (str): LWT message content. Default: ``''``.
            lwt_qos       (int): LWT message QoS (0, 1 or 2). Default: ``0``.
            lwt_retain    (int): ``1`` to mark the LWT message as retained.
                                 Default: ``0``.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd(
            f'AT+MQTTCONNCFG=0,{keepalive},{disable_clean},"{lwt_topic}","{lwt_msg}",{lwt_qos},{lwt_retain}',
            timeout=3000
        )

    def mqtt_connect(self, host: str, port: int = 1883, reconnect: int = 0) -> str:
        """
        Connects to the MQTT broker (``AT+MQTTCONN``).

        Must be called after :meth:`mqtt_user_cfg` and, optionally,
        after :meth:`mqtt_conn_cfg`, :meth:`mqtt_sni` and :meth:`mqtt_alpn`.

        Args:
            host      (str): MQTT broker address (hostname or IP).
                             Example for AWS IoT Core:
                             ``'xxxxxxxx-ats.iot.us-east-1.amazonaws.com'``.
            port      (int): Broker TCP port.
                             Default: ``1883`` (no TLS).
                             Use ``8883`` for MQTT over TLS or ``443`` with ALPN.
            reconnect (int): ``0`` -- no automatic reconnection.
                             ``1`` -- the module reconnects automatically if the
                             connection is lost. Default: ``0``.

        Returns:
            str: AT response (``'OK'`` on success, or error).
        """
        return self.send_cmd(
            f'AT+MQTTCONN=0,"{host}",{port},{reconnect}',
            timeout=15000, expected="OK"
        )

    def mqtt_pub(self, topic: str, data: str, qos: int = 0, retain: int = 0) -> str:
        """
        Publishes a text message to an MQTT topic (``AT+MQTTPUB``).

        For binary payloads or data containing special characters /
        quotes, use :meth:`mqtt_pub_raw`.

        Args:
            topic  (str): Destination MQTT topic (e.g., ``'device/sensor'``).
            data   (str): Message content (text only, no double quotes).
            qos    (int): QoS level: ``0`` (at most once),
                          ``1`` (at least once), ``2`` (exactly once).
                          Default: ``0``.
            retain (int): ``1`` for the broker to retain the message for new
                          subscribers. Default: ``0``.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd(
            f'AT+MQTTPUB=0,"{topic}","{data}",{qos},{retain}',
            timeout=10000
        )

    def mqtt_pub_raw(self, topic: str, payload, qos: int = 0, retain: int = 0) -> str:
        """
        Publishes binary data to an MQTT topic (``AT+MQTTPUBRAW``).

        Unlike :meth:`mqtt_pub`, accepts any byte sequence,
        including data with quotes, null bytes and control characters.
        The module emits the ``'>'`` prompt to indicate it is ready to
        receive the payload; the bytes are then sent directly over UART.

        Args:
            topic   (str): Destination MQTT topic.
            payload (bytes | bytearray | str): Data to be published.
                    Strings are automatically encoded as UTF-8.
            qos     (int): QoS level (0, 1 or 2). Default: ``0``.
            retain  (int): ``1`` to retain the message on the broker. Default: ``0``.

        Returns:
            str: AT response (``'OK'`` on success). Returns the partial
                 response if the ``'>'`` prompt is not received.
        """
        raw = payload if isinstance(payload, (bytes, bytearray)) else payload.encode()
        cmd = f'AT+MQTTPUBRAW=0,"{topic}",{len(raw)},{qos},{retain}'
        while self.uart.any():
            self.uart.read()
        self.uart.write((cmd + "\r\n").encode())
        resp = self._wait_response(5000, ">")
        if ">" in resp:
            self.uart.write(raw)
            return self._wait_response(10000, "OK")
        return resp

    def mqtt_sub(self, topic: str, qos: int = 0) -> str:
        """
        Subscribes the client to an MQTT topic (``AT+MQTTSUB``).

        After subscribing, messages received on this topic are returned
        via UART in the format: ``+MQTTSUBRECV:0,"<topic>",<len>,<data>``.
        To receive them, monitor the UART (e.g., via :meth:`_wait_response`).

        Args:
            topic (str): Topic to subscribe to (supports ``'+'`` and ``'#'`` wildcards).
            qos   (int): Maximum QoS requested for received messages
                         (0, 1 or 2). Default: ``0``.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd(
            f'AT+MQTTSUB=0,"{topic}",{qos}',
            timeout=5000
        )

    def mqtt_unsub(self, topic: str) -> str:
        """
        Unsubscribes the client from an MQTT topic (``AT+MQTTUNSUB``).

        Args:
            topic (str): Topic to unsubscribe from.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd(f'AT+MQTTUNSUB=0,"{topic}"', timeout=3000)

    def mqtt_clean(self) -> str:
        """
        Closes the MQTT connection and releases all allocated resources (``AT+MQTTCLEAN``).

        Should be called when done using MQTT to ensure a clean
        disconnection from the broker.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd("AT+MQTTCLEAN=0", timeout=5000)

    def mqtt_state(self) -> str:
        """
        Queries the current MQTT connection state (``AT+MQTTCONN?``).

        Useful for checking if the client is still connected to the broker
        without attempting a new connection.

        Returns:
            str: AT response containing the connection state. The state field
                 can be: ``0`` disconnected, ``1`` connected, etc.
        """
        return self.send_cmd("AT+MQTTCONN?", timeout=3000)

    def mqtt_sni(self, sni: str) -> str:
        """
        Sets the SNI (Server Name Indication) for MQTT over TLS connections (``AT+MQTTSNI``).

        Required for AWS IoT Core connections, as the broker validates the
        hostname via SNI. **Must be called after** :meth:`mqtt_user_cfg`.

        Args:
            sni (str): Broker hostname to be sent in the TLS SNI extension.
                       Usually the same as the ``host`` passed to :meth:`mqtt_connect`.
                       Example: ``'xxxxxxxx-ats.iot.us-east-1.amazonaws.com'``.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd(
            f'AT+MQTTSNI=0,"{sni}"',
            timeout=3000
        )

    def mqtt_alpn(self, *alpns: str) -> str:
        """
        Sets the ALPN (Application-Layer Protocol Negotiation) protocols for MQTT/TLS (``AT+MQTTALPN``).

        Required when connecting to AWS IoT Core on port 443 with ALPN
        ``'x-amzn-mqtt-ca'``. **Must be called after** :meth:`mqtt_user_cfg`.
        Calling without arguments disables ALPN.

        Args:
            *alpns (str): One or more ALPN protocol identifiers.
                          Example: ``mqtt_alpn('x-amzn-mqtt-ca')``.
                          No arguments: disables ALPN (``AT+MQTTALPN=0,0``).

        Returns:
            str: AT response (``'OK'`` on success).
        """
        if not alpns:
            return self.send_cmd("AT+MQTTALPN=0,0", timeout=3000)
        alpn_str = ",".join(f'"{a}"' for a in alpns)
        return self.send_cmd(
            f'AT+MQTTALPN=0,{len(alpns)},{alpn_str}',
            timeout=3000
        )

    # ─────────── SNTP ───────────

    def sntp_config(self, enable: int = 1, timezone: int = 0,
                    server1: str = "pool.ntp.org",
                    server2: str = "time.nist.gov") -> str:
        """
        Configures time synchronization via SNTP (``AT+CIPSNTPCFG``).

        **Required for TLS connections**, as the module validates the
        certificate expiration date. Should be called after :meth:`connect_wifi`
        and before :meth:`mqtt_connect` with TLS.

        After calling this method, wait a few seconds for the
        synchronization to complete (check with :meth:`sntp_time`).

        Args:
            enable   (int): ``1`` to enable SNTP, ``0`` to disable.
                            Default: ``1``.
            timezone (int): Timezone offset in hours from UTC.
                            E.g., ``-3`` for BRT (Brasilia). Default: ``0`` (UTC).
            server1  (str): Primary NTP server address.
                            Default: ``'pool.ntp.org'``.
            server2  (str): Secondary NTP server address.
                            Default: ``'time.nist.gov'``.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd(
            f'AT+CIPSNTPCFG={enable},{timezone},"{server1}","{server2}"',
            timeout=3000
        )

    def sntp_time(self) -> str:
        """
        Queries the current time synchronized via SNTP (``AT+CIPSNTPTIME?``).

        Returns:
            str: AT response with the current date/time in the format
                 ``+CIPSNTPTIME:<weekday> <month> <day> <HH:MM:SS> <year>``.
                 Returns an invalid date if SNTP has not yet synchronized.
        """
        return self.send_cmd("AT+CIPSNTPTIME?", timeout=3000)

    # ─────────── System ───────────

    def enable_syslog(self) -> str:
        """
        Enables the module's detailed AT log (``AT+SYSLOG=1``).

        When enabled, the ESP-AT firmware returns additional numeric error
        codes in the responses, useful for debugging TLS and MQTT failures
        (e.g., ``ERR CODE:0x010a0004``).

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd("AT+SYSLOG=1", timeout=1000)

    # ─────────── Manufacturing NVS (certificates) ───────────

    def sysmfg_list(self) -> str:
        """
        Lists the available namespaces in the ``mfg_nvs`` partition (``AT+SYSMFG?``).

        The ``mfg_nvs`` partition stores certificates and other manufacturing
        data persistently in the ESP32-C3 flash.

        Returns:
            str: AT response with the list of present namespaces.
        """
        return self.send_cmd("AT+SYSMFG?", timeout=2000)

    def sysmfg_erase(self, namespace: str, key: str = " ") -> bool:
        """
        Erases data from the ``mfg_nvs`` partition (operation 0 of ``AT+SYSMFG``).

        Useful for removing old certificates before writing new ones with
        :meth:`sysmfg_write`.

        Args:
            namespace (str): NVS namespace name to erase
                             (e.g., ``'mqtt_key'``, ``'mqtt_cert'``, ``'mqtt_ca'``).
            key       (str): Key name within the namespace.
                             ``None`` to erase all keys in the namespace.
                             Default: ``None``.

        Returns:
            bool: ``True`` if the operation succeeded or if the data already
                  did not exist; ``False`` on AT error.
        """
        if key:
            resp = self.send_cmd(
                f'AT+SYSMFG=0,"{namespace}","{key}"', timeout=3000)
        else:
            resp = self.send_cmd(
                f'AT+SYSMFG=0,"{namespace}"', timeout=3000)
        return "ERROR" not in resp

    def sysmfg_write(self, namespace: str, key: str, data: bytes,
                     nvs_type: int = 8) -> bool:
        """
        Writes data to the ``mfg_nvs`` partition (operation 2 of ``AT+SYSMFG``).

        The module emits the ``'>'`` prompt to signal it is ready to
        receive the data, which is then sent in 64-byte chunks to
        avoid saturating the UART buffer.

        Args:
            namespace (str):   Destination NVS namespace name
                               (e.g., ``'mqtt_ca'``, ``'mqtt_cert'``, ``'mqtt_key'``).
            key       (str):   Key name within the namespace
                               (e.g., ``'ca_der'``, ``'cert_der'``, ``'key_der'``).
            data      (bytes): Data to be written (e.g., contents of a
                               ``.der`` file).
            nvs_type  (int):   NVS value type:

                               * ``1``-``6`` -- Integers (U8, I8, U16, I16, U32, I32).
                               * ``7`` -- Null-terminated string.
                               * ``8`` -- Binary blob (default, used for certs).

        Returns:
            bool: ``True`` if the write was confirmed with ``'OK'``;
                  ``False`` on timeout or error.
        """

        length = len(data)
        # Clear UART buffer
        time.sleep_ms(200)
        while self.uart.any():
            self.uart.read()
        time.sleep_ms(100)
        while self.uart.any():
            self.uart.read()

        cmd = f'AT+SYSMFG=2,"{namespace}","{key}",{nvs_type},{length}'
        self.uart.write((cmd + "\r\n").encode())

        # Wait for '>' prompt
        buf = b""
        t0 = time.ticks_ms()
        got_prompt = False
        while time.ticks_diff(time.ticks_ms(), t0) < 8000:
            if self.uart.any():
                chunk = self.uart.read()
                if chunk:
                    buf += chunk
                if b">" in buf:
                    got_prompt = True
                    break
                if b"ERROR" in buf:
                    break
            time.sleep_ms(10)

        if not got_prompt:
            return False

        time.sleep_ms(100)

        # Envia dados em chunks
        CHUNK = 64
        for i in range(0, length, CHUNK):
            self.uart.write(data[i:i+CHUNK])
            time.sleep_ms(20)

        time.sleep_ms(500)

        # Wait for confirmation
        resp_buf = b""
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < 15000:
            if self.uart.any():
                chunk = self.uart.read()
                if chunk:
                    resp_buf += chunk
                if b"OK" in resp_buf:
                    return True
                if b"ERROR" in resp_buf:
                    return False
            time.sleep_ms(10)

        return False

    def sysmfg_read(self, namespace: str, key: str,
                    offset: int = 0, length: int = 64):
        """
        Reads data from the ``mfg_nvs`` partition (operation 1 of ``AT+SYSMFG``).

        Args:
            namespace (str): NVS namespace name.
            key       (str): Key name within the namespace.
            offset    (int): Byte offset from the start of the data.
                             Default: ``0``.
            length    (int): Number of bytes to read. Default: ``64``.

        Returns:
            str | None: AT response with the read data on success;
                        ``None`` if the key does not exist or an error occurs.
        """
        resp = self.send_cmd(
            f'AT+SYSMFG=1,"{namespace}","{key}",{offset},{length}',
            timeout=3000
        )
        if "ERROR" in resp:
            return None
        return resp

    def sysmfg_verify(self, namespace: str, key: str) -> bool:
        """
        Checks whether a key exists in the ``mfg_nvs`` partition.

        Tries to read 1 byte from the specified key; if it returns ``ERROR``,
        the key does not exist or there was a read failure.

        Args:
            namespace (str): NVS namespace name.
            key       (str): Key name to check.

        Returns:
            bool: ``True`` if the key exists; ``False`` otherwise.
        """
        resp = self.send_cmd(
            f'AT+SYSMFG=1,"{namespace}","{key}",0,1',
            timeout=3000
        )
        return "ERROR" not in resp

    # ─────────── Flash Partitions (PKI) ───────────

    def sysflash_list(self) -> str:
        """
        Lists the available data partitions in the flash (``AT+SYSFLASH?``).

        The PKI partitions used by MQTT are: ``mqtt_ca``, ``mqtt_cert``
        and ``mqtt_key``. Certificates must be in DER format.

        Returns:
            str: AT response with partition names, offsets and sizes.
        """
        return self.send_cmd("AT+SYSFLASH?", timeout=3000)

    def sysflash_write(self, partition: str, data: bytes, offset: int = 0) -> bool:
        """
        Writes data to a flash partition via ``AT+SYSFLASH``.

        The ESP-AT MQTT module reads PKI certificates directly from
        flash partitions (and **not** from ``mfg_nvs``). Use this function to
        write the ``.der`` files to the correct partitions before connecting
        via MQTT/TLS.

        Default ESP-AT firmware PKI partitions:

        +--------------+----------------------------------+
        | Partition    | Expected content                 |
        +==============+==================================+
        | ``mqtt_ca``  | CA certificate (AmazonRootCA1)   |
        +--------------+----------------------------------+
        | ``mqtt_cert``| Device certificate               |
        +--------------+----------------------------------+
        | ``mqtt_key`` | Device private key               |
        +--------------+----------------------------------+

        The module emits the ``'>'`` prompt before accepting data, which is
        sent in 64-byte chunks.

        Args:
            partition (str):   Destination partition name (e.g., ``'mqtt_ca'``).
            data      (bytes): Data to write (``.der`` file bytes).
            offset    (int):   Byte offset within the partition.
                               Default: ``0`` (start of partition).

        Returns:
            bool: ``True`` if the write was confirmed with ``'OK'``;
                  ``False`` on timeout, error or prompt not received.
        """

        length = len(data)
        # Clear UART buffer
        time.sleep_ms(200)
        while self.uart.any():
            self.uart.read()
        time.sleep_ms(100)
        while self.uart.any():
            self.uart.read()

        cmd = f'AT+SYSFLASH=1,"{partition}",{offset},{length}'
        self.uart.write((cmd + "\r\n").encode())

        # Wait for '>' prompt
        buf = b""
        t0 = time.ticks_ms()
        got_prompt = False
        while time.ticks_diff(time.ticks_ms(), t0) < 8000:
            if self.uart.any():
                chunk = self.uart.read()
                if chunk:
                    buf += chunk
                if b">" in buf:
                    got_prompt = True
                    break
                if b"ERROR" in buf:
                    print(f"  [SYSFLASH] Error: {buf.decode('utf-8', 'replace').strip()}")
                    break
            time.sleep_ms(10)

        if not got_prompt:
            return False

        time.sleep_ms(100)

        # Send data in chunks
        CHUNK = 64
        for i in range(0, length, CHUNK):
            self.uart.write(data[i:i+CHUNK])
            time.sleep_ms(20)

        time.sleep_ms(500)

        # Wait for confirmation
        resp_buf = b""
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < 15000:
            if self.uart.any():
                chunk = self.uart.read()
                if chunk:
                    resp_buf += chunk
                if b"OK" in resp_buf:
                    return True
                if b"ERROR" in resp_buf:
                    print(f"  [SYSFLASH] Resp: {resp_buf.decode('utf-8', 'replace').strip()}")
                    return False
            time.sleep_ms(10)

        return False

    # ─────────── BLE ───────────

    def ble_init(self) -> str:
        """
        Initializes the BLE controller in Peripheral mode (``AT+BLEINIT=2``).

        Must be called before any other BLE method. Peripheral mode
        allows the device to be discovered and connected by smartphones
        and other central devices.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        # 2 = peripheral mode
        return self.send_cmd("AT+BLEINIT=2", timeout=5000)

    def ble_set_name(self, name: str) -> str:
        """
        Sets the BLE device local name (``AT+BLENAME``).

        The name is advertised in advertising packets and displayed to the user
        during pairing. Should be called before :meth:`ble_start_advertising`.

        Args:
            name (str): BLE device name (max 32 characters).

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd(f'AT+BLENAME="{name}"')

    def ble_set_adv_data(self, dev_name: str) -> str:
        """
        Configures the advertising payload with the device's full name (``AT+BLEADVDATA``).

        Manually builds the AD (*Advertising Data*) structure using
        AD Type ``0x09`` (*Complete Local Name*), compatible with all
        ESP-AT firmware versions.

        Payload structure: ``[length][0x09][name bytes]``

        Args:
            dev_name (str): Name to include in the advertising packet.
                            Should match the name set in :meth:`ble_set_name`.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        name_bytes = dev_name.encode()
        # AD Structure: [length][type=0x09][name bytes]
        adv_hex = f"{len(name_bytes) + 1:02x}09" + "".join(f"{b:02x}" for b in name_bytes)
        return self.send_cmd(f'AT+BLEADVDATA="{adv_hex}"', timeout=3000)

    def ble_set_adv_param(self, min_interval: int = 160, max_interval: int = 160,
                          adv_type: int = 0) -> str:
        """
        Configures BLE advertising parameters (``AT+BLEADVPARAM``).

        The ``channel_map`` is fixed at ``7``, enabling all three advertising
        channels (37, 38 and 39) for maximum compatibility.

        Args:
            min_interval (int): Minimum advertising interval in units of
                                0.625 ms. ``160`` = 100 ms. Default: ``160``.
            max_interval (int): Maximum advertising interval in units of
                                0.625 ms. Default: ``160``.
            adv_type     (int): Advertising type:

                                * ``0`` -- *Connectable Undirected* (default and most
                                  compatible, accepts connections from any central).
                                * ``2`` -- *Scannable Undirected*.
                                * ``3`` -- *Non-connectable Undirected*.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd(
            f'AT+BLEADVPARAM={min_interval},{max_interval},{adv_type},0,7',
            timeout=3000
        )

    def ble_get_addr(self) -> str:
        """
        Returns the module's BLE MAC address (``AT+BLEADDR?``).

        Returns:
            str: AT response containing the MAC address in the format
                 ``+BLEADDR:<type>,<mac>``.
        """
        return self.send_cmd("AT+BLEADDR?")

    def ble_gatt_init(self) -> str:
        """
        Creates and starts the GATT server with the default ESP-AT firmware services.

        Executes ``AT+BLEGATTSSRVCRE`` (creates services) followed by
        ``AT+BLEGATTSSRVSTART`` (starts services). Must be called after
        :meth:`ble_init`.

        Default ESP-AT service layout after ``BLEGATTSSRVCRE``:

        +----------+--------------------------------------+
        | srv_idx  | UUID / Description                   |
        +==========+======================================+
        | 1        | ``0x1801`` Generic Attribute Service |
        +----------+--------------------------------------+
        | 2        | ``0x1800`` Generic Access Service    |
        +----------+--------------------------------------+
        | 3        | ``0xA002`` Custom service            |
        +----------+--------------------------------------+

        Custom service characteristics (srv_idx=3):

        +-----------+-----------+------------------------------------+
        | char_idx  | UUID      | Properties                         |
        +===========+===========+====================================+
        | 3         | ``0xC302``| Write (no response)                |
        +-----------+-----------+------------------------------------+
        | 5         | ``0xC304``| Notify (used by :meth:`ble_notify`)|
        +-----------+-----------+------------------------------------+

        Returns:
            str: AT response from ``AT+BLEGATTSSRVSTART``
                 (``'OK'`` on success).
        """
        resp = self.send_cmd("AT+BLEGATTSSRVCRE", timeout=5000)
        if "ERROR" in resp:
            print("[GATT] ERRO em BLEGATTSSRVCRE:", resp.strip())
            return resp
        time.sleep_ms(500)
        return self.send_cmd("AT+BLEGATTSSRVSTART", timeout=5000)

    def ble_notify(self, conn_idx: int, srv_idx: int,
                   char_idx: int, data) -> str:
        """
        Sends a BLE GATT notification to the connected client (``AT+BLEGATTSNTFY``).

        The module emits the ``'>'`` prompt to indicate it is ready for
        the data, which is then sent directly over UART.

        Args:
            conn_idx (int): BLE connection index (typically ``0`` for
                            the first connected client).
            srv_idx  (int): GATT service index on the server
                            (e.g., ``3`` for the custom service ``0xA002``).
            char_idx (int): Characteristic index within the service
                            (e.g., ``5`` for the Notify characteristic ``0xC304``).
            data     (bytes | str): Data to send via notification. Strings
                            are automatically encoded as UTF-8.

        Returns:
            str: AT response (``'OK'`` on success). Returns the partial
                 response if the ``'>'`` prompt is not received.
        """
        raw = data.encode() if isinstance(data, str) else data
        cmd = f"AT+BLEGATTSNTFY={conn_idx},{srv_idx},{char_idx},{len(raw)}"
        while self.uart.any():
            self.uart.read()
        self.uart.write((cmd + "\r\n").encode())
        resp = self._wait_response(3000, ">")
        if ">" in resp:
            self.uart.write(raw)
            return self._wait_response(3000, "OK")
        return resp

    def ble_start_advertising(self) -> str:
        """
        Starts BLE advertising (``AT+BLEADVSTART``).

        After calling, the device begins announcing its presence so that
        centrals can discover and connect to it. Configure beforehand
        with :meth:`ble_set_adv_data` and :meth:`ble_set_adv_param`.

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd("AT+BLEADVSTART", timeout=3000)

    def ble_stop_advertising(self) -> str:
        """
        Stops BLE advertising (``AT+BLEADVSTOP``).

        Returns:
            str: AT response (``'OK'`` on success).
        """
        return self.send_cmd("AT+BLEADVSTOP")