import pytest

from gt86_remote_start.main import cli


def test_check_config(capsys):
    assert cli(["check-config", "--config", "config.yaml"]) == 0
    assert "Configuration OK" in capsys.readouterr().out


def test_bad_config_exits(tmp_path):
    bad = tmp_path / "c.yaml"
    bad.write_text("hardware:\n  live_vehicle_outputs_enabled: true\n")
    with pytest.raises(SystemExit) as e:
        cli(["check-config", "--config", str(bad)])
    assert e.value.code == 2


def test_vehicle_mode_is_locked_out(tmp_path, capsys):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("hardware:\n  mode: vehicle\n")
    assert cli(["run", "--config", str(cfg)]) == 4
    assert "LIVE_OUTPUTS_LOCKED" in capsys.readouterr().err


def test_simulate_one_scenario():
    assert cli(["simulate", "2"]) == 0
