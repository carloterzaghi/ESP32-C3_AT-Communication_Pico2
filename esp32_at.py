import machine
import time


class ESP32AT:
    def __init__(self, uart_id=1, tx=4, rx=5, reset_pin=6, baudrate=115200):
        self.uart = machine.UART(uart_id, baudrate=baudrate,
                                 tx=machine.Pin(tx), rx=machine.Pin(rx))
        self.reset = machine.Pin(reset_pin, machine.Pin.OUT, value=1)
        time.sleep(0.5)
        self._hw_reset()

    def _hw_reset(self):
        """Reset hardware do ESP32-C3 via pino EN e aguarda 'ready'."""
        print("Resetando ESP32-C3 via hardware...")
        self.reset.value(0)
        time.sleep_ms(100)
        self.reset.value(1)
        # Aguardar boot completo (esperar "ready")
        resp = self._wait_response(5000, "ready")
        print("Boot:", resp.strip())

    def send_cmd(self, cmd, timeout=2000, expected="OK"):
        # Limpar buffer antes de enviar
        while self.uart.any():
            self.uart.read()
        self.uart.write((cmd + "\r\n").encode())
        return self._wait_response(timeout, expected)

    def _wait_response(self, timeout, expected):
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
    def connect_wifi(self, ssid, password):
        self.send_cmd("AT+CWMODE=1")
        time.sleep_ms(500)
        resp = self.send_cmd(f'AT+CWJAP="{ssid}","{password}"',
                             timeout=20000, expected="WIFI GOT IP")
        return resp

    def get_ip(self):
        return self.send_cmd("AT+CIFSR")

    def http_get(self, host, path="/", port=80):
        self.send_cmd("AT+CIPMODE=0")
        time.sleep_ms(200)
        resp = self.send_cmd(f'AT+CIPSTART="TCP","{host}",{port}',
                             timeout=10000, expected="CONNECT")
        if "ERROR" in resp:
            print("Erro ao conectar TCP:", resp)
            return resp

        req = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
        self.send_cmd(f"AT+CIPSEND={len(req)}", timeout=3000, expected=">")
        time.sleep_ms(200)
        # Enviar o corpo da requisição (sem \r\n extra)
        self.uart.write(req.encode())
        return self._wait_response(10000, "CLOSED")

    def disconnect_wifi(self):
        return self.send_cmd("AT+CWQAP")

    # ─────────── BLE ───────────
    def ble_init(self):
        return self.send_cmd("AT+BLEINIT=2", timeout=5000)

    def ble_set_name(self, name):
        return self.send_cmd(f'AT+BLENAME="{name}"')

    def ble_start_advertising(self):
        return self.send_cmd("AT+BLEADVSTART")

    def ble_stop_advertising(self):
        return self.send_cmd("AT+BLEADVSTOP")