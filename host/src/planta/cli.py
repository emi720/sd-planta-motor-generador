"""Línea de comandos: planta {ports,status,capture,static-gain,gui}."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .calib import LOADS, Calib
from .device import Device, DeviceError, list_ports
from .protocol import Wave
from .recording import stamped_name


def _add_conn(p: argparse.ArgumentParser) -> None:
    p.add_argument("--port", required=True, help="/dev/ttyUSB0, /dev/ttyACM0 o sim://")


def _add_setup(p: argparse.ArgumentParser) -> None:
    p.add_argument("--rate", type=float, default=500, help="muestreo en Hz (62..1000, default 500)")
    p.add_argument("--pwmf", type=float, default=1000, help="frecuencia del PWM en Hz (default 1000)")
    p.add_argument("--vref", type=float, default=5.0, help="riel de 5 V medido (default 5.0)")
    p.add_argument("--div", type=float, default=2.0, help="relación V_L/V_A0 del divisor (default 2.0)")
    p.add_argument("--vs", type=float, default=12.0, help="alimentación del L298N (default 12)")
    p.add_argument("--load", choices=LOADS + ("no_indicada",), default="no_indicada",
                   help="carga del generador en ohm")


def _setup(dev: Device, args) -> Calib:
    dev.set_pwm(args.pwmf)
    dev.set_rate(args.rate)
    return Calib(vref=args.vref, div=args.div, vs=args.vs)


def _wave_from_args(args) -> Wave:
    def need(*names):
        missing = [n for n in names if getattr(args, n) is None]
        if missing:
            sys.exit(f"error: --wave {args.wave} requiere " + ", ".join("--" + m for m in missing))

    if args.wave == "dc":
        need("pct")
        return Wave.dc(args.pct)
    if args.wave == "sq":
        need("f", "high")
        return Wave.sq(args.f, args.duty, args.low, args.high)
    need("f", "offset", "amp")
    return Wave.sin(args.f, args.offset, args.amp)


def cmd_ports(args) -> None:
    for p in list_ports():
        print(p)


def cmd_status(args) -> None:
    with Device(args.port) as dev:
        for k, v in dev.status().items():
            print(f"{k:10} {v}")


def cmd_capture(args) -> None:
    from .capture import capture
    from .recording import base_meta, write_csv

    wave = _wave_from_args(args)
    out = args.out or Path("data") / stamped_name(wave.kind)
    with Device(args.port) as dev:
        calib = _setup(dev, args)
        wave.validate(dev.rate)
        print(f"capturando {wave.describe()} durante {args.duration:g} s "
              f"(+{args.pre:g} s en 0) a {dev.rate:g} Hz, PWM {dev.pwm_hz:g} Hz")
        rec = capture(dev, wave, args.duration, args.pre,
                      on_progress=lambda t: print(f"\r  {t:6.1f} s", end="", flush=True))
        print()
        meta = base_meta(dev, calib, args.load)
        meta.update(onda=wave.describe(), t_onda_s=f"{rec.onset_idx / dev.rate:g}",
                    duracion_s=f"{args.duration:g}",
                    tramas_crc_mal=str(dev.parser.crc_errors))
        lost_fw = dev.status().get("lost", "?")
        meta["perdidas_firmware"] = lost_fw
        path = write_csv(out, rec, calib, meta)
    vl = calib.vl(rec.adc)
    print(f"{len(rec)} muestras, {rec.missing} faltantes, {lost_fw} perdidas en el firmware, "
          f"{dev.parser.crc_errors} CRC mal")
    if len(rec):
        print(f"V_L: min {vl.min():.2f} V, media {vl.mean():.2f} V, max {vl.max():.2f} V")
    print(f"guardado en {path}")


def cmd_static_gain(args) -> None:
    from .static_gain import run_static_gain
    run_static_gain(args)


def cmd_gui(args) -> None:
    from .gui.app import main as gui_main
    gui_main()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="planta", description="Adquisición de la planta motor-generador DC")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ports", help="lista los puertos serie disponibles")
    p.set_defaults(func=cmd_ports)

    p = sub.add_parser("status", help="muestra el estado del firmware")
    _add_conn(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("capture", help="aplica una onda y graba entrada y salida a CSV")
    _add_conn(p)
    _add_setup(p)
    p.add_argument("--wave", choices=("dc", "sq", "sin"), required=True)
    p.add_argument("--pct", type=float, help="DC: duty en %%")
    p.add_argument("--f", type=float, help="SQ/SIN: frecuencia en Hz")
    p.add_argument("--duty", type=float, default=50, help="SQ: ciclo de trabajo en %% (default 50)")
    p.add_argument("--low", type=float, default=0, help="SQ: nivel bajo en %% de duty (default 0)")
    p.add_argument("--high", type=float, help="SQ: nivel alto en %% de duty")
    p.add_argument("--offset", type=float, help="SIN: offset en %% de duty")
    p.add_argument("--amp", type=float, help="SIN: amplitud en %% de duty")
    p.add_argument("--duration", type=float, required=True, help="segundos con la onda aplicada")
    p.add_argument("--pre", type=float, default=0.5, help="segundos en duty 0 antes de la onda (default 0.5)")
    p.add_argument("--out", type=Path, help="CSV de salida (default data/<fecha>_<onda>.csv)")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("static-gain", help="ensayo de ganancia estática por escalones de duty")
    _add_conn(p)
    _add_setup(p)
    p.add_argument("--from", dest="d_from", type=float, default=0, help="duty inicial en %% (default 0)")
    p.add_argument("--to", dest="d_to", type=float, default=100, help="duty final en %% (default 100)")
    p.add_argument("--step", type=float, default=5, help="paso en %% (default 5)")
    p.add_argument("--settle", type=float, default=2, help="espera de régimen por escalón en s (default 2)")
    p.add_argument("--avg", type=float, default=1, help="ventana de promedio en s (default 1)")
    p.add_argument("--down", action="store_true", help="barrer también de bajada (histéresis)")
    p.add_argument("--out", type=Path, help="CSV de salida (default data/<fecha>_ganancia.csv)")
    p.set_defaults(func=cmd_static_gain)

    p = sub.add_parser("gui", help="interfaz gráfica en tiempo real")
    p.set_defaults(func=cmd_gui)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (DeviceError, ValueError) as e:
        sys.exit(f"error: {e}")
    except KeyboardInterrupt:
        sys.exit("\ninterrumpido (duty en 0)")


if __name__ == "__main__":
    main()
