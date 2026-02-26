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
        # 2 = peripheral mode
        return self.send_cmd("AT+BLEINIT=2", timeout=5000)

    def ble_set_name(self, name):
        return self.send_cmd(f'AT+BLENAME="{name}"')

    def ble_set_adv_data(self, dev_name):
        """Inclui o nome do dispositivo no pacote de advertising (Complete Local Name).
        Constroi o payload manualmente via AT+BLEADVDATA usando AD type 0x09.
        Suportado por todas as versoes do firmware ESP-AT."""
        name_bytes = dev_name.encode()
        # AD Structure: [length][type=0x09][name bytes]
        adv_hex = f"{len(name_bytes) + 1:02x}09" + "".join(f"{b:02x}" for b in name_bytes)
        return self.send_cmd(f'AT+BLEADVDATA="{adv_hex}"', timeout=3000)

    def ble_set_adv_param(self, min_interval=160, max_interval=160, adv_type=0):
        """Configura parametros de advertising.
        adv_type 0 = connectable undirected (padrao, mais compativel).
        Intervalo em unidades de 0.625 ms. 160 = 100 ms.
        channel_map=7 habilita todos os 3 canais de advertising (37, 38, 39)."""
        return self.send_cmd(
            f'AT+BLEADVPARAM={min_interval},{max_interval},{adv_type},0,7',
            timeout=3000
        )

    def ble_get_addr(self):
        """Retorna o endereco MAC BLE do dispositivo."""
        return self.send_cmd("AT+BLEADDR?")

    def ble_gatt_init(self):
        """Cria e inicia o servidor GATT com os servicos padrao do firmware ESP-AT.
        Servico customizado: UUID 0xA002 (srv_idx=3).
        Char write (0xC302, char_idx=3), char notify (0xC304, char_idx=5)."""
        resp = self.send_cmd("AT+BLEGATTSSRVCRE", timeout=5000)
        if "ERROR" in resp:
            print("[GATT] ERRO em BLEGATTSSRVCRE:", resp.strip())
            return resp
        time.sleep_ms(500)
        return self.send_cmd("AT+BLEGATTSSRVSTART", timeout=5000)

    def ble_notify(self, conn_idx, srv_idx, char_idx, data):
        """Envia uma notificacao BLE GATT ao cliente conectado.
        Apos o comando retornar '>', envia os bytes de dados brutos."""
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

    def ble_start_advertising(self):
        return self.send_cmd("AT+BLEADVSTART", timeout=3000)

    def ble_stop_advertising(self):
        return self.send_cmd("AT+BLEADVSTOP")