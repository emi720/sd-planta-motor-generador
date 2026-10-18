#pragma once
#include <stdint.h>

// PWM en el pin 9 (OC1A) por Timer1, Fast PWM modo 14 con TOP = ICR1 y
// prescaler 1: pasos = 16 MHz / f. A 4 kHz son 4000 pasos; a 20 kHz, 800.
#define PWM_HZ_DEFAULT 4000UL
#define PWM_HZ_MIN 250UL    // TOP <= 65535
#define PWM_HZ_MAX 20000UL  // por encima del oído; quedan 800 pasos

void pwm_init(uint32_t hz);

// Cambia la frecuencia con el timer detenido y deja la salida en 0 hasta el
// próximo pwm_set. Devuelve la cantidad de pasos, o 0 si está fuera de rango.
uint16_t pwm_set_freq(uint32_t hz);

uint16_t pwm_steps();
float pwm_freq();

// duty en cuentas: 0 = apagado, pwm_steps() = 100 %. Llamar con las
// interrupciones deshabilitadas o desde una ISR (modifica TCCR1A).
void pwm_set(uint16_t counts);
