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

### `debug_uart.py` — Debug da Comunicação

Script auxiliar para diagnosticar problemas de comunicação UART entre o Pico 2 e o ESP32-C3.
