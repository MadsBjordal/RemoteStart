"""Configuration loading and validation.

The YAML file holds application settings. Several safety properties are NOT
configurable in this release and validation refuses any attempt to relax
them (see ``_validate``). All numeric values here are application proposals,
not validated vehicle safety thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

# Hard-coded release lock. Real ignition/starter outputs cannot be enabled
# from configuration. Changing this constant is a deliberate code change that
# must only follow the validation procedure in docs/installation_plan.md.
LIVE_OUTPUTS_PERMITTED = False

ABSOLUTE_MAX_RUNTIME_S = 1200
HARDWARE_MODES = ("simulation", "bench", "vehicle")


class ConfigError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class RemoteStartConfig:
    enabled: bool = False
    runtime_seconds: int = 600
    maximum_runtime_seconds: int = 900
    fob_press_count: int = 3
    fob_press_window_seconds: float = 3.0
    fob_min_press_interval_seconds: float = 0.25
    fob_stale_event_seconds: float = 1.0
    activation_cooldown_seconds: float = 10.0
    automatic_restart_enabled: bool = False


@dataclass(frozen=True)
class SafetyConfig:
    require_reservation_mode: bool = True
    require_neutral_verification: bool = True
    require_parking_brake: bool = True
    require_hood_closed: bool = True
    require_doors_closed: bool = True
    require_independent_controller: bool = True
    latch_critical_faults: bool = True
    min_start_voltage: float = 12.0
    max_voltage: float = 15.5
    min_running_voltage: float = 12.5
    low_voltage_hold_seconds: float = 5.0
    max_status_age_seconds: float = 0.5
    stationary_speed_kph: float = 0.5


@dataclass(frozen=True)
class EngineConfig:
    running_rpm_min: float = 500.0
    stopped_rpm_max: float = 100.0
    implausible_rpm: float = 9000.0
    start_confirm_seconds: float = 1.0
    signal_timeout_seconds: float = 1.0
    contradiction_seconds: float = 1.0


@dataclass(frozen=True)
class ControllerConfig:
    heartbeat_interval_seconds: float = 0.2
    status_timeout_seconds: float = 1.0
    init_timeout_seconds: float = 5.0
    start_response_timeout_seconds: float = 1.0
    start_validity_ms: int = 3000
    authorized_timeout_seconds: float = 6.0
    crank_confirm_timeout_seconds: float = 8.0
    shutdown_timeout_seconds: float = 10.0
    stop_resend_seconds: float = 0.5
    fault_recovery_seconds: float = 5.0
    serial_port: str = "/dev/serial0"
    baud_rate: int = 115200


@dataclass(frozen=True)
class HardwareConfig:
    mode: str = "simulation"
    live_vehicle_outputs_enabled: bool = False


@dataclass(frozen=True)
class LoggingConfig:
    directory: str = "logs"
    flush_interval_seconds: float = 30.0
    max_file_bytes: int = 1_000_000
    backup_count: int = 5
    echo_to_console: bool = True


@dataclass(frozen=True)
class AppConfig:
    remote_start: RemoteStartConfig = field(default_factory=RemoteStartConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    controller: ControllerConfig = field(default_factory=ControllerConfig)
    hardware: HardwareConfig = field(default_factory=HardwareConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    tick_seconds: float = 0.05


def _build(cls: type, data: Any, path: str, problems: list[str]) -> Any:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        problems.append(f"{path or 'root'}: expected a mapping")
        return cls()
    known = {f.name: f for f in fields(cls)}
    for key in data:
        if key not in known:
            problems.append(f"{path}{key}: unknown setting")
    kwargs: dict[str, Any] = {}
    defaults = cls()
    for name, f in known.items():
        if name not in data:
            continue
        value = data[name]
        default = getattr(defaults, name)
        if is_dataclass(default):
            kwargs[name] = _build(type(default), value, f"{path}{name}.", problems)
            continue
        expected = type(default)
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            problems.append(f"{path}{name}: expected {expected.__name__}, got {value!r}")
            continue
        kwargs[name] = value
    return cls(**kwargs)


def _validate(cfg: AppConfig) -> list[str]:
    p: list[str] = []
    rs, s, hw, c = cfg.remote_start, cfg.safety, cfg.hardware, cfg.controller
    if not 1 <= rs.runtime_seconds <= rs.maximum_runtime_seconds:
        p.append("remote_start.runtime_seconds must be between 1 and maximum_runtime_seconds")
    if rs.maximum_runtime_seconds > ABSOLUTE_MAX_RUNTIME_S:
        p.append(f"remote_start.maximum_runtime_seconds must be <= {ABSOLUTE_MAX_RUNTIME_S}")
    if not 2 <= rs.fob_press_count <= 5:
        p.append("remote_start.fob_press_count must be 2..5")
    if not 1.0 <= rs.fob_press_window_seconds <= 10.0:
        p.append("remote_start.fob_press_window_seconds must be 1..10")
    min_needed = rs.fob_min_press_interval_seconds * (rs.fob_press_count - 1)
    if not 0.05 <= rs.fob_min_press_interval_seconds or min_needed >= rs.fob_press_window_seconds:
        p.append("remote_start.fob_min_press_interval_seconds is too small or too large")
    if rs.activation_cooldown_seconds < rs.fob_press_window_seconds:
        p.append("remote_start.activation_cooldown_seconds must be >= the press window")
    if rs.automatic_restart_enabled:
        p.append("remote_start.automatic_restart_enabled cannot be enabled in this release")
    for name in (
        "require_reservation_mode",
        "require_neutral_verification",
        "require_parking_brake",
        "require_hood_closed",
        "require_doors_closed",
        "require_independent_controller",
        "latch_critical_faults",
    ):
        if not getattr(s, name):
            p.append(f"safety.{name} cannot be disabled")
    if not 10.0 <= s.min_start_voltage < s.max_voltage <= 16.5:
        p.append("safety voltage limits are out of range")
    if hw.mode not in HARDWARE_MODES:
        p.append(f"hardware.mode must be one of {HARDWARE_MODES}")
    if hw.live_vehicle_outputs_enabled and not LIVE_OUTPUTS_PERMITTED:
        p.append(
            "hardware.live_vehicle_outputs_enabled is locked out in this release "
            "(see docs/installation_plan.md)"
        )
    if c.status_timeout_seconds <= 2 * c.heartbeat_interval_seconds:
        p.append("controller.status_timeout_seconds must exceed two heartbeat intervals")
    if not 500 <= c.start_validity_ms <= 10_000:
        p.append("controller.start_validity_ms must be 500..10000")
    if not 0.01 <= cfg.tick_seconds <= 0.2:
        p.append("tick_seconds must be 0.01..0.2")
    return p


def config_from_dict(data: dict[str, Any] | None) -> AppConfig:
    problems: list[str] = []
    cfg = _build(AppConfig, data or {}, "", problems)
    problems += _validate(cfg)
    if problems:
        raise ConfigError(problems)
    return cfg


def load_config(path: str | Path) -> AppConfig:
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError([f"YAML error: {exc}"]) from exc
    return config_from_dict(data)
