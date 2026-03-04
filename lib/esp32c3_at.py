"""
esp32c3_at.py - Driver MicroPython para o módulo ESP32-C3-Mini-1 via comandos AT.

Este módulo fornece uma interface de alto nível para controlar o ESP32-C3-Mini-1
a partir de um Raspberry Pi Pico 2 (ou qualquer placa MicroPython compatível),
utilizando a comunicação UART com o firmware ESP-AT.

Funcionalidades suportadas:
  - WiFi (STA): conexão, obtenção de IP, HTTP GET, desconexão.
  - MQTT: configuração, conexão TLS/TCP, publish, subscribe, LWT, SNI, ALPN.
  - BLE: advertising, servidor GATT, notificações.
  - SNTP: sincronização de horário (necessária para validação de certificados TLS).
  - PKI / Flash Partitions: gravação de certificados nas partições da flash.
  - Manufacturing NVS (mfg_nvs): gravação de certificados e dados na NVS.
  - Utilitários: reset por hardware, log AT (SYSLOG).

Conexão típica (Pico 2 → ESP32-C3-Mini-1):
  Pico GP4  (TX)  → ESP32-C3 RX (GPIO20)
  Pico GP5  (RX)  → ESP32-C3 TX (GPIO19)
  Pico GP6  (OUT) → ESP32-C3 EN  (reset por hardware)
  GND             → GND
  3.3 V           → 3.3 V

Exemplo de uso básico (WiFi + MQTT):

    from esp32c3_at import ESP32C3_AT

    esp = ESP32C3_AT(uart_id=1, tx=4, rx=5, reset_pin=6)
    esp.connect_wifi("MinhaRede", "MinhaSenha")
    esp.sntp_config()
    esp.mqtt_user_cfg("pico-client", scheme=5)
    esp.mqtt_connect("xxxxxxxx.iot.us-east-1.amazonaws.com", port=8883)
    esp.mqtt_pub("topico/teste", "hello")
    esp.mqtt_clean()

Referências:
  - ESP-AT Command Set: https://docs.espressif.com/projects/esp-at/
  - ESP32-C3-Mini-1 Datasheet: https://www.espressif.com/
"""

import machine
import time


