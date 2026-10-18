#pragma once
#include <stdint.h>

// Muestreo determinístico: Timer2 en CTC marca el tick. En cada tick la ISR
// calcula u(t), actualiza el PWM y dispara una conversión del ADC; la ISR del
// ADC guarda la muestra en el ring buffer cuando termina (~104 us después).

struct Sample {
    uint32_t idx;
    uint16_t duty;  // cuentas de PWM aplicadas en ese tick
    uint16_t adc;   // A0 crudo, 0..1023
};

void sampler_init(float rate_hz);

// Configura la frecuencia de muestreo con el divisor entero más cercano.
// Devuelve la frecuencia real, o 0 si está fuera de rango.
float sampler_set_rate(float rate_hz);
float sampler_rate();

// Habilita/corta el envío de muestras. Al habilitar reinicia índice,
// contador de perdidas y buffer.
void sampler_stream(bool on);
bool sampler_streaming();

bool sampler_pop(Sample *s);
uint32_t sampler_lost();
uint16_t sampler_duty();
