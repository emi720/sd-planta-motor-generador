#pragma once
#include <stdint.h>

// Generador de señales por DDS: acumulador de fase de 32 bits que avanza
// `inc` por muestra, f = inc * fs / 2^32. Todo en enteros dentro de la ISR.

enum WaveType : uint8_t { WAVE_DC = 0, WAVE_SQ = 1, WAVE_SIN = 2 };

struct WaveParams {
    uint8_t type;
    uint32_t inc;  // incremento de fase por muestra (SQ y SIN)
    uint32_t thr;  // SQ: la salida está en alto mientras fase < thr
    uint16_t a;    // DC: nivel   SQ: nivel bajo   SIN: offset   (cuentas)
    uint16_t b;    //             SQ: nivel alto   SIN: amplitud (cuentas)
};

// Reemplaza los parámetros de forma atómica y reinicia la fase a 0.
void wavegen_set(const WaveParams &p);

// Devuelve u(t) en cuentas y avanza la fase. Solo desde la ISR de muestreo.
uint16_t wavegen_tick();
