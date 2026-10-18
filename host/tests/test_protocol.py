import random

import pytest

from planta.protocol import (FRAME_LEN, Frame, Line, StreamParser, crc8, encode_frame,
                             parse_kv)


def test_crc8_valor_de_control():
    # Valor de control estándar de CRC-8/MAXIM.
    assert crc8(b"123456789") == 0xA1
    assert crc8(b"") == 0


def test_encode_frame_formato():
    f = encode_frame(0x01020304, 0x0506, 0x0708)
    assert len(f) == FRAME_LEN
    assert f[:2] == b"\xa5\x5a"
    assert f[2:10] == bytes([4, 3, 2, 1, 6, 5, 8, 7])  # little-endian
    assert f[10] == crc8(f[2:10])


def test_ida_y_vuelta():
    frames = [Frame(i, (i * 37) % 16001, (i * 13) % 1024) for i in range(500)]
    data = b"".join(encode_frame(f.idx, f.duty, f.adc) for f in frames)
    p = StreamParser()
    assert p.feed(data) == frames
    assert p.crc_errors == 0 and p.junk_bytes == 0


def test_bytes_de_a_uno():
    data = encode_frame(1, 2, 3) + b"OK STOP\n" + encode_frame(4, 5, 6)
    p = StreamParser()
    out = []
    for b in data:
        out += p.feed(bytes([b]))
    assert out == [Frame(1, 2, 3), Line("OK STOP"), Frame(4, 5, 6)]


def test_lineas_y_tramas_mezcladas():
    data = (b"EVT READY planta-fw 0.2.0\n" + encode_frame(0, 100, 200)
            + b"OK PONG planta-fw 0.2.0\r\n" + encode_frame(1, 100, 201)
            + b"ERR comando desconocido\n")
    out = StreamParser().feed(data)
    assert out == [Line("EVT READY planta-fw 0.2.0"), Frame(0, 100, 200),
                   Line("OK PONG planta-fw 0.2.0"), Frame(1, 100, 201),
                   Line("ERR comando desconocido")]
    assert [x.kind for x in out if isinstance(x, Line)] == ["EVT", "OK", "ERR"]


def test_crc_mal_se_descarta_y_resincroniza():
    bad = bytearray(encode_frame(7, 8, 9))
    bad[5] ^= 0xFF
    p = StreamParser()
    out = p.feed(bytes(bad) + encode_frame(10, 11, 12))
    assert out == [Frame(10, 11, 12)]
    assert p.crc_errors == 1


def test_arranque_a_mitad_de_trama():
    data = encode_frame(1, 2, 3)[5:] + encode_frame(2, 3, 4) + encode_frame(3, 4, 5)
    p = StreamParser()
    assert p.feed(data) == [Frame(2, 3, 4), Frame(3, 4, 5)]
    assert p.junk_bytes > 0


def test_basura_imprimible_sin_prefijo():
    p = StreamParser()
    assert p.feed(b"hola\n" + encode_frame(1, 1, 1)) == [Frame(1, 1, 1)]
    assert p.junk_bytes == 5


def test_trama_partida_espera():
    f = encode_frame(5, 6, 7)
    p = StreamParser()
    assert p.feed(f[:6]) == []
    assert p.feed(f[6:]) == [Frame(5, 6, 7)]


def test_sync_falso_dentro_de_payload():
    # Payload que contiene A5 5A: alineado, la trama se consume entera.
    idx = 0x5AA5
    data = encode_frame(idx, 0xA55A, 0x5A) * 3
    assert StreamParser().feed(data) == [Frame(idx, 0xA55A, 0x5A)] * 3


def test_flujo_aleatorio_con_corrupcion():
    rng = random.Random(1)
    frames = [Frame(i, rng.randrange(16001), rng.randrange(1024)) for i in range(2000)]
    data = bytearray()
    for f in frames:
        data += encode_frame(f.idx, f.duty, f.adc)
        if rng.random() < 0.05:
            data += b"OK PONG\n"
    for _ in range(20):  # corromper 20 bytes al azar
        data[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
    p = StreamParser()
    out = []
    for k in range(0, len(data), 97):
        out += p.feed(bytes(data[k:k + 97]))
    got = [x for x in out if isinstance(x, Frame)]
    good = set(frames)
    # Ninguna trama inventada y se recupera casi todo.
    assert all(f in good for f in got)
    assert len(got) >= len(frames) - 40


def test_parse_kv_status():
    text = ("OK STATUS fw=0.2.0 rate=500.000 steps=16000 pwm_hz=1000.0 duty_max=100.00 "
            "duty=0 streaming=0 lost=0 wdt=2.0 wave=SIN f=1.000 offset=50.00 amp=30.00")
    kv = parse_kv(text)
    assert kv["rate"] == "500.000"
    assert kv["steps"] == "16000"
    assert kv["wave"] == "SIN f=1.000 offset=50.00 amp=30.00"


@pytest.mark.parametrize("idx", [0, 1, 2**31, 2**32 - 1])
def test_indices_extremos(idx):
    assert StreamParser().feed(encode_frame(idx, 16000, 1023)) == [Frame(idx, 16000, 1023)]
