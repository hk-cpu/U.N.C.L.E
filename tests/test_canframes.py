"""Tests for the CAN-C broadcast frame reference and annotator.

The honesty rule here is the same as everywhere: raw values at documented byte
positions, never a scaled value the source cannot back, and a signal whose bytes
run off the end of the frame is skipped rather than guessed.
"""

from cardiag import canframes
from cardiag.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr().out


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


def test_byte_attributed_frames_are_present_and_observed():
    # Frames the source attributed to specific bytes are carried; the many bare
    # cyclic frames it could only time are still left out.
    for can_id in (0x328, 0x3D0, 0x3FF, 0x445, 0x608):
        frame = canframes.get(can_id)
        assert frame is not None, f"0x{can_id:03X} missing"
        assert frame.signals, f"0x{can_id:03X} should carry a byte attribution"
        # None were matched to a reference trace, so none may claim MATCHED.
        assert frame.confidence == canframes.OBSERVED


def test_bare_cyclic_frames_stay_out_of_the_map():
    # e.g. 0x358 was only timed ("~37.3 Hz cyclic frame"), never attributed, so
    # listing it would imply knowledge the source does not have.
    assert canframes.get(0x358) is None
    assert canframes.get(0x3FB) is None


def test_the_multi_byte_dynamic_frame_reads_each_attributed_byte():
    ann = canframes.annotate(0x608, bytes([0x11, 0, 0, 0, 0, 0x55, 0x66, 0]))
    raws = {(r.start_byte, r.raw) for r in ann.readings}
    assert raws == {(0, 0x11), (5, 0x55), (6, 0x66)}


# ---------------------------------------------------------------------------
# CAN-B interior-bus reference (separate bus; never merged with CAN-C)
# ---------------------------------------------------------------------------

def test_canb_frames_are_tagged_to_the_interior_bus():
    canb = canframes.canb_frames()
    assert canb, "the CAN-B table should carry entries"
    assert all(f.bus == canframes.CAN_B for f in canb)
    # No scale was verified on this platform, so they stay note-only.
    assert all(f.signals == () for f in canb)


def test_the_two_buses_are_never_merged_despite_shared_ids():
    # 0x210 and 0x3D0 exist on BOTH buses with different meanings. The CAN-C
    # lookup and annotator must only ever see the CAN-C frame.
    assert "brake" in canframes.get(0x210).name.lower()
    assert canframes.CANB_BY_ID[0x210].name == "Instrument-panel dimmer"
    assert canframes.get(0x210).bus == canframes.CAN_C
    # annotate() is CAN-C only, so a 0x210 capture reads brake pressure.
    ann = canframes.annotate(0x210, bytes([0, 0, 0x55, 0, 0, 0, 0, 0]))
    assert any(r.name == "brake_pressure" for r in ann.readings)


def test_canb_frames_list_prints_the_cross_platform_caveat(capsys):
    code, out = run(capsys, "frames")
    assert code == 0
    assert "CAN-B interior-bus frames" in out
    # The caveat must be shown (single words survive line-wrapping).
    assert "Jeep" in out and "hypothesis" in out
    assert "VES" in out                         # a signature CAN-B entry


def test_canb_shows_in_json_separately_from_can_c(capsys):
    import json
    code, out = run(capsys, "--json", "frames")
    payload = json.loads(out)
    assert any(f["id"] == "0x3EC" for f in payload["canb_frames"])
    # 0x3EC must not leak into the CAN-C list.
    assert all(f["id"] != "0x3EC" for f in payload["frames"])
    assert "Jeep" in payload["canb_caveat"]
