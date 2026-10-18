"""Carga del firmware en el Arduino UNO sin avrdude.

Habla STK500v1 con el bootloader optiboot que traen los UNO (y casi todos
los clones CH340) a 115200 baud. Algunos clones viejos usan el bootloader
ATmegaBOOT a 57600: se prueba esa velocidad si la primera no responde.

El .hex del firmware viaja dentro del paquete (planta/firmware/), así una
PC sin PlatformIO puede dejar lista una placa nueva.
"""
from __future__ import annotations

import re
import time
from importlib import resources
from pathlib import Path
from typing import Callable

STK_OK = 0x10
STK_INSYNC = 0x14
CRC_EOP = 0x20
SIGNATURE_328P = bytes([0x1E, 0x95, 0x0F])
PAGE = 128             # bytes por página de flash del ATmega328P
FLASH_APP = 32256      # 32 KB menos 512 B de optiboot
BAUDS = (115200, 57600)


class FlashError(RuntimeError):
    pass


def bundled_hex() -> tuple[Path, str]:
    """Ruta y versión del firmware empaquetado (planta-fw-<versión>.hex)."""
    files = sorted(resources.files("planta").joinpath("firmware").iterdir(),
                   key=lambda p: p.name)
    for f in files:
        m = re.fullmatch(r"planta-fw-(.+)\.hex", f.name)
        if m:
            return Path(str(f)), m.group(1)
    raise FlashError("no hay firmware empaquetado")


def parse_hex(text: str) -> bytes:
    """Intel HEX -> imagen binaria desde la dirección 0 (huecos en 0xFF)."""
    mem: dict[int, int] = {}
    base = 0
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise FlashError(f"línea {n}: no empieza con ':'")
        raw = bytes.fromhex(line[1:])
        if len(raw) < 5 or len(raw) != raw[0] + 5:
            raise FlashError(f"línea {n}: largo inválido")
        if sum(raw) & 0xFF:
            raise FlashError(f"línea {n}: checksum inválido")
        count, addr, rtype, data = raw[0], (raw[1] << 8) | raw[2], raw[3], raw[4:-1]
        if rtype == 0x00:
            for i, b in enumerate(data):
                mem[base + addr + i] = b
        elif rtype == 0x01:
            break
        elif rtype == 0x02:
            base = ((data[0] << 8) | data[1]) << 4
        elif rtype == 0x04:
            base = ((data[0] << 8) | data[1]) << 16
        # 0x03 y 0x05 (dirección de arranque) no aplican al AVR
        del count
    if not mem:
        return b""
    size = max(mem) + 1
    if size > FLASH_APP:
        raise FlashError(f"el firmware ocupa {size} B y entran {FLASH_APP}")
    img = bytearray(b"\xff" * size)
    for a, b in mem.items():
        img[a] = b
    return bytes(img)


class _Stk:
    def __init__(self, ser):
        self.ser = ser

    def cmd(self, payload: bytes, n_resp: int = 0) -> bytes:
        self.ser.write(payload + bytes([CRC_EOP]))
        r = self.ser.read(n_resp + 2)
        if len(r) != n_resp + 2 or r[0] != STK_INSYNC or r[-1] != STK_OK:
            raise FlashError(f"respuesta inesperada del bootloader: {r.hex() or 'nada'}")
        return r[1:-1]

    def sync(self, tries: int = 10) -> bool:
        for _ in range(tries):
            self.ser.reset_input_buffer()
            self.ser.write(bytes([0x30, CRC_EOP]))
            r = self.ser.read(2)
            if r == bytes([STK_INSYNC, STK_OK]):
                return True
        return False


def _reset(ser) -> None:
    # Bajar DTR/RTS resetea el UNO por el capacitor de auto-reset.
    ser.dtr = False
    ser.rts = False
    time.sleep(0.25)
    ser.dtr = True
    ser.rts = True
    time.sleep(0.05)
    ser.reset_input_buffer()


def flash(port: str, image: bytes, progress: Callable[[str, float], None] | None = None,
          serial_factory=None) -> None:
    """Graba y verifica `image` en el UNO conectado a `port`."""
    if serial_factory is None:
        import serial
        serial_factory = lambda baud: serial.Serial(port, baud, timeout=0.3)  # noqa: E731
    report = progress or (lambda stage, frac: None)

    ser = None
    for baud in BAUDS:
        ser = serial_factory(baud)
        _reset(ser)
        stk = _Stk(ser)
        if stk.sync():
            break
        ser.close()
        ser = None
    if ser is None:
        raise FlashError("el bootloader no responde: ¿es un UNO? ¿el puerto está "
                         "abierto en otro programa?")
    try:
        sig = stk.cmd(bytes([0x75]), 3)
        if sig != SIGNATURE_328P:
            raise FlashError(f"firma {sig.hex()}: no es un ATmega328P (UNO)")
        stk.cmd(bytes([0x50]))  # entrar en modo programación
        pages = [image[i:i + PAGE] for i in range(0, len(image), PAGE)]
        for k, page in enumerate(pages):
            page = page.ljust(PAGE, b"\xff")
            word = (k * PAGE) >> 1
            stk.cmd(bytes([0x55, word & 0xFF, word >> 8]))
            stk.cmd(bytes([0x64, 0, PAGE, ord("F")]) + page)
            report("grabando", (k + 1) / len(pages))
        for k, page in enumerate(pages):
            word = (k * PAGE) >> 1
            stk.cmd(bytes([0x55, word & 0xFF, word >> 8]))
            got = stk.cmd(bytes([0x74, 0, PAGE, ord("F")]), PAGE)
            if got[:len(page)] != page:
                raise FlashError(f"la verificación falló en la página {k}")
            report("verificando", (k + 1) / len(pages))
        stk.cmd(bytes([0x51]))  # salir: optiboot arranca el firmware nuevo
    finally:
        ser.close()


def flash_bundled(port: str, progress: Callable[[str, float], None] | None = None) -> str:
    path, version = bundled_hex()
    flash(port, parse_hex(path.read_text()), progress)
    return version
