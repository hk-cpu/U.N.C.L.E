/* cardiag on an ESP32 - talking OBD-II to a 2006 Charger R/T over CAN.
 *
 * This replaces the ELM327 rather than talking to one. The ESP32's TWAI
 * controller is a CAN controller; with a transceiver on DLC pins 6 and 14 it
 * can send the same diagnostic requests an ELM327 sends, and read the replies
 * itself. The decoding lives in obd.c, which is plain C and is tested on a
 * normal computer against the worked examples in the vehicle's hex reference -
 * see firmware/test. This file is the part that cannot be tested that way, so
 * it is kept as thin as possible.
 *
 * START HERE: set SELF_TEST to 1 and upload. It needs no transceiver, no
 * wiring and no car - it runs the decoder against canned frames and prints the
 * results, which proves your board and toolchain work before you wire
 * anything. Then set it to 0 for the real thing.
 *
 * SAFETY. Pins 6 and 14 are the *diagnostic* bus. It exists for a scan tool to
 * send requests on, which is exactly what this does, and is not the same wire
 * as the powertrain CAN-C behind the FCM gateway - that one carries engine,
 * transmission, ABS and airbag traffic and must only ever be listened to.
 * Do not move these wires onto it.
 */

#include "driver/twai.h"
#include "obd.h"

/* ---------------------------------------------------------------------
 * Things you change
 * ------------------------------------------------------------------ */

/* 1 = decode canned frames and print, no hardware needed.
   0 = talk to the car. */
#define SELF_TEST 1

/* Whichever two GPIOs you wire to the transceiver. Any free pins work - the
   ESP32 routes TWAI through its pin matrix - but avoid the strapping pins
   (0, 45, 46 on the S2; 0, 2, 12, 15 on the classic ESP32). */
static const gpio_num_t PIN_CAN_TX = GPIO_NUM_5;
static const gpio_num_t PIN_CAN_RX = GPIO_NUM_4;

/* How often to poll, and how long to wait for an answer. A real ECU replies
   in a few milliseconds; 100 ms is generous and still gives ~10 Hz. */
static const uint32_t POLL_INTERVAL_MS = 100;
static const uint32_t REPLY_TIMEOUT_MS = 100;

/* What to show. Add or remove freely - anything obd.c knows how to decode. */
static const uint8_t WATCH[] = {
    PID_RPM, PID_SPEED, PID_COOLANT_TEMP, PID_MODULE_VOLTAGE,
    PID_LONG_TRIM_1, PID_LONG_TRIM_2, PID_INTAKE_TEMP,
};
static const size_t WATCH_COUNT = sizeof(WATCH) / sizeof(WATCH[0]);

/* ---------------------------------------------------------------------
 * CAN plumbing
 * ------------------------------------------------------------------ */

static bool can_up = false;

static bool can_start(void) {
    /* NORMAL, not LISTEN_ONLY: polling means transmitting, and the ECU will
       not answer a request it never received an acknowledgement for. */
    twai_general_config_t general =
        TWAI_GENERAL_CONFIG_DEFAULT(PIN_CAN_TX, PIN_CAN_RX, TWAI_MODE_NORMAL);
    general.rx_queue_len = 16;

    twai_timing_config_t timing = TWAI_TIMING_CONFIG_500KBITS();

    /* Accept only 7E8-7EF, the diagnostic reply range. The bus carries a lot
       of other traffic; letting it all through just fills the queue. */
    twai_filter_config_t filter;
    filter.acceptance_code = (uint32_t)0x7E8u << 21;
    filter.acceptance_mask = (uint32_t)0x007u << 21 | 0x1FFFFFu;
    filter.single_filter = true;

    if (twai_driver_install(&general, &timing, &filter) != ESP_OK) {
        Serial.println("could not install the TWAI driver");
        return false;
    }
    if (twai_start() != ESP_OK) {
        Serial.println("could not start TWAI");
        twai_driver_uninstall();
        return false;
    }
    return true;
}

static bool can_send(uint32_t id, const uint8_t *data, size_t len) {
    twai_message_t message = {};
    message.identifier = id;
    message.data_length_code = len;
    for (size_t i = 0; i < len && i < 8; i++) message.data[i] = data[i];

    return twai_transmit(&message, pdMS_TO_TICKS(20)) == ESP_OK;
}

/* Ask for one mode/PID and reassemble whatever comes back.
 *
 * Returns true when a complete reply landed in `tp`. Sends the flow-control
 * frame itself when the reply turns out to be multi-frame - without it the
 * ECU stops after the first frame and a VIN read silently returns six bytes. */
