"""Findings of the 08.11 review: automatic shutdown must not get lost or loop.

1. The PC was shut down at critical, the power came back while it was off, a new
   outage started and the PC was turned on: warnings and shutdown must work again.
2. A station stuck on battery (E114) drains like an outage: same warnings, same shutdown.
3. A first battery reading in "grid" state must not erase the "already shut down" mark.
4. A failed read is not evidence: it must not reset the counters.
"""

from bluetti_power_watch import (
    Actions,
    Machine,
    Reading,
    load_machine,
    resume,
    save_machine,
    tick,
)
from test_power_watch import GRID, FakeHost, battery


def stuck(percent):
    return Reading(voltage_in=230.0, watts_in=0, watts_out=300, percent=percent)


def armed(tmp_path):
    host = FakeHost()
    actions = Actions(run=host.run, state_dir=tmp_path, save_brightness=20)
    actions.armed_path.touch()
    return host, actions


def run(machine, actions, readings, told=None):
    told = [] if told is None else told
    for reading in readings:
        tick(machine, reading, actions, told.append)
    return told


def shut_down_in_outage(tmp_path):
    """Outage, critical, automatic shutdown, state saved: the PC is now off."""
    host, actions = armed(tmp_path)
    machine = Machine(critical=8)
    run(machine, actions, [battery(90), battery(90), battery(20), battery(8), battery(8)])
    assert host.powered_off is True
    save_machine(machine, tmp_path / "state.json", battery(8))
    return host


class TestNewOutageAfterShutdown:
    def test_recharged_station_means_new_outage(self, tmp_path):
        host = shut_down_in_outage(tmp_path)
        host.powered_off = False
        _host, actions = armed(tmp_path)
        actions.run = host.run
        machine = load_machine(tmp_path / "state.json", critical=8)
        resume(machine, actions)
        told = run(
            machine, actions, [battery(90), battery(60), battery(20), battery(8), battery(8)]
        )
        assert any("низький" in t for t in told)
        assert host.powered_off is True

    def test_same_outage_tells_to_shut_down_by_hand(self, tmp_path):
        host = shut_down_in_outage(tmp_path)
        host.powered_off = False
        _host, actions = armed(tmp_path)
        actions.run = host.run
        machine = load_machine(tmp_path / "state.json", critical=8)
        told = run(machine, actions, [battery(7), battery(7), battery(7)])
        assert host.powered_off is False
        assert len([t for t in told if "критичний" in t]) == 1
        assert "вже" in [t for t in told if "критичний" in t][0]

    def test_lowest_charge_survives_restart(self, tmp_path):
        machine = Machine()
        machine.state = "outage"
        machine.step(battery(30))
        save_machine(machine, tmp_path / "s.json")
        assert load_machine(tmp_path / "s.json").min_percent == 30

    def test_solar_charge_during_outage_rearms_warnings(self):
        machine = Machine(low=20, critical=8)
        events = []
        for reading in [battery(25), battery(25), battery(20), battery(30), battery(30), battery(20)]:
            events += machine.step(reading)
        assert events == ["outage", "low", "recharged", "low"]

    def test_single_high_reading_is_not_a_recharge(self):
        # One odd reading must not re-arm the once-per-outage shutdown.
        machine = Machine(low=20, critical=8)
        events = []
        for reading in [battery(25), battery(25), battery(20), battery(50), battery(20)]:
            events += machine.step(reading)
        assert events == ["outage", "low"]

    def test_small_jitter_is_not_a_recharge(self):
        machine = Machine(low=20, critical=8)
        events = []
        for reading in [battery(25), battery(25), battery(20), battery(24), battery(24), battery(20)]:
            events += machine.step(reading)
        assert events == ["outage", "low"]


class TestStuckDrainsLikeAnOutage:
    def test_warnings_and_shutdown(self, tmp_path):
        host, actions = armed(tmp_path)
        machine = Machine(low=20, critical=8)
        told = run(machine, actions, [stuck(p) for p in range(40, 2, -1)])
        assert machine.state == "grid"
        assert any("E114" in t for t in told)
        assert any("низький" in t for t in told)
        assert host.powered_off is True

    def test_shutdown_mark_kept_while_stuck(self, tmp_path):
        host, actions = armed(tmp_path)
        machine = Machine(low=20, critical=8)
        run(machine, actions, [stuck(p) for p in range(40, 6, -1)])
        assert host.powered_off is True
        host.powered_off = False
        run(machine, actions, [stuck(6)] * 5)
        assert actions.poweroff_done_path.exists()
        assert host.powered_off is False

    def test_unstuck_clears_the_mark_and_warnings(self, tmp_path):
        host, actions = armed(tmp_path)
        machine = Machine(low=20, critical=8)
        run(machine, actions, [stuck(p) for p in range(40, 6, -1)])
        run(machine, actions, [Reading(215.0, 1100, 350, 7)])
        assert not actions.poweroff_done_path.exists()
        assert machine.low_sent is False and machine.critical_sent is False

    def test_stuck_then_real_outage_keeps_counting(self):
        machine = Machine(low=20, critical=8)
        events = []
        for reading in [stuck(25)] * 5 + [stuck(19), battery(18), battery(18)]:
            events += machine.step(reading)
        assert events == ["stuck", "low", "outage"]

    def test_no_power_saving_restore_while_stuck(self, tmp_path):
        host, actions = armed(tmp_path)
        actions.apply_save()
        machine = Machine()
        run(machine, actions, [stuck(90)] * 6)
        assert host.profile == "power-saver"


class TestMarkKeptOnFirstBatteryReading:
    def test_lost_state_same_outage_does_not_loop(self, tmp_path):
        host, actions = armed(tmp_path)
        actions.poweroff_done_path.touch()
        machine = load_machine(tmp_path / "missing.json", critical=8)
        told = run(machine, actions, [battery(6)] * 4)
        assert actions.poweroff_done_path.exists()
        assert host.powered_off is False
        assert any("критичний" in t for t in told)

    def test_no_data_keeps_the_mark(self, tmp_path):
        _host, actions = armed(tmp_path)
        actions.poweroff_done_path.touch()
        run(Machine(), actions, [None])
        assert actions.poweroff_done_path.exists()


class TestFailedReadsAreNotEvidence:
    def test_alternating_failures_still_reach_critical(self):
        machine = Machine(critical=8)
        events = []
        for reading in [battery(50), battery(50)] + [battery(6), None] * 3:
            events += machine.step(reading)
        assert "critical" in events

    def test_outage_confirmed_across_a_failed_read(self):
        machine = Machine()
        assert machine.step(battery()) == []
        assert machine.step(None) == []
        assert machine.step(battery()) == ["outage"]

    def test_real_grid_reading_still_breaks_the_sequence(self):
        machine = Machine()
        events = []
        for reading in [battery(), GRID, battery()]:
            events += machine.step(reading)
        assert events == []
