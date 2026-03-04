# 📄 Exemplos de Uso

Todos os exemplos importam o driver de `lib/esp32c3_at.py` via `from lib.esp32c3_at import ESP32C3_AT`.
Certifique-se de que a pasta `lib/` com o driver esteja no filesystem do Pico 2.

---

### `wifi_simple_exemple.py` — Wi-Fi + HTTP GET

Exemplo mínimo de conexão Wi-Fi e requisição HTTP.

**O que faz:**
1. Inicializa o driver e testa a comunicação (`AT`)
2. Exibe a versão do firmware AT (`AT+GMR`)
3. Conecta a uma rede Wi-Fi com `connect_wifi(ssid, password)`
4. Obtém o IP local com `get_ip()`
5. Faz um HTTP GET para `api.ipify.org/?format=text` (retorna o IP público)
6. Desconecta do Wi-Fi com `disconnect_wifi()`

**Uso:**
```python
# Edite "SeuSSID" e "SuaSenhaWiFi" no arquivo antes de executar
import wifi_simple_exemple
```

---

### `ble_simple_exemple.py` — BLE Peripheral (Advertising)

Configura o ESP32-C3 como dispositivo BLE Peripheral visível para scanners.

**O que faz:**
1. Inicializa o BLE no modo Peripheral (`ble_init()` → `AT+BLEINIT=2`)
2. Define o nome do dispositivo como `"Pico2-BLE"` (`ble_set_name()`)
3. Configura advertising (connectable, 100 ms, todos os canais) (`ble_set_adv_param()`)
4. Inclui o nome no pacote de advertising via AD Type `0x09` (Complete Local Name) usando `ble_set_adv_data()` — **sem este passo o dispositivo anuncia sem nome**
5. Exibe o endereço MAC BLE para debug (`ble_get_addr()`)
6. Inicia advertising (`ble_start_advertising()`)
7. Monitora a UART em loop, imprimindo eventos de conexão e dados recebidos

**Uso:**
```python
import ble_simple_exemple
# O dispositivo aparecerá como "Pico2-BLE" no scanner BLE do celular
```

---

### `ble_web_exemple/` — Controle de LED via BLE + Web Bluetooth

Exemplo completo que integra o Pico 2 (MicroPython) com uma interface web para
controlar o LED onboard (GP25) via BLE.

#### `main_ble_led.py` — Script MicroPython (roda no Pico 2)

**O que faz:**
1. Inicializa BLE + GATT Server (`ble_init()` + `ble_gatt_init()`)
2. Exibe os UUIDs reais dos serviços e características GATT
3. Configura advertising com nome `"Pico2-BLE"` e inicia
4. No loop principal, processa eventos UART linha a linha:
   - **`+BLECONN:`** — cliente conectou, envia Notify `"CONNECTED"`
   - **`+BLEDISCONN:`** — cliente desconectou, reinicia advertising
   - **`+WRITE:`** — comando recebido via GATT Write (char `0xC302`):
     - `"1"` → liga o LED, responde Notify `"OK:ON"`
     - `"0"` → desliga o LED, responde Notify `"OK:OFF"`
     - Outro → responde Notify `"ERR:<cmd>"`
5. Suporta dois estilos de `+WRITE` do firmware ESP-AT v4.x:
   - **Estilo A:** dado inline na mesma linha (`+WRITE:0,1,3,0,1,1`)
   - **Estilo B:** dado na próxima linha (`+WRITE:0,1,3,0,1` + linha seguinte)

**Índices GATT usados:**

| Constante | Valor | UUID | Função |
|---|---|---|---|
| `SRV_IDX` | 1 | `0xA002` | Serviço customizado |
| `WRITE_CHAR_IDX` | 3 | `0xC302` | Característica Write (recebe comandos) |
| `NTFY_CHAR_IDX` | 6 | `0xC305` | Característica Notify (envia confirmações) |

#### `index.html` — Interface Web Bluetooth (roda no navegador)

