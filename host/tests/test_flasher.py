import pytest

from planta.flasher import (PAGE, SIGNATURE_328P, FlashError, bundled_hex, flash,
                            parse_hex)


def _rec(addr: int, rtype: int, data: bytes) -> str:
    raw = bytes([len(data), addr >> 8, addr & 0xFF, rtype]) + data
    return ":" + (raw + bytes([(-sum(raw)) & 0xFF])).hex().upper()


def test_parse_hex_basico():
    text = "\n".join([_rec(0, 0, b"\x01\x02"), _rec(4, 0, b"\x05"), _rec(0, 1, b"")])
    assert parse_hex(text) == b"\x01\x02\xff\xff\x05"


def test_parse_hex_checksum_mal():
    line = _rec(0, 0, b"\x01\x02")
    bad = line[:-2] + ("00" if line[-2:] != "00" else "01")
    with pytest.raises(FlashError):
        parse_hex(bad)


def test_parse_hex_demasiado_grande():
    with pytest.raises(FlashError):
        parse_hex(_rec(0x7E00, 0, b"\x00"))  # zona del bootloader


def test_firmware_empaquetado():
    path, version = bundled_hex()
    img = parse_hex(path.read_text())
    assert version == "0.2.0"
    assert 5_000 < len(img) < 32_256
    assert img[:2] == b"\x0c\x94"  # tabla de vectores: jmp


class FakeOptiboot:
    """Lado del bootloader de STK500v1, con memoria de flash propia."""

    def __init__(self, baud_ok=115200, baud=115200, signature=SIGNATURE_328P, corrupt=False):
        self.alive = baud == baud_ok
        self.signature = signature
        self.corrupt = corrupt
        self.flash = bytearray(b"\xff" * 32768)
        self.addr = 0
        self.out = bytearray()
        self.inbuf = bytearray()
        self.dtr = self.rts = True
        self.left_progmode = False

    def reset_input_buffer(self):
        self.out.clear()

    def close(self):
        pass

    def read(self, n):
        r = bytes(self.out[:n])
        del self.out[:n]
        return r

    def _ok(self, data=b""):
        self.out += b"\x14" + data + b"\x10"

    def write(self, data):
        if not self.alive:
            return len(data)
        self.inbuf += data
        while self.inbuf and self.inbuf[-1] == 0x20:
            cmd = bytes(self.inbuf)
            c = cmd[0]
            need = {0x64: 4 + PAGE + 1}.get(c, None)
            if need and len(cmd) < need:
                return len(data)
            self.inbuf.clear()
            if c in (0x30, 0x50):
                self._ok()
            elif c == 0x51:
                self.left_progmode = True
                self._ok()
            elif c == 0x75:
                self._ok(self.signature)
            elif c == 0x55:
                self.addr = (cmd[1] | cmd[2] << 8) * 2
                self._ok()
            elif c == 0x64:
                page = bytearray(cmd[4:4 + PAGE])
                if self.corrupt and self.addr == PAGE:
                    page[0] ^= 0xFF
                self.flash[self.addr:self.addr + PAGE] = page
                self._ok()
            elif c == 0x74:
                self._ok(bytes(self.flash[self.addr:self.addr + PAGE]))
        return len(data)


def test_flash_completo_contra_optiboot_falso():
    path, _ = bundled_hex()
    img = parse_hex(path.read_text())
    boards = []

    def factory(baud):
        b = FakeOptiboot(baud=baud)
        boards.append(b)
        return b

    stages = []
    flash("fake", img, lambda s, f: stages.append(s), serial_factory=factory)
    b = boards[-1]
    assert bytes(b.flash[:len(img)]) == img
    assert b.left_progmode
    assert stages[0] == "grabando" and stages[-1] == "verificando"


def test_flash_prueba_57600_si_115200_no_responde():
    boards = []

    def factory(baud):
        b = FakeOptiboot(baud_ok=57600, baud=baud)
        boards.append(b)
        return b

    flash("fake", b"\x0c\x94" * 100, serial_factory=factory)
    assert len(boards) == 2 and boards[1].left_progmode


def test_flash_sin_bootloader():
    with pytest.raises(FlashError, match="no responde"):
        flash("fake", b"\x00" * 10, serial_factory=lambda baud: FakeOptiboot(baud_ok=0, baud=baud))


def test_flash_otra_placa():
    with pytest.raises(FlashError, match="no es un ATmega328P"):
        flash("fake", b"\x00" * 10,
              serial_factory=lambda baud: FakeOptiboot(baud=baud, signature=b"\x1e\x98\x01"))


def test_flash_verificacion_falla():
    with pytest.raises(FlashError, match="verificación"):
        flash("fake", b"\x11" * (3 * PAGE),
              serial_factory=lambda baud: FakeOptiboot(baud=baud, corrupt=True))
