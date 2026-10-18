"""Ensayo de ganancia estática: escalones de duty, espera de régimen y promedio.

Para cada duty se aplica DC, se descartan `settle` s y se promedian los
`avg` s siguientes. Las ventanas se cuentan en muestras (índice del firmware),
no con el reloj de la PC. Con --down se vuelve a barrer de bajada para ver
la histéresis de la zona muerta.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .calib import Calib
from .device import Device, DeviceError
from .protocol import Frame, Wave
from .recording import Recording, base_meta, stamped_name, write_csv

COLUMNS = ("paso", "sentido", "duty_pct", "Vm_nom_V", "VL_V", "VL_std_V",
           "adc_raw_medio", "n_muestras", "ganancia_VL_Vm")
TH_GIRA_V = 0.3  # V_L por encima de esto se considera que el motor gira


@dataclass
class Step:
    paso: int
    sentido: str
    duty_pct: float
    vm_nom: float
    vl: float
    vl_std: float
    raw: float
    n: int

    @property
    def gain(self) -> float:
        return self.vl / self.vm_nom if self.vm_nom > 0 else float("nan")


def duty_list(d_from: float, d_to: float, step: float, down: bool) -> list[tuple[float, str]]:
    if step <= 0:
        raise ValueError("el paso tiene que ser positivo")
    if not 0 <= d_from <= d_to <= 100:
        raise ValueError("se necesita 0 <= from <= to <= 100")
    up = list(np.round(np.arange(d_from, d_to + step / 2, step), 6))
    if up[-1] > d_to:
        up[-1] = d_to
    out = [(float(d), "sube") for d in up]
    if down:
        out += [(float(d), "baja") for d in reversed(up[:-1])]
    return out


def run_steps(dev: Device, duties: list[tuple[float, str]], settle: float, avg: float,
              calib: Calib, on_step: Callable[[Step], None] | None = None
              ) -> tuple[list[Step], Recording]:
    n_settle = round(settle * dev.rate)
    n_avg = max(round(avg * dev.rate), 1)
    frames: list[Frame] = []
    steps: list[Step] = []
    n_events = len(dev.events)
    dev.stop()
    try:
        dev.start()
        got = 0
        for k, (duty, sentido) in enumerate(duties):
            dev.set_wave(Wave.dc(duty))
            i0 = got  # primera muestra que puede tener el duty nuevo
            lo, hi = i0 + n_settle, i0 + n_settle + n_avg
            while got < hi:
                new = dev.pop_frames()
                if new:
                    frames += new
                    got = new[-1].idx + 1
                if any(e.startswith("EVT WDT") for e in list(dev.events)[n_events:]):
                    raise DeviceError("el watchdog del firmware cortó el ensayo")
                if not new:
                    import time
                    time.sleep(0.02)
            win = np.array([f.adc for f in frames if lo <= f.idx < hi], dtype=float)
            vl = calib.vl(win)
            s = Step(k, sentido, duty, float(calib.vm_nom(duty)), float(vl.mean()),
                     float(vl.std()), float(win.mean()), len(win))
            steps.append(s)
            if on_step:
                on_step(s)
    finally:
        dev.stop()
    frames += dev.pop_frames()
    return steps, Recording.from_frames(frames, dev.rate, dev.steps)


def dead_zone(steps: list[Step], th: float = TH_GIRA_V) -> tuple[float | None, float | None]:
    """Duty de arranque (primer escalón de subida que gira) y de parada
    (primer escalón de bajada que ya no gira)."""
    start = next((s.duty_pct for s in steps if s.sentido == "sube" and s.vl > th), None)
    stop = next((s.duty_pct for s in steps if s.sentido == "baja" and s.vl < th), None)
    return start, stop


def write_steps_csv(path: Path, steps: list[Step], meta: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# planta motor-generador DC: ganancia estática\n")
        for k, v in meta.items():
            fh.write(f"# {k}: {v}\n")
        fh.write("# VL_V: media en la ventana de promedio; ganancia_VL_Vm = VL_V / Vm_nom_V\n")
        fh.write(",".join(COLUMNS) + "\n")
        for s in steps:
            fh.write(f"{s.paso},{s.sentido},{s.duty_pct:.4f},{s.vm_nom:.4f},{s.vl:.4f},"
                     f"{s.vl_std:.4f},{s.raw:.2f},{s.n},{s.gain:.5f}\n")
    return path


def plot_steps(path: Path, steps: list[Step], title: str) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)
    for sentido, mk, col in (("sube", "o-", "#1565c0"), ("baja", "s--", "#ef6c00")):
        ss = [s for s in steps if s.sentido == sentido]
        if not ss:
            continue
        vm = np.array([s.vm_nom for s in ss])
        vl = np.array([s.vl for s in ss])
        sd = np.array([s.vl_std for s in ss])
        g = np.array([s.gain for s in ss])
        a1.errorbar(vm, vl, yerr=sd, fmt=mk, color=col, ms=4, capsize=2, label=sentido)
        a2.plot(vm, g, mk, color=col, ms=4, label=sentido)
    a1.set_xlabel("V_m nominal = duty · Vs [V]")
    a1.set_ylabel("V_L [V]")
    a1.set_title("Característica estática")
    a2.set_xlabel("V_m nominal [V]")
    a2.set_ylabel("V_L / V_m")
    a2.set_title("Ganancia estática")
    for a in (a1, a2):
        a.grid(True, alpha=0.3)
        a.legend()
    fig.suptitle(title, fontsize=10)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def run_static_gain(args) -> None:
    """Entrada de la CLI: planta static-gain ..."""
    duties = duty_list(args.d_from, args.d_to, args.step, args.down)
    out = Path(args.out) if args.out else Path("data") / stamped_name("ganancia")
    with Device(args.port) as dev:
        dev.set_pwm(args.pwmf)
        dev.set_rate(args.rate)
        calib = Calib(vref=args.vref, div=args.div, vs=args.vs)
        total = len(duties) * (args.settle + args.avg)
        print(f"{len(duties)} escalones, ~{total:.0f} s, {dev.rate:g} Hz, PWM {dev.pwm_hz:g} Hz")
        print(" paso sentido  duty%   Vm_nom   V_L      std     V_L/Vm")

        def show(s: Step) -> None:
            print(f"{s.paso:5d} {s.sentido:7} {s.duty_pct:6.1f} {s.vm_nom:7.2f} V "
                  f"{s.vl:6.3f} V {s.vl_std:6.3f} {s.gain:8.4f}", flush=True)

        steps, raw = run_steps(dev, duties, args.settle, args.avg, calib, show)
        meta = base_meta(dev, calib, args.load)
        start, stop = dead_zone(steps)
        meta.update({
            "ensayo": (f"escalones de {args.d_from:g} a {args.d_to:g} % paso {args.step:g} %"
                       + (" ida y vuelta" if args.down else "")),
            "settle_s": f"{args.settle:g}",
            "avg_s": f"{args.avg:g}",
            "umbral_gira_V": f"{TH_GIRA_V:g}",
            "arranque_duty_pct": "no detectado" if start is None else f"{start:g}",
            "parada_duty_pct": "no detectado" if stop is None else f"{stop:g}",
        })
    write_steps_csv(out, steps, meta)
    raw_path = write_csv(out.with_name(out.stem + "_crudo.csv"), raw, calib,
                         dict(meta, onda="escalones DC (ver archivo de ganancia)"))
    png = plot_steps(out.with_suffix(".png"), steps,
                     f"{out.name} — carga {args.load}, PWM {meta['pwm_Hz']} Hz")
    print(f"arranque: {meta['arranque_duty_pct']} %   parada: {meta['parada_duty_pct']} %")
    print(f"guardado {out}\n         {raw_path}\n         {png}")
