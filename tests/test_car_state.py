"""Ignition detection: what a dash-mounted screen follows.

The distinction that carries the weight here is between a car that is off and
an adapter that is gone. Both leave the ECU silent; only one is normal. Getting
it wrong means either a phone that never sleeps in a parked car, or a dashboard
that quietly shows nothing when the adapter falls out.
"""

import pytest

from cardiag.session import (CAR_ASLEEP, CAR_IGNITION, CAR_RUNNING,
                             CAR_UNREACHABLE, CarState, Session)
from cardiag.web.service import ServiceError, VehicleService


@pytest.fixture
def car():
    with Session("sim://?profile=charger") as session:
        session.connect()
        yield session


def set_key(car, *, ignition, running):
    car.transport.ignition = ignition
    car.transport.engine_running = running


# ---------------------------------------------------------------------------
# The three states the key puts the car in
# ---------------------------------------------------------------------------

def test_a_running_engine_is_reported_as_running(car):
    set_key(car, ignition=True, running=True)
    state = car.car_state()

    assert state.state == CAR_RUNNING
    assert state.awake is True
    assert state.rpm > 0
    assert state.voltage > 13.0, "the alternator should be charging"


def test_key_on_engine_off_is_awake_but_not_running(car):
    set_key(car, ignition=True, running=False)
    state = car.car_state()

    assert state.state == CAR_IGNITION
    assert state.awake is True
    assert state.rpm == 0


def test_key_out_is_asleep_not_unreachable(car):
    """The adapter is on pin 16, which is live with the key out.

    So a parked car still reports a voltage while answering no PID at all.
    Reading that as a missing adapter would put an error on a dashboard every
    single time the driver walks away from the car.
    """
    set_key(car, ignition=False, running=False)
    state = car.car_state()

    assert state.state == CAR_ASLEEP
    assert state.awake is False
    assert state.voltage is not None, "the adapter still answers ATRV"
    assert "car is off" in state.detail.lower()


def test_a_silent_adapter_is_unreachable_rather_than_asleep(car):
    set_key(car, ignition=False, running=False)

    # Nothing answers at all, the adapter included.
    car.elm.read_voltage = lambda: None
    state = car.car_state()

    assert state.state == CAR_UNREACHABLE
    assert state.awake is False
    assert "adapter" in state.detail.lower()


def test_the_alternator_breaks_the_tie_when_rpm_reads_zero(car):
    """Some ECUs report 0 rpm for a moment after a start.

    Charging voltage means something is turning the engine regardless, so the
    dashboard should not blink back to "ignition" mid-drive.
    """
    state = CarState(CAR_RUNNING, rpm=0.0, voltage=14.2)
    assert state.awake is True

    set_key(car, ignition=True, running=False)
    car.elm.read_voltage = lambda: 14.2
    assert car.car_state().state == CAR_RUNNING


def test_states_serialise_for_the_dashboard(car):
    import json
    json.dumps(car.car_state().to_dict())


def test_asleep_is_cheap_enough_to_poll(car):
    """Parked polling must not walk the whole PID set."""
    set_key(car, ignition=False, running=False)
    sent = []
    original = car.transport.write_line
    car.transport.write_line = lambda line: (sent.append(line), original(line))[1]

    car.car_state()
    assert len(sent) <= 4, f"too chatty for a parked poll: {sent}"


# ---------------------------------------------------------------------------
# Through the service, which is what the dashboard actually calls
# ---------------------------------------------------------------------------

@pytest.fixture
def service():
    svc = VehicleService()
    yield svc
    svc.disconnect()


def test_the_service_reports_the_car_state(service):
    service.connect("sim://?profile=charger")
    payload = service.car_state()

    assert payload["state"] == CAR_RUNNING
    assert payload["awake"] is True
    assert payload["detail"]


def test_a_sleeping_car_is_not_an_error(service):
    """Cockpit mode polls this while parked; it must answer, not raise."""
    service.connect("sim://?profile=charger")
    service._session.transport.ignition = False

    payload = service.car_state()
    assert payload["state"] == CAR_ASLEEP
    assert payload["awake"] is False


def test_car_state_needs_a_connection(service):
    with pytest.raises(ServiceError, match="not connected"):
        service.car_state()


def test_reconnect_reopens_the_same_adapter_and_profile(service):
    service.connect("sim://?profile=charger")
    assert service.status()["profile_key"] == "charger-rt-2006"

    status = service.reconnect()
    assert status["connected"] is True
    assert status["url"] == "sim://?profile=charger"
    # Turning the key must not lose the model-specific knowledge.
    assert status["profile_key"] == "charger-rt-2006"


def test_reconnect_without_a_previous_connection_is_refused(service):
    with pytest.raises(ServiceError, match="no previous connection"):
        service.reconnect()
