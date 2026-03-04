# ESP32-C3_AT-Communication_Pico2

Projeto de comunicação entre **Raspberry Pi Pico 2 (RP2350)** e **ESP32-C3-Mini-1** via comandos **AT** por UART.

O ESP32-C3 roda o firmware oficial **ESP-AT da Espressif** (v4.1.1.0) e funciona como módulo Wi-Fi/BLE controlado pelo Pico 2 através de comandos AT enviados por cabo serial.

---

## ✨ Funcionalidades

- **Wi-Fi (STA):** conexão, obtenção de IP, requisições HTTP GET
- **MQTT com TLS mútuo:** conexão ao AWS IoT Core com autenticação de cliente e servidor (scheme 5), SNI e ALPN
- **Certificados via NVS:** gravação de certificados `.der` diretamente na partição `mfg_nvs` do ESP32-C3 via `AT+SYSMFG`, sem ferramentas externas
- **BLE Peripheral:** advertising, servidor GATT, Write e Notify — com exemplo de controle de LED via Web Bluetooth
- **SNTP:** sincronização de horário (obrigatória para validação de certificados TLS)
- **Reset por hardware:** pino EN controlado pelo Pico para garantir estado limpo na inicialização
- **Log AT (`AT+SYSLOG`):** códigos de erro detalhados para depuração de falhas TLS/MQTT
- **Zero dependências externas:** funciona com MicroPython puro, sem bibliotecas adicionais no Pico

---

## 📁 Estrutura do Projeto

```
lib/
└── esp32c3_at.py              # Driver principal (classe ESP32C3_AT)
examples/
├── wifi_simple_exemple.py     # Exemplo Wi-Fi + HTTP GET
├── ble_simple_exemple.py      # Exemplo BLE Peripheral (advertising)
├── ble_web_exemple/
│   ├── main_ble_led.py        # Controle de LED via BLE + GATT server
│   └── index.html             # Interface Web Bluetooth (HTML/CSS/JS)
└── mqtt_aws_exemple/
    ├── umqtt_test.py           # MQTT TLS mútuo para AWS IoT Core
    └── certs/                  # Certificados .der (não versionados)
utils/
└── debug_uart.py              # Utilitário de diagnóstico UART
```

---

## 🔧 Hardware Necessário

| Componente | Descrição |
|---|---|
| **Raspberry Pi Pico 2** | Microcontrolador RP2350 rodando MicroPython |
| **ESP32-C3-Mini-1** | Módulo Wi-Fi/BLE com firmware AT da Espressif |
| **Jumpers/Cabos** | Para conexão UART entre os dois módulos |

---

## 🔌 Conexões (Pinout)

```
  Pico 2                      ESP32-C3-Mini-1
  ┌──────────┐                ┌──────────────┐
  │ GP4 (TX) ──────────────▶  GPIO6 (RX)     │
  │ GP5 (RX) ◀──────────────  GPIO7 (TX)     │
  │ GP6      ──────────────▶  EN (Reset)     │
  │ GND ─────────────────────  GND            │
  │ 3V3(OUT) ────────────────  3V3            │
  └──────────┘                └──────────────┘
```

![Pinout](pinout.jpg)

| Pico 2 | ESP32-C3-Mini-1 | Função |
|---|---|---|
| GP4 (UART1 TX) | GPIO6 (RX) | Dados Pico → ESP |
| GP5 (UART1 RX) | GPIO7 (TX) | Dados ESP → Pico |
| GP6 | EN | Reset hardware do ESP |
| GND | GND | Referência comum |
| 3V3(OUT) | 3V3 | Alimentação |

> ⚠️ **TX do Pico vai no RX do ESP e vice-versa** (conexão cruzada).

---

## 🚀 Como Usar

### 1. Gravar o Firmware AT no ESP32-C3-Mini-1

