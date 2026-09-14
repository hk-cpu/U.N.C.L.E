#include "obd.h"

#include <string.h>

/* Every formula here is transcribed from the vehicle's hex reference and is
 * covered by a test built from that document's own worked examples. If one of
 * these looks wrong, check the test first - it encodes what the car actually
 * replied, which outranks anything remembered about "the standard". */

bool obd_decode_pid(uint8_t pid, const uint8_t *data, size_t len,
                    obd_reading_t *out) {
    out->valid = false;
    out->value = 0.0f;
    out->name = "unknown";
    out->unit = "";

    /* A one-byte PID decoded from a zero-byte payload would read whatever is
     * next in the buffer, so width is checked before anything is touched. */
    size_t need = 1;
    switch (pid) {
    case PID_RPM: case PID_MAF: case PID_RUNTIME: case PID_MODULE_VOLTAGE:
    case PID_ABSOLUTE_LOAD: case PID_FUEL_RATE:
        need = 2;
        break;
    default:
        need = 1;
        break;
    }
    if (len < need) return false;

    const float a = (float)data[0];
    const float b = (need >= 2) ? (float)data[1] : 0.0f;

    switch (pid) {
    case PID_ENGINE_LOAD:
        out->name = "load";        out->unit = "%";
        out->value = a * 100.0f / 255.0f; break;
    case PID_COOLANT_TEMP:
        out->name = "coolant";     out->unit = "degC";
        out->value = a - 40.0f; break;
    case PID_SHORT_TRIM_1:
        out->name = "stft1";       out->unit = "%";
        out->value = (a - 128.0f) * 100.0f / 128.0f; break;
    case PID_LONG_TRIM_1:
        out->name = "ltft1";       out->unit = "%";
        out->value = (a - 128.0f) * 100.0f / 128.0f; break;
    case PID_SHORT_TRIM_2:
        out->name = "stft2";       out->unit = "%";
        out->value = (a - 128.0f) * 100.0f / 128.0f; break;
    case PID_LONG_TRIM_2:
        out->name = "ltft2";       out->unit = "%";
        out->value = (a - 128.0f) * 100.0f / 128.0f; break;
    case PID_MAP:
        out->name = "map";         out->unit = "kPa";
        out->value = a; break;
    case PID_RPM:
        out->name = "rpm";         out->unit = "rpm";
        out->value = ((a * 256.0f) + b) / 4.0f; break;
    case PID_SPEED:
        out->name = "speed";       out->unit = "km/h";
        out->value = a; break;
    case PID_TIMING_ADVANCE:
        out->name = "timing";      out->unit = "deg";
        out->value = (a / 2.0f) - 64.0f; break;
    case PID_INTAKE_TEMP:
        out->name = "intake";      out->unit = "degC";
        out->value = a - 40.0f; break;
    case PID_MAF:
        out->name = "maf";         out->unit = "g/s";
        out->value = ((a * 256.0f) + b) / 100.0f; break;
    case PID_THROTTLE:
        out->name = "throttle";    out->unit = "%";
        out->value = a * 100.0f / 255.0f; break;
    case PID_RUNTIME:
        out->name = "runtime";     out->unit = "s";
        out->value = (a * 256.0f) + b; break;
    case PID_FUEL_LEVEL:
        out->name = "fuel";        out->unit = "%";
        out->value = a * 100.0f / 255.0f; break;
    case PID_BAROMETRIC:
        out->name = "baro";        out->unit = "kPa";
        out->value = a; break;
    case PID_MODULE_VOLTAGE:
        out->name = "volts";       out->unit = "V";
        out->value = ((a * 256.0f) + b) / 1000.0f; break;
    case PID_ABSOLUTE_LOAD:
        out->name = "abs load";    out->unit = "%";
        out->value = ((a * 256.0f) + b) * 100.0f / 255.0f; break;
    case PID_AMBIENT_TEMP:
        out->name = "ambient";     out->unit = "degC";
        out->value = a - 40.0f; break;
    case PID_OIL_TEMP:
        out->name = "oil";         out->unit = "degC";
        out->value = a - 40.0f; break;
    case PID_FUEL_RATE:
        out->name = "fuel rate";   out->unit = "L/h";
        out->value = ((a * 256.0f) + b) / 20.0f; break;
    default:
        return false;
    }

    out->valid = true;
    return true;
}

