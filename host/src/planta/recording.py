"""Muestras capturadas y su escritura/lectura en CSV."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import __version__
from .calib import Calib
from .protocol import Frame

COLUMNS = ("t_s", "sample_idx", "duty_pct", "Vm_nom_V", "adc_raw", "VL_V")


@dataclass
class Recording:
    rate: float
    steps: int
    idx: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    duty: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    adc: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    onset_idx: int = 0  # primera muestra con la onda aplicada (ver capture)

    @classmethod
    def from_frames(cls, frames: list[Frame], rate: float, steps: int) -> Recording:
        a = np.array([(f.idx, f.duty, f.adc) for f in frames], dtype=np.int64).reshape(-1, 3)
        return cls(rate, steps, a[:, 0], a[:, 1], a[:, 2])

    def __len__(self) -> int:
        return len(self.idx)

    @property
    def t(self) -> np.ndarray:
        return self.idx / self.rate

    @property
    def duty_pct(self) -> np.ndarray:
        return self.duty * 100.0 / self.steps

    @property
    def missing(self) -> int:
        """Muestras que faltan según los huecos del índice."""
        if len(self.idx) == 0:
            return 0
        return int(self.idx[-1] - self.idx[0] + 1 - len(self.idx))


def base_meta(dev, calib: Calib, load: str) -> dict[str, str]:
    return {
        "fecha": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "puerto": dev.port,
        "firmware": dev.fw,
        "host": f"planta {__version__}",
        "rate_Hz": f"{dev.rate:g}",
        "pwm_Hz": f"{dev.pwm_hz:g}",
        "pwm_steps": str(dev.steps),
        "VREF_V": f"{calib.vref:g}",
        "divisor": f"{calib.div:g}",
        "Vs_V": f"{calib.vs:g}",
        "carga_ohm": load,
    }


def write_csv(path: str | Path, rec: Recording, calib: Calib, meta: dict[str, str]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    duty_pct = rec.duty_pct
    data = np.column_stack([rec.t, rec.idx, duty_pct, calib.vm_nom(duty_pct),
                            rec.adc, calib.vl(rec.adc)])
    meta = dict(meta)
    meta.setdefault("muestras", str(len(rec)))
    meta.setdefault("muestras_faltantes", str(rec.missing))
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# planta motor-generador DC: captura\n")
        for k, v in meta.items():
            fh.write(f"# {k}: {v}\n")
        fh.write("# VL_V = divisor * adc_raw * VREF_V / 1023\n")
        fh.write("# Vm_nom_V = duty_pct/100 * Vs_V (nominal, sin la caida del L298N)\n")
        fh.write(",".join(COLUMNS) + "\n")
        np.savetxt(fh, data, delimiter=",",
                   fmt=["%.6f", "%d", "%.4f", "%.4f", "%d", "%.4f"])
    return path


def read_csv(path: str | Path) -> tuple[dict[str, str], dict[str, np.ndarray]]:
    meta: dict[str, str] = {}
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("#"):
                header = line.strip().split(",")
                break
            k, sep, v = line[1:].strip().partition(": ")
            if sep:
                meta[k] = v
        data = np.loadtxt(fh, delimiter=",", ndmin=2)
    return meta, {name: data[:, i] for i, name in enumerate(header)}
