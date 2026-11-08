"""Станція застрягла: напруга в мережі є, а з мережі не бере нічого (E114, 08.11).

Замір 08.11: о 17:12 напруга просіла до 187 В, вхід упав до 0 Вт і не
повернувся, хоча за кілька хвилин напруга знову була 213-215 В. ПК 20 хвилин
жив від батареї, а сторож писав «світло є». Допомогло лише перепідключити вилку.
"""

import json

from bluetti_power_watch import Actions, Machine, Reading, handle_events, load_machine, save_machine

GRID = Reading(voltage_in=227.0, watts_in=420, watts_out=420, percent=100)


def stuck(percent=99, voltage=214.8):
    """Справжній показ 08.11 17:31."""
    return Reading(voltage_in=voltage, watts_in=0, watts_out=419, percent=percent)


def battery(percent=80):
    return Reading(voltage_in=0.0, watts_in=0, watts_out=400, percent=percent)


def feed(machine, *readings):
    events = []
    for reading in readings:
        events.extend(machine.step(reading))
    return events


class TestStuckReading:
    def test_voltage_without_input_under_load_is_stuck(self):
        assert stuck().stuck_on_battery is True

    def test_normal_grid_is_not_stuck(self):
        assert GRID.stuck_on_battery is False

    def test_real_outage_is_not_stuck(self):
        # Справжнє вимкнення це інша подія, її веде «outage».
        assert battery().stuck_on_battery is False

    def test_no_load_is_not_stuck(self):
        # Нічого не споживає: вхід 0 Вт при повній батареї це норма.
        assert Reading(227.0, 0, 0, 100).stuck_on_battery is False

    def test_some_input_is_not_stuck(self):
        assert Reading(200.2, 1133, 357, 98).stuck_on_battery is False


class TestStuckMachine:
    def test_warns_after_five_readings_in_a_row(self):
        machine = Machine()
        assert feed(machine, *[stuck()] * 4) == []
        assert feed(machine, stuck()) == ["stuck"]

    def test_warns_once(self):
        machine = Machine()
        feed(machine, *[stuck()] * 5)
        assert feed(machine, *[stuck()] * 20) == []

    def test_single_zero_reading_is_noise(self):
        # 05.11 і 06.11 по одному такому показу посеред нормальних.
        machine = Machine()
        assert feed(machine, *[stuck(), GRID] * 10) == []

    def test_interruption_resets_the_count(self):
        machine = Machine()
        assert feed(machine, *[stuck()] * 4, GRID, *[stuck()] * 4) == []

    def test_no_data_does_not_reset_the_count(self):
        # Review 08.11: a failed read is not evidence that the station recovered.
        machine = Machine()
        assert feed(machine, *[stuck()] * 4, None, stuck()) == ["stuck"]

    def test_input_back_after_warning_says_so(self):
        machine = Machine()
        feed(machine, *[stuck()] * 5)
        assert feed(machine, Reading(200.2, 1133, 357, 98)) == ["unstuck"]

    def test_no_unstuck_without_warning(self):
        machine = Machine()
        assert feed(machine, *[stuck()] * 4, GRID) == []

    def test_state_stays_grid(self):
        # Ознаку вимкнення світла (напруга < 100 В) ця зміна не чіпає.
        machine = Machine()
        feed(machine, *[stuck()] * 10)
        assert machine.state == "grid"

    def test_real_outage_clears_warning(self):
        # Застрягла, потім справжнє вимкнення: після повернення світла
        # окремого «знову бере» не треба, а нове застрягання знову попередить.
        machine = Machine()
        feed(machine, *[stuck()] * 5)
        events = feed(machine, battery(), battery(), GRID, GRID)
        assert events == ["outage", "restored"]
        assert feed(machine, *[stuck()] * 5) == ["stuck"]

    def test_custom_limit(self):
        machine = Machine(stuck_limit=2)
        assert feed(machine, stuck(), stuck()) == ["stuck"]


class TestStuckMessages:
    def _handle(self, events, reading, tmp_path):
        told = []
        actions = Actions(run=lambda cmd: (0, ""), state_dir=tmp_path)
        handle_events(events, reading, actions, told.append)
        return told

    def test_stuck_message_names_the_fix(self, tmp_path):
        told = self._handle(["stuck"], stuck(percent=97, voltage=201.4), tmp_path)
        assert len(told) == 1
        text = told[0]
        assert "вилку" in text
        assert "201 В" in text
        assert "97 %" in text
        assert "E114" in text

    def test_stuck_does_not_touch_the_pc(self, tmp_path):
        calls = []
        actions = Actions(run=lambda cmd: calls.append(cmd) or (0, ""), state_dir=tmp_path)
        handle_events(["stuck", "unstuck"], stuck(), actions, lambda _text: None)
        assert calls == []

    def test_unstuck_message(self, tmp_path):
        told = self._handle(["unstuck"], Reading(200.2, 1133, 357, 98), tmp_path)
        assert told == ["Станція знову бере струм із мережі."]


class TestStuckSaved:
    """Віджет на робочому столі читає state.json: застрягання має бути видно там."""

    def test_saved_to_state_file(self, tmp_path):
        machine = Machine()
        feed(machine, *[stuck()] * 5)
        path = tmp_path / "state.json"
        save_machine(machine, path, stuck())
        assert json.loads(path.read_text(encoding="utf-8"))["stuck_sent"] is True

    def test_not_stuck_saved_as_false(self, tmp_path):
        path = tmp_path / "state.json"
        save_machine(Machine(), path, GRID)
        assert json.loads(path.read_text(encoding="utf-8"))["stuck_sent"] is False

    def test_restart_remembers_warning(self, tmp_path):
        # Після перезапуску сторожа «знову бере» все одно має прийти.
        machine = Machine()
        feed(machine, *[stuck()] * 5)
        path = tmp_path / "state.json"
        save_machine(machine, path, stuck())
        loaded = load_machine(path)
        assert feed(loaded, Reading(200.2, 1133, 357, 98)) == ["unstuck"]

    def test_old_state_file_without_field(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"state": "grid"}), encoding="utf-8")
        assert load_machine(path).stuck_sent is False
