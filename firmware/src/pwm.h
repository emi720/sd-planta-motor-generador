#pragma once
#include <stdint.h>

// PWM en el pin 9 (OC1A) por Timer1, Fast PWM modo 14 con TOP = ICR1.
// 16 MHz / 4000 = 4 kHz, prescaler 1.
#define PWM_STEPS 4000u
#define PWM_HZ (F_CPU / PWM_STEPS)

void pwm_init();

// duty en cuentas: 0 = apagado, PWM_STEPS = 100 %. Llamar con las
// interrupciones deshabilitadas o desde una ISR (modifica TCCR1A).
void pwm_set(uint16_t counts);