Página web moderna (dark mode) que:
- Conecta ao dispositivo `"Pico2-BLE"` via Web Bluetooth
- Descobre automaticamente os UUIDs dos serviços e características GATT
- Exibe os UUIDs descobertos em campos somente-leitura
- Permite ligar/desligar o LED com botões (envia `"1"` ou `"0"` via GATT Write)
- Visualiza o estado do LED com animação de brilho
- Exibe log de eventos em tempo real (conexão, comandos, respostas Notify)
- Recebe confirmações via GATT Notify (`OK:ON`, `OK:OFF`, `CONNECTED`)

> ⚠️ A Web Bluetooth API exige **HTTPS** ou **localhost**. Para testar localmente:
> `python -m http.server 8080` e acesse `http://localhost:8080`

---

### `mqtt_aws_exemple/` — MQTT TLS Mútuo com AWS IoT Core

Teste completo de publicação MQTT com autenticação mútua (TLS) usando comandos AT
nativos — **sem biblioteca `umqtt` nem `ssl` no Pico**.

#### Arquitetura

```
Pico 2 (MicroPython)
  └─ UART1 (AT commands) ──▶ ESP32-C3 (ESP-AT firmware)
                                 └─ TLS mutual auth ──▶ AWS IoT Core :8883
```

#### `umqtt_test.py` — Script principal

**Fluxo de execução (6 passos):**

| Passo | Descrição | Detalhes |
|---|---|---|
| **0** | Diagnóstico | Exibe versão do firmware e estado dos namespaces NVS (`mqtt_ca`, `mqtt_cert`, `mqtt_key`) |
| **1** | Wi-Fi + SNTP | Conecta à rede, configura SNTP (UTC) e aguarda até 15s pela sincronização do relógio |
| **2** | Certificados | Apaga namespaces antigos, converte DER→PEM com `der_to_pem()`, grava na `mfg_nvs` via `sysmfg_write()` e verifica |
| **3** | Conectividade | Confirma IP válido e hora sincronizada antes do TLS |
| **4** | MQTT | Configura `mqtt_user_cfg(scheme=5)` + `mqtt_sni()` + `mqtt_conn_cfg()`, conecta com até 3 tentativas |
| **5** | Publicação | Publica payload binário via `mqtt_pub_raw()` (`AT+MQTTPUBRAW`) |

**Funções auxiliares:**

| Função | Descrição |
|---|---|
| `detect_der_key_type(der_data)` | Detecta se a chave privada DER é PKCS#1 (`RSA PRIVATE KEY`) ou PKCS#8 (`PRIVATE KEY`) via inspeção do ASN.1 |
| `der_to_pem(der_data, pem_type)` | Converte DER binário para PEM (Base64 com headers). Adiciona `\x00` final exigido pelo mbedTLS |
| `step_header(n, title)` | Imprime cabeçalho de seção numerado para facilitar leitura do log |

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
> conjuntos. Gravar em `mqtt_ca.0` causa `AT_MQTT_CA_LENGTH_ERROR` (0x6021).

#### `certs/` — Certificados (não versionados)

Coloque aqui os certificados `.der` exportados do console AWS IoT Core:
- `AmazonRootCA1.der` — CA raiz da Amazon (verifica o servidor)
- `device.der` — Certificado X.509 do dispositivo (identifica o cliente)
- `privace.key.der` — Chave privada RSA/EC do dispositivo (PKCS#1 ou PKCS#8)

**Pré-requisitos AWS:**
1. Certificado ativo no console AWS IoT Core
2. Policy anexada ao certificado com permissão de `iot:Connect` e `iot:Publish`
3. Endpoint correto (formato: `xxxxxxxx-ats.iot.<region>.amazonaws.com`)

**Uso:**
```python
# Edite as constantes no topo do arquivo:
WIFI_SSID      = "sua_rede"
WIFI_PASSWORD  = "sua_senha"
AWS_HOST       = "abc123-ats.iot.us-east-1.amazonaws.com"
MQTT_CLIENT_ID = "pico2_device"
MQTT_TOPIC     = "test/pico2"

# Execute:
import umqtt_test
```
