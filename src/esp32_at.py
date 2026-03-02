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

    # ─────────── MQTT ───────────
    def mqtt_user_cfg(self, client_id, scheme=1, username="", password="",
                      cert_key_id=0, ca_id=0, path=""):
        """Configura o cliente MQTT.
        scheme: 1=TCP, 2=TLS(no verify), 3=TLS(server cert),
                4=TLS(client cert), 5=TLS(mutual), 6=WS, 7=WSS.
        Para AWS IoT Core com certificados pre-gravados: scheme=5, cert_key_id=0, ca_id=0.
        """
        return self.send_cmd(
            f'AT+MQTTUSERCFG=0,{scheme},"{client_id}","{username}","{password}",{cert_key_id},{ca_id},"{path}"',
            timeout=5000
        )

    def mqtt_conn_cfg(self, keepalive=120, disable_clean=0, lwt_topic="",
                      lwt_msg="", lwt_qos=0, lwt_retain=0):
        """Configura parametros de conexao MQTT (keepalive, LWT, etc)."""
        return self.send_cmd(
            f'AT+MQTTCONNCFG=0,{keepalive},{disable_clean},"{lwt_topic}","{lwt_msg}",{lwt_qos},{lwt_retain}',
            timeout=3000
        )

    def mqtt_connect(self, host, port=1883, reconnect=0):
        """Conecta ao broker MQTT.
        reconnect: 0=sem reconexao automatica, 1=reconecta automaticamente."""
        return self.send_cmd(
            f'AT+MQTTCONN=0,"{host}",{port},{reconnect}',
            timeout=15000, expected="OK"
        )

    def mqtt_pub(self, topic, data, qos=0, retain=0):
        """Publica uma string no topico MQTT (dados texto, sem binario)."""
        return self.send_cmd(
            f'AT+MQTTPUB=0,"{topic}","{data}",{qos},{retain}',
            timeout=10000
        )

    def mqtt_pub_raw(self, topic, payload, qos=0, retain=0):
        """Publica dados binarios (bytes) no topico MQTT.
        Usa AT+MQTTPUBRAW que aceita dados brutos apos o prompt '>'."""
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

    def mqtt_sub(self, topic, qos=0):
        """Inscreve-se num topico MQTT."""
        return self.send_cmd(
            f'AT+MQTTSUB=0,"{topic}",{qos}',
            timeout=5000
        )

    def mqtt_unsub(self, topic):
        """Cancela inscricao num topico MQTT."""
        return self.send_cmd(f'AT+MQTTUNSUB=0,"{topic}"', timeout=3000)

    def mqtt_clean(self):
        """Desconecta e limpa a sessao MQTT."""
        return self.send_cmd("AT+MQTTCLEAN=0", timeout=5000)

    def mqtt_state(self):
        """Consulta estado atual da conexao MQTT (AT+MQTTCONN?)."""
        return self.send_cmd("AT+MQTTCONN?", timeout=3000)

    def mqtt_sni(self, sni):
        """Define MQTT Server Name Indication (SNI) para TLS.
        Obrigatorio para AWS IoT Core. Deve ser chamado APOS AT+MQTTUSERCFG."""
        return self.send_cmd(
            f'AT+MQTTSNI=0,"{sni}"',
            timeout=3000
        )

    def mqtt_alpn(self, *alpns):
        """Define MQTT ALPN (Application Layer Protocol Negotiation).
        Deve ser chamado APOS AT+MQTTUSERCFG.
        Ex: mqtt_alpn("x-amzn-mqtt-ca") para AWS IoT porta 443."""
        if not alpns:
            return self.send_cmd("AT+MQTTALPN=0,0", timeout=3000)
        alpn_str = ",".join(f'"{a}"' for a in alpns)
        return self.send_cmd(
            f'AT+MQTTALPN=0,{len(alpns)},{alpn_str}',
            timeout=3000
        )

    # ─────────── SNTP ───────────
    def sntp_config(self, enable=1, timezone=0, server1="pool.ntp.org",
                    server2="time.nist.gov"):
        """Configura sincronizacao SNTP. Necessario para TLS (validacao de certificados)."""
        return self.send_cmd(
            f'AT+CIPSNTPCFG={enable},{timezone},"{server1}","{server2}"',
            timeout=3000
        )

    def sntp_time(self):
        """Retorna a hora atual sincronizada via SNTP."""
        return self.send_cmd("AT+CIPSNTPTIME?", timeout=3000)

    # ─────────── System ───────────
    def enable_syslog(self):
        """Habilita log AT detalhado (mostra codigos de erro do TLS/MQTT)."""
        return self.send_cmd("AT+SYSLOG=1", timeout=1000)

    # ─────────── Manufacturing NVS (certificados) ───────────
    def sysmfg_list(self):
        """Lista namespaces na particao mfg_nvs (AT+SYSMFG?)."""
        return self.send_cmd("AT+SYSMFG?", timeout=2000)

    def sysmfg_erase(self, namespace, key=None):
        """Apaga dados da mfg_nvs via AT+SYSMFG.
        Se key=None, apaga todas as chaves do namespace.
        Se key eh fornecida, apaga apenas aquela chave.
        Retorna True se apagou com sucesso (ou se ja nao existia)."""
        if key:
            resp = self.send_cmd(
                f'AT+SYSMFG=0,"{namespace}","{key}"', timeout=3000)
        else:
            resp = self.send_cmd(
                f'AT+SYSMFG=0,"{namespace}"', timeout=3000)
        return "ERROR" not in resp

    def sysmfg_write(self, namespace, key, data, nvs_type=8):
        """Grava dados na mfg_nvs via AT+SYSMFG.
        nvs_type: 1-6=integers, 7=string, 8=binary (blob).
        Retorna True se gravou com sucesso."""
        length = len(data)
        # Limpa buffer UART
        time.sleep_ms(200)
        while self.uart.any():
            self.uart.read()
        time.sleep_ms(100)
        while self.uart.any():
            self.uart.read()

        cmd = f'AT+SYSMFG=2,"{namespace}","{key}",{nvs_type},{length}'
        self.uart.write((cmd + "\r\n").encode())

        # Espera prompt '>'
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

        # Espera confirmacao
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

    def sysmfg_read(self, namespace, key, offset=0, length=64):
        """Le dados da mfg_nvs via AT+SYSMFG (operacao 1).
        Retorna os bytes lidos ou None se falhou."""
        resp = self.send_cmd(
            f'AT+SYSMFG=1,"{namespace}","{key}",{offset},{length}',
            timeout=3000
        )
        if "ERROR" in resp:
            return None
        return resp

    def sysmfg_verify(self, namespace, key):
        """Verifica se um certificado existe na mfg_nvs tentando ler 1 byte."""
        resp = self.send_cmd(
            f'AT+SYSMFG=1,"{namespace}","{key}",0,1',
            timeout=3000
        )
        return "ERROR" not in resp

    # ─────────── Flash Partitions (PKI) ───────────
    def sysflash_list(self):
        """Lista particoes de dados na flash (AT+SYSFLASH?).
        Retorna resposta com particoes disponiveis."""
        return self.send_cmd("AT+SYSFLASH?", timeout=3000)

    def sysflash_write(self, partition, data, offset=0):
        """Grava dados numa particao flash via AT+SYSFLASH.
        Usado para gravar certificados PKI (mqtt_ca, mqtt_cert, mqtt_key).
        O modulo MQTT do ESP-AT le certs das particoes PKI na flash,
        nao do mfg_nvs. Retorna True se gravou com sucesso."""
        length = len(data)
        # Limpa buffer UART
        time.sleep_ms(200)
        while self.uart.any():
            self.uart.read()
        time.sleep_ms(100)
        while self.uart.any():
            self.uart.read()

        cmd = f'AT+SYSFLASH=1,"{partition}",{offset},{length}'
        self.uart.write((cmd + "\r\n").encode())

        # Espera prompt '>'
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
                    print(f"  [SYSFLASH] Erro: {buf.decode('utf-8', 'replace').strip()}")
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

        # Espera confirmacao
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