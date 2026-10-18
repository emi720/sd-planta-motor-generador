#pragma once
#include <stdint.h>

// Trama binaria de una muestra (11 bytes, little-endian):
//   [0]    0xA5        sync
//   [1]    0x5A        sync
//   [2:6]  uint32      índice de muestra (arranca en 0 con START)
//   [6:8]  uint16      duty aplicado, en cuentas (0..PWM_STEPS)
//   [8:10] uint16      lectura cruda del ADC (0..1023)
//   [10]   uint8       CRC-8/MAXIM de los bytes [2:10]
//
// Las respuestas de texto empiezan con "OK", "ERR" o "EVT" y terminan en '\n'.
// Nunca contienen 0xA5, así que el host las separa de las tramas por el sync.

#define FRAME_SYNC0 0xA5
#define FRAME_SYNC1 0x5A
#define FRAME_LEN 11

// CRC-8/MAXIM (Dallas): polinomio 0x31 reflejado (0x8C), valor inicial 0.
// Valor de control: crc8("123456789") = 0xA1.
static inline uint8_t crc8(const uint8_t *data, uint8_t len) {
    uint8_t crc = 0;
    while (len--) {
        crc ^= *data++;
        for (uint8_t i = 0; i < 8; i++)
            crc = (crc & 1) ? (crc >> 1) ^ 0x8C : (crc >> 1);
    }
    return crc;
}
