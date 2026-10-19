"""Factory immobilizer integration point.

The factory immobilizer is never defeated, cloned or emulated by this
project. A remote start can only be authorised by a dedicated, commercially
supported immobilizer data interface (for the 2013 FR-S regular-key car,
Fortin documents the EVO-ONE with the TOY3 T-harness; see
docs/vehicle_research.md). Such a module is programmed by its own procedure
and only grants authorisation while it is in its remote-start mode.

The Pi only reads whether such an interface reports itself ready. If none is
fitted, the answer is NOT_PRESENT and starting is impossible: that is a
hardware blocker, not something software works around.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum


class ImmobilizerStatus(Enum):
    UNKNOWN = "unknown"
    NOT_PRESENT = "not_present"
    READY = "ready"
    FAULT = "fault"


class ImmobilizerInterface(ABC):
    @abstractmethod
    async def status(self) -> ImmobilizerStatus: ...


class NoImmobilizerInterface(ImmobilizerInterface):
    """Default: no authorised interface fitted, so starting is blocked."""

    async def status(self) -> ImmobilizerStatus:
        return ImmobilizerStatus.NOT_PRESENT


class StaticImmobilizerInterface(ImmobilizerInterface):
    """Reports a fixed status (simulation and bench tests)."""

    def __init__(self, value: ImmobilizerStatus = ImmobilizerStatus.READY) -> None:
        self.value = value

    async def status(self) -> ImmobilizerStatus:
        return self.value
