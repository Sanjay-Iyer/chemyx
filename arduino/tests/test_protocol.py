import pytest

from arduino.python.errors import ProtocolError
from arduino.python.protocol import bool_field, parse_response


def test_parse_ready_identity():
    result = parse_response("READY device=needle_controller board=uno_r4_minima version=0.1.0")
    assert result.kind == "READY"
    assert result.fields["board"] == "uno_r4_minima"


def test_parse_ack_done_error_and_event():
    assert parse_response("ACK 4 JOG 200 300").sequence == 4
    assert parse_response("DONE 4 position_steps=200").fields["position_steps"] == "200"
    assert parse_response("ERR 5 NOT_HOMED").code == "NOT_HOMED"
    assert parse_response("EVENT LIMIT_UP ACTIVE").kind == "EVENT"


@pytest.mark.parametrize("line", ["", "WHAT 1", "ACK nope PING", "DONE 1 bad==value"])
def test_reject_malformed_response(line):
    with pytest.raises(ProtocolError):
        parse_response(line)


def test_boolean_fields_are_strict():
    assert bool_field({"moving": "false"}, "moving") is False
    with pytest.raises(ProtocolError):
        bool_field({"moving": "perhaps"}, "moving")

