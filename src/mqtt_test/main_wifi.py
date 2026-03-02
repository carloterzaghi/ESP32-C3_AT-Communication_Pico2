import network
import utime as time
from lib.rtc.rv3032 import rtc
from src.debug.debug_mode import debug_print
from src.debug.errors_manage import save_error
import socket
import requests


class WiFiConnection():
    def __init__(self, ssid: str, password: str, device: dict):
        self.ssid = ssid
        self.password = password
        self.device = device
        self.wlan = network.WLAN(network.STA_IF)
        self.ap = network.WLAN(network.AP_IF)
        self.ping_time = time.time()
        self.send_test = "NOT_TESTED"
        self.conectado_wifi = False
        self.cancelled_by_user = False
        self.last_cancel_time = 0

    def get_info_test(self) -> str:
        """
        Retorna o estado atual da conexão Wi-Fi.
        """
        return self.send_test
    
    def get_device(self) -> dict:
        """
        Retorna o estado atual da conexão Wi-Fi.
        """
        return self.device
    
    def get_rssi(self) -> int|None:
        """
        Retorna o RSSI da conexão Wi-Fi.
        """
        if self.wlan.isconnected():
            rssi = self.wlan.status('rssi')
            return rssi
        else:
            return None

    def ping_rede(self) -> bool:
        """
        Função para verificar a conectividade com a rede.
        Tenta fazer um ping para o Google DNS
        """
        try:
            addr = socket.getaddrinfo('8.8.8.8', 53)[0][-1]
            s = socket.socket()
            s.settimeout(3)
            s.connect(addr)
            s.close()
            debug_print('Ping OK')
            return True
        except Exception as e:
            save_error(f'Erro - Ping falhou: {e}')
            return False

    def send_weather_data_package_https(self, payload, send_url) -> str:
        """ 
        Envia os dados do pacote de clima via HTTPS para o Mesh.
        """
        debug_print("Enviando dados para o radio")

        if "amazonaws" in send_url:
            from config import AWS_MQTT_PORT
            from lib.umqtt.execute import MQTTControl

            AWS_IOT_ENDPOINT = send_url
            STATION_ID = self.device['data']['deviceInfo']['id']
            MQTT_TOPIC = self.device['data']['deviceConnection']['environment']
            AWS_IOT_CLIENT_ID = f"device_{STATION_ID}"

            # No MicroPython do Pico W, usamos arquivos DER (binário)
            # Caminhos absolutos para garantir que os arquivos sejam encontrados
            CERT_CA_PATH = '/src/data/certs/AmazonRootCA1.der'
            CERT_CRT_PATH = '/src/data/certs/device.der'
            CERT_KEY_PATH = '/src/data/certs/privace.key.der'

            # Verifica se os arquivos existem
            import os
            try:
                os.stat(CERT_CA_PATH)
                os.stat(CERT_CRT_PATH)
                os.stat(CERT_KEY_PATH)
                debug_print(f"Certificados encontrados!")
            except OSError as e:
                # Tenta listar o diretório para debug
                try:
                    files = os.listdir('/src/data/certs')
                    debug_print(f"Arquivos em /src/data/certs: {files}")
                except:
                    debug_print("Diretório /src/data/certs não encontrado")
                save_error(f'Certificados ausentes: {e}')
                return "NOT_SENT"

            aws_iot_core_info = {
                "AWS_CERT_CRT_PATH": CERT_CRT_PATH,
                "AWS_CERT_KEY_PATH": CERT_KEY_PATH,
                "AWS_CERT_CA_PATH": CERT_CA_PATH,
                "AWS_IOT_ENDPOINT": AWS_IOT_ENDPOINT,
                "AWS_IOT_CLIENT_ID": AWS_IOT_CLIENT_ID,
                "AWS_MQTT_PORT": AWS_MQTT_PORT,
                "MQTT_TOPIC": MQTT_TOPIC
            }

            debug_print("Iniciando conexao MQTT com AWS IoT Core...")
            debug_print(f"Dados de conexão via MQTT:\n{aws_iot_core_info}")

            try:
                mqtt_control = MQTTControl(aws_iot_core_info)
                
                # Verifica se a conexão foi bem-sucedida
                connect_response = mqtt_control.connect()
                if not connect_response["success"]:
                    debug_print(f"Falha na conexao MQTT: {connect_response.get('error', 'Erro desconhecido')}")
                    return "NOT_SENT"
                
                # Só publica se a conexão foi bem-sucedida
                response = mqtt_control.publish(payload)

                if response["success"]:
                    debug_print("Sucesso na transmissao")
                    return "SENT"
                else:
                    debug_print(f"Falha na transmissao: {response['error']}")
                    return "NOT_SENT"
            except Exception as e:
                save_error(f'Erro na conexao MQTT: {e}')
                return "NOT_SENT"

        else:
            headers = {'Content-Type':'application/text'}
            server_url = f"https://{send_url}"

            response = requests.post(server_url, data=payload, headers=headers)

            debug_print(f"Resposta: {response}")

            if response.status_code == 200:
                debug_print("Sucesso na transmissao")
                return "SENT"
            else:
                debug_print(f"Falha na transmissao: {response.status_code}")
                return "NOT_SENT"

    def connect(self, check_button_fn=None) -> bool:
        """
        Conecta ao Wi-Fi com o SSID e senha fornecidos.
        
        Args:
            check_button_fn: Função para verificar se o botão foi pressionado
            
        Returns:
            True se a conexao for bem-sucedida, False caso contrário.
        """
        debug_print('Iniciando conexao Wi-Fi robusta...')
        # Desliga AP residual
        ap = network.WLAN(network.AP_IF)
        if ap.active():
            debug_print('Desligando AP...')
            ap.active(False)
            time.sleep(1)

        # Aguarda até AP ficar realmente inativo
        t0 = time.time()
        while ap.active():
            if check_button_fn and check_button_fn():
                debug_print('Conexão WiFi cancelada pelo botão')
                return False
            
            debug_print('Aguardando AP desligar...')
            time.sleep(0.5)
            if time.time() - t0 > 5:
                debug_print('Timeout ao aguardar AP desligar.')
                break

        if self.wlan.active():
            debug_print('Desligando STA...')
            self.wlan.active(False)
            time.sleep(1)

        # Aguarda mais para limpar o estado do driver
        time.sleep(2)

        # Ativa STA
        debug_print('Ativando STA...')
        self.wlan.active(True)
        time.sleep(1)

        # Garante desconexão prévia
        if self.wlan.isconnected():
            debug_print('Desconectando conexao antiga...')
            self.wlan.disconnect()
            time.sleep(1)

        debug_print(f'Conectando ao SSID "{self.ssid}"...')
        self.wlan.connect(self.ssid, self.password)

        timeout = 60  # Timeout de 1 minuto
        start = time.time()
        while not self.wlan.isconnected():
            # Verifica se o botão foi pressionado
            if check_button_fn and check_button_fn():
                debug_print('Conexão WiFi cancelada pelo botão')
                self.wlan.disconnect()
                return False
            
            status = self.wlan.status()
            if status == network.STAT_WRONG_PASSWORD:
                debug_print('Senha incorreta!')
                return False
            elif status == network.STAT_NO_AP_FOUND:
                debug_print('Rede nao encontrada!')
                return False
            elif status == network.STAT_CONNECT_FAIL:
                debug_print('Falha na conexao!')
                return False

            if time.time() - start > timeout:
                debug_print(f'Timeout de 1 minuto na conexao Wi-Fi. Último status: {status}')
                debug_print('Reiniciando o dispositivo...')
                save_error(f'Timeout WiFi após 60s - Reiniciando dispositivo')
                time.sleep(1)  # Aguarda salvar o erro
                import machine
                machine.reset()
            time.sleep(1)

        config = self.wlan.ifconfig()
        debug_print(f'Conectado! IP: {config[0]}, Gateway: {config[2]}, DNS: {config[3]}')
        if config[3] == '0.0.0.0':
            self.wlan.ifconfig((config[0], config[1], config[2], '8.8.8.8'))
            debug_print('DNS configurado manualmente.')
        return True

    def disconnect(self) -> None:
        """ 
        Finaliza a conexão Wi-Fi.
        Desativa a interface STA e limpa o estado do AP.
        """
        if self.wlan.active():
            self.wlan.active(False)
            debug_print('Wi-Fi desligado.')
        else:
            debug_print('Wi-Fi já estava desligado.')


    def start_wifi(self, check_button_fn=None) -> None:
        """
        Inicia a conexão Wi-Fi com o SSID e senha fornecidos.
        
        Args:
            check_button_fn: Função para verificar se o botão foi pressionado
        """
        from config import TRANSMISSION_TEST_INTERVAL
        
        # Se foi cancelado pelo usuário, aguarda cooldown antes de tentar novamente
        if self.cancelled_by_user:
            if time.time() - self.last_cancel_time < 1.0:
                # Cooldown de 1 segundo após cancelamento
                return
            else:
                # Reset da flag após cooldown
                self.cancelled_by_user = False

        # Se já falhou todas as tentativas, não tenta novamente
        # O sistema deve seguir para leitura dos sensores conforme fluxograma
        if self.send_test == "WIFI_FAILED":
            return

        if not self.conectado_wifi:
            # Verifica se o botão foi pressionado antes de conectar
            if check_button_fn and check_button_fn():
                debug_print("Botão pressionado - cancelando conexão WiFi")
                self.cancelled_by_user = True
                self.last_cancel_time = time.time()
                return
            
            # Tenta conectar 3 vezes antes de desistir
            MAX_WIFI_RETRIES = 3
            for attempt in range(1, MAX_WIFI_RETRIES + 1):
                debug_print(f'Tentativa {attempt}/{MAX_WIFI_RETRIES} de conectar no SSID {self.ssid}')
                
                if self.connect(check_button_fn):
                    debug_print(f'Conexão bem-sucedida na tentativa {attempt}.')
                    self.conectado_wifi = True
                    break
                else:
                    debug_print(f'Falha na tentativa {attempt}/{MAX_WIFI_RETRIES}.')
                    # Se foi cancelado, marca a flag e sai
                    if check_button_fn and check_button_fn():
                        self.cancelled_by_user = True
                        self.last_cancel_time = time.time()
                        return
                    
                    # Se não é a última tentativa, aguarda antes de tentar novamente
                    if attempt < MAX_WIFI_RETRIES:
                        debug_print(f'Aguardando 5 segundos antes da próxima tentativa...')
                        time.sleep(5)
            
            # Se após 3 tentativas não conectou, marca para seguir em frente
            if not self.conectado_wifi:
                debug_print(f'Falha em todas as {MAX_WIFI_RETRIES} tentativas de conexão WiFi.')
                # Marca que falhou para que o transmission_controller siga em frente
                self.send_test = "WIFI_FAILED"
        
        elif time.time() - self.ping_time > TRANSMISSION_TEST_INTERVAL:
            # Dado de teste de envio (payload 128)
            from config import data_test_aws
            from ..send_controller import send_data

            debug_print("Tentando enviar payload 128 (dado teste).")

            data_test_aws["station_id"] = self.device['data']['deviceInfo']['id']
            data_test_aws["timestamp"] = rtc().get_unix_time()

            self.send_test = send_data('wifi', self.device, data_test_aws, self)

            if self.send_test == "SENT":
                debug_print("Payload 128 enviado com sucesso.")
                debug_print("Comecando enviar dados dos sensores...")
            else:
                debug_print("Falha ao enviar payload 128. Será salvo com flag NÃO ENVIADO.")
                # O payload 128 será salvo pelo TransmissionController quando detectar a falha

            self.ping_time = time.time()