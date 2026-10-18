"""Conversión de cuentas del ADC y del PWM a tensiones."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LOADS = ("abierta", "560", "470", "255")


@dataclass(frozen=True)
class Calib:
    vref: float = 5.0  # riel de 5 V real (referencia AVcc del ADC)
    div: float = 2.0   # relación del divisor V_L / V_A0 (10k/10k ideal = 2)
    vs: float = 12.0   # alimentación del L298N

    def vl(self, raw):
        """V_L = div * raw * VREF / 1023."""
        return self.div * np.asarray(raw, dtype=float) * self.vref / 1023.0

    def vm_nom(self, duty_pct):
        """Tensión de motor nominal: duty * Vs, sin la caída del L298N."""
        return np.asarray(duty_pct, dtype=float) / 100.0 * self.vs
