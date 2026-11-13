from machine import Pin, UART
import json
import time

# ---------- AJUSTE ----------
SOLO_PRUEBA_TM = False  # True = solo prueba del TM1638. False = programa completo.

# ---------- PINES ----------
UART_TX = 27    # TX del ESP32 -> PA3 de la Blue Pill
UART_RX = 34    # RX del ESP32 <- PA2 de la Blue Pill
TM_STB = 25
TM_CLK = 26
TM_DIO = 33


# ---------- CONTROLADOR TM1638 ----------
class TM1638:
    def __init__(self, stb, clk, dio):
        self.stb = Pin(stb, Pin.OUT, value=1)
        self.clk = Pin(clk, Pin.OUT, value=0)
        self.dio = Pin(dio, Pin.OUT, value=0)
        self.brightness = 7
        self.clear()

    def _write_byte(self, data):
        for _ in range(8):
            self.clk.value(0)
            self.dio.value(data & 1)
            data >>= 1
            self.clk.value(1)
            time.sleep_us(2)

    def _command(self, cmd):
        self.stb.value(0)
        self._write_byte(cmd)
        self.stb.value(1)

    def _write_data(self, addr, data):
        self._command(0x44)
        self.stb.value(0)
        self._write_byte(0xC0 | (addr & 0x0F))
        self._write_byte(data)
        self.stb.value(1)

    def clear(self):
        for i in range(16):
            self._write_data(i, 0x00)
        self._command(0x88 | self.brightness)

    def display_text(self, text):
        chars = {
            ' ': 0x00, '0': 0x3F, '1': 0x06, '2': 0x5B, '3': 0x4F, '4': 0x66,
            '5': 0x6D, '6': 0x7D, '7': 0x07, '8': 0x7F, '9': 0x6F,
            'A': 0x77, 'B': 0x7C, 'C': 0x39, 'D': 0x5E, 'E': 0x79, 'F': 0x71,
            'L': 0x38, 'O': 0x5C, 'P': 0x73, 'R': 0x50, 'S': 0x6D, 'T': 0x78,
            'U': 0x3E, 'N': 0x54, 'G': 0x3D, 'K': 0x76, '-': 0x40,
            'I': 0x06, 'M': 0x37, 'Y': 0x6E, 'H': 0x76
        }
        text = (text.upper() + "        ")[:8]
        for i, c in enumerate(text):
            self._write_data(i * 2, chars.get(c, 0x00))
        self._command(0x88 | self.brightness)

    def set_leds(self, mask):
        for i in range(8):
            val = 1 if (mask & (1 << i)) else 0
            self._write_data(i * 2 + 1, val)
        self._command(0x88 | self.brightness)

    def read_buttons(self):
        self.stb.value(0)
        self._write_byte(0x42)
        self.dio.init(Pin.IN, Pin.PULL_UP)
        keys = 0
        for i in range(4):
            for j in range(8):
                self.clk.value(0)
                time.sleep_us(2)
                if self.dio.value() == 1:
                    keys |= (1 << (i * 8 + j))
                self.clk.value(1)
                time.sleep_us(2)
        self.dio.init(Pin.OUT)
        self.stb.value(1)
        return keys


# ---------- DISPOSITIVOS ----------
uart = UART(2, baudrate=115200, tx=UART_TX, rx=UART_RX)
tm = TM1638(TM_STB, TM_CLK, TM_DIO)

# Bit de cada boton en el numero que devuelve read_buttons()
# S1, S2, S3, S4 y S5 medidos en el modulo. S6 a S8 sin medir (no se usan).
MASCARA_BOTON = [1 << 0, 1 << 8, 1 << 16, 1 << 24,
                 1 << 4, 1 << 12, 1 << 20, 1 << 28]   # S1 ... S8

ESTADOS = {"IDLE": 0, "ACCEL": 1, "STEADY": 2, "FALLA": 3, "EMERGENCIA": 4}
TEXTO_TM = {"IDLE": "IDLE", "ACCEL": "ACCEL", "STEADY": "STEADY",
            "FALLA": "FALLA", "EMERGENCIA": "EMERGENCIA"}
BOTONES = {MASCARA_BOTON[0]: b"G",    # S1 = arrancar
           MASCARA_BOTON[1]: b"E",    # S2 = emergencia
           MASCARA_BOTON[2]: b"R"}    # S3 = reiniciar


# ---------- MODO PRUEBA SOLO TM1638 ----------
if SOLO_PRUEBA_TM:
    tm.display_text("HOLA")
    tm.set_leds(0b00000001)
    anterior = 0
    while True:
        teclas = tm.read_buttons()
        nuevas = teclas & ~anterior      # solo botones recien apretados
        anterior = teclas
        if nuevas:
            print("Mascara cruda:", hex(nuevas))
            for n, m in enumerate(MASCARA_BOTON):
                if nuevas & m:
                    print("Boton S%d" % (n + 1))
                    tm.display_text("S%d" % (n + 1))
                    tm.set_leds(1 << n)
        time.sleep_ms(50)


