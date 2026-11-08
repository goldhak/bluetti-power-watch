"""Тести сторожа живлення. Bluetooth, екран і служби тут не чіпаються."""

from decimal import Decimal

import pytest
from bluetti_power_watch import (
    Actions,
    Machine,
    Reading,
    handle_events,
    load_machine,
    parse_reading,
    save_machine,
)

GRID = Reading(voltage_in=227.0, watts_in=420, watts_out=420, percent=100)


def battery(percent=80):
    return Reading(voltage_in=0.0, watts_in=0, watts_out=400, percent=percent)


def feed(machine, *readings):
    events = []
    for reading in readings:
        events.extend(machine.step(reading))
    return events


def in_outage(**kwargs):
    machine = Machine(**kwargs)
    feed(machine, battery(), battery())
    assert machine.state == "outage"
    return machine


class TestOnBattery:
    def test_normal_grid_is_not_battery(self):
        assert GRID.on_battery is False

    def test_no_input_voltage_is_battery(self):
        assert Reading(0.0, 0, 400, 80).on_battery is True

    def test_voltage_present_without_input_power_is_not_outage(self):
        # Проба 03.11: справжнє вимкнення дає 0 В, а 0 Вт при 227 В це не вимкнення.
        assert Reading(227.0, 0, 400, 80).on_battery is False

    def test_low_voltage_is_battery(self):
        assert Reading(60.0, 50, 400, 80).on_battery is True


class TestParseReading:
    RAW = {
        "ac_input_voltage": Decimal("227.1"),
        "ac_input_power": 420,
        "ac_output_power": 415,
        "total_battery_percent": 97,
    }

    def test_full_answer(self):
        assert parse_reading(self.RAW) == Reading(227.1, 420, 415, 97)

    def test_none_is_no_data(self):
        assert parse_reading(None) is None

    @pytest.mark.parametrize("key", sorted(RAW))
    def test_missing_field_is_no_data(self, key):
        raw = {k: v for k, v in self.RAW.items() if k != key}
        assert parse_reading(raw) is None

    @pytest.mark.parametrize(
        "key,value",
        [
            ("total_battery_percent", 101),
            ("total_battery_percent", -1),
            ("ac_input_voltage", 400),
            ("ac_input_voltage", -1),
            ("ac_input_power", -1),
            ("ac_input_power", 100000),
            ("ac_output_power", 100000),
            ("total_battery_percent", "сміття"),
        ],
    )
    def test_impossible_value_is_no_data(self, key, value):
        assert parse_reading({**self.RAW, key: value}) is None


class TestOutageDetection:
    def test_grid_gives_no_events(self):
        assert feed(Machine(), GRID, GRID, GRID) == []

    def test_one_battery_reading_is_not_enough(self):
        machine = Machine()
        assert feed(machine, battery()) == []
        assert machine.state == "grid"

    def test_two_battery_readings_start_outage_once(self):
        machine = Machine()
        assert feed(machine, battery(), battery(), battery()) == ["outage"]
        assert machine.state == "outage"

    def test_interrupted_sequence_does_not_count(self):
        machine = Machine()
        assert feed(machine, battery(), GRID, battery()) == []
        assert machine.state == "grid"

    def test_no_answer_is_not_an_outage(self):
        machine = Machine()
        assert feed(machine, None, None, None) == []
        assert machine.state == "grid"

    def test_no_answer_between_readings_does_not_break_the_sequence(self):
        # Review 08.11: a failed read is not evidence, only a real reading is.
        machine = Machine()
        assert feed(machine, battery(), None, battery()) == ["outage"]
        assert machine.state == "outage"


class TestRestore:
    def test_one_grid_reading_is_not_enough(self):
        machine = in_outage()
        assert feed(machine, GRID) == []
        assert machine.state == "outage"

    def test_two_grid_readings_restore_once(self):
        machine = in_outage()
        assert feed(machine, GRID, GRID, GRID) == ["restored"]
        assert machine.state == "grid"

    def test_no_answer_does_not_restore(self):
        machine = in_outage()
        assert feed(machine, None, None, None) == []
        assert machine.state == "outage"

    def test_no_answer_between_readings_does_not_break_restore(self):
        machine = in_outage()
        assert feed(machine, GRID, None, GRID) == ["restored"]
        assert machine.state == "grid"


