/* Native tests for the ESP32 firmware's decode layer.
 *
 * The cases below are taken from the vehicle's own hex reference - its worked
 * examples, its raw-hex/DTC table, and its VIN capture - so a pass here means
 * the firmware agrees with what that car was actually observed to reply, not
 * with my recollection of a standard.
 *
 * Build and run:  make -C firmware/test
 */

#include "../cardiag-esp32/obd.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static int failures = 0;
static int checks = 0;

static void check(bool ok, const char *what) {
    checks++;
    if (!ok) {
        failures++;
        printf("  FAIL  %s\n", what);
    }
}

static void check_near(float got, float want, float tol, const char *what) {
    checks++;
    if (fabsf(got - want) > tol) {
        failures++;
        printf("  FAIL  %s: got %.4f, wanted %.4f\n", what, got, want);
    }
}

static float decode(uint8_t pid, const uint8_t *data, size_t len) {
    obd_reading_t reading;
    if (!obd_decode_pid(pid, data, len, &reading)) return NAN;
    return reading.value;
}

/* ---------------------------------------------------------------------
 * The reference document's worked examples, verbatim
 * ------------------------------------------------------------------ */
static void test_worked_examples(void) {
    printf("worked examples from the hex reference\n");

    /* >0105  ->  41 05 7A  ->  0x7A - 40 = 82 degC */
    const uint8_t coolant[] = {0x7A};
    check_near(decode(PID_COOLANT_TEMP, coolant, 1), 82.0f, 0.01f, "coolant 82 degC");

    /* >010C  ->  41 0C 2F 9C  ->  ((0x2F*256)+0x9C)/4 = 3047 rpm */
    const uint8_t rpm[] = {0x2F, 0x9C};
    check_near(decode(PID_RPM, rpm, 2), 3047.0f, 0.01f, "rpm 3047");

    /* >010D  ->  41 0D 64  ->  100 km/h */
    const uint8_t speed[] = {0x64};
    check_near(decode(PID_SPEED, speed, 1), 100.0f, 0.01f, "speed 100 km/h");

    /* >0111  ->  41 11 40  ->  0x40*100/255 = 25.1 % */
    const uint8_t throttle[] = {0x40};
    check_near(decode(PID_THROTTLE, throttle, 1), 25.098f, 0.01f, "throttle 25.1 %");

    /* >0142  ->  41 42 38 68  ->  14440/1000 = 14.44 V */
    const uint8_t volts[] = {0x38, 0x68};
    check_near(decode(PID_MODULE_VOLTAGE, volts, 2), 14.44f, 0.001f, "module voltage 14.44 V");

    /* The frame in section 1: 41 0C 1A F8 = 1726 rpm */
    const uint8_t rpm2[] = {0x1A, 0xF8};
    check_near(decode(PID_RPM, rpm2, 2), 1726.0f, 0.01f, "rpm 1726");
}

static void test_other_pids(void) {
    printf("remaining mode 01 formulas\n");

    const uint8_t centred[] = {128};        /* the no-correction midpoint */
    check_near(decode(PID_LONG_TRIM_1, centred, 1), 0.0f, 0.01f, "trim 128 is 0 %");

    const uint8_t lean[] = {158};
    check_near(decode(PID_LONG_TRIM_2, lean, 1), 23.4375f, 0.01f, "trim 158 is +23.4 %");

    const uint8_t rich[] = {98};
    check_near(decode(PID_SHORT_TRIM_1, rich, 1), -23.4375f, 0.01f, "trim 98 is -23.4 %");

    const uint8_t oil[] = {0x69};           /* 105 - 40 */
    check_near(decode(PID_OIL_TEMP, oil, 1), 65.0f, 0.01f, "oil 65 degC");

    const uint8_t intake[] = {0x3C};        /* 60 - 40 */
    check_near(decode(PID_INTAKE_TEMP, intake, 1), 20.0f, 0.01f, "intake 20 degC");

    const uint8_t timing[] = {0x80};        /* 128/2 - 64 */
    check_near(decode(PID_TIMING_ADVANCE, timing, 1), 0.0f, 0.01f, "timing 0 deg");

    const uint8_t maf[] = {0x01, 0xF4};     /* 500/100 */
    check_near(decode(PID_MAF, maf, 2), 5.0f, 0.01f, "maf 5.00 g/s");

    obd_reading_t reading;
    check(!obd_decode_pid(0xAB, centred, 1, &reading), "an unknown PID is refused");
    check(!reading.valid, "an unknown PID leaves the reading invalid");
}

static void test_short_payloads_are_refused(void) {
    printf("truncated replies\n");

    obd_reading_t reading;
    const uint8_t one[] = {0x2F};

    /* RPM needs two bytes. Decoding it from one would read past the payload
     * and report a confident, wrong engine speed. */
    check(!obd_decode_pid(PID_RPM, one, 1, &reading), "rpm needs two bytes");
    check(!reading.valid, "a short rpm reply is not valid");
    check(!obd_decode_pid(PID_COOLANT_TEMP, one, 0, &reading), "coolant needs a byte");
}

