// Firmware de adquisición para la planta motor-generador DC.
// Comandos ASCII por línea (ver README); streaming binario (ver protocol.h).

#include <Arduino.h>
#include <stdlib.h>
#include <string.h>
#include <util/atomic.h>

#include "config.h"
#include "protocol.h"
#include "pwm.h"
#include "sampler.h"
#include "wavegen.h"

#define DUTY_MAX_COUNTS ((uint16_t)(DUTY_MAX_PCT * PWM_STEPS / 100.0f + 0.5f))

// Último comando de forma de onda, en las unidades del usuario. Se guarda
// para recalcular el incremento de fase si cambia la frecuencia de muestreo.
struct WaveCmd {
    uint8_t type;
    float f;
    float p1, p2, p3;  // DC: pct   SQ: duty, low, high   SIN: offset, amp
};

static WaveCmd wave = {WAVE_DC, 0, 0, 0, 0};
static float wdt_s = WDT_DEFAULT_S;
static uint32_t last_rx_ms = 0;

static char line[CMD_MAX_LEN + 1];
static uint8_t line_len = 0;
static bool line_overflow = false;

static uint16_t pct_to_counts(float pct) {
    return (uint16_t)(pct * PWM_STEPS / 100.0f + 0.5f);
}

static uint32_t phase_inc(float f, float fs) {
    return (uint32_t)(f / fs * 4294967296.0f + 0.5f);
}

static void apply_wave(const WaveCmd &c) {
    WaveParams p = {c.type, 0, 0, 0, 0};
    float fs = sampler_rate();
    switch (c.type) {
    case WAVE_SQ:
        p.inc = phase_inc(c.f, fs);
        // 100 % no entra en 32 bits como umbral: se satura.
        p.thr = (c.p1 >= 100.0f) ? 0xFFFFFFFFUL
                                 : (uint32_t)(c.p1 / 100.0f * 4294967296.0f);
        p.a = pct_to_counts(c.p2);
        p.b = pct_to_counts(c.p3);
        break;
    case WAVE_SIN:
        p.inc = phase_inc(c.f, fs);
        p.a = pct_to_counts(c.p1);
        p.b = pct_to_counts(c.p2);
        break;
    default:
        p.a = pct_to_counts(c.p1);
        break;
    }
    wavegen_set(p);
    wave = c;
}

// Duty 0 inmediato, sin esperar al próximo tick.
static void stop_all() {
    sampler_stream(false);
    WaveCmd c = {WAVE_DC, 0, 0, 0, 0};
    apply_wave(c);
    ATOMIC_BLOCK(ATOMIC_RESTORESTATE) { pwm_set(0); }
}

// ---- Parseo de argumentos -------------------------------------------------

static bool parse_float(const char *tok, float *out) {
    if (!tok)
        return false;
    char *end;
    double v = strtod(tok, &end);
    if (end == tok || *end != '\0')
        return false;
    *out = (float)v;
    return true;
}

// Lee exactamente n números del resto de la línea.
static bool parse_args(float *v, uint8_t n) {
    for (uint8_t i = 0; i < n; i++)
        if (!parse_float(strtok(NULL, " \t"), &v[i]))
            return false;
    return strtok(NULL, " \t") == NULL;
}

static bool duty_ok(float pct) { return pct >= 0 && pct <= DUTY_MAX_PCT; }

static bool freq_ok(float f) { return f > 0 && f <= sampler_rate() / 2; }

// ---- Respuestas -----------------------------------------------------------

static void print_wave() {
    switch (wave.type) {
    case WAVE_SQ:
        Serial.print(F("SQ f="));
        Serial.print(wave.f, 3);
        Serial.print(F(" duty="));
        Serial.print(wave.p1, 2);
        Serial.print(F(" low="));
        Serial.print(wave.p2, 2);
        Serial.print(F(" high="));
        Serial.print(wave.p3, 2);
        break;
    case WAVE_SIN:
        Serial.print(F("SIN f="));
        Serial.print(wave.f, 3);
        Serial.print(F(" offset="));
        Serial.print(wave.p1, 2);
        Serial.print(F(" amp="));
        Serial.print(wave.p2, 2);
        break;
    default:
        Serial.print(F("DC pct="));
        Serial.print(wave.p1, 2);
        break;
    }
}

static void print_status() {
    Serial.print(F("OK STATUS fw=" FW_VERSION " rate="));
    Serial.print(sampler_rate(), 3);
    Serial.print(F(" steps="));
    Serial.print(PWM_STEPS);
    Serial.print(F(" pwm_hz="));
    Serial.print(PWM_HZ);
    Serial.print(F(" duty_max="));
    Serial.print(DUTY_MAX_PCT, 2);
    Serial.print(F(" duty="));
    Serial.print(sampler_duty());
    Serial.print(F(" streaming="));
    Serial.print(sampler_streaming() ? 1 : 0);
    Serial.print(F(" lost="));
    Serial.print(sampler_lost());
    Serial.print(F(" wdt="));
    Serial.print(wdt_s, 1);
    Serial.print(F(" wave="));
    print_wave();
    Serial.print('\n');
}

static void err(const __FlashStringHelper *msg) {
    Serial.print(F("ERR "));
    Serial.print(msg);
    Serial.print('\n');
}

// ---- Comandos -------------------------------------------------------------