void obd_decode_dtc(uint8_t hi, uint8_t lo, char out[6]) {
    static const char letters[4] = {'P', 'C', 'B', 'U'};
    static const char digits[] = "0123456789ABCDEF";

    out[0] = letters[(hi >> 6) & 0x03u];
    out[1] = digits[(hi >> 4) & 0x03u];
    out[2] = digits[hi & 0x0Fu];
    out[3] = digits[(lo >> 4) & 0x0Fu];
    out[4] = digits[lo & 0x0Fu];
    out[5] = '\0';
}

size_t obd_decode_dtc_list(const uint8_t *payload, size_t len,
                           char (*out)[6], size_t max) {
    if (len < 1) return 0;

    /* payload[0] is the reply mode (0x43/0x47/0x4A). On CAN a count byte
     * follows it, but not every ECU sends one: if the remaining length is odd,
     * the extra byte is the count and the pairs start after it. */
    size_t index = 1;
    if (((len - index) % 2u) == 1u) index += 1;

    size_t found = 0;
    for (; index + 1 < len && found < max; index += 2) {
        if (payload[index] == 0x00u && payload[index + 1] == 0x00u) {
            continue;   /* padding, not a code */
        }
        obd_decode_dtc(payload[index], payload[index + 1], out[found]);
        found++;
    }
    return found;
}

void obd_isotp_reset(obd_isotp_t *tp) {
    memset(tp, 0, sizeof(*tp));
}

bool obd_isotp_feed(obd_isotp_t *tp, const uint8_t *frame, size_t frame_len) {
    if (frame_len < 2) return false;

    tp->want_flow_control = false;
    const uint8_t kind = (uint8_t)(frame[0] >> 4);

    if (kind == 0x0u) {                       /* single frame */
        size_t length = frame[0] & 0x0Fu;
        if (length == 0 || length > frame_len - 1) return false;
        if (length > OBD_ISOTP_MAX) return false;

        memcpy(tp->buf, frame + 1, length);
        tp->len = length;
        tp->expected = length;
        tp->complete = true;
        return true;
    }

    if (kind == 0x1u) {                       /* first frame */
        size_t length = (size_t)((frame[0] & 0x0Fu) << 8) | frame[1];
        if (length == 0 || length > OBD_ISOTP_MAX) return false;

        size_t take = frame_len - 2;
        if (take > length) take = length;

        memcpy(tp->buf, frame + 2, take);
        tp->len = take;
        tp->expected = length;
        tp->complete = (tp->len >= length);
        tp->next_index = 1;
        /* The ECU sends nothing further until it is told to continue. */
        tp->want_flow_control = !tp->complete;
        return tp->complete;
    }

    if (kind == 0x2u) {                       /* consecutive frame */
        if (tp->expected == 0 || tp->complete) return false;

        /* Sequence runs 1..15 then wraps to 0. A frame that is not the one
         * expected means something was lost; appending it anyway would shift
         * every later byte and produce a plausible-looking wrong answer. */
        if ((frame[0] & 0x0Fu) != tp->next_index) return false;
        tp->next_index = (uint8_t)((tp->next_index + 1u) & 0x0Fu);

        size_t take = frame_len - 1;
        size_t remaining = tp->expected - tp->len;
        if (take > remaining) take = remaining;
        if (tp->len + take > OBD_ISOTP_MAX) return false;

        memcpy(tp->buf + tp->len, frame + 1, take);
        tp->len += take;
        tp->complete = (tp->len >= tp->expected);
        return tp->complete;
    }

    return false;                              /* flow control, or nonsense */
}

size_t obd_build_request(uint8_t mode, uint8_t pid, bool has_pid,
                         uint8_t out[8]) {
    /* Padding is 0x55 rather than 0x00 purely because that is what the
     * reference capture from this car shows on the wire. */
    memset(out, 0x55, 8);
    out[0] = has_pid ? 0x02u : 0x01u;          /* payload length */
    out[1] = mode;
    if (has_pid) out[2] = pid;
    return 8;
}

size_t obd_build_flow_control(uint8_t out[8]) {
    memset(out, 0x55, 8);
    out[0] = 0x30u;   /* clear to send */
    out[1] = 0x00u;   /* no block size limit */
    out[2] = 0x00u;   /* no separation time */
    return 8;
}

bool obd_is_negative(const uint8_t *payload, size_t len) {
    return len >= 1 && payload[0] == 0x7Fu;
}

bool obd_pid_supported(const uint8_t *data, size_t len, uint8_t base,
                       uint8_t pid) {
    if (len < 4) return false;
    if (pid <= base || pid > base + 32) return false;

    const unsigned offset = (unsigned)(pid - base) - 1u;   /* 0..31 */
    const uint8_t byte = data[offset / 8u];
    const uint8_t mask = (uint8_t)(0x80u >> (offset % 8u));
    return (byte & mask) != 0;
}
