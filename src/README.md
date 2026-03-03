# 📄 Descrição dos Arquivos

### `esp32c3_at.py` — Driver AT

Classe `ESP32C3_AT` que encapsula toda a comunicação AT com o ESP32-C3:

- Reset automático via hardware (pino EN) na inicialização
- Envio de comandos AT com timeout e resposta esperada configuráveis
- Limpeza automática do buffer UART antes de cada comando
- Métodos prontos para Wi-Fi, MQTT, SNTP e BLE
- Escrita/leitura/apagamento de dados na partição `mfg_nvs` via `AT+SYSMFG`

```python
from esp32c3_at import ESP32C3_AT

esp = ESP32C3_AT(uart_id=1, tx=4, rx=5, reset_pin=6)
print(esp.send_cmd("AT"))  # OK
```

**Métodos de NVS para certificados:**

| Método | Descrição |
|---|---|
| `sysmfg_write(ns, key, data)` | Grava blob binário (tipo 8) na `mfg_nvs` |
| `sysmfg_read(ns, key, offset, length)` | Lê dados da `mfg_nvs` |
| `sysmfg_erase(ns, key=None)` | Apaga uma chave ou namespace inteiro |
| `sysmfg_verify(ns, key)` | Verifica se uma chave existe |

### `main_wifi.py` — Exemplo Wi-Fi + HTTP

1. Conecta a uma rede Wi-Fi
2. Obtém o IP local
3. Faz uma requisição HTTP GET para `api.ipify.org` (retorna o IP público)

### `main_ble.py` — Exemplo BLE Peripheral

1. Inicializa o BLE no modo Peripheral
2. Define o nome como `"Pico2-BLE"`
3. Inicia advertising e aguarda conexões

### `ble_web_test` — Exemplo BLE Peripheral com uma página web

Exemplo completo que integra o Pico 2 (MicroPython) e uma interface Web Bluetooth
para controlar o LED do Pico via ESP32-C3 (firmware AT).

- `main_ble_led.py`: script MicroPython que configura o ESP32 (advertising + GATT),
	interpreta comandos recebidos via GATT Write e envia confirmações via Notify.
- `index.html`: página web (HTML/CSS/JS) que conecta ao ESP32 via Web Bluetooth,
	descobre os serviços/características automaticamente e envia comandos.

### `mqtt_test` — MQTT via ESP32-C3 AT Commands (AWS IoT Core)

Teste de publicação MQTT com TLS mútuo usando os comandos AT nativos do firmware
ESP-AT do ESP32-C3 — sem necessidade de biblioteca `umqtt` nem `ssl` no Pico.

- `umqtt_test.py`: conecta ao Wi-Fi, sincroniza o relógio via SNTP, grava os
  certificados no ESP32 via `AT+SYSMFG`, configura o cliente MQTT com
  `AT+MQTTUSERCFG` (scheme=5 = TLS mútuo), e publica um payload binário via
  `AT+MQTTPUBRAW`.
- `load_certs.py`: script isolado para gravar apenas os certificados (útil para
  re-gravar sem executar o fluxo MQTT completo).
- `certs/`: certificados `.der` para AWS IoT Core:
  - `AmazonRootCA1.der` — CA raiz da Amazon
  - `device.der` — certificado do dispositivo
  - `privace.key.der` — chave privada do dispositivo
- `umqtt/`: implementação alternativa usando MicroPython nativo (Pico W com
  `ssl.wrap_socket`) — referência, não usada com ESP32-C3.

**Como os certificados são armazenados:**

O firmware ESP-AT lê os certificados MQTT da partição `mfg_nvs` em namespaces
dedicados. A convenção de chaves segue o padrão do build system do ESP-AT
([`mfg_nvs.py`](https://github.com/espressif/esp-at/blob/master/components/customized_partitions/generation_tools/mfg_nvs.py)):

| Namespace NVS | Chave NVS | Conteúdo |
|---|---|---|
| `mqtt_ca` | `mqtt_ca` | CA do servidor (AmazonRootCA1) |
| `mqtt_cert` | `mqtt_cert` | Certificado do cliente |
| `mqtt_key` | `mqtt_key` | Chave privada do cliente |

> ⚠️ A chave NVS é igual ao nome do namespace (sem sufixo `.0`). Isso é diferente
> dos namespaces SSL (`client_cert.0`, `client_cert.1`) que suportam múltiplos
> conjuntos.

**Uso com AWS IoT Core:**
1. Copie os arquivos `.der` para a pasta `certs/` no filesystem do Pico
2. Edite `WIFI_SSID`, `WIFI_PASSWORD`, `AWS_HOST`, `MQTT_CLIENT_ID` e `MQTT_TOPIC`
3. Copie `esp32c3_at.py`, `umqtt_test.py` e a pasta `certs/` para o Pico 2
4. Execute `umqtt_test.py` — o script grava os certificados automaticamente

### `debug_uart.py` — Debug da Comunicação

Script auxiliar para diagnosticar problemas de comunicação UART entre o Pico 2 e o ESP32-C3.
