#pragma once

// Versión informada por PING y STATUS, y registrada en la metadata del CSV.
#define FW_VERSION "0.2.0"

#define SERIAL_BAUD 250000UL

// Duty máximo admitido por cualquier comando, en %. La V_L medida a 100 %
// (7,43 V -> A0 = 3,7 V) no compromete el ADC, así que por defecto no limita.
#define DUTY_MAX_PCT 100.0f

// Muestreo por Timer2.
#define RATE_DEFAULT_HZ 500.0f
#define RATE_MAX_HZ 1000.0f

// Watchdog de comunicación durante el streaming, en segundos (0 = desactivado).
#define WDT_DEFAULT_S 2.0f
#define WDT_MAX_S 60.0f

// Largo máximo de una línea de comando.
#define CMD_MAX_LEN 64