Baixe o firmware oficial: [ESP32-C3-MINI-1 AT v4.1.1.0](https://docs.espressif.com/projects/esp-at/en/latest/esp32c3/AT_Binary_Lists/esp_at_binaries.html)

Grave com `esptool`:

```bash
python -m esptool --chip esp32c3 --port COM7 --baud 460800 --before default-reset --after hard-reset write-flash --flash-mode dio --flash-freq 40m --flash-size 4MB 0x0 bootloader/bootloader.bin 0x8000 partition_table/partition-table.bin 0xd000 ota_data_initial.bin 0x1e000 at_customize.bin 0x1f000 customized_partitions/mfg_nvs.bin 0x60000 esp-at.bin
```

### 2. Instalar MicroPython no Pico 2

Baixe o firmware MicroPython para o Pico 2: [micropython.org](https://micropython.org/download/RPI_PICO2/)

### 3. Copiar os Arquivos para o Pico 2

Usando **Thonny**, **mpremote** ou a extensão **MicroPico** do VS Code, copie os arquivos para o Pico 2.

> **Importante:** o driver deve ficar na pasta `lib/` do Pico para que os imports
> `from lib.esp32c3_at import ESP32C3_AT` funcionem corretamente.

Estrutura mínima no filesystem do Pico:
```
/
├── lib/
│   └── esp32c3_at.py
└── <exemplo>.py
```

### 4. Executar

**Exemplo Wi-Fi + HTTP GET:**

Copie `lib/esp32c3_at.py` (na pasta `lib/`) e `examples/wifi_simple_exemple.py` para o Pico 2:
```python
import wifi_simple_exemple
```
Conecta ao Wi-Fi, obtém o IP local e faz um HTTP GET para `api.ipify.org` (retorna o IP público).

**Exemplo BLE Peripheral (advertising):**

Copie `lib/esp32c3_at.py` e `examples/ble_simple_exemple.py` para o Pico 2:
```python
import ble_simple_exemple
```
Inicializa o BLE como `"Pico2-BLE"`, configura advertising com nome visível e aguarda conexões.

**Exemplo BLE + LED via Web Bluetooth:**

Copie `lib/esp32c3_at.py` e `examples/ble_web_exemple/main_ble_led.py` para o Pico 2.
Abra `index.html` no Chrome (via HTTPS ou `localhost`) e conecte via Web Bluetooth.
A página descobre automaticamente os UUIDs GATT e permite ligar/desligar o LED onboard (GP25)
com confirmação via Notify.

**Teste MQTT com AWS IoT Core (TLS mútuo):**

1. Copie os certificados `.der` para a pasta `certs/` no filesystem do Pico.
2. Edite os parâmetros em `examples/mqtt_aws_exemple/umqtt_test.py`:
   ```python
   WIFI_SSID      = "sua_rede"
   WIFI_PASSWORD  = "sua_senha"
   AWS_HOST       = "abc123-ats.iot.us-east-1.amazonaws.com"
   MQTT_CLIENT_ID = "pico2_device"
   MQTT_TOPIC     = "seu/topico"
   ```
3. Copie para o Pico 2: `lib/esp32c3_at.py` (na pasta `lib/`), `umqtt_test.py` e a pasta `certs/`.
4. Execute — o script executa 6 passos automaticamente:
   - **Passo 0:** Diagnóstico do firmware e estado NVS
   - **Passo 1:** Conecta Wi-Fi + sincroniza relógio via SNTP
   - **Passo 2:** Converte DER→PEM e grava certificados na `mfg_nvs` via `AT+SYSMFG`
   - **Passo 3:** Confirma IP e hora válida pré-conexão
   - **Passo 4:** Configura MQTT TLS mútuo (scheme=5) com SNI + ALPN e conecta
   - **Passo 5:** Publica payload binário via `AT+MQTTPUBRAW`

---

## 📌 Comandos AT Úteis

| Comando | Descrição |
|---|---|
| `AT` | Teste de comunicação |
| `AT+GMR` | Versão do firmware AT |
| `AT+RST` | Reset por software |
| `AT+CWMODE=1` | Modo Station (cliente Wi-Fi) |
| `AT+CWJAP="ssid","pwd"` | Conectar ao Wi-Fi |
| `AT+CWLAP` | Listar redes disponíveis |
| `AT+CIFSR` | Ver IP atribuído |
| `AT+CIPSTART="TCP","host",port` | Abrir conexão TCP |
| `AT+CIPSEND=<len>` | Enviar dados |
| `AT+CIPCLOSE` | Fechar conexão |
| `AT+BLEINIT=2` | Iniciar BLE (Peripheral) |
| `AT+BLENAME="nome"` | Definir nome BLE |
| `AT+BLEADVSTART` | Iniciar advertising BLE |
| `AT+MQTTUSERCFG=0,5,"id","","",0,0,""` | Configurar MQTT com TLS mútuo (scheme=5) |
| `AT+MQTTSNI=0,"host"` | Definir SNI para TLS |
| `AT+MQTTALPN=0,1,"x-amzn-mqtt-ca"` | Definir ALPN (AWS IoT Core porta 443) |
| `AT+MQTTCONN=0,"host",8883,0` | Conectar ao broker MQTT |
| `AT+MQTTPUB=0,"topic","msg",0,0` | Publicar mensagem de texto |
| `AT+MQTTPUBRAW=0,"topic",<len>,0,0` | Publicar dados binários |
| `AT+MQTTSUB=0,"topic",0` | Subscrever tópico MQTT |
| `AT+MQTTCLEAN=0` | Encerrar conexão MQTT |
| `AT+SYSMFG=2,"mqtt_ca","mqtt_ca",8,<len>` | Gravar certificado CA na NVS |
| `AT+CIPSNTPCFG=1,-3,"pool.ntp.org"` | Configurar SNTP (fuso BRT) |
| `AT+SYSLOG=1` | Ativar log AT detalhado |

📖 Documentação completa: [ESP-AT Command Set](https://docs.espressif.com/projects/esp-at/en/latest/esp32c3/AT_Command_Set/index.html)

---

## 🛠️ Tecnologias

- **MicroPython** v1.27.0 (RP2350)
- **ESP-AT Firmware** v4.1.1.0 (ESP32-C3)
- **esptool** v5.2.0

---

## 📄 Licença

MIT License — © 2026 Carlo Terzaghi Tuck Schneider. Veja [LICENSE](LICENSE).