"""Сценарії із зауважень рев'ю: кожен тут падав би на першій версії сторожа."""

from datetime import datetime

from bluetti_power_watch import Actions, Machine, cycle, handle_events, load_machine, save_machine
from test_power_watch import GRID, FakeHost, battery, feed, in_outage

NOW = datetime(2026, 11, 3, 21, 5, 0)


def make(tmp_path):
    host = FakeHost()
    return host, Actions(run=host.run, state_dir=tmp_path, save_brightness=20)


def quiet(_text):
    return None


class TestPoweroffRetry:
    def test_failed_poweroff_is_tried_again(self, tmp_path):
        host, actions = make(tmp_path)
        (tmp_path / "poweroff.armed").touch()
        host.broken.add("systemctl")
        machine = in_outage(critical=7)
        sent = []
        for _ in range(2):
            handle_events(machine.step(battery(5)), battery(5), actions, sent.append)
        assert host.powered_off is False
        assert any("не вдалось" in text for text in sent)
        host.broken.clear()
        handle_events(machine.step(battery(5)), battery(5), actions, sent.append)
        assert host.powered_off is True

    def test_armed_after_critical_still_powers_off(self, tmp_path):
        host, actions = make(tmp_path)
        machine = in_outage(critical=7)
        for _ in range(2):
            handle_events(machine.step(battery(5)), battery(5), actions, quiet)
        assert host.powered_off is False
        (tmp_path / "poweroff.armed").touch()
        handle_events(machine.step(battery(5)), battery(5), actions, quiet)
        assert host.powered_off is True

    def test_not_armed_repeat_stays_silent(self, tmp_path):
        host, actions = make(tmp_path)
        sent = []
        handle_events(["critical_again"], battery(5), actions, sent.append)
        assert sent == []
        assert host.powered_off is False

    def test_charge_recovery_stops_repeats(self):
        machine = in_outage(critical=7)
        feed(machine, battery(5), battery(5))
        assert feed(machine, battery(50)) == []
        assert feed(machine, battery(5)) == []

    def test_next_outage_warns_about_critical_again(self):
        machine = in_outage(critical=7)
        feed(machine, battery(5), battery(5), GRID, GRID, battery(), battery())
        assert feed(machine, battery(5), battery(5)) == ["low", "critical"]

    def test_critical_speaks_again_after_restart(self, tmp_path):
        # Review 08.11: after a reboot the person must hear why the PC is not
        # shut down (handle_events picks the "already shut down" message).
        path = tmp_path / "state.json"
        machine = in_outage(critical=7)
        feed(machine, battery(5), battery(5))
        save_machine(machine, path)
        loaded = load_machine(path, critical=7)
        assert feed(loaded, battery(5)) == []
        assert feed(loaded, battery(5)) == ["critical"]


class TestWriteFailuresDoNotStopTheLoop:
    def test_unwritable_state_file_does_not_raise(self, tmp_path):
        host, actions = make(tmp_path)
        (tmp_path / "state.json").mkdir()
        machine = Machine()
        for _ in range(2):
            cycle(machine, battery(), actions, quiet, tmp_path, NOW)
        assert host.profile == "power-saver"

    def test_unwritable_log_does_not_block_state_file(self, tmp_path):
        _host, actions = make(tmp_path)
        (tmp_path / "readings-2026-11.csv").mkdir()
        cycle(Machine(), GRID, actions, quiet, tmp_path, NOW)
        assert (tmp_path / "state.json").exists()

    def test_cycle_writes_state_and_log(self, tmp_path):
        _host, actions = make(tmp_path)
        cycle(Machine(), GRID, actions, quiet, tmp_path, NOW)
        assert (tmp_path / "state.json").exists()
        assert (tmp_path / "readings-2026-11.csv").exists()

    def test_cycle_keeps_critical_count_across_failed_writes(self, tmp_path):
        host, actions = make(tmp_path)
        (tmp_path / "poweroff.armed").touch()
        (tmp_path / "state.json").mkdir()
        machine = in_outage(critical=7)
        for _ in range(2):
            cycle(machine, battery(5), actions, quiet, tmp_path, NOW)
        assert host.powered_off is True

    def test_failed_action_does_not_stop_the_cycle(self, tmp_path):
        host = FakeHost()

        class BrokenRestore(Actions):
            def restore(self):
                raise PermissionError("немає прав")

        actions = BrokenRestore(run=host.run, state_dir=tmp_path, save_brightness=20)
        cycle(Machine(), GRID, actions, quiet, tmp_path, NOW)
        assert (tmp_path / "state.json").exists()

    def test_unwritable_snapshot_does_not_block_save(self, tmp_path):
        host = FakeHost()
        blocked = tmp_path / "файл"
        blocked.touch()
        Actions(run=host.run, state_dir=blocked, save_brightness=20).apply_save()
        assert host.profile == "power-saver"
        assert host.brightness == 20


class TestPartialRestore:
    def test_restored_item_is_not_touched_again(self, tmp_path):
        host, actions = make(tmp_path)
        actions.apply_save()
        host.broken.add("ddcutil")
        actions.restore()
        assert host.profile == "balanced"
        host.profile = "performance"
        host.broken.clear()
        actions.restore()
        assert host.profile == "performance"
        assert host.brightness == 30

    def test_new_outage_remembers_owner_new_profile(self, tmp_path):
        host, actions = make(tmp_path)
        actions.apply_save()
        host.broken.add("ddcutil")
        actions.restore()
        host.profile = "performance"
        host.broken.clear()
        actions.apply_save()
        assert host.profile == "power-saver"
        actions.restore()
        assert host.profile == "performance"
        assert host.brightness == 30

    def test_snapshot_is_gone_after_full_restore(self, tmp_path):
        _host, actions = make(tmp_path)
        actions.apply_save()
        actions.restore()
        assert not actions.snapshot_path.exists()


class TestBrokenSnapshot:
    def test_broken_file_does_not_block_new_snapshot(self, tmp_path):
        host, actions = make(tmp_path)
        actions.snapshot_path.write_text("{зламано", encoding="utf-8")
        actions.apply_save()
        actions.restore()
        assert host.profile == "balanced"
        assert host.brightness == 30

    def test_broken_file_is_dropped_on_restore(self, tmp_path):
        host, actions = make(tmp_path)
        actions.snapshot_path.write_text("[1, 2]", encoding="utf-8")
        host.profile = "performance"
        actions.restore()
        assert host.profile == "performance"
        assert not actions.snapshot_path.exists()