class TestLowBattery:
    def test_low_fires_once_at_threshold(self):
        machine = in_outage(low=20)
        assert feed(machine, battery(21)) == []
        assert feed(machine, battery(20), battery(19)) == ["low"]

    def test_low_never_fires_on_grid(self):
        machine = Machine(low=20)
        charging = Reading(227.0, 900, 400, 5)
        assert feed(machine, charging, charging, charging) == []

    def test_low_fires_again_in_next_outage(self):
        machine = in_outage(low=20)
        feed(machine, battery(19), GRID, GRID, battery(), battery())
        assert feed(machine, battery(19)) == ["low"]


class TestCritical:
    def test_two_critical_readings_fire_once(self):
        machine = in_outage(low=20, critical=7)
        assert "critical" not in feed(machine, battery(7))
        assert feed(machine, battery(7)) == ["critical"]
        assert feed(machine, battery(6)) == ["critical_again"]

    def test_one_glitch_reading_does_not_fire(self):
        machine = in_outage(critical=7)
        assert "critical" not in feed(machine, battery(0), battery(70), battery(0))

    def test_no_answer_does_not_fire(self):
        machine = in_outage(critical=7)
        feed(machine, battery(5))
        assert "critical" not in feed(machine, None, None, None)

    def test_no_answer_between_readings_still_fires(self):
        # Review 08.11: with every other read failing the shutdown never came.
        machine = in_outage(critical=7)
        assert "critical" in feed(machine, battery(5), None, battery(5))

    def test_never_fires_on_grid_with_empty_battery(self):
        machine = Machine(critical=7)
        charging = Reading(227.0, 900, 400, 1)
        assert "critical" not in feed(machine, charging, charging, charging)

    def test_grid_reading_breaks_critical_sequence(self):
        machine = in_outage(critical=7)
        assert "critical" not in feed(machine, battery(5), GRID, battery(5))

    def test_start_of_outage_reading_does_not_count(self):
        machine = Machine(critical=7)
        assert feed(machine, battery(5), battery(5)) == ["outage", "low"]
        assert feed(machine, battery(5)) == []
        assert feed(machine, battery(5)) == ["critical"]


class TestNoData:
    def test_fires_once_after_limit(self):
        machine = Machine(no_data_limit=3)
        assert feed(machine, None, None) == []
        assert feed(machine, None, None, None) == ["no_data"]

    def test_answer_resets_counter(self):
        machine = Machine(no_data_limit=3)
        assert feed(machine, None, None, GRID, None, None) == []

    def test_data_back_is_reported(self):
        machine = Machine(no_data_limit=2)
        feed(machine, None, None)
        assert feed(machine, GRID, GRID) == ["data_back"]


