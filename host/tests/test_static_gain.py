import numpy as np
import pytest

from planta.calib import Calib
from planta.device import Device
from planta.simulator import PlantParams
from planta.static_gain import dead_zone, duty_list, plot_steps, run_steps, write_steps_csv


def test_duty_list_ida_y_vuelta():
    d = duty_list(0, 20, 5, down=True)
    assert [x for x, _ in d] == [0, 5, 10, 15, 20, 15, 10, 5, 0]
    assert [s for _, s in d] == ["sube"] * 5 + ["baja"] * 4


def test_duty_list_paso_no_exacto():
    assert [x for x, _ in duty_list(0, 10, 3, down=False)] == [0, 3, 6, 9]
    assert [x for x, _ in duty_list(50, 50, 5, down=True)] == [50]


@pytest.mark.parametrize("args", [(0, 100, 0), (50, 10, 5), (-5, 50, 5), (0, 120, 5)])
def test_duty_list_invalida(args):
    with pytest.raises(ValueError):
        duty_list(*args, down=False)


def test_ensayo_simulado_con_histeresis(tmp_path):
    with Device("sim://") as dev:
        dev.ser.plant.p = PlantParams(tau_m=0.05)  # acelera el ensayo
        dev.set_rate(1000)
        duties = duty_list(10, 60, 10, down=True)
        steps, raw = run_steps(dev, duties, settle=0.3, avg=0.1, calib=Calib())
    assert len(steps) == len(duties)
    assert all(s.n == 100 for s in steps)
    start, stop = dead_zone(steps)
    assert start == 50  # el simulador arranca en 48 %
    assert stop == 20   # y para debajo de 22 %
    sube = {s.duty_pct: s.vl for s in steps if s.sentido == "sube"}
    baja = {s.duty_pct: s.vl for s in steps if s.sentido == "baja"}
    assert sube[30] < 0.1 < 1.0 < baja[30]  # histéresis
    assert not np.isnan(steps[0].gain) and steps[0].vm_nom == pytest.approx(1.2)
    assert raw.missing == 0

    csv = write_steps_csv(tmp_path / "g.csv", steps, {"carga_ohm": "abierta"})
    lines = [ln for ln in csv.read_text().splitlines() if not ln.startswith("#")]
    assert lines[0].startswith("paso,sentido,duty_pct")
    assert len(lines) == len(steps) + 1
    png = plot_steps(tmp_path / "g.png", steps, "prueba")
    assert png.stat().st_size > 10_000
