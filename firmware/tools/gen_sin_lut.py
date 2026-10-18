"""Genera firmware/src/sin_lut.h: 256 muestras de sin(2*pi*i/256) en Q15.

Uso: python3 firmware/tools/gen_sin_lut.py > firmware/src/sin_lut.h
"""
import math

N = 256

vals = [round(32767 * math.sin(2 * math.pi * i / N)) for i in range(N)]
print("// Generado por tools/gen_sin_lut.py. No editar a mano.")
print("#pragma once")
print("#include <avr/pgmspace.h>")
print("#include <stdint.h>")
print()
print(f"#define SIN_LUT_LEN {N}")
print()
print("// sin(2*pi*i/256) en Q15 (32767 = 1.0)")
print("static const int16_t SIN_LUT[SIN_LUT_LEN] PROGMEM = {")
for i in range(0, N, 8):
    print("    " + ", ".join(f"{v:6d}" for v in vals[i:i + 8]) + ",")
print("};")
