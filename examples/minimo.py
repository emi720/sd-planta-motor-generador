"""Cliente mínimo del firmware: aplica una senoidal, graba 5 s y guarda un CSV.

Solo necesita pyserial (pip install pyserial). No usa nada del paquete
`planta`: es la base para escribir tu propio host en el lenguaje que quieras.
El protocolo completo está en docs/PROTOCOLO.md.

Uso: python minimo.py /dev/ttyUSB0      (en Windows: python minimo.py COM3)
"""
import struct
import sys
import time

import serial

PUERTO = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
DURACION_S = 5.0
VREF, DIVISOR = 5.0, 2.0  # V_L = DIVISOR * adc * VREF / 1023


def crc8(data: bytes) -> int:
    """CRC-8/MAXIM: polinomio 0x31 reflejado (0x8C), valor inicial 0."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


def comando(s: serial.Serial, linea: str) -> str:
    """Manda una línea y espera la respuesta OK/ERR (ignora los EVT)."""
    s.write((linea + "\n").encode())
    while True:
        r = s.readline().decode(errors="replace").strip()
        if r.startswith(("OK", "ERR")):
            print(f"> {linea:24} {r}")
            return r
        if not r:
            raise TimeoutError(f"sin respuesta a {linea}")


s = serial.Serial(PUERTO, 250000, timeout=1.0)
time.sleep(2.0)            # abrir el puerto resetea el UNO: esperar al bootloader
s.reset_input_buffer()

status = comando(s, "STATUS")
campos = dict(t.split("=", 1) for t in status.split(" wave=")[0].split() if "=" in t)
rate, pasos = float(campos["rate"]), int(campos["steps"])

comando(s, "SIN 1 60 25")  # 1 Hz, offset 60 %, amplitud 25 %: duty entre 35 y 85 %
comando(s, "START")         # a partir de acá llegan tramas binarias de 11 bytes

muestras = []               # (idx, duty_cuentas, adc)
buf = bytearray()
t_fin = time.monotonic() + DURACION_S
t_keepalive = 0.0
while time.monotonic() < t_fin:
    if time.monotonic() - t_keepalive > 0.5:
        s.write(b"\n")      # línea vacía: mantiene vivo el watchdog sin respuesta
        t_keepalive = time.monotonic()
    buf += s.read(s.in_waiting or 1)
    while True:
        i = buf.find(b"\xa5\x5a")            # buscar el sync
        if i < 0 or len(buf) - i < 11:
            break
        trama = buf[i:i + 11]
        if crc8(trama[2:10]) == trama[10]:
            muestras.append(struct.unpack("<IHH", trama[2:10]))
            del buf[:i + 11]
        else:
            del buf[:i + 1]                   # sync falso: correrse un byte

s.write(b"STOP\n")          # duty 0 y fin del streaming
time.sleep(0.2)
s.close()

with open("minimo.csv", "w") as f:
    f.write("t_s,duty_pct,VL_V\n")
    for idx, duty, adc in muestras:
        f.write(f"{idx / rate:.4f},{100 * duty / pasos:.3f},{DIVISOR * adc * VREF / 1023:.4f}\n")
huecos = muestras[-1][0] + 1 - len(muestras) if muestras else 0
print(f"{len(muestras)} muestras a {rate:g} Hz ({huecos} perdidas) -> minimo.csv")