class TestPersistence:
    def test_outage_survives_restart(self, tmp_path):
        path = tmp_path / "state.json"
        machine = in_outage(low=20)
        feed(machine, battery(19))
        save_machine(machine, path)
        loaded = load_machine(path, low=20)
        assert loaded.state == "outage"
        assert feed(loaded, battery(18)) == []
        assert feed(loaded, GRID, GRID) == ["restored"]

    def test_missing_file_means_grid(self, tmp_path):
        assert load_machine(tmp_path / "немає.json").state == "grid"

    def test_broken_file_means_grid(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{зламано", encoding="utf-8")
        assert load_machine(path).state == "grid"

    def test_unknown_state_means_grid(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text('{"state": "марс"}', encoding="utf-8")
        assert load_machine(path).state == "grid"


class FakeHost:
    """Імітує команди ПК: тести дивляться на стан, а не на перелік викликів."""

    def __init__(self, profile="balanced", brightness=30):
        self.profile = profile
        self.brightness = brightness
        self.broken = set()
        self.powered_off = False

    def run(self, cmd):
        name = cmd[0]
        if name in self.broken:
            raise OSError(f"{name} зламано")
        if name == "powerprofilesctl":
            if cmd[1] == "get":
                return 0, f"{self.profile}\n"
            self.profile = cmd[2]
            return 0, ""
        if name == "ddcutil":
            if cmd[1] == "getvcp":
                return 0, f"VCP 10 C {self.brightness} 100\n"
            self.brightness = int(cmd[3])
            return 0, ""
        if cmd == ["systemctl", "poweroff"]:
            self.powered_off = True
            return 0, ""
        raise AssertionError(f"несподівана команда: {cmd}")


@pytest.fixture
def host():
    return FakeHost()


@pytest.fixture
def actions(host, tmp_path):
    return Actions(run=host.run, state_dir=tmp_path, save_brightness=20)


class TestSaveAndRestore:
    def test_save_lowers_everything(self, host, actions):
        actions.apply_save()
        assert host.profile == "power-saver"
        assert host.brightness == 20

    def test_restore_brings_back_what_was_before(self, host, actions):
        host.profile = "performance"
        host.brightness = 55
        actions.apply_save()
        actions.restore()
        assert host.profile == "performance"
        assert host.brightness == 55

    def test_second_save_keeps_original_snapshot(self, host, actions):
        actions.apply_save()
        actions.apply_save()
        actions.restore()
        assert host.profile == "balanced"
        assert host.brightness == 30

    def test_snapshot_survives_new_actions_object(self, host, actions, tmp_path):
        actions.apply_save()
        Actions(run=host.run, state_dir=tmp_path, save_brightness=20).restore()
        assert host.profile == "balanced"
        assert host.brightness == 30

    def test_restore_without_save_touches_nothing(self, host, actions):
        host.profile = "performance"
        host.brightness = 55
        actions.restore()
        assert host.profile == "performance"
        assert host.brightness == 55

    def test_second_restore_touches_nothing(self, host, actions):
        actions.apply_save()
        actions.restore()
        host.profile = "performance"
        actions.restore()
        assert host.profile == "performance"

    def test_save_never_raises_brightness(self, host, actions):
        host.brightness = 10
        actions.apply_save()
        assert host.brightness == 10

    def test_broken_screen_does_not_block_profile(self, host, actions):
        host.broken.add("ddcutil")
        actions.apply_save()
        assert host.profile == "power-saver"

    def test_broken_profile_does_not_block_screen(self, host, actions):
        host.broken.add("powerprofilesctl")
        actions.apply_save()
        assert host.brightness == 20

    def test_value_unknown_at_save_is_not_restored(self, host, actions):
        host.broken.add("ddcutil")
        actions.apply_save()
        host.broken.clear()
        host.brightness = 44
        actions.restore()
        assert host.brightness == 44
        assert host.profile == "balanced"

    def test_broken_restore_keeps_snapshot_for_retry(self, host, actions):
        actions.apply_save()
        host.broken.add("ddcutil")
        actions.restore()
        host.broken.clear()
        actions.restore()
        assert host.brightness == 30


class TestPoweroff:
    def test_not_armed_does_not_power_off(self, host, actions):
        assert actions.poweroff_if_armed() is False
        assert host.powered_off is False

    def test_armed_powers_off(self, host, actions, tmp_path):
        (tmp_path / "poweroff.armed").touch()
        assert actions.poweroff_if_armed() is True
        assert host.powered_off is True


class TestHandleEvents:
    def run(self, host, actions, events, reading=None):
        sent = []
        handle_events(events, reading, actions, sent.append)
        return sent

    def test_outage_applies_save_and_tells_owner(self, host, actions):
        sent = self.run(host, actions, ["outage"], battery(90))
        assert host.profile == "power-saver"
        assert len(sent) == 1
        assert "90" in sent[0]

    def test_restored_restores_and_tells_owner(self, host, actions):
        actions.apply_save()
        sent = self.run(host, actions, ["restored"], GRID)
        assert host.profile == "balanced"
        assert len(sent) == 1

    def test_low_only_tells_owner(self, host, actions):
        sent = self.run(host, actions, ["low"], battery(20))
        assert host.profile == "balanced"
        assert "20" in sent[0]

    def test_critical_not_armed_warns_and_stays_on(self, host, actions):
        sent = self.run(host, actions, ["critical"], battery(6))
        assert host.powered_off is False
        assert "6" in sent[0]

    def test_critical_armed_powers_off(self, host, actions, tmp_path):
        (tmp_path / "poweroff.armed").touch()
        self.run(host, actions, ["critical"], battery(6))
        assert host.powered_off is True

    def test_no_data_only_tells_owner(self, host, actions):
        sent = self.run(host, actions, ["no_data"])
        assert host.profile == "balanced"
        assert host.powered_off is False
        assert len(sent) == 1

    def test_broken_notification_does_not_block_action(self, host, actions):
        def boom(_text):
            raise OSError("немає робочого столу")

        handle_events(["outage"], battery(90), actions, boom)
        assert host.profile == "power-saver"
