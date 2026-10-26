"""Tests for the CAN-C broadcast frame reference and annotator.

The honesty rule here is the same as everywhere: raw values at documented byte
positions, never a scaled value the source cannot back, and a signal whose bytes
run off the end of the frame is skipped rather than guessed.
"""

from cardiag import canframes


def test_every_signal_fits_inside_its_frame():
    for frame in canframes.frames():
        assert frame.confidence in (canframes.MATCHED, canframes.OBSERVED)
        for signal in frame.signals:
            assert signal.start_byte + signal.length <= frame.dlc, frame.code


def test_a_known_frame_reads_raw_bytes_at_the_signal_position():
    # 0x210 brake pressure is a single byte at index 2.
    ann = canframes.annotate(0x210, bytes([0, 0, 0x55, 0, 0, 0, 0, 0]))
    assert ann.known and not ann.diagnostic
    reading = next(r for r in ann.readings if r.name == "brake_pressure")
    assert reading.raw == 0x55
    assert reading.start_byte == 2


def test_a_signed_signal_reads_negative():
    # 0x236 steering angle is a signed 16-bit value at bytes 0-1.
    ann = canframes.annotate(0x236, bytes([0xFF, 0xFE, 0, 0, 0, 0, 0, 0]))
    reading = next(r for r in ann.readings if r.name == "steering_angle")
    assert reading.signed is True
    assert reading.raw == -2


def test_multi_byte_speed_is_big_endian():
    ann = canframes.annotate(0x200, bytes([0, 0, 0x12, 0x34, 0, 0, 0, 0]))
    first = ann.readings[0]
    assert first.raw == 0x1234          # big-endian, not 0x3412


def test_an_unknown_id_is_reported_as_unknown():
    ann = canframes.annotate(0x999, b"\x00")
    assert ann.known is False
    assert ann.name == "unknown"
    assert ann.readings == []


def test_a_diagnostic_id_is_named_not_decoded():
    ann = canframes.annotate(0x7E8, b"\x41\x0c\x1a\xf8")
    assert ann.diagnostic is True
    assert "PCM" in ann.name


def test_a_signal_running_past_the_data_is_skipped_not_guessed():
    # 0x210's signal is at byte 2, but only two bytes are present.
    ann = canframes.annotate(0x210, b"\x00\x00")
    assert ann.known is True
    assert ann.readings == []           # skipped, not a crash or a zero


def test_the_engine_frame_carries_no_decoded_signal():
    # 0x312 is attributed RPM/torque/throttle but the bytes are not split, so it
    # must not pretend to decode one.
    frame = canframes.get(0x312)
    assert frame is not None
    assert frame.signals == ()
    assert "correlate" in frame.note.lower()
