import pytest

from planta.protocol import Wave, cmd_pwmf, cmd_rate, cmd_wdt, fmt


@pytest.mark.parametrize("x,s", [(1, "1"), (0.5, "0.5"), (50.0, "50"), (0, "0"),
                                 (12.345678, "12.345678"), (1e-7, "0"), (-0.0, "0")])
def test_fmt(x, s):
    assert fmt(x) == s


def test_comandos_de_onda():
    assert Wave.dc(37.5).command() == "DC 37.5"
    assert Wave.sq(2, 50, 20, 60).command() == "SQ 2 50 20 60"
    assert Wave.sin(1, 50, 30).command() == "SIN 1 50 30"


def test_comandos_de_configuracion():
    assert cmd_rate(500) == "RATE 500"
    assert cmd_pwmf(1000) == "PWMF 1000"
    assert cmd_wdt(2.5) == "WDT 2.5"


@pytest.mark.parametrize("bad", [lambda: cmd_rate(30), lambda: cmd_rate(1500),
                                 lambda: cmd_pwmf(100), lambda: cmd_pwmf(30000),
                                 lambda: cmd_wdt(-1), lambda: cmd_wdt(61)])
def test_configuracion_fuera_de_rango(bad):
    with pytest.raises(ValueError):
        bad()


@pytest.mark.parametrize("wave", [
    Wave.dc(-1), Wave.dc(100.1),
    Wave.sq(0, 50, 0, 50), Wave.sq(251, 50, 0, 50), Wave.sq(1, 101, 0, 50),
    Wave.sq(1, 50, -5, 50), Wave.sq(1, 50, 0, 120),
    Wave.sin(1, 20, 30),   # offset - amp < 0
    Wave.sin(1, 80, 30),   # offset + amp > 100
    Wave.sin(1, 50, -1), Wave.sin(300, 50, 10),
    Wave("triangulo"),
])
def test_validacion_rechaza(wave):
    with pytest.raises(ValueError):
        wave.validate(rate=500)


@pytest.mark.parametrize("wave", [Wave.dc(0), Wave.dc(100), Wave.sq(250, 0, 0, 100),
                                  Wave.sin(1, 50, 50), Wave.sin(0.01, 30, 0)])
def test_validacion_acepta(wave):
    wave.validate(rate=500)


def test_duty_max_configurable():
    with pytest.raises(ValueError):
        Wave.sin(1, 50, 30).validate(rate=500, duty_max=70)
    Wave.sin(1, 40, 30).validate(rate=500, duty_max=70)


def test_describe():
    assert Wave.sin(1, 50, 30).describe() == "SIN f=1Hz offset=50% amp=30%"


@pytest.mark.parametrize("dev,plat,ok", [
    ("/dev/ttyUSB0", "linux", True), ("/dev/ttyACM0", "linux", True), ("/dev/ttyS0", "linux", False),
    ("COM3", "win32", True),
    ("/dev/cu.usbserial-1410", "darwin", True), ("/dev/tty.usbserial-1410", "darwin", False),
    ("/dev/cu.Bluetooth-Incoming-Port", "darwin", False),
])
def test_puertos_candidatos(dev, plat, ok):
    from planta.device import port_is_candidate
    assert port_is_candidate(dev, plat) is ok
