import numpy as np

from planta.gui.pwm_trace import pwm_trace


def test_pulsos_con_zoom():
    t = np.arange(0, 0.02, 0.002)            # 500 Hz
    d = np.full_like(t, 0.25)
    x, y = pwm_trace(t, d, pwm_hz=1000, vs=12, t0=0.0, t1=0.01)
    assert len(x) == 4 * 10                   # 10 períodos de 1 ms
    assert np.allclose(x[:4], [0, 0, 0.00025, 0.00025])
    assert np.allclose(y[:4], [0, 12, 12, 0])
    # valor medio de la reconstrucción = d * Vs
    area = np.sum((x[2::4] - x[1::4]) * 12) / 0.01
    assert area == np.float64(3.0) or np.isclose(area, 3.0)


def test_duty_cero_no_dibuja_pulsos():
    t = np.arange(0, 0.01, 0.002)
    x, y = pwm_trace(t, np.zeros_like(t), 1000, 12, 0, 0.01)
    assert np.all(y == 0)


def test_envolvente_sin_zoom():
    t = np.arange(0, 10, 0.002)
    d = np.where(t < 5, 0.0, 0.6)
    x, y = pwm_trace(t, d, 1000, 12, 0, 10)
    assert len(x) == len(t)                  # 10000 pulsos: envolvente
    assert y[0] == 0 and y[-1] == 12


def test_rango_vacio():
    t = np.arange(0, 1, 0.002)
    assert len(pwm_trace(t, np.ones_like(t), 1000, 12, 2, 3)[0]) == 0
    assert len(pwm_trace(np.zeros(0), np.zeros(0), 1000, 12, 0, 1)[0]) == 0
