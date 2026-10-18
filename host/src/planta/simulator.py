"""Dispositivo falso: emula el firmware y la planta para trabajar sin hardware.

Implementa la misma interfaz que serial.Serial que usa Device (read, write,
close) y habla exactamente el mismo protocolo, tramas binarias incluidas.
El tiempo avanza con el reloj real en cada read/write, así que la GUI y la
CLI lo ven como un Arduino de verdad. Se abre con el puerto "sim://".

Modelo de la planta (ajustado al barrido de la planta real a PWM 1 kHz,
carga abierta):
  - Zona muerta con histéresis: arranca con duty >= 48 % y se detiene
    con duty < 22 %.
  - Régimen: V_L = Vmax * (1 - exp(-(d - d0) / ds)) para d > d0.
  - Dinámica de primer orden (tau mecánica) y filtro RC de 5 ms en A0.
  - Cuantización de 10 bits y ruido gaussiano en cuentas.
"""
from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass

from .protocol import (DUTY_MAX_PCT, PWM_HZ_MAX, PWM_HZ_MIN, RATE_MAX_HZ,
                       RATE_MIN_HZ, WDT_MAX_S, Wave, encode_frame)

SIM_VERSION = "0.2.0-sim"
F_CPU = 16_000_000
T2_PRESC = (1, 8, 32, 64, 128, 256, 1024)


@dataclass
class PlantParams:
    start_pct: float = 48.0
    stop_pct: float = 22.0
    d0_pct: float = 21.0
    ds_pct: float = 41.4
    vmax: float = 8.2
    tau_m: float = 0.25    # s
    tau_rc: float = 0.005  # s, filtro de A0
    vref: float = 5.0
    div: float = 2.0
    noise_lsb: float = 0.6


class Plant:
    def __init__(self, p: PlantParams | None = None, seed: int | None = None):
        self.p = p or PlantParams()
        self.running = False
        self.v = 0.0     # V_L "mecánica"
        self.v_a0 = 0.0  # tensión en A0 tras el RC
        self.rng = random.Random(seed)

    def v_ss(self, duty_pct: float) -> float:
        p = self.p
        if not self.running or duty_pct <= p.d0_pct:
            return 0.0
        return p.vmax * (1 - math.exp(-(duty_pct - p.d0_pct) / p.ds_pct))

    def step(self, duty_pct: float, dt: float) -> int:
        p = self.p
        if not self.running and duty_pct >= p.start_pct:
            self.running = True
        elif self.running and duty_pct < p.stop_pct:
            self.running = False
        self.v += (self.v_ss(duty_pct) - self.v) * (1 - math.exp(-dt / p.tau_m))
        self.v_a0 += (self.v / p.div - self.v_a0) * (1 - math.exp(-dt / p.tau_rc))
        raw = self.v_a0 / p.vref * 1023 + self.rng.gauss(0, p.noise_lsb)
        return min(1023, max(0, round(raw)))


