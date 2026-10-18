#include "pwm.h"

#include <avr/io.h>

void pwm_init() {
    // Pin 9 = PB1 como salida en bajo antes de tocar el timer.
    PORTB &= ~_BV(PORTB1);
    DDRB |= _BV(DDB1);

    // Modo 14 (WGM13:0 = 1110), prescaler 1. COM1A arranca desconectado:
    // el pin queda en bajo por PORTB hasta que haya un duty distinto de 0.
    TCCR1B = 0;
    TCCR1A = _BV(WGM11);
    ICR1 = PWM_STEPS - 1;
    OCR1A = 0;
    TCNT1 = 0;
    TCCR1B = _BV(WGM13) | _BV(WGM12) | _BV(CS10);
}

void pwm_set(uint16_t counts) {
    if (counts == 0) {
        // En Fast PWM, OCR1A = 0 igual deja un pulso de 1 ciclo por período.
        // Para un 0 % real se desconecta el pin del timer.
        TCCR1A &= ~_BV(COM1A1);
        PORTB &= ~_BV(PORTB1);
        return;
    }
    if (counts > PWM_STEPS)
        counts = PWM_STEPS;
    // La salida está en alto durante OCR1A + 1 ciclos de PWM_STEPS. OCR1A
    // tiene doble buffer: el cambio se aplica en el próximo BOTTOM, sin glitches.
    OCR1A = counts - 1;
    TCCR1A |= _BV(COM1A1);
}
