"""Device + capture + CSV de punta a punta contra el firmware simulado."""
import numpy as np
import pytest

from planta.calib import Calib
from planta.capture import capture
from planta.device import Device, DeviceError
from planta.protocol import Wave
from planta.recording import base_meta, read_csv, write_csv


@pytest.fixture
def dev():
    d = Device("sim://")
    yield d
    d.close()


def test_conexion_y_status(dev):
    assert dev.fw.endswith("sim")
    assert dev.rate == 500
    assert dev.steps == 16000
    assert dev.pwm_hz == 1000
    assert any(e.startswith("EVT READY") for e in dev.events)


def test_rate_real_y_errores(dev):
    assert dev.set_rate(1000) == 1000
    assert dev.set_rate(300) == pytest.approx(300.0, rel=0.01)
    with pytest.raises(DeviceError):
        dev.command("SIN 1 80 30")
    with pytest.raises(DeviceError):
        dev.command("FOO")


def test_captura_senoidal(dev):
    rec = capture(dev, Wave.sin(2, 70, 20), duration=1.0, pre=0.2)
    k = rec.onset_idx
    assert 100 <= k < 150                            # base de 0,2 s + latencia
    assert len(rec) == k + 500                       # 1 s exacto con la onda
    assert rec.missing == 0
    assert np.array_equal(rec.idx, np.arange(len(rec)))
    d = rec.duty_pct
    assert np.all(d[:k] == 0)                        # línea de base
    assert d[k] == pytest.approx(70, abs=0.01)       # fase 0: arranca en el offset
    assert d[k:].min() == pytest.approx(50, abs=0.1)
    assert d[k:].max() == pytest.approx(90, abs=0.1)


def test_zona_muerta_simulada(dev):
    calib = Calib()
    quieto = capture(dev, Wave.dc(30), duration=0.6, pre=0)
    assert calib.vl(quieto.adc)[-50:].mean() < 0.1   # 30 % no arranca desde reposo
    gira = capture(dev, Wave.dc(80), duration=1.5, pre=0)
    assert calib.vl(gira.adc)[-50:].mean() > 5.0


def test_watchdog(dev):
    dev._alive.clear()          # corta keepalive y lector, como si el host se colgara
    dev._ka.join(timeout=1)
    dev._rx.join(timeout=1)
    sim = dev.ser
    sim.write(b"START\n")
    sim.wdt_s = 0.3
    import time
    time.sleep(0.5)
    sim.read(1 << 20)
    assert not sim.streaming and sim.wave == Wave.dc(0)


def test_csv_ida_y_vuelta(dev, tmp_path):
    calib = Calib(vref=4.95, div=2.03)
    rec = capture(dev, Wave.sq(5, 50, 10, 60), duration=0.5, pre=0.1)
    meta = base_meta(dev, calib, "470")
    meta["onda"] = "SQ de prueba"
    path = write_csv(tmp_path / "x.csv", rec, calib, meta)
    m, cols = read_csv(path)
    assert m["carga_ohm"] == "470"
    assert m["VREF_V"] == "4.95"
    assert m["muestras"] == str(len(rec))
    assert list(cols) == ["t_s", "sample_idx", "duty_pct", "Vm_nom_V", "adc_raw", "VL_V"]
    assert np.allclose(cols["t_s"], rec.t)
    assert np.allclose(cols["Vm_nom_V"], rec.duty_pct * 12 / 100, atol=1e-4)
    assert np.allclose(cols["VL_V"], 2.03 * rec.adc * 4.95 / 1023, atol=1e-4)


def test_nombre_con_fecha_adelante():
    import re

    from planta.recording import stamped_name
    assert re.fullmatch(r"\d{8}_\d{6}_sin\.csv", stamped_name("sin"))
    assert re.fullmatch(r"\d{8}_\d{6}_escalon_50_.csv", stamped_name("escalon 50%"))
    assert stamped_name("  ").endswith("_captura.csv")
