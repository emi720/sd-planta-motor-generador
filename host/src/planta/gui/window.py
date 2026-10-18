"""Ventana principal: conexión, señal de entrada, gráficos en vivo y grabación."""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from ..calib import LOADS, Calib
from ..device import Device, DeviceError, list_ports
from ..protocol import (PWM_HZ_MAX, PWM_HZ_MIN, RATE_MAX_HZ, RATE_MIN_HZ, Frame,
                        Wave)
from ..recording import Recording, base_meta, write_csv

REFRESH_MS = 33
HISTORY_S = 120  # lo que se guarda para el gráfico (no afecta la grabación)

pg.setConfigOptions(background="w", foreground="k", antialias=False)


class SampleBuffer:
    """Últimas muestras para el gráfico, en arrays de numpy."""

    def __init__(self, cap: int):
        self.cap = cap
        self.data = np.zeros((cap, 3), dtype=np.int64)
        self.n = 0

    def clear(self) -> None:
        self.n = 0

    def extend(self, frames: list[Frame]) -> None:
        a = np.array([(f.idx, f.duty, f.adc) for f in frames], dtype=np.int64).reshape(-1, 3)
        if len(a) >= self.cap:
            a = a[-self.cap:]
        if self.n + len(a) > self.cap:
            keep = self.cap - len(a)
            self.data[:keep] = self.data[self.n - keep:self.n]
            self.n = keep
        self.data[self.n:self.n + len(a)] = a
        self.n += len(a)

    def last(self, n: int) -> np.ndarray:
        return self.data[max(self.n - n, 0):self.n]


