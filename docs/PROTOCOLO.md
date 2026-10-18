# Protocolo serie y funcionamiento del firmware

Esta guía alcanza para escribir tu propio programa (en Python, MATLAB,
LabVIEW, C#, lo que sea) que hable con el Arduino tal como quedó cargado,
o para modificar el firmware. Un ejemplo completo y probado de ~80 líneas,
que solo usa `pyserial`, está en [`examples/minimo.py`](../examples/minimo.py).

## 1. Conexión

| Parámetro | Valor |
|---|---|
| Velocidad | **250000 baud**, 8N1, sin control de flujo |
| Fin de línea | `\n` (el `\r` se ignora) |
| Al abrir el puerto | El UNO se **resetea** (DTR). Esperar ~2 s o hasta la línea `EVT READY ...` |

¿Por qué 250000? A 16 MHz el divisor de la UART es exacto (error 0 %), y a
1 kHz de muestreo el streaming ocupa el 44 % del enlace. A 115200 baud
ocuparía el 95 % y no quedaría lugar para las respuestas.

## 2. Comandos (PC → Arduino)

Una línea de texto ASCII por comando. Los números pueden tener decimales
(`0.5`); los porcentajes son **% de duty**, de 0 a `DUTY_MAX` (100 por defecto).

| Comando | Qué hace | Rango |
|---|---|---|
| `PING` | Responde `OK PONG planta-fw <versión>` | |
| `STATUS` | Estado completo (ver abajo) | |
| `RATE <Hz>` | Frecuencia de muestreo. Responde con la frecuencia **real** (divisor entero de Timer2) | 62 a 1000 Hz. No durante el streaming |
| `PWMF <Hz>` | Frecuencia del PWM. Responde `OK PWMF <Hz> steps=<n>` | 250 a 20000 Hz. No durante el streaming |
| `DC <pct>` | Duty constante | 0..DUTY_MAX |
| `SQ <f> <duty> <low> <high>` | Cuadrada: frecuencia, ciclo de trabajo (0..100 %), nivel bajo y nivel alto (% de duty). Arranca en el nivel alto | 0 < f ≤ rate/2 |
| `SIN <f> <offset> <amp>` | offset + amp·sin(2πft). Arranca en el offset, subiendo | 0 < f ≤ rate/2, offset − amp ≥ 0, offset + amp ≤ DUTY_MAX |
| `START` | Empieza a mandar muestras. El índice vuelve a 0 | |
| `STOP` | **Duty 0** inmediato y fin del streaming | |
| `WDT <s>` | Tiempo del watchdog de comunicación (0 = desactivado) | 0 a 60 s, 2 por defecto |
| *(línea vacía)* | Keepalive: reinicia el watchdog y **no genera respuesta** | |

Aplicar una onda reinicia su fase. La señal se genera **en el Arduino**: la
PC solo manda los parámetros.

## 3. Respuestas (Arduino → PC)

Cada comando recibe exactamente una línea:

- `OK ...`: aceptado. Ejemplo: `OK SIN f=1.000 offset=60.00 amp=25.00`.
- `ERR <motivo>`: rechazado, **no se cambió nada**. Ejemplo:
  `ERR SIN requiere offset-amp >= 0 y offset+amp <= DUTY_MAX`.

Y además puede llegar en cualquier momento un evento asincrónico:

- `EVT READY planta-fw 0.2.0`: el firmware arrancó (duty 0).
- `EVT WDT sin comandos, duty=0 y streaming cortado`: saltó el watchdog.
- `EVT onda anulada por Nyquist, duty=0`: un `RATE` dejó la onda por encima de rate/2.

`STATUS` devuelve pares `clave=valor`; todo lo que sigue a `wave=` es la onda:

```
OK STATUS fw=0.2.0 rate=500.000 steps=16000 pwm_hz=1000.0 duty_max=100.00 duty=0 streaming=0 lost=0 wdt=2.0 wave=SIN f=1.000 offset=60.00 amp=25.00
```

| Clave | Significado |
|---|---|
| `rate` | Frecuencia de muestreo real (Hz) |
| `steps` | Cuentas de PWM que equivalen a 100 % (16 MHz / f_PWM) |
| `duty` | Duty aplicado en este momento, en cuentas |
| `lost` | Muestras perdidas por desborde del buffer desde el último `START` |

## 4. Tramas de muestras (durante el streaming)

Después de `OK START` llega una trama binaria por muestra, **11 bytes,
little-endian**:

| Byte | Tipo | Campo |
|---|---|---|
| 0 | `0xA5` | sync |
| 1 | `0x5A` | sync |
| 2–5 | uint32 | índice de muestra (0, 1, 2, …) |
| 6–7 | uint16 | duty aplicado en esa muestra, en cuentas (0..`steps`) |
| 8–9 | uint16 | ADC de A0, 0..1023 |
| 10 | uint8 | CRC-8/MAXIM de los bytes 2 a 9 |

Las líneas de texto pueden aparecer **entre** tramas, nunca en el medio de
una, y nunca contienen el byte `0xA5`. Para separarlas:

1. Si el byte es `0xA5` y el siguiente es `0x5A`, tomar 11 bytes y
   verificar el CRC. Si da bien, es una muestra; si no, descartar un byte y
   seguir buscando.
2. Si no, juntar bytes hasta `\n`: si empieza con `OK`, `ERR` o `EVT` es una
   respuesta; si no, es basura (pasa al abrir el puerto a mitad de una trama).

En Python la trama se decodifica con `struct.unpack("<IHH", trama[2:10])`.

**CRC-8/MAXIM** (polinomio 0x31 reflejado = 0x8C, valor inicial 0). Valor de
control: `crc8("123456789") = 0xA1`.

```c
uint8_t crc8(const uint8_t *data, uint8_t len) {
    uint8_t crc = 0;
    while (len--) {
        crc ^= *data++;
        for (uint8_t i = 0; i < 8; i++)
            crc = (crc & 1) ? (crc >> 1) ^ 0x8C : (crc >> 1);
    }
    return crc;
}
```

**Muestras perdidas.** El índice avanza aunque una muestra no se pueda
encolar, así que un salto en el índice indica exactamente cuántas se
perdieron. `STATUS` también las cuenta en `lost`.

## 5. Conversión a unidades físicas

```
t      = índice / rate                      [s]
duty   = cuentas / steps                    [0..1]
V_m    = duty · Vs                          [V]   nominal: sin la caída del L298N
V_L    = divisor · adc · VREF / 1023        [V]   divisor = 2 (10k/10k), VREF ≈ 5 V
```

## 6. Watchdog

Durante el streaming, si pasan más de `WDT` segundos (2 por defecto) sin que
llegue **ninguna** línea, el firmware pone duty 0, corta el streaming y avisa
con `EVT WDT`. Un programa que graba tiene que mandar una línea vacía (`\n`)
cada ~0,5 s. Fuera del streaming el watchdog no actúa: desde el monitor serie
se puede mandar `DC 50` y el motor sigue girando hasta un `STOP`.

## 7. Cómo funciona el firmware (por si querés modificarlo)

Código en `firmware/src/`:

| Archivo | Qué hace |
|---|---|
| `pwm.cpp` | **Timer1** en Fast PWM modo 14 (TOP = ICR1), prescaler 1, salida OC1A = **pin 9**. Duty 0 desconecta el pin del timer (en Fast PWM, OCR1A = 0 igual deja un pulso por período). OCR1A tiene doble buffer, así que el duty cambia sin glitches al empezar el próximo período |
| `sampler.cpp` | **Timer2** en CTC marca cada muestra. Su ISR calcula u(t), actualiza OCR1A y **dispara** el ADC. La ISR del ADC lee el resultado unos 104 µs después y lo guarda en un ring buffer de 64 muestras. No se usa `analogRead` porque espera la conversión bloqueando la ISR |
| `wavegen.cpp` | **DDS**: un acumulador de fase de 32 bits avanza `f·2³²/fs` por muestra. El seno sale de una tabla de 256 valores (`sin_lut.h`, generada con `tools/gen_sin_lut.py`) con interpolación lineal. Todo en enteros, sin `sin()` de punto flotante en la ISR |
| `main.cpp` | Lectura de comandos, validación, envío de tramas desde `loop()` sin bloquear (solo si entran enteras en el buffer de TX) y watchdog |
| `config.h` | `DUTY_MAX_PCT`, baudrate, rate por defecto, watchdog |

Recursos que **no** se tocan: Timer0 (lo usan `millis()` y `delay()`).
Ocupación: ~900 B de RAM de 2 KB y ~10 KB de flash de 32 KB.

**Latencias:** entre el tick de Timer2 y la conversión hay ~104 µs; el duty
nuevo se aplica al comienzo del siguiente período de PWM (≤ 1 ms a 1 kHz).
Cada trama lleva el duty que se escribió en **ese** tick, así que entrada y
salida quedan alineadas en la misma fila.

## 8. Ideas para portar a otro lenguaje

- **MATLAB/Octave**: `serialport(puerto, 250000)`, `writeline` para los
  comandos y `read(s, n, "uint8")` para los bytes, con el mismo algoritmo de
  separación de la sección 4. Para decodificar:
  `typecast(uint8(trama(3:6)), "uint32")`. (No está probado en este repo.)
- **Cualquier lenguaje**: con poder abrir el puerto a 250000 baud, escribir
  líneas y leer bytes alcanza. El orden mínimo es: esperar `EVT READY`,
  `STATUS` (para `rate` y `steps`), la onda, `START`, leer tramas mandando
  `\n` cada 0,5 s, y `STOP`.
