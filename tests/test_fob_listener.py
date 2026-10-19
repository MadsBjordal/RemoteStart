import pytest

from gt86_remote_start.config import RemoteStartConfig
from gt86_remote_start.fob_listener import FobCommand, FobListener, LockEvent


@pytest.fixture
def fob(clock, logger):
    return FobListener(RemoteStartConfig(), clock, logger)


def press(fob, clock, command=FobCommand.LOCK, authenticated=True, event_id=None, age=0.0):
    return fob.feed(LockEvent(command, clock.monotonic() - age, authenticated, event_id))


def test_three_presses_within_window_activate(fob, clock):
    assert press(fob, clock) is None
    clock.advance(0.8)
    assert press(fob, clock) is None
    clock.advance(0.8)
    act = press(fob, clock)
    assert act is not None and len(act.press_times) == 3
    assert fob.take_activations() == [act]
    assert fob.take_activations() == []


def test_two_presses_do_not_activate(fob, clock):
    press(fob, clock)
    clock.advance(0.5)
    assert press(fob, clock) is None
    clock.advance(5)
    fob.expire()
    assert fob.presses_in_sequence == 0


def test_window_exceeded_rejects_sequence(fob, clock):
    press(fob, clock)
    clock.advance(1.6)
    press(fob, clock)
    clock.advance(1.6)  # 3.2 s after the first press
    assert press(fob, clock) is None
    assert fob.presses_in_sequence == 1  # the late press starts a new sequence


def test_debounce_ignores_bounces(fob, clock):
    press(fob, clock)
    for _ in range(5):
        clock.advance(0.05)
        assert press(fob, clock) is None
    assert fob.presses_in_sequence == 1


def test_duplicate_event_id_ignored(fob, clock):
    press(fob, clock, event_id="a")
    clock.advance(0.5)
    press(fob, clock, event_id="a")
    clock.advance(0.5)
    assert press(fob, clock, event_id="b") is None
    assert fob.presses_in_sequence == 2


def test_unauthenticated_and_stale_events_ignored(fob, clock):
    press(fob, clock, authenticated=False)
    clock.advance(0.5)
    press(fob, clock, age=5.0)
    assert fob.presses_in_sequence == 0


def test_future_timestamp_rejected(fob, clock):
    assert fob.feed(LockEvent(FobCommand.LOCK, clock.monotonic() + 5, True)) is None
    assert fob.presses_in_sequence == 0


def test_unlock_resets_sequence(fob, clock):
    press(fob, clock)
    clock.advance(0.5)
    press(fob, clock)
    clock.advance(0.5)
    press(fob, clock, command=FobCommand.UNLOCK)
    clock.advance(0.5)
    assert press(fob, clock) is None
    assert fob.presses_in_sequence == 1


def test_one_sequence_triggers_once_and_cooldown(fob, clock):
    for _ in range(3):
        press(fob, clock)
        clock.advance(0.5)
    assert len(fob.take_activations()) == 1
    for _ in range(6):  # more presses right after: cooldown
        press(fob, clock)
        clock.advance(0.5)
    assert fob.take_activations() == []
    clock.advance(10)
    for _ in range(3):
        press(fob, clock)
        clock.advance(0.5)
    assert len(fob.take_activations()) == 1


def test_configurable_count(clock, logger):
    fob = FobListener(RemoteStartConfig(fob_press_count=2, fob_press_window_seconds=2.0), clock, logger)
    press(fob, clock)
    clock.advance(0.5)
    assert press(fob, clock) is not None