class ESP32C3_AT:
    """
    Driver para o módulo ESP32-C3-Mini-1 utilizando o firmware ESP-AT.

    Toda a comunicação é feita via UART, enviando comandos AT e recebendo
    as respostas correspondentes. O módulo é reiniciado por hardware durante
    a inicialização para garantir um estado limpo e conhecido.

    Attributes:
        uart (machine.UART): Instância UART configurada para comunicação AT.
        reset (machine.Pin): Pino de reset (EN) do ESP32-C3 (ativo em nível baixo).
    """

    def __init__(self, uart_id: int = 1, tx: int = 4, rx: int = 5,
                 reset_pin: int = 6, baudrate: int = 115200):
        """
        Inicializa a UART e aplica um reset por hardware no ESP32-C3.

        Args:
            uart_id   (int): ID do periférico UART do Pico (0 ou 1). Padrão: 1.
            tx        (int): Número do pino GP usado como TX. Padrão: GP4.
            rx        (int): Número do pino GP usado como RX. Padrão: GP5.
            reset_pin (int): Número do pino GP ligado ao pino EN do módulo.
                             Padrão: GP6.
            baudrate  (int): Taxa de comunicação UART em bps. Padrão: 115200.

        Raises:
            Exception: Propagada pelo MicroPython se os pinos forem inválidos
                       para o UART escolhido.
        """
        self.uart = machine.UART(uart_id, baudrate=baudrate,
                                 tx=machine.Pin(tx), rx=machine.Pin(rx))
        self.reset = machine.Pin(reset_pin, machine.Pin.OUT, value=1)
        time.sleep(0.5)
        self._hw_reset()

    def _hw_reset(self):
        """
        Reinicia o ESP32-C3 por hardware via pino EN e aguarda a mensagem 'ready'.

        Coloca o pino EN em nível baixo por 100 ms e depois em nível alto,
        aguardando até 5 segundos pelo boot completo do firmware ESP-AT.
        Chamado automaticamente pelo construtor.
        """
        print("Resetando ESP32-C3 via hardware...")
        self.reset.value(0)
        time.sleep_ms(100)
        self.reset.value(1)
        # Aguardar boot completo (esperar "ready")
        resp = self._wait_response(5000, "ready")
        print("Boot:", resp.strip())

    def send_cmd(self, cmd: str, timeout: int = 2000, expected: str = "OK") -> str:
        """
        Envia um comando AT e retorna a resposta completa do módulo.

        Limpa o buffer UART antes de enviar para evitar contaminação por
        dados residuais de comandos anteriores.

        Args:
            cmd      (str): Comando AT sem terminador (ex.: ``'AT+GMR'``).
            timeout  (int): Tempo máximo de espera pela resposta em ms.
                            Padrão: 2000 ms.
            expected (str): Substring que indica o fim bem-sucedido da resposta.
                            A espera também termina se ``'ERROR'`` for recebido.
                            Padrão: ``'OK'``.

        Returns:
            str: Resposta completa recebida do módulo (pode conter múltiplas
                 linhas). Decodificada como UTF-8 com substituição de erros.
        """
        # Limpar buffer antes de enviar
        while self.uart.any():
            self.uart.read()
        self.uart.write((cmd + "\r\n").encode())
        return self._wait_response(timeout, expected)

    def _wait_response(self, timeout: int, expected: str) -> str:
        """
        Aguarda e acumula a resposta UART até receber o token esperado ou atingir timeout.

        Realiza polling a cada 10 ms consumindo todos os bytes disponíveis no
        buffer UART. A leitura é encerrada antecipadamente caso ``expected`` ou
        ``'ERROR'`` sejam encontrados na resposta acumulada.

        Args:
            timeout  (int): Tempo máximo de espera em milissegundos.
            expected (str): Substring que sinaliza fim da resposta válida.

        Returns:
            str: Resposta acumulada decodificada como UTF-8 (erros substituídos
                 por ``U+FFFD``). Pode estar incompleta se ocorrer timeout.
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
        Conecta o ESP32-C3 a uma rede WiFi no modo Station (STA).

        Define o modo WiFi como Station (``AT+CWMODE=1``) e então inicia
        a associação ao AP informado (``AT+CWJAP``). Aguarda até 20 segundos
        pela confirmação ``WIFI GOT IP``.

        Args:
            ssid     (str): Nome da rede WiFi (SSID).
            password (str): Senha da rede WiFi.

        Returns:
            str: Resposta AT completa. Contém ``'WIFI GOT IP'`` em caso de
                 sucesso ou ``'ERROR'`` / timeout em caso de falha.
        """
        self.send_cmd("AT+CWMODE=1")
        time.sleep_ms(500)
        resp = self.send_cmd(f'AT+CWJAP="{ssid}","{password}"',
                             timeout=20000, expected="WIFI GOT IP")
        return resp

    def get_ip(self) -> str:
        """
        Retorna as informações de IP da interface WiFi do módulo.

        Usa o comando ``AT+CIFSR`` que retorna o IP local (STAIP)
        e o endereço MAC (STAMAC).

        Returns:
            str: Resposta AT com IP e MAC do módulo.
        """
        return self.send_cmd("AT+CIFSR")

    def http_get(self, host: str, path: str = "/", port: int = 80) -> str:
        """
        Realiza uma requisição HTTP GET simples sobre TCP.

        Abre uma conexão TCP com o servidor, envia a requisição HTTP 1.1
        e aguarda o fechamento da conexão pelo servidor (``CLOSED``).
        Utiliza o modo de transmissão normal (``AT+CIPMODE=0``).

        Args:
            host (str): Nome de host ou endereço IP do servidor (ex.: ``'example.com'``).
            path (str): Caminho do recurso HTTP (ex.: ``'/api/data'``). Padrão: ``'/'``.
            port (int): Porta TCP do servidor. Padrão: ``80``.

        Returns:
            str: Resposta completa recebida, incluindo cabeçalhos e corpo HTTP,
                 ou a resposta de erro AT se a conexão falhar.
        """
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

    def disconnect_wifi(self) -> str:
        """
        Desconecta o ESP32-C3 da rede WiFi atual.

        Envia ``AT+CWQAP`` para encerrar a associação com o AP.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd("AT+CWQAP")

    # ─────────── MQTT ───────────

    def mqtt_user_cfg(self, client_id: str, scheme: int = 1, username: str = "",
                      password: str = "", cert_key_id: int = 0,
                      ca_id: int = 0, path: str = "") -> str:
        """
        Configura as credenciais e o esquema de segurança do cliente MQTT (``AT+MQTTUSERCFG``).

        Deve ser chamado antes de :meth:`mqtt_connect`. Para TLS com ALPN/SNI
        (AWS IoT Core), chame :meth:`mqtt_sni` e :meth:`mqtt_alpn` **após** este método.

        Args:
            client_id   (str): Identificador único do cliente MQTT.
            scheme      (int): Esquema de segurança / transporte:

                * ``1`` – TCP sem criptografia.
                * ``2`` – TLS sem verificação de certificado.
                * ``3`` – TLS com verificação do certificado do servidor.
                * ``4`` – TLS com certificado de cliente.
                * ``5`` – TLS mútuo (cliente + servidor). Usar para AWS IoT Core.
                * ``6`` – WebSocket.
                * ``7`` – WebSocket Secure (WSS).

                Padrão: ``1``.
            username    (str): Nome de usuário MQTT. Padrão: ``''``.
            password    (str): Senha MQTT. Padrão: ``''``.
            cert_key_id (int): Índice do par de certificados PKI de cliente
                               armazenado na flash. Padrão: ``0``.
            ca_id       (int): Índice do certificado CA armazenado na flash.
                               Padrão: ``0``.
            path        (str): Caminho customizado de autenticação (raramente usado).
                               Padrão: ``''``.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd(
            f'AT+MQTTUSERCFG=0,{scheme},"{client_id}","{username}","{password}",{cert_key_id},{ca_id},"{path}"',
            timeout=5000
        )

    def mqtt_conn_cfg(self, keepalive: int = 120, disable_clean: int = 0,
                      lwt_topic: str = "", lwt_msg: str = "",
                      lwt_qos: int = 0, lwt_retain: int = 0) -> str:
        """
        Configura parâmetros de conexão MQTT: keepalive, sessão limpa e LWT (``AT+MQTTCONNCFG``).

        Deve ser chamado antes de :meth:`mqtt_connect`.

        Args:
            keepalive     (int): Intervalo de keepalive em segundos (0 desabilita).
                                 Padrão: ``120``.
            disable_clean (int): ``0`` = sessão limpa a cada conexão (clean session).
                                 ``1`` = mantém a sessão persistente.
                                 Padrão: ``0``.
            lwt_topic     (str): Tópico da mensagem LWT (Last Will and Testament).
                                 Deixe vazio para desabilitar. Padrão: ``''``.
            lwt_msg       (str): Conteúdo da mensagem LWT. Padrão: ``''``.
            lwt_qos       (int): QoS da mensagem LWT (0, 1 ou 2). Padrão: ``0``.
            lwt_retain    (int): ``1`` para marcar a mensagem LWT como retained.
                                 Padrão: ``0``.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd(
            f'AT+MQTTCONNCFG=0,{keepalive},{disable_clean},"{lwt_topic}","{lwt_msg}",{lwt_qos},{lwt_retain}',
            timeout=3000
        )

    def mqtt_connect(self, host: str, port: int = 1883, reconnect: int = 0) -> str:
        """
        Conecta ao broker MQTT (``AT+MQTTCONN``).

        Deve ser chamado após :meth:`mqtt_user_cfg` e, opcionalmente,
        após :meth:`mqtt_conn_cfg`, :meth:`mqtt_sni` e :meth:`mqtt_alpn`.

        Args:
            host      (str): Endereço do broker MQTT (hostname ou IP).
                             Exemplo para AWS IoT Core:
                             ``'xxxxxxxx-ats.iot.us-east-1.amazonaws.com'``.
            port      (int): Porta TCP do broker.
                             Padrão: ``1883`` (sem TLS).
                             Use ``8883`` para MQTT sobre TLS ou ``443`` com ALPN.
            reconnect (int): ``0`` – sem reconexão automática.
                             ``1`` – o módulo reconecta automaticamente se perder
                             a conexão. Padrão: ``0``.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso, ou erro).
        """
        return self.send_cmd(
            f'AT+MQTTCONN=0,"{host}",{port},{reconnect}',
            timeout=15000, expected="OK"
        )

    def mqtt_pub(self, topic: str, data: str, qos: int = 0, retain: int = 0) -> str:
        """
        Publica uma mensagem de texto em um tópico MQTT (``AT+MQTTPUB``).

        Para payloads binários ou dados que contenham caracteres especiais /
        aspas, utilize :meth:`mqtt_pub_raw`.

        Args:
            topic  (str): Tópico MQTT de destino (ex.: ``'dispositivo/sensor'``).
            data   (str): Conteúdo da mensagem (somente texto, sem aspas duplas).
            qos    (int): Nível de QoS: ``0`` (no máximo uma vez),
                          ``1`` (pelo menos uma vez), ``2`` (exatamente uma vez).
                          Padrão: ``0``.
            retain (int): ``1`` para que o broker retenha a mensagem para novos
                          subscribers. Padrão: ``0``.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd(
            f'AT+MQTTPUB=0,"{topic}","{data}",{qos},{retain}',
            timeout=10000
        )

    def mqtt_pub_raw(self, topic: str, payload, qos: int = 0, retain: int = 0) -> str:
        """
        Publica dados binários em um tópico MQTT (``AT+MQTTPUBRAW``).

        Diferente de :meth:`mqtt_pub`, aceita qualquer sequência de bytes,
        incluindo dados com aspas, zeros e caracteres de controle.
        O módulo emite o prompt ``'>'`` para indicar que está pronto para
        receber o payload; os bytes são então enviados diretamente pela UART.

        Args:
            topic   (str): Tópico MQTT de destino.
            payload (bytes | bytearray | str): Dados a serem publicados.
                    Strings são automaticamente codificadas como UTF-8.
            qos     (int): Nível de QoS (0, 1 ou 2). Padrão: ``0``.
            retain  (int): ``1`` para reter a mensagem no broker. Padrão: ``0``.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso). Retorna a resposta
                 parcial caso o prompt ``'>'`` não seja recebido.
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
        Inscreve o cliente em um tópico MQTT (``AT+MQTTSUB``).

        Após a inscrição, mensagens recebidas nesse tópico são retornadas
        pela UART no formato: ``+MQTTSUBRECV:0,"<topic>",<len>,<data>``.
        Para recebê-las, monitore a UART (ex.: via :meth:`_wait_response`).

        Args:
            topic (str): Tópico a assinar (suporta wildcards ``'+'`` e ``'#'``).
            qos   (int): QoS máximo solicitado para as mensagens recebidas
                         (0, 1 ou 2). Padrão: ``0``.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd(
            f'AT+MQTTSUB=0,"{topic}",{qos}',
            timeout=5000
        )

    def mqtt_unsub(self, topic: str) -> str:
        """
        Cancela a inscrição do cliente em um tópico MQTT (``AT+MQTTUNSUB``).

        Args:
            topic (str): Tópico do qual se deseja cancelar a inscrição.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd(f'AT+MQTTUNSUB=0,"{topic}"', timeout=3000)

    def mqtt_clean(self) -> str:
        """
        Encerra a conexão MQTT e libera todos os recursos alocados (``AT+MQTTCLEAN``).

        Deve ser chamado ao finalizar o uso do MQTT para garantir uma
        desconexão limpa do broker.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd("AT+MQTTCLEAN=0", timeout=5000)

    def mqtt_state(self) -> str:
        """
        Consulta o estado atual da conexão MQTT (``AT+MQTTCONN?``).

        Útil para verificar se o cliente ainda está conectado ao broker
        sem tentar uma nova conexão.

        Returns:
            str: Resposta AT contendo o estado da conexão. O campo de estado
                 pode ser: ``0`` desconectado, ``1`` conectado, etc.
        """
        return self.send_cmd("AT+MQTTCONN?", timeout=3000)

    def mqtt_sni(self, sni: str) -> str:
        """
        Define o SNI (Server Name Indication) para conexões MQTT sobre TLS (``AT+MQTTSNI``).

        Obrigatório para conexões ao AWS IoT Core, pois o broker valida o
        hostname pelo SNI. **Deve ser chamado após** :meth:`mqtt_user_cfg`.

        Args:
            sni (str): Hostname do broker que será enviado na extensão TLS SNI.
                       Geralmente igual ao ``host`` passado em :meth:`mqtt_connect`.
                       Exemplo: ``'xxxxxxxx-ats.iot.us-east-1.amazonaws.com'``.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd(
            f'AT+MQTTSNI=0,"{sni}"',
            timeout=3000
        )

    def mqtt_alpn(self, *alpns: str) -> str:
        """
        Define os protocolos ALPN (Application-Layer Protocol Negotiation) para MQTT/TLS (``AT+MQTTALPN``).

        Necessário ao conectar ao AWS IoT Core pela porta 443 com ALPN
        ``'x-amzn-mqtt-ca'``. **Deve ser chamado após** :meth:`mqtt_user_cfg`.
        Chamar sem argumentos desabilita o ALPN.

        Args:
            *alpns (str): Um ou mais identificadores de protocolo ALPN.
                          Exemplo: ``mqtt_alpn('x-amzn-mqtt-ca')``.
                          Sem argumentos: desabilita ALPN (``AT+MQTTALPN=0,0``).

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
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
        Configura a sincronização de horário via SNTP (``AT+CIPSNTPCFG``).

        **Necessário para conexões TLS**, pois o módulo valida a data de
        validade dos certificados. Deve ser chamado após :meth:`connect_wifi`
        e antes de :meth:`mqtt_connect` com TLS.

        Após chamar este método, aguarde alguns segundos para que a
        sincronização ocorra (verifique com :meth:`sntp_time`).

        Args:
            enable   (int): ``1`` para ativar SNTP, ``0`` para desativar.
                            Padrão: ``1``.
            timezone (int): Fuso horário em horas em relação ao UTC.
                            Ex.: ``-3`` para BRT (Brasília). Padrão: ``0`` (UTC).
            server1  (str): Endereço do servidor NTP primário.
                            Padrão: ``'pool.ntp.org'``.
            server2  (str): Endereço do servidor NTP secundário.
                            Padrão: ``'time.nist.gov'``.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd(
            f'AT+CIPSNTPCFG={enable},{timezone},"{server1}","{server2}"',
            timeout=3000
        )

    def sntp_time(self) -> str:
        """
        Consulta a hora atual sincronizada via SNTP (``AT+CIPSNTPTIME?``).

        Returns:
            str: Resposta AT com a data/hora atual no formato
                 ``+CIPSNTPTIME:<weekday> <month> <day> <HH:MM:SS> <year>``.
                 Retorna uma data inválida se o SNTP ainda não sincronizou.
        """
        return self.send_cmd("AT+CIPSNTPTIME?", timeout=3000)

    # ─────────── System ───────────

    def enable_syslog(self) -> str:
        """
        Habilita o log AT detalhado do módulo (``AT+SYSLOG=1``).

        Quando ativado, o firmware ESP-AT passa a retornar códigos de erro
        numéricos adicionais nas respostas, úteis para depurar falhas de
        TLS e MQTT (ex.: ``ERR CODE:0x010a0004``).

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd("AT+SYSLOG=1", timeout=1000)

    # ─────────── Manufacturing NVS (certificados) ───────────

    def sysmfg_list(self) -> str:
        """
        Lista os namespaces disponíveis na partição ``mfg_nvs`` (``AT+SYSMFG?``).

        A partição ``mfg_nvs`` armazena certificados e outros dados de
        fabricação de forma persistente na flash do ESP32-C3.

        Returns:
            str: Resposta AT com a lista de namespaces presentes.
        """
        return self.send_cmd("AT+SYSMFG?", timeout=2000)

    def sysmfg_erase(self, namespace: str, key: str = " ") -> bool:
        """
        Apaga dados da partição ``mfg_nvs`` (operação 0 de ``AT+SYSMFG``).

        Útil para remover certificados antigos antes de gravar novos com
        :meth:`sysmfg_write`.

        Args:
            namespace (str): Nome do namespace NVS a apagar
                             (ex.: ``'mqtt_key'``, ``'mqtt_cert'``, ``'mqtt_ca'``).
            key       (str): Nome da chave dentro do namespace.
                             ``None`` para apagar todas as chaves do namespace.
                             Padrão: ``None``.

        Returns:
            bool: ``True`` se a operação foi bem-sucedida ou se o dado já não
                  existia; ``False`` em caso de erro AT.
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
        Grava dados na partição ``mfg_nvs`` (operação 2 de ``AT+SYSMFG``).

        O módulo emite o prompt ``'>'`` para sinalizar que está pronto para
        receber os dados, que são então enviados em blocos de 64 bytes para
        não saturar o buffer UART.

        Args:
            namespace (str):   Nome do namespace NVS de destino
                               (ex.: ``'mqtt_ca'``, ``'mqtt_cert'``, ``'mqtt_key'``).
            key       (str):   Nome da chave dentro do namespace
                               (ex.: ``'ca_der'``, ``'cert_der'``, ``'key_der'``).
            data      (bytes): Dados a serem gravados (ex.: conteúdo de um
                               arquivo ``.der``).
            nvs_type  (int):   Tipo NVS do valor:

                               * ``1``–``6`` – Inteiros (U8, I8, U16, I16, U32, I32).
                               * ``7`` – String terminada em ``\\0``.
                               * ``8`` – Blob binário (padrão, usado para certs).

        Returns:
            bool: ``True`` se a gravação foi confirmada com ``'OK'``;
                  ``False`` se ocorrer timeout ou erro.
        """

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

    def sysmfg_read(self, namespace: str, key: str,
                    offset: int = 0, length: int = 64):
        """
        Lê dados da partição ``mfg_nvs`` (operação 1 de ``AT+SYSMFG``).

        Args:
            namespace (str): Nome do namespace NVS.
            key       (str): Nome da chave dentro do namespace.
            offset    (int): Deslocamento em bytes a partir do início do dado.
                             Padrão: ``0``.
            length    (int): Quantidade de bytes a ler. Padrão: ``64``.

        Returns:
            str | None: Resposta AT com os dados lidos em caso de sucesso;
                        ``None`` se a chave não existir ou ocorrer erro.
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
        Verifica se uma chave existe na partição ``mfg_nvs``.

        Tenta ler 1 byte da chave especificada; se retornar ``ERROR``,
        a chave não existe ou houve falha de leitura.

        Args:
            namespace (str): Nome do namespace NVS.
            key       (str): Nome da chave a verificar.

        Returns:
            bool: ``True`` se a chave existir; ``False`` caso contrário.
        """
        resp = self.send_cmd(
            f'AT+SYSMFG=1,"{namespace}","{key}",0,1',
            timeout=3000
        )
        return "ERROR" not in resp

    # ─────────── Flash Partitions (PKI) ───────────

    def sysflash_list(self) -> str:
        """
        Lista as partições de dados disponíveis na flash (``AT+SYSFLASH?``).

        As partições PKI usadas pelo MQTT são: ``mqtt_ca``, ``mqtt_cert``
        e ``mqtt_key``. Os certificados devem estar no formato DER.

        Returns:
            str: Resposta AT com nomes, offsets e tamanhos das partições.
        """
        return self.send_cmd("AT+SYSFLASH?", timeout=3000)

    def sysflash_write(self, partition: str, data: bytes, offset: int = 0) -> bool:
        """
        Grava dados em uma partição da flash via ``AT+SYSFLASH``.

        O módulo MQTT do ESP-AT lê os certificados PKI diretamente das
        partições da flash (e **não** da ``mfg_nvs``). Use esta função para
        gravar os arquivos ``.der`` nas partições corretas antes de conectar
        via MQTT/TLS.

        Partições PKI padrão do firmware ESP-AT:

        +--------------+----------------------------------+
        | Partição     | Conteúdo esperado                |
        +==============+==================================+
        | ``mqtt_ca``  | Certificado CA (AmazonRootCA1)   |
        +--------------+----------------------------------+
        | ``mqtt_cert``| Certificado do dispositivo       |
        +--------------+----------------------------------+
        | ``mqtt_key`` | Chave privada do dispositivo     |
        +--------------+----------------------------------+

        O módulo emite o prompt ``'>'`` antes de aceitar os dados, que são
        enviados em blocos de 64 bytes.

        Args:
            partition (str):   Nome da partição de destino (ex.: ``'mqtt_ca'``).
            data      (bytes): Dados a gravar (arquivo ``.der`` em bytes).
            offset    (int):   Deslocamento em bytes dentro da partição.
                               Padrão: ``0`` (início da partição).

        Returns:
            bool: ``True`` se a gravação foi confirmada com ``'OK'``;
                  ``False`` em caso de timeout, erro ou prompt não recebido.
        """

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

    def ble_init(self) -> str:
        """
        Inicializa o controlador BLE no modo Peripheral (``AT+BLEINIT=2``).

        Deve ser chamado antes de qualquer outro método BLE. O modo
        Peripheral permite que o dispositivo seja descoberto e conectado
        por smartphones e outros dispositivos central.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        # 2 = peripheral mode
        return self.send_cmd("AT+BLEINIT=2", timeout=5000)

    def ble_set_name(self, name: str) -> str:
        """
        Define o nome local do dispositivo BLE (``AT+BLENAME``).

        O nome é anunciado nos pacotes de advertising e exibido ao usuário
        durante o pareamento. Deve ser chamado antes de :meth:`ble_start_advertising`.

        Args:
            name (str): Nome do dispositivo BLE (máx. 32 caracteres).

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd(f'AT+BLENAME="{name}"')

    def ble_set_adv_data(self, dev_name: str) -> str:
        """
        Configura o payload de advertising com o nome completo do dispositivo (``AT+BLEADVDATA``).

        Constrói manualmente a estrutura AD (*Advertising Data*) usando o
        AD Type ``0x09`` (*Complete Local Name*), compatível com todas as
        versões do firmware ESP-AT.

        Estrutura do payload: ``[length][0x09][bytes do nome]``

        Args:
            dev_name (str): Nome que será incluído no pacote de advertising.
                            Deve corresponder ao nome definido em :meth:`ble_set_name`.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        name_bytes = dev_name.encode()
        # AD Structure: [length][type=0x09][name bytes]
        adv_hex = f"{len(name_bytes) + 1:02x}09" + "".join(f"{b:02x}" for b in name_bytes)
        return self.send_cmd(f'AT+BLEADVDATA="{adv_hex}"', timeout=3000)

    def ble_set_adv_param(self, min_interval: int = 160, max_interval: int = 160,
                          adv_type: int = 0) -> str:
        """
        Configura os parâmetros do advertising BLE (``AT+BLEADVPARAM``).

        O ``channel_map`` é fixado em ``7``, habilitando os três canais de
        advertising (37, 38 e 39) para máxima compatibilidade.

        Args:
            min_interval (int): Intervalo mínimo de advertising em unidades de
                                0,625 ms. ``160`` = 100 ms. Padrão: ``160``.
            max_interval (int): Intervalo máximo de advertising em unidades de
                                0,625 ms. Padrão: ``160``.
            adv_type     (int): Tipo de advertising:

                                * ``0`` – *Connectable Undirected* (padrão e mais
                                  compatível, aceita conexões de qualquer central).
                                * ``2`` – *Scannable Undirected*.
                                * ``3`` – *Non-connectable Undirected*.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd(
            f'AT+BLEADVPARAM={min_interval},{max_interval},{adv_type},0,7',
            timeout=3000
        )

    def ble_get_addr(self) -> str:
        """
        Retorna o endereço MAC BLE do módulo (``AT+BLEADDR?``).

        Returns:
            str: Resposta AT contendo o endereço MAC no formato
                 ``+BLEADDR:<type>,<mac>``.
        """
        return self.send_cmd("AT+BLEADDR?")

    def ble_gatt_init(self) -> str:
        """
        Cria e inicia o servidor GATT com os serviços padrão do firmware ESP-AT.

        Executa ``AT+BLEGATTSSRVCRE`` (cria serviços) seguido de
        ``AT+BLEGATTSSRVSTART`` (inicia serviços). Deve ser chamado após
        :meth:`ble_init`.

        Layout padrão dos serviços ESP-AT após ``BLEGATTSSRVCRE``:

        +----------+--------------------------------------+
        | srv_idx  | UUID / Descrição                     |
        +==========+======================================+
        | 1        | ``0x1801`` Generic Attribute Service |
        +----------+--------------------------------------+
        | 2        | ``0x1800`` Generic Access Service    |
        +----------+--------------------------------------+
        | 3        | ``0xA002`` Serviço customizado       |
        +----------+--------------------------------------+

        Características do serviço customizado (srv_idx=3):

        +-----------+-----------+------------------------------------+
        | char_idx  | UUID      | Propriedades                       |
        +===========+===========+====================================+
        | 3         | ``0xC302``| Write (sem resposta)               |
        +-----------+-----------+------------------------------------+
        | 5         | ``0xC304``| Notify (usado em :meth:`ble_notify`)|
        +-----------+-----------+------------------------------------+

        Returns:
            str: Resposta AT do ``AT+BLEGATTSSRVSTART``
                 (``'OK'`` em caso de sucesso).
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
        Envia uma notificação BLE GATT ao cliente conectado (``AT+BLEGATTSNTFY``).

        O módulo emite o prompt ``'>'`` para indicar que está pronto para
        os dados, que são então enviados diretamente pela UART.

        Args:
            conn_idx (int): Índice da conexão BLE (normalmente ``0`` para
                            o primeiro cliente conectado).
            srv_idx  (int): Índice do serviço GATT no servidor
                            (ex.: ``3`` para o serviço customizado ``0xA002``).
            char_idx (int): Índice da característica dentro do serviço
                            (ex.: ``5`` para a característica Notify ``0xC304``).
            data     (bytes | str): Dados a enviar via notificação. Strings
                            são automaticamente codificadas como UTF-8.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso). Retorna a resposta
                 parcial se o prompt ``'>'`` não for recebido.
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
        Inicia o advertising BLE (``AT+BLEADVSTART``).

        Após a chamada, o dispositivo passa a anunciar sua presença para
        que centrais possam descobri-lo e conectar-se. Configure antes
        com :meth:`ble_set_adv_data` e :meth:`ble_set_adv_param`.

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd("AT+BLEADVSTART", timeout=3000)

    def ble_stop_advertising(self) -> str:
        """
        Interrompe o advertising BLE (``AT+BLEADVSTOP``).

        Returns:
            str: Resposta AT (``'OK'`` em caso de sucesso).
        """
        return self.send_cmd("AT+BLEADVSTOP")