def _spin(lo: float, hi: float, val: float, dec: int = 2, step: float = 1.0,
          suffix: str = "") -> QtWidgets.QDoubleSpinBox:
    s = QtWidgets.QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setSingleStep(step)
    s.setValue(val)
    if suffix:
        s.setSuffix(" " + suffix)
    s.setKeyboardTracking(False)
    return s


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Planta motor-generador — adquisición")
        self.resize(1280, 800)
        self.dev: Device | None = None
        self.buf = SampleBuffer(int(HISTORY_S * RATE_MAX_HZ))
        self.wave = Wave.dc(0)
        self.rec_frames: list[Frame] | None = None
        self.rec_waves: list[tuple[int, str]] = []
        self.last_idx: int | None = None
        self.missing = 0
        self.n_events = 0
        self.rate_count = 0
        self.rate_t0 = time.monotonic()

        self._build_ui()
        self._refresh_ports()
        self._set_connected(False)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(REFRESH_MS)

    # ---- construcción de la interfaz ----

    def _build_ui(self) -> None:
        side = QtWidgets.QWidget()
        side.setFixedWidth(330)
        lay = QtWidgets.QVBoxLayout(side)

        # Conexión
        g = QtWidgets.QGroupBox("Conexión")
        f = QtWidgets.QGridLayout(g)
        self.port_cb = QtWidgets.QComboBox()
        self.port_cb.setEditable(True)
        btn_ref = QtWidgets.QPushButton("↻")
        btn_ref.setToolTip("Actualizar la lista de puertos")
        btn_ref.setFixedWidth(32)
        btn_ref.clicked.connect(self._refresh_ports)
        self.conn_btn = QtWidgets.QPushButton("Conectar")
        self.conn_btn.clicked.connect(self._toggle_connect)
        self.conn_lbl = QtWidgets.QLabel("Desconectado")
        f.addWidget(self.port_cb, 0, 0)
        f.addWidget(btn_ref, 0, 1)
        f.addWidget(self.conn_btn, 1, 0, 1, 2)
        f.addWidget(self.conn_lbl, 2, 0, 1, 2)
        lay.addWidget(g)

        # Configuración
        g = QtWidgets.QGroupBox("Configuración")
        f = QtWidgets.QFormLayout(g)
        self.rate_sp = _spin(RATE_MIN_HZ, RATE_MAX_HZ, 500, 0, 50, "Hz")
        self.pwm_sp = _spin(PWM_HZ_MIN, PWM_HZ_MAX, 1000, 0, 250, "Hz")
        self.cfg_btn = QtWidgets.QPushButton("Aplicar muestreo y PWM")
        self.cfg_btn.setToolTip("Corta el streaming un instante y deja el duty en 0")
        self.cfg_btn.clicked.connect(self._apply_config)
        self.vref_sp = _spin(4.0, 5.5, 5.0, 3, 0.01, "V")
        self.div_sp = _spin(1.0, 3.0, 2.0, 3, 0.01)
        self.div_sp.setToolTip("V_L / V_A0. Calibrar midiendo V_L con el tester en simultáneo")
        self.vs_sp = _spin(0, 30, 12.0, 2, 0.1, "V")
        self.load_cb = QtWidgets.QComboBox()
        self.load_cb.addItems([f"{x} Ω" if x != "abierta" else x for x in LOADS])
        f.addRow("Muestreo", self.rate_sp)
        f.addRow("PWM", self.pwm_sp)
        f.addRow(self.cfg_btn)
        f.addRow("VREF", self.vref_sp)
        f.addRow("Divisor", self.div_sp)
        f.addRow("Vs (L298N)", self.vs_sp)
        f.addRow("Carga", self.load_cb)
        lay.addWidget(g)

        # Señal
        g = QtWidgets.QGroupBox("Señal de entrada")
        v = QtWidgets.QVBoxLayout(g)
        self.wave_cb = QtWidgets.QComboBox()
        self.wave_cb.addItems(["Continua (DC)", "Cuadrada", "Senoidal"])
        self.stack = QtWidgets.QStackedWidget()
        self.wave_cb.currentIndexChanged.connect(self.stack.setCurrentIndex)

        p = QtWidgets.QWidget()
        fl = QtWidgets.QFormLayout(p)
        self.dc_pct = _spin(0, 100, 60, 2, 1, "%")
        fl.addRow("Duty", self.dc_pct)
        self.stack.addWidget(p)

        p = QtWidgets.QWidget()
        fl = QtWidgets.QFormLayout(p)
        self.sq_f = _spin(0.001, 500, 0.5, 3, 0.1, "Hz")
        self.sq_duty = _spin(0, 100, 50, 1, 5, "%")
        self.sq_low = _spin(0, 100, 30, 2, 1, "%")
        self.sq_high = _spin(0, 100, 70, 2, 1, "%")
        fl.addRow("Frecuencia", self.sq_f)
        fl.addRow("Ciclo de trabajo", self.sq_duty)
        fl.addRow("Nivel bajo", self.sq_low)
        fl.addRow("Nivel alto", self.sq_high)
        self.stack.addWidget(p)

        p = QtWidgets.QWidget()
        fl = QtWidgets.QFormLayout(p)
        self.sin_f = _spin(0.001, 500, 1.0, 3, 0.1, "Hz")
        self.sin_off = _spin(0, 100, 60, 2, 1, "%")
        self.sin_amp = _spin(0, 100, 30, 2, 1, "%")
        fl.addRow("Frecuencia", self.sin_f)
        fl.addRow("Offset", self.sin_off)
        fl.addRow("Amplitud", self.sin_amp)
        self.stack.addWidget(p)

        self.apply_btn = QtWidgets.QPushButton("Aplicar señal")
        self.apply_btn.clicked.connect(self._apply_wave)
        self.zero_btn = QtWidgets.QPushButton("Duty 0  (Esc)")
        self.zero_btn.setStyleSheet("QPushButton { background: #c62828; color: white; font-weight: bold; }")
        self.zero_btn.clicked.connect(self._zero)
        QtGui.QShortcut(QtGui.QKeySequence("Esc"), self, activated=self._zero)
        self.wave_lbl = QtWidgets.QLabel("Aplicada: DC 0 %")
        self.wave_lbl.setWordWrap(True)
        v.addWidget(self.wave_cb)
        v.addWidget(self.stack)
        h = QtWidgets.QHBoxLayout()
        h.addWidget(self.apply_btn)
        h.addWidget(self.zero_btn)
        v.addLayout(h)
        v.addWidget(self.wave_lbl)
        lay.addWidget(g)

        # Grabación
        g = QtWidgets.QGroupBox("Grabación")
        f = QtWidgets.QGridLayout(g)
        self.name_ed = QtWidgets.QLineEdit("captura")
        self.dir_ed = QtWidgets.QLineEdit(str(Path.cwd() / "data"))
        btn_dir = QtWidgets.QPushButton("…")
        btn_dir.setFixedWidth(32)
        btn_dir.clicked.connect(self._pick_dir)
        self.rec_btn = QtWidgets.QPushButton("● Grabar")
        self.rec_btn.clicked.connect(self._toggle_record)
        self.rec_lbl = QtWidgets.QLabel("")
        f.addWidget(QtWidgets.QLabel("Nombre"), 0, 0)
        f.addWidget(self.name_ed, 0, 1, 1, 2)
        f.addWidget(QtWidgets.QLabel("Carpeta"), 1, 0)
        f.addWidget(self.dir_ed, 1, 1)
        f.addWidget(btn_dir, 1, 2)
        f.addWidget(self.rec_btn, 2, 0, 1, 3)
        f.addWidget(self.rec_lbl, 3, 0, 1, 3)
        lay.addWidget(g)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        lay.addWidget(self.log, 1)

        # Gráficos
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        top = QtWidgets.QHBoxLayout()
        self.win_sp = _spin(1, HISTORY_S, 10, 0, 1, "s")
        self.in_cb = QtWidgets.QComboBox()
        self.in_cb.addItems(["Duty [%]", "V_m nominal [V]"])
        self.vl_lbl = QtWidgets.QLabel("V_L: —")
        font = self.vl_lbl.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        self.vl_lbl.setFont(font)
        top.addWidget(QtWidgets.QLabel("Ventana"))
        top.addWidget(self.win_sp)
        top.addSpacing(16)
        top.addWidget(QtWidgets.QLabel("Entrada en"))
        top.addWidget(self.in_cb)
        top.addStretch(1)
        top.addWidget(self.vl_lbl)
        rl.addLayout(top)

        self.p_in = pg.PlotWidget()
        self.p_out = pg.PlotWidget()
        for p, name in ((self.p_in, "Entrada"), (self.p_out, "Salida V_L [V]")):
            p.showGrid(x=True, y=True, alpha=0.3)
            p.setClipToView(True)
            p.setDownsampling(auto=True, mode="peak")
            p.setTitle(name)
            p.setMouseEnabled(x=False, y=True)
        self.p_out.setXLink(self.p_in)
        self.p_out.setLabel("bottom", "t", units="s")
        self.p_in.setYRange(0, 100)
        self.p_out.setYRange(0, 8)
        self.c_in = self.p_in.plot(pen=pg.mkPen("#1565c0", width=1.5))
        self.c_out = self.p_out.plot(pen=pg.mkPen("#c62828", width=1.5))
        self.in_cb.currentIndexChanged.connect(self._input_units_changed)
        rl.addWidget(self.p_in, 1)
        rl.addWidget(self.p_out, 1)

        central = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(central)
        h.addWidget(side)
        h.addWidget(right, 1)
        self.setCentralWidget(central)

        self.stats_lbl = QtWidgets.QLabel("")
        self.statusBar().addPermanentWidget(self.stats_lbl)

    # ---- helpers ----

    def _log(self, text: str) -> None:
        self.log.appendPlainText(f"{dt.datetime.now():%H:%M:%S}  {text}")

    def _error(self, text: str) -> None:
        self._log("ERROR: " + text)
        QtWidgets.QMessageBox.warning(self, "Planta", text)

    def calib(self) -> Calib:
        return Calib(vref=self.vref_sp.value(), div=self.div_sp.value(), vs=self.vs_sp.value())

    def load(self) -> str:
        return LOADS[self.load_cb.currentIndex()]

    def _set_connected(self, on: bool) -> None:
        self.conn_btn.setText("Desconectar" if on else "Conectar")
        self.port_cb.setEnabled(not on)
        for w in (self.apply_btn, self.zero_btn, self.cfg_btn, self.rec_btn):
            w.setEnabled(on)

    def _refresh_ports(self) -> None:
        cur = self.port_cb.currentText()
        self.port_cb.clear()
        ports = list_ports()
        self.port_cb.addItems(ports)
        if cur in ports:
            self.port_cb.setCurrentText(cur)

    def _pick_dir(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Carpeta de capturas", self.dir_ed.text())
        if d:
            self.dir_ed.setText(d)

    def _input_units_changed(self) -> None:
        if self.in_cb.currentIndex() == 0:
            self.p_in.setYRange(0, 100)
        else:
            self.p_in.setYRange(0, self.vs_sp.value())

    def _reset_stream(self) -> None:
        self.buf.clear()
        self.last_idx = None
        self.missing = 0

    # ---- acciones ----

    def _toggle_connect(self) -> None:
        if self.dev:
            self._disconnect()
            return
        port = self.port_cb.currentText().strip()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.dev = Device(port)
            self.n_events = 0
            self.dev.set_pwm(self.pwm_sp.value())
            self.dev.set_rate(self.rate_sp.value())
            self._reset_stream()
            self.dev.start()
        except Exception as e:
            if self.dev:
                self.dev.close()
            self.dev = None
            QtWidgets.QApplication.restoreOverrideCursor()
            self._error(f"No se pudo conectar a {port}: {e}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self.wave = Wave.dc(0)
        self.wave_lbl.setText("Aplicada: DC 0 %")
        self.conn_lbl.setText(f"{port} — fw {self.dev.fw}")
        self._log(f"conectado a {port}, fw {self.dev.fw}, {self.dev.rate:g} Hz, PWM {self.dev.pwm_hz:g} Hz")
        self._set_connected(True)

    def _disconnect(self) -> None:
        if self.rec_frames is not None:
            self._stop_record()
        if self.dev:
            self.dev.close()
        self.dev = None
        self.conn_lbl.setText("Desconectado")
        self._log("desconectado (duty 0)")
        self._set_connected(False)

    def _apply_config(self) -> None:
        if not self.dev:
            return
        try:
            self.dev.stop()
            self.dev.set_pwm(self.pwm_sp.value())
            self.dev.set_rate(self.rate_sp.value())
            self._reset_stream()
            self.dev.start()
        except (DeviceError, ValueError) as e:
            self._error(str(e))
            return
        self.wave = Wave.dc(0)
        self.wave_lbl.setText("Aplicada: DC 0 % (la config. vuelve el duty a 0)")
        self._log(f"muestreo {self.dev.rate:g} Hz, PWM {self.dev.pwm_hz:g} Hz "
                  f"({self.dev.steps} pasos)")

    def _wave_from_ui(self) -> Wave:
        i = self.wave_cb.currentIndex()
        if i == 0:
            return Wave.dc(self.dc_pct.value())
        if i == 1:
            return Wave.sq(self.sq_f.value(), self.sq_duty.value(),
                           self.sq_low.value(), self.sq_high.value())
        return Wave.sin(self.sin_f.value(), self.sin_off.value(), self.sin_amp.value())

    def _set_wave(self, w: Wave) -> None:
        if not self.dev:
            return
        try:
            self.dev.set_wave(w)
        except (DeviceError, ValueError) as e:
            self._error(str(e))
            return
        self.wave = w
        self.wave_lbl.setText("Aplicada: " + w.describe())
        self._log("onda: " + w.describe())
        if self.rec_frames is not None:
            nxt = self.last_idx + 1 if self.last_idx is not None else 0
            self.rec_waves.append((nxt, w.describe()))

    def _apply_wave(self) -> None:
        self._set_wave(self._wave_from_ui())

    def _zero(self) -> None:
        self._set_wave(Wave.dc(0))

    def _toggle_record(self) -> None:
        if self.rec_frames is None:
            self._start_record()
        else:
            self._stop_record()

    def _start_record(self) -> None:
        self.rec_frames = []
        nxt = self.last_idx + 1 if self.last_idx is not None else 0
        self.rec_waves = [(nxt, self.wave.describe())]
        self.rec_btn.setText("■ Detener y guardar")
        self.rec_btn.setStyleSheet("QPushButton { color: #c62828; font-weight: bold; }")
        for w in (self.cfg_btn, self.rate_sp, self.pwm_sp, self.load_cb):
            w.setEnabled(False)
        self._log("grabando…")

    def _stop_record(self) -> None:
        frames, self.rec_frames = self.rec_frames, None
        self.rec_btn.setText("● Grabar")
        self.rec_btn.setStyleSheet("")
        for w in (self.cfg_btn, self.rate_sp, self.pwm_sp, self.load_cb):
            w.setEnabled(self.dev is not None or w is not self.cfg_btn)
        self.rec_lbl.setText("")
        if not frames or not self.dev:
            self._log("grabación vacía, no se guardó nada")
            return
        rec = Recording.from_frames(frames, self.dev.rate, self.dev.steps)
        calib = self.calib()
        i0 = int(rec.idx[0])
        meta = base_meta(self.dev, calib, self.load())
        meta["origen"] = "GUI"
        if len(self.rec_waves) == 1:
            meta["onda"] = self.rec_waves[0][1]
        else:
            meta["onda"] = "varias (ver cambios_onda)"
            meta["cambios_onda"] = "; ".join(
                f"t={max(i - i0, 0) / rec.rate:.3f}s {d}" for i, d in self.rec_waves)
        meta["tramas_crc_mal"] = str(self.dev.parser.crc_errors)
        name = self.name_ed.text().strip() or "captura"
        path = Path(self.dir_ed.text()) / f"{name}_{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
        try:
            write_csv(path, rec, calib, meta)
        except OSError as e:
            self._error(f"No se pudo guardar: {e}")
            return
        self._log(f"guardado {path.name}: {len(rec)} muestras, {rec.missing} faltantes")
        self.statusBar().showMessage(f"Guardado: {path.name}", 10000)
        self.statusBar().setToolTip(str(path))

    # ---- ciclo de refresco ----

    def _tick(self) -> None:
        dev = self.dev
        if not dev:
            return
        if not dev._rx.is_alive():
            self._error("Se perdió la conexión con la placa")
            self._disconnect()
            return

        ev = list(dev.events)
        for e in ev[self.n_events:]:
            self._log(e)
            if e.startswith("EVT WDT"):
                self.wave = Wave.dc(0)
                self.wave_lbl.setText("Aplicada: DC 0 % (watchdog)")
        self.n_events = len(ev)

        frames = dev.pop_frames()
        if frames:
            if self.last_idx is not None and frames[0].idx < self.last_idx:
                self._reset_stream()  # el firmware reinició el índice
            idx = np.fromiter((f.idx for f in frames), dtype=np.int64, count=len(frames))
            prev = self.last_idx if self.last_idx is not None else idx[0] - 1
            self.missing += int(idx[-1] - prev - len(idx))
            self.last_idx = int(idx[-1])
            self.buf.extend(frames)
            if self.rec_frames is not None:
                self.rec_frames += frames
            self.rate_count += len(frames)

        self._update_plot(dev)

        now = time.monotonic()
        if now - self.rate_t0 >= 1.0:
            sps = self.rate_count / (now - self.rate_t0)
            self.rate_count = 0
            self.rate_t0 = now
            self.stats_lbl.setText(
                f"{sps:.0f} muestras/s   faltantes: {self.missing}   "
                f"CRC mal: {dev.parser.crc_errors}   muestreo {dev.rate:g} Hz   PWM {dev.pwm_hz:g} Hz")
        if self.rec_frames is not None:
            self.rec_lbl.setText(f"● {len(self.rec_frames) / dev.rate:.1f} s grabados "
                                 f"({len(self.rec_frames)} muestras)")

    def _update_plot(self, dev: Device) -> None:
        win = self.win_sp.value()
        d = self.buf.last(int(win * dev.rate))
        if len(d) == 0:
            return
        calib = self.calib()
        t = d[:, 0] / dev.rate
        duty_pct = d[:, 1] * 100.0 / dev.steps
        y_in = duty_pct if self.in_cb.currentIndex() == 0 else calib.vm_nom(duty_pct)
        vl = calib.vl(d[:, 2])
        self.c_in.setData(t, y_in)
        self.c_out.setData(t, vl)
        self.p_in.setXRange(t[-1] - win, t[-1], padding=0)
        k = max(int(0.5 * dev.rate), 1)
        self.vl_lbl.setText(f"V_L = {vl[-k:].mean():.3f} V   (raw {d[-k:, 2].mean():.1f})")

    def closeEvent(self, ev: QtGui.QCloseEvent) -> None:
        if self.dev:
            self._disconnect()  # guarda la grabación en curso y deja duty 0
        super().closeEvent(ev)