# ---------- CRC-16/CCITT (igual que en la Blue Pill) ----------
def crc16(datos):
    crc = 0xFFFF
    for b in datos:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


# ---------- TM1638: display y LEDs ----------
texto_actual = ""
scroll_pos = 0
t_scroll = time.ticks_ms()
pantalla = 1              # 0 = nombre del estado, 1 = RPM (normal), 2 = mensaje fijo
t_pantalla = time.ticks_ms()
estado_actual = None
rpm_actual = 0


def refrescar_texto():
    """Muestra el texto. Si tiene mas de 8 letras, lo desplaza."""
    if len(texto_actual) <= 8:
        tm.display_text(texto_actual)
    else:
        base = texto_actual + "   "
        ventana = (base + base)[scroll_pos:scroll_pos + 8]
        tm.display_text(ventana)


def mostrar_rpm():
    tm.display_text("RPM%5d" % rpm_actual)


def mostrar_estado_un_rato():
    """Al tocar un boton: muestra el estado unos segundos."""
    global pantalla, t_pantalla, scroll_pos
    if estado_actual is None:
        return
    pantalla = 0
    t_pantalla = time.ticks_ms()
    scroll_pos = 0
    refrescar_texto()


def actualizar_pantalla():
    """Cuando termina el tiempo del estado, vuelve a las RPM."""
    global pantalla, scroll_pos, t_scroll
    if pantalla != 0:
        return
    ahora = time.ticks_ms()
    duracion = 6000 if len(texto_actual) > 8 else 4000
    if time.ticks_diff(ahora, t_pantalla) > duracion:
        pantalla = 1
        mostrar_rpm()
        return
    if len(texto_actual) > 8 and time.ticks_diff(ahora, t_scroll) > 400:
        t_scroll = ahora
        scroll_pos = (scroll_pos + 1) % (len(texto_actual) + 3)
        refrescar_texto()


def mostrar_tm(estado, rpm):
    global texto_actual, scroll_pos, pantalla, estado_actual, rpm_actual
    rpm_actual = rpm
    if estado != estado_actual:
        # estado nuevo: prende su LED
        estado_actual = estado
        texto_actual = TEXTO_TM.get(estado, str(estado))
        scroll_pos = 0
        n = ESTADOS.get(estado)
        tm.set_leds(1 << n if n is not None else 0)
        if pantalla == 0:
            refrescar_texto()     # si se esta viendo el estado, lo actualiza
    if pantalla == 2:
        pantalla = 1              # volvieron los datos
    if pantalla == 1:
        mostrar_rpm()


# ---------- BOTONES: envia G / E / R ----------
teclas_prev = 0


def revisar_botones():
    global teclas_prev
    teclas = tm.read_buttons()
    nuevas = teclas & ~teclas_prev
    teclas_prev = teclas
    for mascara, letra in BOTONES.items():
        if nuevas & mascara:
            uart.write(letra)
            print("Comando enviado:", letra)
            mostrar_estado_un_rato()


# ---------- PROCESAR UNA LINEA ----------
def procesar(linea):
    """Devuelve (datos, crc_ok). datos es None si la trama no sirve."""
    linea = linea.strip()           # quita \r y espacios
    marca = b',"crc":"'
    i = linea.rfind(marca)
    if i < 0:
        return None, False

    cuerpo = linea[:i]
    try:
        crc_rx = int(linea[i + len(marca):i + len(marca) + 4], 16)
    except ValueError:
        return None, False

    crc_ok = (crc16(cuerpo) == crc_rx)
    try:
        datos = json.loads(linea.decode())
    except Exception:
        return None, False
    return datos, crc_ok


# ---------- PRINCIPAL ----------
tm.display_text("")
buf = b""
t_ultimo = time.ticks_ms()   # ultima trama VALIDA (CRC OK)

while True:
    revisar_botones()

    if uart.any():
        nuevo = uart.read()
        if nuevo:
            buf += nuevo
            if len(buf) > 600:      # evita que el buffer crezca sin fin
                buf = buf[-300:]

    while b"\n" in buf:
        linea, buf = buf.split(b"\n", 1)
        datos, crc_ok = procesar(linea)

        if datos is None:
            print("Trama invalida:", linea)
            continue

        if not crc_ok:
            print("CRC ERROR, trama descartada:", datos)
            continue

        estado = datos.get("fsm_state")
        if estado is None:
            print("Falta fsm_state:", datos)
            continue

        t_ultimo = time.ticks_ms()
        print(datos, "CRC OK")
        try:
            rpm = int(datos.get("rpm", 0))
        except Exception:
            rpm = 0
        mostrar_tm(estado, rpm)

    actualizar_pantalla()

    # si pasan 2 s sin tramas validas, avisa
    if time.ticks_diff(time.ticks_ms(), t_ultimo) > 2000:
        texto_actual = "SIN DATO"
        pantalla = 2            # mensaje fijo, no alterna
        estado_actual = None
        tm.display_text(texto_actual)
        tm.set_leds(0)
        print("Sin datos validos: revisa TX/RX y el GND")
        t_ultimo = time.ticks_ms()

    time.sleep_ms(10)