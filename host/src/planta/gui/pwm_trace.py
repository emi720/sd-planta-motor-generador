"""Reconstrucción del PWM para dibujarlo detrás del valor medio.

El firmware no informa la fase del PWM respecto del muestreo, así que los
pulsos se reconstruyen con el duty vigente de cada muestra (retención de
orden cero) y períodos que arrancan en múltiplos de 1/f_pwm. Sirve para
visualizar, no como medición.
"""
from __future__ import annotations

import numpy as np

MAX_PULSES = 3000


def pwm_trace(t: np.ndarray, duty_frac: np.ndarray, pwm_hz: float, vs: float,
              t0: float, t1: float, max_pulses: int = MAX_PULSES
              ) -> tuple[np.ndarray, np.ndarray]:
    """Puntos (x, y) del PWM entre t0 y t1.

    Si entran más de `max_pulses` pulsos, devuelve la envolvente (Vs donde el
    duty es mayor que 0), que es lo que se vería igual a esa escala.
    """
    if len(t) == 0 or t1 <= t0:
        return np.zeros(0), np.zeros(0)
    if (t1 - t0) * pwm_hz > max_pulses:
        sel = (t >= t0) & (t <= t1)
        return t[sel], np.where(duty_frac[sel] > 0, vs, 0.0)

    k0 = int(np.floor(max(t0, t[0]) * pwm_hz))
    k1 = int(np.ceil(min(t1, t[-1]) * pwm_hz))
    if k1 <= k0:
        return np.zeros(0), np.zeros(0)
    tk = np.arange(k0, k1) / pwm_hz
    j = np.searchsorted(t, tk, side="right") - 1
    ok = j >= 0
    tk, d = tk[ok], duty_frac[j[ok]]
    tf = tk + d / pwm_hz
    x = np.column_stack([tk, tk, tf, tf]).ravel()
    hi = np.where(d > 0, vs, 0.0)
    y = np.column_stack([np.zeros_like(hi), hi, hi, np.zeros_like(hi)]).ravel()
    return x, y
