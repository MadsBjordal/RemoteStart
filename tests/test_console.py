from simulator.console import Console, run_console


def run(console, *lines):
    return [console.handle(line) for line in lines]


def test_every_action_confirms():
    c = Console(echo=False)
    replies = run(c, "door open", "door close", "hood open", "hood close", "brake off",
                  "brake on", "gear 1", "gear n", "battery 12.6", "nostart", "move 0",
                  "unlock", "wait 1")
    assert all(r.startswith("ok:") for r in replies), replies


def test_status_does_not_move_time():
    c = Console(echo=False)
    before = c.clock
    c.handle("status")
    c.handle("log")
    assert c.clock == before


def test_normal_start_sequence():
    c = Console(echo=False)
    run(c, "reserve", "wait 4", "lock")
    assert "pre-crank wait" in c.handle("status")
    c.handle("wait 5")
    status = c.handle("status")
    assert "Pi        ENGINE_RUNNING" in status and "engine    running" in status


def test_nostart_and_move_show_as_injected_until_cleared():
    c = Console(echo=False)
    run(c, "nostart", "move 5")
    status = c.handle("status")
    assert "nostart" in status and "move 5 km/h" in status
    c.handle("move 0")
    assert "move" not in c.handle("status").split("injected")[1]
    assert c.h.world.speed_kph == 0.0


def test_reset_gives_a_fresh_system():
    c = Console(echo=False)
    run(c, "reserve", "wait 4", "lock", "wait 5", "move 5", "wait 2")
    assert "LOCKOUT" in c.handle("status")
    c.handle("reset")
    status = c.handle("status")
    assert "injected  none" in status and "faults    none" in status
    assert c.h.world.engine.will_start


def test_bad_input_is_explained():
    c = Console(echo=False)
    assert "bad value" in c.handle("wait soon")
    assert "use: door" in c.handle("door ajar")
    assert "unknown command" in c.handle("fly")
    assert c.handle("quit") is None


def test_run_console_loop():
    lines = iter(["status", "quit"])
    out = []
    assert run_console(lambda _p: next(lines), out.append) == 0
    assert any("Pi        " in o for o in out)
