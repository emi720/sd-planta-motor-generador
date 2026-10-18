#include "wavegen.h"

#include <avr/pgmspace.h>
#include <util/atomic.h>

#include "sin_lut.h"

static WaveParams wp = {WAVE_DC, 0, 0, 0, 0};
static uint32_t phase = 0;

void wavegen_set(const WaveParams &p) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        wp = p;
        phase = 0;
    }
}

// Seno en Q15 con interpolación lineal entre entradas de la LUT: los 8 bits
// altos de la fase eligen la entrada y los 8 siguientes interpolan.
static inline int16_t sin_q15(uint32_t ph) {
    uint8_t i = ph >> 24;
    uint8_t frac = (ph >> 16) & 0xFF;
    int16_t s0 = pgm_read_word(&SIN_LUT[i]);
    int16_t s1 = pgm_read_word(&SIN_LUT[(uint8_t)(i + 1)]);
    return s0 + (int16_t)(((int32_t)(s1 - s0) * frac) >> 8);
}

uint16_t wavegen_tick() {
    uint16_t u;
    switch (wp.type) {
    case WAVE_SQ:
        u = (phase < wp.thr) ? wp.b : wp.a;
        break;
    case WAVE_SIN: {
        // offset y amplitud ya validados en main: offset ± amp queda en rango.
        int32_t v = (int32_t)wp.a + (((int32_t)wp.b * sin_q15(phase)) >> 15);
        u = (v < 0) ? 0 : (uint16_t)v;
        break;
    }
    default:
        u = wp.a;
        break;
    }
    phase += wp.inc;
    return u;
}
