"""Protocolo serie con el firmware: tramas binarias, respuestas de texto y comandos.

Trama de muestra (11 bytes, little-endian):
    A5 5A | idx u32 | duty u16 (cuentas) | adc u16 | CRC-8/MAXIM de idx..adc

Las líneas de texto empiezan con OK, ERR o EVT y terminan en '\\n'. Nunca
contienen 0xA5, así que se separan de las tramas por el byte de sync.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

BAUD = 250000
SYNC = b"\xa5\x5a"
FRAME_LEN = 11
_PAYLOAD = struct.Struct("<IHH")
MAX_LINE = 160
LINE_PREFIXES = ("OK", "ERR", "EVT")

# Límites del firmware (firmware/src/config.h y pwm.h).
DUTY_MAX_PCT = 100.0
RATE_MIN_HZ = 62.0
RATE_MAX_HZ = 1000.0
PWM_HZ_MIN = 250.0
PWM_HZ_MAX = 20000.0
WDT_MAX_S = 60.0


def crc8(data: bytes) -> int:
    """CRC-8/MAXIM (polinomio 0x31 reflejado, valor inicial 0)."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8C if crc & 1 else crc >> 1
    return crc


@dataclass(frozen=True)
class Frame:
    idx: int
    duty: int  # cuentas de PWM
    adc: int


@dataclass(frozen=True)
class Line:
    text: str

    @property
    def kind(self) -> str:
        return self.text.split(" ", 1)[0]


def encode_frame(idx: int, duty: int, adc: int) -> bytes:
    payload = _PAYLOAD.pack(idx & 0xFFFFFFFF, duty, adc)
    return SYNC + payload + bytes([crc8(payload)])


@dataclass
class StreamParser:
    """Parser incremental: se le pasan bytes y devuelve tramas y líneas.

    Tolera arrancar a mitad de una trama y bytes corruptos: lo que no es una
    trama con CRC válido ni una línea OK/ERR/EVT se descarta y se cuenta.
    """

    buf: bytearray = field(default_factory=bytearray)
    crc_errors: int = 0
    junk_bytes: int = 0

    def feed(self, data: bytes) -> list[Frame | Line]:
        self.buf += data
        out: list[Frame | Line] = []
        b = self.buf
        i = 0
        n = len(b)
        while i < n:
            if b[i] == 0xA5:
                if n - i < 2:
                    break
                if b[i + 1] != 0x5A:
                    i += 1
                    self.junk_bytes += 1
                    continue
                if n - i < FRAME_LEN:
                    break
                payload = bytes(b[i + 2:i + 10])
                if crc8(payload) == b[i + 10]:
                    out.append(Frame(*_PAYLOAD.unpack(payload)))
                    i += FRAME_LEN
                else:
                    self.crc_errors += 1
                    i += 1
                continue

            # Posible línea de texto: bytes imprimibles hasta '\n'.
            j = i
            while j < n and (0x20 <= b[j] <= 0x7E or b[j] == 0x0D):
                j += 1
            if j == n:
                if j - i > MAX_LINE:
                    self.junk_bytes += j - i
                    i = j
                break  # esperar más bytes
            if b[j] == 0x0A and j > i:
                text = bytes(b[i:j]).decode("ascii").rstrip("\r")
                if text.startswith(LINE_PREFIXES):
                    out.append(Line(text))
                else:
                    self.junk_bytes += j + 1 - i
                i = j + 1
            else:
                # Corte por un byte no imprimible (o '\n' suelto): lo de antes
                # no era una línea. Ese byte se reprocesa en la próxima vuelta
                # por si es el sync de una trama.
                skip = j - i if j > i else 1
                self.junk_bytes += skip
                i += skip
        del b[:i]
        return out


# ---- Respuestas -----------------------------------------------------------

def parse_kv(text: str) -> dict[str, str]:
    """Extrae los pares clave=valor de una respuesta.

    En STATUS, todo lo que sigue a "wave=" es la descripción de la onda
    (tiene espacios), así que se guarda entero.
    """
    out: dict[str, str] = {}
    head, sep, wave = text.partition(" wave=")
    for tok in head.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    if sep:
        out["wave"] = wave
    return out


# ---- Comandos -------------------------------------------------------------