/* ---------------------------------------------------------------------
 * Trouble codes - the raw-hex table from the reference
 * ------------------------------------------------------------------ */
static void test_dtc_pairs(void) {
    printf("DTC decoding\n");

    struct { uint8_t hi, lo; const char *code; } cases[] = {
        {0x03, 0x00, "P0300"},
        {0x03, 0x01, "P0301"},
        {0x03, 0x08, "P0308"},
        {0x05, 0x20, "P0520"},
        {0x04, 0x20, "P0420"},
        {0x04, 0x30, "P0430"},
        {0x01, 0x33, "P0133"},
        {0x04, 0x56, "P0456"},
        {0x04, 0x06, "P0406"},
        {0x07, 0x00, "P0700"},
        {0x21, 0x10, "P2110"},
        {0xC1, 0x00, "U0100"},
    };

    char out[6];
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        obd_decode_dtc(cases[i].hi, cases[i].lo, out);
        if (strcmp(out, cases[i].code) != 0) {
            printf("  FAIL  %02X %02X decoded as %s, wanted %s\n",
                   cases[i].hi, cases[i].lo, out, cases[i].code);
            failures++;
        }
        checks++;
    }

    /* Each letter prefix, so the two-bit field is not accidentally masked. */
    obd_decode_dtc(0x41, 0x23, out); check(strcmp(out, "C0123") == 0, "C prefix");
    obd_decode_dtc(0x81, 0x23, out); check(strcmp(out, "B0123") == 0, "B prefix");
    obd_decode_dtc(0x11, 0x23, out); check(strcmp(out, "P1123") == 0, "second digit 1");
    obd_decode_dtc(0x31, 0x23, out); check(strcmp(out, "P3123") == 0, "second digit 3");
}

static void test_dtc_list(void) {
    printf("DTC lists\n");

    /* The document's example session:  43 02 05 20 03 00  ->  P0520, P0300 */
    const uint8_t reply[] = {0x43, 0x02, 0x05, 0x20, 0x03, 0x00};
    char codes[8][6];
    size_t found = obd_decode_dtc_list(reply, sizeof(reply), codes, 8);

    check(found == 2, "two codes found");
    if (found == 2) {
        check(strcmp(codes[0], "P0520") == 0, "first code is P0520");
        check(strcmp(codes[1], "P0300") == 0, "second code is P0300");
    }

    /* Trailing 00 00 pairs are padding and must not become "P0000". */
    const uint8_t padded[] = {0x43, 0x01, 0x03, 0x04, 0x00, 0x00, 0x00, 0x00};
    found = obd_decode_dtc_list(padded, sizeof(padded), codes, 8);
    check(found == 1, "padding is not mistaken for a code");
    if (found == 1) check(strcmp(codes[0], "P0304") == 0, "the real code survives");

    /* A car with nothing stored answers 43 00. */
    const uint8_t clean[] = {0x43, 0x00};
    check(obd_decode_dtc_list(clean, sizeof(clean), codes, 8) == 0, "no codes");
}

/* ---------------------------------------------------------------------
 * ISO-TP - the VIN capture from the reference
 * ------------------------------------------------------------------ */
static void test_isotp_single_frame(void) {
    printf("ISO-TP single frame\n");

    obd_isotp_t tp;
    obd_isotp_reset(&tp);

    /* 04 41 0C 1A F8 ... - four payload bytes */
    const uint8_t frame[8] = {0x04, 0x41, 0x0C, 0x1A, 0xF8, 0x55, 0x55, 0x55};
    check(obd_isotp_feed(&tp, frame, 8), "single frame completes at once");
    check(tp.len == 4, "four bytes of payload");
    check(tp.buf[0] == 0x41 && tp.buf[1] == 0x0C, "mode and pid preserved");
    check(!obd_isotp_needs_flow_control(&tp), "no flow control for one frame");
}

static void test_isotp_vin(void) {
    printf("ISO-TP multi frame (the VIN)\n");

    /* Straight from the reference's 0902 capture: a first frame declaring
     * 0x14 bytes and carrying six, then consecutive frames of seven. */
    const uint8_t f0[8] = {0x10, 0x14, 0x49, 0x02, 0x01, '2', 'B', '3'};
    const uint8_t f1[8] = {0x21, 'K', 'A', '5', '3', 'H', '4', '6'};
    const uint8_t f2[8] = {0x22, 'H', '3', '1', '5', '7', '2', '0'};

    obd_isotp_t tp;
    obd_isotp_reset(&tp);

    check(!obd_isotp_feed(&tp, f0, 8), "first frame is not the whole message");
    check(obd_isotp_needs_flow_control(&tp),
          "the ECU waits for flow control before continuing");
    check(tp.expected == 0x14, "declared length is 20 bytes");

    check(!obd_isotp_feed(&tp, f1, 8), "still assembling");
    check(obd_isotp_feed(&tp, f2, 8), "third frame completes it");
    check(tp.len == 0x14, "all 20 bytes gathered");

    /* 49 02 01 then 17 ASCII characters. */
    check(tp.buf[0] == 0x49 && tp.buf[1] == 0x02, "mode 09 pid 02");
    char vin[18];
    memcpy(vin, tp.buf + 3, 17);
    vin[17] = '\0';
    check(strcmp(vin, "2B3KA53H46H315720") == 0, "VIN reads back correctly");
}