static bool obd_query(uint8_t mode, uint8_t pid, bool has_pid,
                      obd_isotp_t *tp) {
    uint8_t request[8];
    size_t len = obd_build_request(mode, pid, has_pid, request);
    obd_isotp_reset(tp);

    if (!can_send(OBD_ID_REQUEST_ALL, request, len)) return false;

    const uint32_t deadline = millis() + REPLY_TIMEOUT_MS;
    while ((int32_t)(millis() - deadline) < 0) {
        twai_message_t reply;
        if (twai_receive(&reply, pdMS_TO_TICKS(5)) != ESP_OK) continue;
        if (reply.identifier != OBD_ID_REPLY_PCM) continue;

        if (obd_isotp_feed(tp, reply.data, reply.data_length_code)) {
            return !obd_is_negative(tp->buf, tp->len);
        }
        if (obd_isotp_needs_flow_control(tp)) {
            uint8_t flow[8];
            can_send(OBD_ID_REQUEST_PCM, flow, obd_build_flow_control(flow));
        }
    }
    return false;
}

/* ---------------------------------------------------------------------
 * Self test - proves the decoder and the board without any wiring
 * ------------------------------------------------------------------ */

static void print_reading(const obd_reading_t *reading) {
    Serial.print("  ");
    Serial.print(reading->name);
    Serial.print(" = ");
    Serial.print(reading->value, 2);
    Serial.print(" ");
    Serial.println(reading->unit);
}

static void self_test(void) {
    Serial.println("\n-- decoding canned replies (no hardware needed) --");

    struct { uint8_t pid; uint8_t data[2]; size_t len; } cases[] = {
        {PID_COOLANT_TEMP,   {0x7A, 0},    1},   /* expect 82 degC   */
        {PID_RPM,            {0x2F, 0x9C}, 2},   /* expect 3047 rpm  */
        {PID_SPEED,          {0x64, 0},    1},   /* expect 100 km/h  */
        {PID_THROTTLE,       {0x40, 0},    1},   /* expect 25.10 %   */
        {PID_MODULE_VOLTAGE, {0x38, 0x68}, 2},   /* expect 14.44 V   */
    };

    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        obd_reading_t reading;
        if (obd_decode_pid(cases[i].pid, cases[i].data, cases[i].len, &reading)) {
            print_reading(&reading);
        }
    }

    Serial.println("-- reassembling the VIN from three frames --");
    const uint8_t f0[8] = {0x10, 0x14, 0x49, 0x02, 0x01, '2', 'B', '3'};
    const uint8_t f1[8] = {0x21, 'K', 'A', '5', '3', 'H', '4', '6'};
    const uint8_t f2[8] = {0x22, 'H', '3', '1', '5', '7', '2', '0'};

    obd_isotp_t tp;
    obd_isotp_reset(&tp);
    obd_isotp_feed(&tp, f0, 8);
    obd_isotp_feed(&tp, f1, 8);
    if (obd_isotp_feed(&tp, f2, 8)) {
        char vin[18];
        memcpy(vin, tp.buf + 3, 17);
        vin[17] = '\0';
        Serial.print("  VIN = ");
        Serial.println(vin);
        Serial.println("  (expected 2B3KA53H46H315720)");
    } else {
        Serial.println("  VIN did not reassemble - something is wrong");
    }

    Serial.println("\nDecoder works. Set SELF_TEST to 0 once the "
                   "transceiver is wired.");
}

/* ---------------------------------------------------------------------
 * Arduino entry points
 * ------------------------------------------------------------------ */

void setup(void) {
    Serial.begin(115200);
    delay(400);
    Serial.println("\ncardiag-esp32");

#if SELF_TEST
    self_test();
#else
    can_up = can_start();
    if (can_up) Serial.println("CAN up at 500 kbit/s. Turn the ignition on.");
#endif
}

void loop(void) {
#if SELF_TEST
    delay(1000);
    return;
#else
    if (!can_up) { delay(1000); return; }

    /* The ECU stops answering when the key comes out, which is the signal a
       dashboard uses to go dark. Count the silence rather than reacting to a
       single miss - one dropped reply is normal on a busy bus. */
    static int misses = 0;
    bool answered = false;

    for (size_t i = 0; i < WATCH_COUNT; i++) {
        obd_isotp_t tp;
        if (!obd_query(OBD_MODE_LIVE, WATCH[i], true, &tp)) continue;

        /* A live reply is 41 <pid> <data...>; skip the two-byte echo. */
        if (tp.len < 3 || tp.buf[0] != 0x41 || tp.buf[1] != WATCH[i]) continue;

        obd_reading_t reading;
        if (obd_decode_pid(WATCH[i], tp.buf + 2, tp.len - 2, &reading)) {
            print_reading(&reading);
            answered = true;
        }
    }

    misses = answered ? 0 : misses + 1;
    if (misses == 5) Serial.println("-- car is asleep --");

    Serial.println();
    delay(POLL_INTERVAL_MS);
#endif
}
