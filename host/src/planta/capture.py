"""Captura headless: aplica una onda, graba entrada y salida, y deja duty 0."""
from __future__ import annotations

import time
from typing import Callable

import numpy as np

from .device import Device, DeviceError
from .protocol import Frame, Wave
from .recording import Recording


def capture(dev: Device, wave: Wave, duration: float, pre: float = 0.5,
            on_progress: Callable[[float], None] | None = None) -> Recording:
    """Graba al menos `pre` s en duty 0 (línea de base), aplica la onda y graba
    `duration` s más.

    La onda se aplica cuando el host ya recibió las muestras de `pre`, así que
    la base real dura algunas decenas de ms más. El instante exacto queda en
    rec.onset_idx: la primera muestra con el duty de la onda (o, si la onda
    arranca en 0, la última recibida al aplicarla).

    El tiempo sale del índice de muestra (t = idx / rate), no del reloj de la
    PC. Siempre termina con STOP, aunque haya un error o un Ctrl+C.
    """
    wave.validate(dev.rate, float(dev.info.get("duty_max", 100)))
    frames: list[Frame] = []
    n_pre = round(pre * dev.rate)
    n_dur = round(duration * dev.rate)
    n_total = None
    apply_idx = 0
    n_events = len(dev.events)
    dev.stop()
    try:
        dev.start()
        if n_pre == 0:
            dev.set_wave(wave)
            n_total = n_dur
        t_last = 0.0
        while True:
            frames += dev.pop_frames()
            got = frames[-1].idx + 1 if frames else 0
            if n_total is None and got >= n_pre:
                dev.set_wave(wave)
                apply_idx = got
                n_total = got + n_dur
            if n_total is not None and got >= n_total:
                break
            if on_progress and time.monotonic() - t_last > 0.5:
                t_last = time.monotonic()
                on_progress(got / dev.rate)
            new = list(dev.events)[n_events:]
            if any(e.startswith("EVT WDT") for e in new):
                raise DeviceError("el watchdog del firmware cortó la captura")
            time.sleep(0.02)
    finally:
        dev.stop()
    frames += dev.pop_frames()
    rec = Recording.from_frames([f for f in frames if f.idx < n_total], dev.rate, dev.steps)
    nz = np.flatnonzero((rec.idx >= apply_idx) & (rec.duty != 0))
    rec.onset_idx = int(rec.idx[nz[0]]) if len(nz) else apply_idx
    return rec
