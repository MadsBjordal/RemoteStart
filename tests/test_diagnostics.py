import json

from gt86_remote_start.diagnostics import SERVICE_CLEAR_PHRASE, FaultManager
from gt86_remote_start.event_logger import EventLogger
from gt86_remote_start.fault_codes import FAULT_INFO, FaultCode


def test_every_code_has_info():
    assert set(FAULT_INFO) == set(FaultCode)


def test_latched_fault_survives_restart(tmp_path, clock, logger):
    store = tmp_path / "latched.json"
    fm = FaultManager(clock, logger, persist_path=store)
    fm.raise_fault(FaultCode.VEHICLE_MOVEMENT, "speed 4")
    fm.raise_fault(FaultCode.START_FAILED)
    assert not fm.clear(FaultCode.VEHICLE_MOVEMENT)
    fm2 = FaultManager(clock, EventLogger(clock), persist_path=store)
    assert fm2.is_active(FaultCode.VEHICLE_MOVEMENT)
    assert not fm2.is_active(FaultCode.START_FAILED)
    assert fm2.service_clear("nope") == []
    assert fm2.service_clear(SERVICE_CLEAR_PHRASE)
    assert not FaultManager(clock, logger, persist_path=store).has_latched()


def test_corrupt_fault_store_is_a_latched_fault(tmp_path, clock, logger):
    store = tmp_path / "latched.json"
    store.write_text("{not json")
    assert FaultManager(clock, logger, persist_path=store).has_latched()


def test_logger_buffers_info_and_syncs_warnings(tmp_path, clock):
    path = tmp_path / "events.jsonl"
    log = EventLogger(clock, path, flush_interval_s=30)
    log.log("Factory lock event received")
    assert not path.exists() or path.read_text() == ""
    log.log("Safety controller rejected start", level="warning", reason=FaultCode.HOOD_OPEN)
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert [x["event"] for x in lines] == [
        "Factory lock event received", "Safety controller rejected start"]
    assert lines[1]["reason"] == "HOOD_OPEN"


def test_logger_rotates(tmp_path, clock):
    path = tmp_path / "events.jsonl"
    log = EventLogger(clock, path, max_bytes=500, backup_count=2)
    for i in range(50):
        log.log("x", level="warning", i=i)
    assert path.with_suffix(".1.jsonl").exists()
    assert not path.with_suffix(".3.jsonl").exists()
