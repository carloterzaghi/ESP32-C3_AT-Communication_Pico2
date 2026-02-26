from esp32_at import ESP32AT
import machine
import time

# ─────────── Configuracoes ───────────
BLE_NAME    = "Pico2-BLE"
LED_PIN     = 25    # LED onboard do Pico 2 (GP25)

# Indices do servico GATT — confirmados via AT+BLEGATTSSRV? e AT+BLEGATTSCHAR?
# +BLEGATTSSRV:1,1,0xA002,1
# +BLEGATTSCHAR:"char",1,3,0xC302,0x08  -> WRITE (char_idx=3)
# +BLEGATTSCHAR:"char",1,6,0xC305,0x10  -> NOTIFY (char_idx=6)
SRV_IDX        = 1   # Servico customizado UUID 0xA002
WRITE_CHAR_IDX = 3   # Caracteristica WRITE  UUID 0xC302
NTFY_CHAR_IDX  = 6   # Caracteristica NOTIFY UUID 0xC305

# Comandos curtos (1 byte) para caber no atributo GATT do firmware ESP-AT
# O firmware define a caracteristica 0xC302 com tamanho maximo de 1 byte
CMD_ON  = "1"   # Ligar LED
CMD_OFF = "0"   # Desligar LED

# ─────────── Hardware ───────────
led = machine.Pin(LED_PIN, machine.Pin.OUT)

# ─────────── Inicializa ESP32 ───────────
esp = ESP32AT(uart_id=1, tx=4, rx=5, reset_pin=6)

print("\n=== BLE LED Control ===")

resp = esp.ble_init()
print("[BLEINIT]", resp.strip())
if "ERROR" in resp:
    print("ERRO: falha ao inicializar BLE. Verifique o firmware AT.")
    raise SystemExit

time.sleep_ms(500)

resp = esp.ble_set_name(BLE_NAME)
print("[BLENAME]", resp.strip())

# ── GATT Server ──
resp = esp.ble_gatt_init()
print("[GATT INIT]", resp.strip())
if "ERROR" in resp:
    print("ERRO: AT+BLEGATTSSRVCRE ou AT+BLEGATTSSRVSTART falhou.")
    print("Verifique se o firmware AT suporta GATT server.")
    raise SystemExit

# Imprime os UUIDs reais para copiar no site
print("\n--- Servicos GATT disponíveis (copie para o site) ---")
print(esp.send_cmd("AT+BLEGATTSSRV?", timeout=3000).strip())
print("--- Características GATT ---")
print(esp.send_cmd("AT+BLEGATTSCHAR?", timeout=3000).strip())
print("----------------------------------------------------\n")

# Lê srv_idx e char_idx reais da resposta
resp = esp.ble_set_adv_param()
print("[BLEADVPARAM]", resp.strip())

resp = esp.ble_set_adv_data(BLE_NAME)
print("[BLEADVDATA]", resp.strip())

resp = esp.ble_start_advertising()
print("[BLEADVSTART]", resp.strip())

print(f"\nAguardando conexao BLE como '{BLE_NAME}'...")

# ─────────── Loop principal ───────────
conn_idx     = None
buf          = b""
pending_write = None   # guarda metadados do +WRITE enquanto espera a linha de dados

while True:
    if esp.uart.any():
        chunk = esp.uart.read(esp.uart.any())
        if chunk:
            buf += chunk

    # Processa linha por linha
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        text = line.strip().decode("utf-8", "replace")
        if not text:
            continue

        # ── Linha de dados pendente de um +WRITE anterior ──
        if pending_write is not None:
            cmd = text.strip()
            pending_write = None

            print(f"[CMD] Recebido: '{cmd}'")

            if cmd == CMD_ON:
                led.value(1)
                confirmation = "OK:ON"
                print("[LED] Ligado")
            elif cmd == CMD_OFF:
                led.value(0)
                confirmation = "OK:OFF"
                print("[LED] Desligado")
            else:
                confirmation = f"ERR:{cmd}"
                print(f"[LED] Comando desconhecido: '{cmd}'")

            if conn_idx is not None:
                resp = esp.ble_notify(conn_idx, SRV_IDX, NTFY_CHAR_IDX, confirmation)
                print(f"[NOTIFY] {confirmation} ->", resp.strip())
            else:
                print("[NOTIFY] Nenhum cliente conectado.")
            continue

        # ── Cliente conectou ──
        if text.startswith("+BLECONN:"):
            try:
                conn_idx = int(text.split(":")[1].split(",")[0])
            except Exception:
                conn_idx = 0
            print(f"[+] Cliente conectado (conn_idx={conn_idx})")
            # Notifica o site que a conexao foi estabelecida
            resp = esp.ble_notify(conn_idx, SRV_IDX, NTFY_CHAR_IDX, "CONNECTED")
            print("[NOTIFY] CONNECTED ->", resp.strip())

        # ── Cliente desconectou ──
        elif text.startswith("+BLEDISCONN:"):
            print("[-] Cliente desconectado. Reiniciando advertising...")
            conn_idx = None
            esp.ble_start_advertising()

        # ── Cabecalho WRITE GATT recebido ──
        # Formato v4.x (dois estilos possiveis):
        #   Estilo A (dado inline):  +WRITE:0,1,3,0,1,1
        #   Estilo B (dado na prox linha): +WRITE:0,1,3,0,1  -> prox linha = 1
        # Ignora writes no CCCD (desc_idx>0 ou char_idx != WRITE_CHAR_IDX)
        elif text.startswith("+WRITE:"):
            parts = text[7:].split(",", 5)
            # char_idx esta em parts[2]
            try:
                char_idx_recv = int(parts[2]) if len(parts) > 2 else -1
            except ValueError:
                char_idx_recv = -1

            if char_idx_recv != WRITE_CHAR_IDX:
                # Write no CCCD ou outra caracteristica — ignorar
                continue

            if len(parts) >= 6 and parts[5].strip():
                # Estilo A — dado ja esta na mesma linha
                cmd = parts[5].strip()
                print(f"[CMD] Recebido: '{cmd}'")
                if cmd == CMD_ON:
                    led.value(1)
                    confirmation = "OK:ON"
                    print("[LED] Ligado")
                elif cmd == CMD_OFF:
                    led.value(0)
                    confirmation = "OK:OFF"
                    print("[LED] Desligado")
                else:
                    confirmation = f"ERR:{cmd}"
                    print(f"[LED] Comando desconhecido: '{cmd}'")
                if conn_idx is not None:
                    resp = esp.ble_notify(conn_idx, SRV_IDX, NTFY_CHAR_IDX, confirmation)
                    print(f"[NOTIFY] {confirmation} ->", resp.strip())
            else:
                # Estilo B — dado vem na proxima linha
                pending_write = parts

    time.sleep_ms(10)
