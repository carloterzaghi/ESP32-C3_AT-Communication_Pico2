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
    
### `debug_uart.py` — Debug da Comunicação

Script auxiliar para diagnosticar problemas de comunicação UART entre o Pico 2 e o ESP32-C3.
