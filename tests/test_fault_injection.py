import pytest

from simulator.scenarios import SCENARIOS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.__name__)
def test_scenario_reaches_expected_safe_state(scenario):
    result = scenario()
    failed = [d for d, ok in result.checks if not ok]
    assert result.checks, "scenario made no checks"
    assert not failed, f"{result.name}: {failed}\n" + "\n".join(result.log[-40:])
