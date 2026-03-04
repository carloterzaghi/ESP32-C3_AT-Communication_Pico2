from machine import UART, Pin
import time

uart = UART(1, baudrate=115200, tx=Pin(4), rx=Pin(5))
esp_reset = Pin(6, Pin.OUT, value=1)  # GP6 → EN do ESP

def reset_esp():
    """Reset hardware do ESP32-C3 via pino EN."""
    print("Resetando ESP32-C3 via hardware...")
    esp_reset.value(0)    # EN = LOW → reset
    time.sleep_ms(100)
    esp_reset.value(1)    # EN = HIGH → boot
    time.sleep(3)         # Aguardar boot completo
    
    # Limpar mensagem de boot
    while uart.any():
        boot = uart.read()
        print("Boot:", boot.decode('utf-8', 'ignore')) #type: ignore

def send_at(cmd, timeout=2000):
    print(f">> {cmd}")
    uart.write(cmd + '\r\n')
    time.sleep_ms(100)

    start = time.ticks_ms()
    response = b''

    while time.ticks_diff(time.ticks_ms(), start) < timeout:
        if uart.any():
            response += uart.read() #type: ignore
            time.sleep_ms(50)

    decoded = response.decode('utf-8', 'ignore')
    print(f"<< {decoded}")
    print("---")
    return decoded

# Reset e testar
reset_esp()
send_at("AT")
send_at("AT+GMR", timeout=3000)
send_at("AT+CWMODE=1")