static void handle_line(char *s) {
    char *cmd = strtok(s, " \t");
    if (!cmd)
        return;  // línea vacía: sirve igual como keepalive
    float v[4];

    if (!strcmp(cmd, "PING")) {
        if (!parse_args(v, 0))
            return err(F("uso: PING"));
        Serial.print(F("OK PONG planta-fw " FW_VERSION "\n"));

    } else if (!strcmp(cmd, "STATUS")) {
        if (!parse_args(v, 0))
            return err(F("uso: STATUS"));
        print_status();

    } else if (!strcmp(cmd, "RATE")) {
        if (!parse_args(v, 1))
            return err(F("uso: RATE <Hz>"));
        if (sampler_streaming())
            return err(F("RATE no se puede cambiar durante el streaming"));
        if (v[0] > RATE_MAX_HZ || sampler_set_rate(v[0]) == 0)
            return err(F("RATE fuera de rango (62..1000 Hz)"));
        // Si la onda actual ya no respeta Nyquist, se vuelve a duty 0.
        if (wave.type != WAVE_DC && !freq_ok(wave.f)) {
            stop_all();
            Serial.print(F("EVT onda anulada por Nyquist, duty=0\n"));
        } else {
            apply_wave(wave);
        }
        Serial.print(F("OK RATE "));
        Serial.print(sampler_rate(), 3);
        Serial.print('\n');

    } else if (!strcmp(cmd, "DC")) {
        if (!parse_args(v, 1))
            return err(F("uso: DC <pct>"));
        if (!duty_ok(v[0]))
            return err(F("DC fuera de rango (0..DUTY_MAX)"));
        WaveCmd c = {WAVE_DC, 0, v[0], 0, 0};
        apply_wave(c);
        Serial.print(F("OK "));
        print_wave();
        Serial.print('\n');

    } else if (!strcmp(cmd, "SQ")) {
        if (!parse_args(v, 4))
            return err(F("uso: SQ <f_Hz> <duty_pct> <low_pct> <high_pct>"));
        if (!freq_ok(v[0]))
            return err(F("SQ frecuencia fuera de rango (0..rate/2)"));
        if (v[1] < 0 || v[1] > 100)
            return err(F("SQ ciclo de trabajo fuera de rango (0..100)"));
        if (!duty_ok(v[2]) || !duty_ok(v[3]))
            return err(F("SQ niveles fuera de rango (0..DUTY_MAX)"));
        WaveCmd c = {WAVE_SQ, v[0], v[1], v[2], v[3]};
        apply_wave(c);
        Serial.print(F("OK "));
        print_wave();
        Serial.print('\n');

    } else if (!strcmp(cmd, "SIN")) {
        if (!parse_args(v, 3))
            return err(F("uso: SIN <f_Hz> <offset_pct> <amp_pct>"));
        if (!freq_ok(v[0]))
            return err(F("SIN frecuencia fuera de rango (0..rate/2)"));
        if (v[2] < 0 || v[1] - v[2] < 0 || v[1] + v[2] > DUTY_MAX_PCT)
            return err(F("SIN requiere offset-amp >= 0 y offset+amp <= DUTY_MAX"));
        WaveCmd c = {WAVE_SIN, v[0], v[1], v[2], 0};
        apply_wave(c);
        Serial.print(F("OK "));
        print_wave();
        Serial.print('\n');

    } else if (!strcmp(cmd, "START")) {
        if (!parse_args(v, 0))
            return err(F("uso: START"));
        sampler_stream(true);
        Serial.print(F("OK START\n"));

    } else if (!strcmp(cmd, "STOP")) {
        if (!parse_args(v, 0))
            return err(F("uso: STOP"));
        stop_all();
        Serial.print(F("OK STOP\n"));

    } else if (!strcmp(cmd, "WDT")) {
        if (!parse_args(v, 1))
            return err(F("uso: WDT <s>"));
        if (v[0] < 0 || v[0] > WDT_MAX_S)
            return err(F("WDT fuera de rango (0..60 s, 0 = desactivado)"));
        wdt_s = v[0];
        Serial.print(F("OK WDT "));
        Serial.print(wdt_s, 1);
        Serial.print('\n');

    } else {
        err(F("comando desconocido"));
    }
}

static void read_serial() {
    while (Serial.available()) {
        char ch = Serial.read();
        if (ch == '\r')
            continue;
        if (ch == '\n') {
            last_rx_ms = millis();
            if (line_overflow) {
                err(F("linea demasiado larga"));
            } else {
                line[line_len] = '\0';
                handle_line(line);
            }
            line_len = 0;
            line_overflow = false;
        } else if (line_len < CMD_MAX_LEN) {
            line[line_len++] = ch;
        } else {
            line_overflow = true;
        }
    }
}

static void send_samples() {
    Sample s;
    uint8_t f[FRAME_LEN];
    f[0] = FRAME_SYNC0;
    f[1] = FRAME_SYNC1;
    // Sin bloquear: solo se escribe si la trama entra entera en el buffer TX.
    while (Serial.availableForWrite() >= FRAME_LEN && sampler_pop(&s)) {
        memcpy(&f[2], &s.idx, 4);  // AVR es little-endian
        memcpy(&f[6], &s.duty, 2);
        memcpy(&f[8], &s.adc, 2);
        f[10] = crc8(&f[2], 8);
        Serial.write(f, FRAME_LEN);
    }
}

static void check_watchdog() {
    if (!sampler_streaming() || wdt_s <= 0)
        return;
    if (millis() - last_rx_ms > (uint32_t)(wdt_s * 1000.0f)) {
        stop_all();
        Serial.print(F("EVT WDT sin comandos, duty=0 y streaming cortado\n"));
    }
}

void setup() {
    // Seguridad primero: pin 9 en bajo antes que nada.
    pwm_init();
    Serial.begin(SERIAL_BAUD);
    sampler_init(RATE_DEFAULT_HZ);
    stop_all();
    last_rx_ms = millis();
    Serial.print(F("EVT READY planta-fw " FW_VERSION "\n"));
}

void loop() {
    read_serial();
    send_samples();
    check_watchdog();
}