def fmt(x: float) -> str:
    """Número compacto y exacto para el firmware (strtod)."""
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _check_duty(name: str, pct: float, duty_max: float) -> None:
    if not 0 <= pct <= duty_max:
        raise ValueError(f"{name} = {pct} % fuera de rango (0..{duty_max:g} %)")


def _check_freq(f: float, rate: float) -> None:
    if not 0 < f <= rate / 2:
        raise ValueError(f"frecuencia {f} Hz fuera de rango (0..{rate / 2:g} Hz, Nyquist)")


@dataclass(frozen=True)
class Wave:
    """Forma de onda de entrada, en % de duty."""

    kind: str  # "dc", "sq" o "sin"
    f: float = 0.0
    pct: float = 0.0      # DC
    duty: float = 50.0    # SQ: ciclo de trabajo
    low: float = 0.0      # SQ
    high: float = 0.0     # SQ
    offset: float = 0.0   # SIN
    amp: float = 0.0      # SIN

    @staticmethod
    def dc(pct: float) -> Wave:
        return Wave("dc", pct=pct)

    @staticmethod
    def sq(f: float, duty: float, low: float, high: float) -> Wave:
        return Wave("sq", f=f, duty=duty, low=low, high=high)

    @staticmethod
    def sin(f: float, offset: float, amp: float) -> Wave:
        return Wave("sin", f=f, offset=offset, amp=amp)

    def validate(self, rate: float = 500.0, duty_max: float = DUTY_MAX_PCT) -> None:
        """Mismas reglas que el firmware; ValueError si no se cumplen."""
        if self.kind == "dc":
            _check_duty("DC", self.pct, duty_max)
        elif self.kind == "sq":
            _check_freq(self.f, rate)
            if not 0 <= self.duty <= 100:
                raise ValueError(f"ciclo de trabajo {self.duty} % fuera de rango (0..100 %)")
            _check_duty("nivel bajo", self.low, duty_max)
            _check_duty("nivel alto", self.high, duty_max)
        elif self.kind == "sin":
            _check_freq(self.f, rate)
            if self.amp < 0:
                raise ValueError("la amplitud no puede ser negativa")
            if self.offset - self.amp < 0:
                raise ValueError(f"offset - amp = {self.offset - self.amp:g} % < 0")
            if self.offset + self.amp > duty_max:
                raise ValueError(f"offset + amp = {self.offset + self.amp:g} % > {duty_max:g} %")
        else:
            raise ValueError(f"forma de onda desconocida: {self.kind!r}")

    def command(self) -> str:
        if self.kind == "dc":
            return f"DC {fmt(self.pct)}"
        if self.kind == "sq":
            return f"SQ {fmt(self.f)} {fmt(self.duty)} {fmt(self.low)} {fmt(self.high)}"
        if self.kind == "sin":
            return f"SIN {fmt(self.f)} {fmt(self.offset)} {fmt(self.amp)}"
        raise ValueError(f"forma de onda desconocida: {self.kind!r}")

    def describe(self) -> str:
        """Texto para la metadata del CSV."""
        if self.kind == "dc":
            return f"DC pct={self.pct:g}"
        if self.kind == "sq":
            return (f"SQ f={self.f:g}Hz duty={self.duty:g}% "
                    f"low={self.low:g}% high={self.high:g}%")
        return f"SIN f={self.f:g}Hz offset={self.offset:g}% amp={self.amp:g}%"


def cmd_rate(hz: float) -> str:
    if not RATE_MIN_HZ <= hz <= RATE_MAX_HZ:
        raise ValueError(f"rate {hz} Hz fuera de rango ({RATE_MIN_HZ:g}..{RATE_MAX_HZ:g} Hz)")
    return f"RATE {fmt(hz)}"


def cmd_pwmf(hz: float) -> str:
    if not PWM_HZ_MIN <= hz <= PWM_HZ_MAX:
        raise ValueError(f"PWM {hz} Hz fuera de rango ({PWM_HZ_MIN:g}..{PWM_HZ_MAX:g} Hz)")
    return f"PWMF {fmt(hz)}"


def cmd_wdt(s: float) -> str:
    if not 0 <= s <= WDT_MAX_S:
        raise ValueError(f"watchdog {s} s fuera de rango (0..{WDT_MAX_S:g} s)")
    return f"WDT {fmt(s)}"
