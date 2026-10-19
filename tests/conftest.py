import pytest

from gt86_remote_start.clock import FakeClock
from gt86_remote_start.event_logger import EventLogger


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def logger(clock: FakeClock) -> EventLogger:
    return EventLogger(clock)
