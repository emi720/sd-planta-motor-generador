#include "pwm.h"

#include <avr/io.h>
#include <util/atomic.h>

static volatile uint16_t steps = 0;

void pwm_init(uint32_t hz) {
    // Pin 9 = PB1 como salida en bajo antes de tocar el timer.
    PORTB &= ~_BV(PORTB1);
    DDRB |= _BV(DDB1);
    pwm_set_freq(hz);
}

uint16_t pwm_set_freq(uint32_t hz) {
    if (hz < PWM_HZ_MIN || hz > PWM_HZ_MAX)
        return 0;
    uint16_t n = (uint16_t)((F_CPU + hz / 2) / hz);
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
        // ICR1 no tiene doble buffer en modo 14: si el TOP nuevo quedara por
        // debajo de TCNT1, el contador daría la vuelta entera (pulso largo).
        // Por eso se cambia con el timer detenido y la salida desconectada.
        TCCR1B = 0;
        TCCR1A = _BV(WGM11);  // modo 14 (WGM13:0 = 1110), COM1A desconectado
        PORTB &= ~_BV(PORTB1);
        ICR1 = n - 1;
        OCR1A = 0;
        TCNT1 = 0;
        steps = n;
        TCCR1B = _BV(WGM13) | _BV(WGM12) | _BV(CS10);
    }
    return n;
}

uint16_t pwm_steps() { return steps; }

float pwm_freq() { return (float)F_CPU / pwm_steps(); }

void pwm_set(uint16_t counts) {
    if (counts == 0) {
        // En Fast PWM, OCR1A = 0 igual deja un pulso de 1 ciclo por período.
        // Para un 0 % real se desconecta el pin del timer.
        TCCR1A &= ~_BV(COM1A1);
        PORTB &= ~_BV(PORTB1);
        return;
    }
    if (counts > steps)
        counts = steps;
    // La salida está en alto durante OCR1A + 1 ciclos de `steps`. OCR1A
    // tiene doble buffer: el cambio se aplica en el próximo BOTTOM, sin glitches.
    OCR1A = counts - 1;
    TCCR1A |= _BV(COM1A1);
}
