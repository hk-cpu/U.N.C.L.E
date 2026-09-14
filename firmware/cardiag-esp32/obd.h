/* OBD-II decoding for the ESP32, with no dependency on the ESP32.
 *
 * Everything in this header is plain C99 against plain byte buffers: no
 * Arduino, no ESP-IDF, no floats-in-printf tricks. That is deliberate. It means
 * the part of the firmware most likely to be subtly wrong - the arithmetic that
 * turns two hex bytes into a number you trust enough to act on - can be
 * compiled and tested on a normal computer, against the worked examples in the
 * vehicle's own hex reference, before any of it goes near a car.
 *
 * The transport half (TWAI setup, sending requests, timing) cannot be tested
 * that way and is in the sketch, kept as thin as it can be.
 */

#ifndef CARDIAG_OBD_H
#define CARDIAG_OBD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* CAN identifiers for ISO 15765-4, 11-bit, 500 kbit/s - the diagnostic bus on
 * DLC pins 6 and 14. Requests to 7DF are functional (any ECU may answer); 7E0
 * addresses the powertrain module directly and 7E8 is its reply. */
#define OBD_ID_REQUEST_ALL 0x7DFu
#define OBD_ID_REQUEST_PCM 0x7E0u
#define OBD_ID_REPLY_PCM   0x7E8u
#define OBD_ID_REPLY_TCM   0x7E9u

/* Modes. A positive reply is the request mode plus 0x40. */
#define OBD_MODE_LIVE       0x01u
#define OBD_MODE_FREEZE     0x02u
#define OBD_MODE_STORED_DTC 0x03u
#define OBD_MODE_PENDING    0x07u
#define OBD_MODE_VEHICLE    0x09u

/* PIDs this firmware knows how to decode. */
#define PID_SUPPORTED_01_20 0x00u
#define PID_MONITOR_STATUS  0x01u
#define PID_ENGINE_LOAD     0x04u
#define PID_COOLANT_TEMP    0x05u
#define PID_SHORT_TRIM_1    0x06u
#define PID_LONG_TRIM_1     0x07u
#define PID_SHORT_TRIM_2    0x08u
#define PID_LONG_TRIM_2     0x09u
#define PID_MAP             0x0Bu
#define PID_RPM             0x0Cu
#define PID_SPEED           0x0Du
#define PID_TIMING_ADVANCE  0x0Eu
#define PID_INTAKE_TEMP     0x0Fu
#define PID_MAF             0x10u
#define PID_THROTTLE        0x11u
#define PID_RUNTIME         0x1Fu
#define PID_FUEL_LEVEL      0x2Fu
#define PID_BAROMETRIC      0x33u
#define PID_MODULE_VOLTAGE  0x42u
#define PID_ABSOLUTE_LOAD   0x43u
#define PID_AMBIENT_TEMP    0x46u
#define PID_OIL_TEMP        0x5Cu
#define PID_FUEL_RATE       0x5Eu

typedef struct {
    const char *name;
    const char *unit;
    float value;
    bool valid;
} obd_reading_t;

/* Decode the data bytes that follow "41 <pid>" in a reply.
 *
 * `data` is the payload only - the caller strips the 0x41 and the PID echo.
 * Returns false for a PID this build does not know, or for a payload too short
 * to hold the value, leaving `out->valid` false. A short payload is never
 * decoded from whatever happens to sit after it in the buffer. */
bool obd_decode_pid(uint8_t pid, const uint8_t *data, size_t len,
                    obd_reading_t *out);

/* Format a DTC pair into "P0301" and a NUL. `out` must hold 6 bytes.
 *
 * Byte 1 bits 7-6 give the letter, bits 5-4 the second character, and the low
 * nibble the third; byte 2 supplies the last two as hex nibbles. */
void obd_decode_dtc(uint8_t hi, uint8_t lo, char out[6]);

/* Pull the DTCs out of a reassembled mode 03/07/0A reply.
 *
 * `payload` starts at the reply mode byte (0x43). Writes up to `max` codes of
 * 6 bytes each into `out` and returns how many it wrote. All-zero pairs are
 * padding, not codes, and are skipped. */
size_t obd_decode_dtc_list(const uint8_t *payload, size_t len,
                           char (*out)[6], size_t max);

/* ---- ISO-TP reassembly -------------------------------------------------
 *
 * Anything longer than seven bytes - the VIN, the calibration ID - arrives as
 * a first frame carrying the total length and six bytes, then consecutive
 * frames of seven. The ECU will not send the consecutive frames until it gets
 * a flow-control frame back, which is why obd_isotp_needs_flow_control()
 * exists and why the sketch must act on it.
 */

#define OBD_ISOTP_MAX 128

typedef struct {
    uint8_t buf[OBD_ISOTP_MAX];
    size_t len;        /* bytes gathered so far */
    size_t expected;   /* total the first frame promised, 0 until known */
    bool complete;
    bool want_flow_control;
    uint8_t next_index; /* sequence number the next consecutive frame must use */
} obd_isotp_t;

void obd_isotp_reset(obd_isotp_t *tp);

/* Feed one 8-byte CAN frame. Returns true when the message is complete.
 *
 * Out-of-order or duplicated consecutive frames are dropped rather than
 * appended: a VIN assembled from frames in the wrong order is worse than no
 * VIN, because it looks like a real answer. */
bool obd_isotp_feed(obd_isotp_t *tp, const uint8_t *frame, size_t frame_len);

static inline bool obd_isotp_needs_flow_control(const obd_isotp_t *tp) {
    return tp->want_flow_control;
}

/* Build the 8-byte request for a mode/PID. Returns the length to send. */
size_t obd_build_request(uint8_t mode, uint8_t pid, bool has_pid,
                         uint8_t out[8]);

/* Build the flow-control frame that unblocks a multi-frame reply. */
size_t obd_build_flow_control(uint8_t out[8]);

/* True when a reply is a negative response (7F) rather than data. */
bool obd_is_negative(const uint8_t *payload, size_t len);

/* Read the supported-PID bitmap that answers 0100/0120/0140.
 *
 * `data` is the four bytes after "41 <base>". Bit 7 of the first byte means
 * base+1 is supported, counting down through to base+32. */
bool obd_pid_supported(const uint8_t *data, size_t len, uint8_t base,
                       uint8_t pid);

#endif /* CARDIAG_OBD_H */
