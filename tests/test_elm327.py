"""Response-framing tests: the part most likely to break against real hardware."""

import pytest

from cardiag.elm327 import ELM327, NoDataError, ObdError, parse_response
from cardiag.transport.base import Transport


class FakeTransport(Transport):
    """Replays canned adapter replies, one per command."""

    def __init__(self, replies):
        super().__init__(timeout=1.0)
        self.replies = list(replies)
        self.sent = []

    def open(self):
        pass

    def close(self):
        pass

    def _write_raw(self, data):
        self.sent.append(data.decode().strip())

    def _read_raw(self, timeout):
        if not self.replies:
            return b""
        return (self.replies.pop(0) + "\r>").encode()


def test_single_frame():
    assert parse_response("410C1AF8\r\r>") == [b"\x41\x0c\x1a\xf8"]


def test_spaces_are_tolerated():
    assert parse_response("41 0C 1A F8") == [b"\x41\x0c\x1a\xf8"]


def test_search_noise_is_dropped():
    frames = parse_response("SEARCHING...\r41 00 BE 3F A8 13")
    assert frames == [bytes.fromhex("4100BE3FA813")]


def test_echo_is_dropped_when_known():
    frames = parse_response("010C\r410C1AF8", echo="010C")
    assert frames == [b"\x41\x0c\x1a\xf8"]


def test_multiple_ecus_produce_multiple_frames():
    frames = parse_response("410C1AF8\r410C1B00")
    assert len(frames) == 2


def test_multiline_can_reply_is_reassembled():
    # A mode 09 VIN reply: 49 02 01 then 17 characters, split 6 + 7 + 7.
    raw = "014\r0:490201314847\r1:434D3832363333\r2:41303034333532"
    (frame,) = parse_response(raw)
    assert frame[:3] == b"\x49\x02\x01"
    assert len(frame) == 0x14
    assert frame[3:].decode() == "1HGCM82633A004352"


def test_multiline_tolerates_spaces_inside_segments():
    raw = "014\r0:49 02 01 31 48 47\r1:43 4D 38 32 36 33 33\r2:41 30 30 34 33 35 32"
    (frame,) = parse_response(raw)
    assert frame[3:].decode() == "1HGCM82633A004352"


def test_multiline_without_a_length_line_still_assembles():
    raw = "0:490201314847\r1:434D3832363333"
    (frame,) = parse_response(raw)
    assert len(frame) == 13


def test_multiline_segments_are_ordered_by_index():
    raw = "00D\r1:434D3832363333\r0:490201314847"
    (frame,) = parse_response(raw)
    assert frame[:3] == b"\x49\x02\x01"
    assert frame[3:].decode() == "1HGCM82633"


def test_odd_nibble_is_discarded_rather_than_shifting_bytes():
    # A truncated frame must not turn 41 0C into a byte-shifted value.
    assert parse_response("410C1AF") == [b"\x41\x0c\x1a"]


@pytest.mark.parametrize("reply", ["NO DATA", "UNABLE TO CONNECT", "CAN ERROR", "?"])
def test_error_replies_raise(reply):
    with pytest.raises(ObdError):
        parse_response(reply)


def test_no_data_gets_its_own_exception_type():
    with pytest.raises(NoDataError):
        parse_response("NO DATA")


def test_bus_init_error_is_recognised_with_trailing_text():
    with pytest.raises(ObdError):
        parse_response("BUS INIT: ...ERROR")


def test_request_strips_the_mode_and_pid_echo():
    elm = ELM327(FakeTransport(["410C1AF8"]))
    assert elm.request(0x01, 0x0C) == b"\x1a\xf8"


def test_request_ignores_a_frame_for_a_different_pid():
    elm = ELM327(FakeTransport(["410D40"]))
    with pytest.raises(NoDataError):
        elm.request(0x01, 0x0C)


def test_request_without_a_pid_keeps_the_whole_payload():
    elm = ELM327(FakeTransport(["4302014300"]))
    assert elm.request(0x03) == bytes.fromhex("02014300")


def test_negative_response_is_reported():
    elm = ELM327(FakeTransport(["7F0112"]))
    with pytest.raises(ObdError, match="rejected"):
        elm.request(0x01, 0x0C)


def test_reply_count_hint_is_remembered_and_appended():
    transport = FakeTransport(["410C1AF8", "410C1B00"])
    elm = ELM327(transport)
    elm.request(0x01, 0x0C)
    elm.request(0x01, 0x0C)
    assert transport.sent == ["010C", "010C1"]


def test_clear_codes_requires_an_acknowledgement():
    elm = ELM327(FakeTransport(["44"]))
    elm.clear_codes()

    elm = ELM327(FakeTransport(["NO DATA"]))
    with pytest.raises(ObdError):
        elm.clear_codes()


# ---------------------------------------------------------------------------
# Protocol negotiation and fallback
# ---------------------------------------------------------------------------

class ScriptedAdapter(Transport):
    """An adapter whose auto-detect fails but which works on a named protocol.

    This is the documented behaviour of cheap ELM327 clones against some makes'
    CAN timing: ``ATSP0`` yields CAN ERROR, stating the protocol works fine.
    """

    def __init__(self, working_protocol="6"):
        super().__init__(timeout=1.0)
        self.working_protocol = working_protocol
        self.selected = None
        self.sent = []

    def open(self):
        pass

    def close(self):
        pass

    def _write_raw(self, data):
        self.sent.append(data.decode().strip())

    def _read_raw(self, timeout):
        last = self.sent[-1] if self.sent else ""
        if last.startswith("ATSP"):
            self.selected = last[4:]
            return b"OK\r>"
        if last == "ATDPN":
            return f"{self.selected}\r>".encode()
        if last.startswith("AT"):
            return b"ELM327 v1.5\r>" if last in ("ATZ", "ATI") else b"OK\r>"
        if last.startswith("0100"):
            if self.selected != self.working_protocol:
                return b"CAN ERROR\r>"
            return b"4100BE3FA813\r>"
        return b"NO DATA\r>"

    @property
    def protocols_tried(self):
        return [s[4:] for s in self.sent if s.startswith("ATSP")]


def test_auto_detect_is_tried_first():
    transport = ScriptedAdapter(working_protocol="0")
    info = ELM327(transport).connect()
    assert transport.protocols_tried == ["0"]
    assert info.protocol_id == "0"


def test_falls_back_to_a_named_protocol_when_auto_detect_fails():
    transport = ScriptedAdapter(working_protocol="6")
    info = ELM327(transport).connect()

    assert transport.protocols_tried == ["0", "6"]
    assert info.protocol_id == "6"
    assert "CAN" in info.protocol


def test_a_forced_protocol_skips_auto_detect():
    transport = ScriptedAdapter(working_protocol="6")
    info = ELM327(transport).connect(protocol="6")

    assert transport.protocols_tried == ["6"]
    assert info.protocol_id == "6"


def test_every_attempt_failing_reports_what_was_tried():
    transport = ScriptedAdapter(working_protocol="nothing works")
    with pytest.raises(ObdError) as excinfo:
        ELM327(transport).connect()

    message = str(excinfo.value)
    assert "auto-detect" in message
    assert "protocol 6" in message
    # The advice has to name the protocol worth forcing.
    assert "protocol 6" in message