static void test_isotp_rejects_out_of_order(void) {
    printf("ISO-TP ordering\n");

    const uint8_t f0[8] = {0x10, 0x14, 0x49, 0x02, 0x01, '2', 'B', '3'};
    const uint8_t f2[8] = {0x22, 'H', '3', '1', '5', '7', '2', '0'};

    obd_isotp_t tp;
    obd_isotp_reset(&tp);
    obd_isotp_feed(&tp, f0, 8);

    /* Frame 2 arriving where frame 1 belongs must be dropped. Appending it
     * would shift every later byte and yield a wrong VIN that looks right. */
    check(!obd_isotp_feed(&tp, f2, 8), "a skipped frame is not accepted");
    check(tp.len == 6, "nothing was appended");
    check(!tp.complete, "the message stays incomplete");
}

static void test_isotp_bounds(void) {
    printf("ISO-TP bounds\n");

    obd_isotp_t tp;
    obd_isotp_reset(&tp);

    /* A single frame claiming more bytes than the frame holds. */
    const uint8_t liar[8] = {0x07, 0x41, 0x0C, 0x55, 0x55, 0x55, 0x55, 0x55};
    check(obd_isotp_feed(&tp, liar, 3) == false, "length beyond the frame is refused");

    obd_isotp_reset(&tp);
    const uint8_t huge[8] = {0x1F, 0xFF, 0x49, 0x02, 0x01, 0x00, 0x00, 0x00};
    check(!obd_isotp_feed(&tp, huge, 8), "a length past the buffer is refused");
    check(tp.expected == 0, "nothing was recorded from it");
}

/* ---------------------------------------------------------------------
 * Requests and bitmaps
 * ------------------------------------------------------------------ */
static void test_requests(void) {
    printf("request framing\n");

    uint8_t frame[8];
    obd_build_request(OBD_MODE_LIVE, PID_RPM, true, frame);
    check(frame[0] == 0x02 && frame[1] == 0x01 && frame[2] == 0x0C,
          "010C is framed as 02 01 0C");

    obd_build_request(OBD_MODE_STORED_DTC, 0, false, frame);
    check(frame[0] == 0x01 && frame[1] == 0x03, "mode 03 carries no pid");

    obd_build_flow_control(frame);
    check(frame[0] == 0x30 && frame[1] == 0x00 && frame[2] == 0x00,
          "flow control is 30 00 00");

    const uint8_t negative[] = {0x7F, 0x22, 0x12};
    check(obd_is_negative(negative, 3), "7F is a negative response");
    const uint8_t positive[] = {0x41, 0x0C, 0x1A, 0xF8};
    check(!obd_is_negative(positive, 4), "41 is not");
}

static void test_supported_bitmap(void) {
    printf("supported-PID bitmap\n");

    /* The reference's 0100 reply: 41 00 BE 3F A8 13 */
    const uint8_t map[] = {0xBE, 0x3F, 0xA8, 0x13};

    /* 0xBE = 1011 1110: bit 7 set means PID 01, bit 6 clear means PID 02. */
    check(obd_pid_supported(map, 4, 0x00, 0x01), "PID 01 supported");
    check(!obd_pid_supported(map, 4, 0x00, 0x02), "PID 02 not supported");
    check(obd_pid_supported(map, 4, 0x00, 0x03), "PID 03 supported");
    check(obd_pid_supported(map, 4, 0x00, 0x05), "PID 05 (coolant) supported");
    check(obd_pid_supported(map, 4, 0x00, 0x0C), "PID 0C (rpm) supported");
    check(obd_pid_supported(map, 4, 0x00, 0x0D), "PID 0D (speed) supported");

    /* Out of the window the bitmap describes. */
    check(!obd_pid_supported(map, 4, 0x00, 0x00), "the base itself is not in range");
    check(!obd_pid_supported(map, 4, 0x00, 0x21), "past the window");
    check(!obd_pid_supported(map, 2, 0x00, 0x01), "a short bitmap is refused");
}

int main(void) {
    test_worked_examples();
    test_other_pids();
    test_short_payloads_are_refused();
    test_dtc_pairs();
    test_dtc_list();
    test_isotp_single_frame();
    test_isotp_vin();
    test_isotp_rejects_out_of_order();
    test_isotp_bounds();
    test_requests();
    test_supported_bitmap();

    printf("\n%d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
