#include "sampler.h"

#include <avr/interrupt.h>
#include <avr/io.h>
#include <util/atomic.h>

#include "config.h"
#include "pwm.h"
#include "wavegen.h"

// ¿Por qué ADC por interrupción y no analogRead en la ISR?
// analogRead espera la conversión con un busy-wait de ~104 us (13 ciclos de
// ADC a 125 kHz). Dentro de una ISR las interrupciones están deshabilitadas,
// así que durante ese tiempo se frena la recepción y transmisión serie. Además, el ADC no puede dispararse solo desde Timer2
// (ADTS acepta Timer0 y Timer1; Timer0 lo usa millis() y Timer1 es el PWM).
// Entonces: la ISR de Timer2 arranca la conversión (ADSC) y la ISR del ADC
// toma el resultado. La latencia entre tick y muestra es fija (~104 us).

#define RING_LEN 64  // potencia de 2; 64 x 8 B = 512 B de RAM
#define RING_MASK (RING_LEN - 1)

static Sample ring[RING_LEN];
static volatile uint8_t head = 0;  // escribe la ISR del ADC
static volatile uint8_t tail = 0;  // lee el loop
static volatile bool streaming = false;
static volatile uint32_t next_idx = 0;
static volatile uint32_t lost = 0;
static volatile uint16_t tick_duty = 0;
static float rate_actual = 0;

// Prescalers de Timer2 y sus bits CS22:0.
static const uint16_t T2_PRESC[] = {1, 8, 32, 64, 128, 256, 1024};

ISR(TIMER2_COMPA_vect) {
    uint16_t u = wavegen_tick();
    pwm_set(u);
    tick_duty = u;
    ADCSRA |= _BV(ADSC);
}

ISR(ADC_vect) {
    uint16_t v = ADC;
    if (!streaming)
        return;
    uint8_t h = head;
    uint8_t n = (h + 1) & RING_MASK;
    if (n == tail) {
        lost++;
    } else {
        ring[h].idx = next_idx;
        ring[h].duty = tick_duty;
        ring[h].adc = v;
        head = n;
    }
    // El índice avanza aunque la muestra se pierda: el hueco queda a la vista.
    next_idx++;
}

void sampler_init(float rate_hz) {
    // ADC: referencia AVcc (el riel de 5 V, VREF configurable en el host),
    // canal A0, prescaler 128 -> 125 kHz, interrupción al completar.
    ADMUX = _BV(REFS0);
    DIDR0 = _BV(ADC0D);
    ADCSRB = 0;
    ADCSRA = _BV(ADEN) | _BV(ADIE) | _BV(ADPS2) | _BV(ADPS1) | _BV(ADPS0);
    sampler_set_rate(rate_hz);
}

float sampler_set_rate(float rate_hz) {
    if (!(rate_hz > 0) || rate_hz > RATE_MAX_HZ)
        return 0;
    // Prescaler más chico que deje OCR2A <= 255 (mejor resolución).
    for (uint8_t k = 0; k < 7; k++) {
        float ticks = (float)F_CPU / ((float)T2_PRESC[k] * rate_hz);
        long ocr = (long)(ticks + 0.5f) - 1;
        if (ocr > 255)
            continue;
        if (ocr < 1)
            return 0;
        ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
            TCCR2B = 0;
            TCCR2A = _BV(WGM21);  // CTC, TOP = OCR2A
            TCNT2 = 0;
            OCR2A = (uint8_t)ocr;
            TIFR2 = _BV(OCF2A);
            TIMSK2 = _BV(OCIE2A);
            TCCR2B = k + 1;
        }
        rate_actual = (float)F_CPU / ((float)T2_PRESC[k] * (ocr + 1));
        return rate_actual;
    }
    return 0;  // por debajo de ~61 Hz no alcanza ni con prescaler 1024
}

float sampler_rate() { return rate_actual; }

void sampler_stream(bool on) {
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        if (on) {
            head = tail = 0;
            next_idx = 0;
            lost = 0;
        }
        streaming = on;
    }
}

bool sampler_streaming() { return streaming; }

bool sampler_pop(Sample *s) {
    uint8_t t = tail;
    if (t == head)
        return false;
    // La ISR no toca esta entrada hasta que avance tail.
    *s = ring[t];
    tail = (t + 1) & RING_MASK;
    return true;
}

uint32_t sampler_lost() {
    uint32_t v;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { v = lost; }
    return v;
}

uint16_t sampler_duty() {
    uint16_t v;
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { v = tick_duty; }
    return v;
}
