from pathlib import Path

import pytest

from gt86_remote_start.config import ConfigError, config_from_dict, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_config_is_valid_and_safe():
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.remote_start.enabled is False
    assert cfg.hardware.mode == "simulation"
    assert cfg.hardware.live_vehicle_outputs_enabled is False
    assert cfg.remote_start.runtime_seconds == 600


@pytest.mark.parametrize(
    "data, fragment",
    [
        ({"hardware": {"live_vehicle_outputs_enabled": True}}, "locked out"),
        ({"safety": {"require_parking_brake": False}}, "cannot be disabled"),
        ({"safety": {"require_neutral_verification": False}}, "cannot be disabled"),
        ({"remote_start": {"automatic_restart_enabled": True}}, "cannot be enabled"),
        ({"remote_start": {"runtime_seconds": 2000}}, "runtime_seconds"),
        ({"remote_start": {"maximum_runtime_seconds": 5000, "runtime_seconds": 600}}, "<="),
        ({"remote_start": {"fob_press_count": 1}}, "fob_press_count"),
        ({"remote_start": {"runtime_seconds": "ten"}}, "expected int"),
        ({"hardware": {"mode": "race"}}, "hardware.mode"),
        ({"remote_start": {"typo_setting": 1}}, "unknown setting"),
    ],
)
def test_invalid_configs_rejected(data, fragment):
    with pytest.raises(ConfigError) as err:
        config_from_dict(data)
    assert any(fragment in p for p in err.value.problems)


def test_int_accepted_for_float():
    cfg = config_from_dict({"remote_start": {"fob_press_window_seconds": 3}})
    assert cfg.remote_start.fob_press_window_seconds == 3.0