class SimSerial:
    """Firmware emulado. Mismos comandos, respuestas y tramas que el real."""

    def __init__(self, plant: Plant | None = None, clock=time.monotonic,
                 timeout: float = 0.05):
        self.plant = plant or Plant()
        self.clock = clock
        self.timeout = timeout
        self.out = bytearray()
        self.inbuf = bytearray()
        self.rate = 500.0
        self.pwm_steps = F_CPU // 1000
        self.wave = Wave.dc(0)
        self.phase = 0.0
        self.streaming = False
        self.idx = 0
        self.lost = 0
        self.wdt_s = 2.0
        self.duty = 0
        self.t_last = self.clock()
        self.t_rx = self.t_last
        self.tick_acc = 0.0
        self.is_open = True
        # Device lee desde un hilo y escribe desde otro.
        self._lock = threading.RLock()
        self._emit(f"EVT READY planta-fw {SIM_VERSION}")

    # ---- interfaz tipo serial.Serial ----

    def write(self, data: bytes) -> int:
        with self._lock:
            self._advance()
            self.inbuf += data
            while b"\n" in self.inbuf:
                line, _, rest = self.inbuf.partition(b"\n")
                self.inbuf = bytearray(rest)
                self.t_rx = self.clock()
                self._handle(line.decode("ascii", "replace").strip())
        return len(data)

    def read(self, n: int = 1) -> bytes:
        t_end = time.monotonic() + self.timeout
        while True:
            with self._lock:
                self._advance()
                if self.out or time.monotonic() >= t_end:
                    data = bytes(self.out[:n])
                    del self.out[:n]
                    return data
            time.sleep(0.002)

    def reset_input_buffer(self) -> None:
        with self._lock:
            self.out.clear()

    def close(self) -> None:
        self.is_open = False

    # ---- firmware ----

    def _emit(self, text: str) -> None:
        self.out += text.encode("ascii") + b"\n"

    def _counts(self, pct: float) -> int:
        return int(pct * self.pwm_steps / 100 + 0.5)

    def _u_pct(self) -> float:
        w = self.wave
        if w.kind == "sq":
            return w.high if self.phase < w.duty / 100 else w.low
        if w.kind == "sin":
            return w.offset + w.amp * math.sin(2 * math.pi * self.phase)
        return w.pct

    def _advance(self) -> None:
        now = self.clock()
        self.tick_acc += (now - self.t_last) * self.rate
        self.t_last = now
        n = int(self.tick_acc)
        self.tick_acc -= n
        n = min(n, int(5 * self.rate))  # no simular más de 5 s de atraso
        dt = 1 / self.rate
        for _ in range(n):
            self.duty = min(self._counts(max(self._u_pct(), 0.0)), self.pwm_steps)
            self.phase = (self.phase + self.wave.f / self.rate) % 1.0
            adc = self.plant.step(self.duty / self.pwm_steps * 100, dt)
            if self.streaming:
                self.out += encode_frame(self.idx, self.duty, adc)
                self.idx += 1
        if self.streaming and self.wdt_s > 0 and now - self.t_rx > self.wdt_s:
            self._stop_all()
            self._emit("EVT WDT sin comandos, duty=0 y streaming cortado")

    def _stop_all(self) -> None:
        self.streaming = False
        self.wave = Wave.dc(0)
        self.phase = 0.0

    def _set_rate(self, hz: float) -> float:
        for k in T2_PRESC:
            ocr = round(F_CPU / (k * hz)) - 1
            if ocr <= 255:
                if ocr < 1:
                    return 0.0
                return F_CPU / (k * (ocr + 1))
        return 0.0

    def _wave_text(self) -> str:
        w = self.wave
        if w.kind == "sq":
            return f"SQ f={w.f:.3f} duty={w.duty:.2f} low={w.low:.2f} high={w.high:.2f}"
        if w.kind == "sin":
            return f"SIN f={w.f:.3f} offset={w.offset:.2f} amp={w.amp:.2f}"
        return f"DC pct={w.pct:.2f}"

    def _handle(self, line: str) -> None:
        tok = line.split()
        if not tok:
            return  # keepalive
        cmd, args = tok[0], tok[1:]
        try:
            vals = [float(a) for a in args]
        except ValueError:
            self._emit(f"ERR argumentos invalidos para {cmd}")
            return

        def need(n: int, usage: str) -> bool:
            if len(vals) != n:
                self._emit(f"ERR uso: {usage}")
                return False
            return True

        if cmd == "PING":
            if need(0, "PING"):
                self._emit(f"OK PONG planta-fw {SIM_VERSION}")
        elif cmd == "STATUS":
            if need(0, "STATUS"):
                self._emit(
                    f"OK STATUS fw={SIM_VERSION} rate={self.rate:.3f} "
                    f"steps={self.pwm_steps} pwm_hz={F_CPU / self.pwm_steps:.1f} "
                    f"duty_max={DUTY_MAX_PCT:.2f} duty={self.duty} "
                    f"streaming={int(self.streaming)} lost={self.lost} "
                    f"wdt={self.wdt_s:.1f} wave={self._wave_text()}")
        elif cmd == "RATE":
            if not need(1, "RATE <Hz>"):
                return
            if self.streaming:
                self._emit("ERR RATE no se puede cambiar durante el streaming")
                return
            actual = self._set_rate(vals[0]) if RATE_MIN_HZ <= vals[0] <= RATE_MAX_HZ else 0
            if not actual:
                self._emit("ERR RATE fuera de rango (62..1000 Hz)")
                return
            self.rate = actual
            if self.wave.kind != "dc" and not 0 < self.wave.f <= self.rate / 2:
                self._stop_all()
                self._emit("EVT onda anulada por Nyquist, duty=0")
            self._emit(f"OK RATE {self.rate:.3f}")
        elif cmd == "PWMF":
            if not need(1, "PWMF <Hz>"):
                return
            if self.streaming:
                self._emit("ERR PWMF no se puede cambiar durante el streaming")
                return
            if not PWM_HZ_MIN <= vals[0] <= PWM_HZ_MAX:
                self._emit("ERR PWMF fuera de rango (250..20000 Hz)")
                return
            hz = int(vals[0] + 0.5)
            self.pwm_steps = (F_CPU + hz // 2) // hz
            self._emit(f"OK PWMF {F_CPU / self.pwm_steps:.1f} steps={self.pwm_steps}")
        elif cmd in ("DC", "SQ", "SIN"):
            n = {"DC": 1, "SQ": 4, "SIN": 3}[cmd]
            if not need(n, f"{cmd} ..."):
                return
            w = {"DC": lambda v: Wave.dc(*v), "SQ": lambda v: Wave.sq(*v),
                 "SIN": lambda v: Wave.sin(*v)}[cmd](vals)
            try:
                w.validate(self.rate)
            except ValueError as e:
                self._emit(f"ERR {e}")
                return
            self.wave = w
            self.phase = 0.0
            self._emit(f"OK {self._wave_text()}")
        elif cmd == "START":
            if need(0, "START"):
                self.streaming = True
                self.idx = 0
                self.lost = 0
                self._emit("OK START")
        elif cmd == "STOP":
            if need(0, "STOP"):
                self._stop_all()
                self._emit("OK STOP")
        elif cmd == "WDT":
            if not need(1, "WDT <s>"):
                return
            if not 0 <= vals[0] <= WDT_MAX_S:
                self._emit("ERR WDT fuera de rango (0..60 s, 0 = desactivado)")
                return
            self.wdt_s = vals[0]
            self._emit(f"OK WDT {self.wdt_s:.1f}")
        else:
            self._emit("ERR comando desconocido")
