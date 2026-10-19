import pytest

from gt86_remote_start.state_machine import (
    ALWAYS_ALLOWED,
    TRANSITIONS,
    InvalidTransition,
    State,
    StateMachine,
)


def test_every_state_has_a_transition_entry():
    assert set(TRANSITIONS) == set(State)


def test_valid_path(clock, logger):
    sm = StateMachine(clock, logger)
    for target in (
        State.INITIALIZING, State.SELF_TEST, State.STANDBY, State.RESERVATION_REQUIRED,
        State.RESERVATION_VALID, State.START_REQUESTED, State.SAFETY_CHECK,
        State.START_AUTHORIZED, State.CRANK_REQUESTED, State.ENGINE_RUNNING,
        State.SHUTDOWN_REQUESTED, State.RESERVATION_REQUIRED,
    ):
        sm.go(target, "test")
    assert len(sm.history) == 12


@pytest.mark.parametrize(
    "source, target",
    [
        (State.SYSTEM_OFF, State.ENGINE_RUNNING),
        (State.STANDBY, State.START_REQUESTED),  # no start without reservation
        (State.RESERVATION_REQUIRED, State.START_REQUESTED),
        (State.RESERVATION_VALID, State.CRANK_REQUESTED),  # cannot skip safety check
        (State.SAFETY_CHECK, State.CRANK_REQUESTED),
        (State.ENGINE_RUNNING, State.RESERVATION_VALID),  # must confirm stop first
        (State.ENGINE_RUNNING, State.CRANK_REQUESTED),  # no restart while running
        (State.SHUTDOWN_REQUESTED, State.START_REQUESTED),  # no automatic retry
        (State.LOCKOUT, State.STANDBY),  # lockout only via service reset
        (State.FAULT, State.RESERVATION_VALID),
    ],
)
def test_forbidden_transitions(clock, logger, source, target):
    sm = StateMachine(clock, logger, initial=source)
    with pytest.raises(InvalidTransition):
        sm.go(target, "test")
    assert sm.state is source
    assert "Rejected state transition" in logger.events()


@pytest.mark.parametrize("source", list(State))
def test_fault_and_lockout_always_reachable(clock, logger, source):
    for target in ALWAYS_ALLOWED:
        sm = StateMachine(clock, logger, initial=source)
        sm.go(target, "fault")
        assert sm.state is target


def test_crank_only_reachable_through_authorization():
    sources = [s for s, targets in TRANSITIONS.items() if State.CRANK_REQUESTED in targets]
    assert sources == [State.START_AUTHORIZED]
    sources = [s for s, targets in TRANSITIONS.items() if State.START_AUTHORIZED in targets]
    assert sources == [State.SAFETY_CHECK]
