# 📄 Descrição dos Arquivos

### `esp32_at.py` — Driver AT

Classe `ESP32AT` que encapsula toda a comunicação AT com o ESP32-C3:

- Reset automático via hardware (pino EN) na inicialização
- Envio de comandos AT com timeout e resposta esperada configuráveis
- Limpeza automática do buffer UART antes de cada comando
- Métodos prontos para Wi-Fi, HTTP e BLE

```python
from esp32_at import ESP32AT

esp = ESP32AT(uart_id=1, tx=4, rx=5, reset_pin=6)
print(esp.send_cmd("AT"))  # OK
```

### `main_wifi.py` — Exemplo Wi-Fi + HTTP

1. Conecta a uma rede Wi-Fi
2. Obtém o IP local
3. Faz uma requisição HTTP GET para `api.ipify.org` (retorna o IP público)

### `main_ble.py` — Exemplo BLE Peripheral

1. Inicializa o BLE no modo Peripheral
2. Define o nome como `"Pico2-BLE"`
3. Inicia advertising e aguarda conexões

### `ble_web_test` — Exemplo BLE Peripheral com uma página web

Conteúdo desta pasta: exemplo completo que integra o `Pico 2` (MicroPython) e uma
interface Web Bluetooth para controlar o LED do Pico via ESP32-C3 (firmware AT).

- `main_ble_led.py`: script MicroPython que configura o ESP32 (advertising + GATT),
	interpreta comandos recebidos via GATT Write e envia confirmações via Notify.
- `index.html`: página web (HTML/CSS/JS) que conecta ao ESP32 via Web Bluetooth,
	descobre os serviços/características automaticamente e envia comandos.

### `mqtt_test` — Exemplo MQTT via ESP32-C3 AT Commands

Conteúdo desta pasta: teste de publicação MQTT usando os comandos AT nativos do
firmware ESP-AT do ESP32-C3 (sem necessidade de biblioteca umqtt no Pico).

- `umqtt_test.py`: script MicroPython que conecta ao Wi-Fi via ESP32, configura o
	cliente MQTT (`AT+MQTTUSERCFG`), conecta ao broker (`AT+MQTTCONN`) e publica
	um payload binário via `AT+MQTTPUBRAW`.
- `main_wifi.py`: código de referência do projeto original (Pico W nativo) —
	útil como comparação mas **não é usado** neste teste.
- `umqtt/`: biblioteca umqtt original de referência (não utilizada pelo ESP32-AT).
- `certs/`: certificados `.der` para AWS IoT Core (devem ser pré-gravados na
	partição PKI do ESP32 para uso com `MQTT_SCHEME=5`).

**Uso rápido (broker público, sem TLS):**
1. Edite `WIFI_SSID` e `WIFI_PASSWORD` em `umqtt_test.py`
2. Copie `esp32_at.py` e `umqtt_test.py` para o Pico 2
3. Execute — o payload será publicado em `pico2/sensor/data` no `test.mosquitto.org`

**Uso com AWS IoT Core (TLS mútuo):**
1. Grave os certificados na flash do ESP32 (`AT+SYSFLASH`)
2. Altere: `MQTT_SCHEME=5`, `MQTT_PORT=8883`, `MQTT_HOST="seu-endpoint.iot.region.amazonaws.com"`
3. Execute normalmente

### `debug_uart.py` — Debug da Comunicação

Script auxiliar para diagnosticar problemas de comunicação UART entre o Pico 2 e o ESP32-C3.
