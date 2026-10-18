"""Conexión con el firmware (real por puerto serie o simulado con "sim://")."""
from __future__ import annotations

import queue
import threading
import time
from collections import deque

from .protocol import (BAUD, Frame, Line, StreamParser, Wave, cmd_pwmf, cmd_rate,
                       cmd_wdt, parse_kv)

SIM_PREFIX = "sim://"
KEEPALIVE_S = 0.5


class DeviceError(RuntimeError):
    """El firmware respondió ERR o no respondió."""


def open_transport(port: str):
    if port.startswith(SIM_PREFIX):
        from .simulator import SimSerial
        return SimSerial()
    import serial
    return serial.Serial(port, BAUD, timeout=0.05)


def port_is_candidate(device: str, platform: str | None = None) -> bool:
    """¿Puede ser un Arduino? Descarta los puertos que nunca lo son."""
    import sys
    platform = platform or sys.platform
    if platform.startswith("linux"):
        return "ttyUSB" in device or "ttyACM" in device  # no los ttyS* de la placa madre
    if platform == "darwin":
        # En macOS cada puerto aparece como tty.* y cu.*: se usa cu.* (no espera DCD).
        return device.startswith("/dev/cu.") and "Bluetooth" not in device
    return True  # Windows: COMn


def list_ports() -> list[str]:
    from serial.tools import list_ports as lp
    ports = sorted(p.device for p in lp.comports() if port_is_candidate(p.device))
    return ports + [SIM_PREFIX]


class Device:
    """Hilo lector + comandos sincrónicos + keepalive para el watchdog.

    Las muestras se acumulan en una cola que se vacía con pop_frames(). Los
    EVT del firmware (READY, WDT) quedan en `events`.
    """

    def __init__(self, port: str, transport=None, ready_timeout: float = 3.0):
        self.port = port
        self.ser = transport if transport is not None else open_transport(port)
        self.parser = StreamParser()
        self._frames: deque[Frame] = deque()
        self._lines: queue.Queue[str] = queue.Queue()
        self.events: deque[str] = deque(maxlen=200)
        self._wlock = threading.Lock()
        self._cmd_lock = threading.Lock()
        self._alive = threading.Event()
        self._alive.set()
        self._ready = threading.Event()
        self._rx = threading.Thread(target=self._rx_loop, name="planta-rx", daemon=True)
        self._ka = threading.Thread(target=self._ka_loop, name="planta-ka", daemon=True)
        self._rx.start()
        self.info: dict[str, str] = {}
        try:
            # Abrir el puerto resetea el UNO (DTR): esperar al bootloader.
            if not self._ready.wait(ready_timeout):
                self.command("PING", timeout=1.0)
            self._ka.start()
            self.command("STOP")
            self.status()
        except Exception:
            self.close()
            raise

    # ---- hilos ----

    def _rx_loop(self) -> None:
        while self._alive.is_set():
            try:
                data = self.ser.read(4096)
            except Exception as e:  # puerto desenchufado
                self.events.append(f"EVT ERROR lectura: {e}")
                break
            if not data:
                continue
            for item in self.parser.feed(data):
                if isinstance(item, Frame):
                    self._frames.append(item)
                elif item.kind == "EVT":
                    self.events.append(item.text)
                    if item.text.startswith("EVT READY"):
                        self._ready.set()
                else:
                    self._lines.put(item.text)

    def _ka_loop(self) -> None:
        # Línea vacía: reinicia el watchdog del firmware sin generar respuesta.
        while self._alive.is_set():
            time.sleep(KEEPALIVE_S)
            try:
                self.send("")
            except Exception:
                break

    # ---- comandos ----

    def send(self, line: str) -> None:
        with self._wlock:
            self.ser.write((line + "\n").encode("ascii"))

    def command(self, line: str, timeout: float = 1.0) -> str:
        """Manda un comando y devuelve la respuesta OK; DeviceError si es ERR."""
        with self._cmd_lock:
            while not self._lines.empty():
                self._lines.get_nowait()
            self.send(line)
            try:
                resp = self._lines.get(timeout=timeout)
            except queue.Empty:
                raise DeviceError(f"sin respuesta a {line!r}") from None
        if resp.startswith("ERR"):
            raise DeviceError(f"{line}: {resp[4:]}")
        return resp

    def status(self) -> dict[str, str]:
        self.info = parse_kv(self.command("STATUS"))
        return self.info

    @property
    def rate(self) -> float:
        return float(self.info["rate"])

    @property
    def steps(self) -> int:
        return int(self.info["steps"])

    @property
    def pwm_hz(self) -> float:
        return float(self.info["pwm_hz"])

    @property
    def fw(self) -> str:
        return self.info.get("fw", "?")

    def set_rate(self, hz: float) -> float:
        self.command(cmd_rate(hz))
        return float(self.status()["rate"])

    def set_pwm(self, hz: float) -> float:
        self.command(cmd_pwmf(hz))
        return float(self.status()["pwm_hz"])

    def set_wdt(self, s: float) -> None:
        self.command(cmd_wdt(s))
        self.status()

    def set_wave(self, wave: Wave) -> str:
        wave.validate(self.rate, float(self.info.get("duty_max", 100)))
        return self.command(wave.command())

    def start(self) -> None:
        self._frames.clear()
        self.command("START")

    def stop(self) -> None:
        self.command("STOP")

    def pop_frames(self) -> list[Frame]:
        out = []
        while self._frames:
            out.append(self._frames.popleft())
        return out

    def close(self) -> None:
        if self._alive.is_set():
            try:
                with self._cmd_lock:
                    self.send("STOP")
            except Exception:
                pass
        self._alive.clear()
        self._rx.join(timeout=1.0)
        try:
            self.ser.close()
        except Exception:
            pass

    def __enter__(self) -> Device:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
