"""Пороги з віджета (08.11): віджет пише thresholds.json, сторож читає на кожному замірі."""

import json

import pytest
from bluetti_power_watch import (
    DEFAULT_THRESHOLDS,
    Machine,
    Reading,
    load_thresholds,
    main_set_thresholds,
    save_machine,
    stuck_readings,
    write_thresholds,
)


def battery(percent):
    return Reading(voltage_in=0.0, watts_in=0, watts_out=400, percent=percent)


def stuck():
    return Reading(voltage_in=214.8, watts_in=0, watts_out=419, percent=99)


def feed(machine, *readings):
    events = []
    for reading in readings:
        events.extend(machine.step(reading))
    return events


class TestDefaults:
    def test_defaults_are_the_old_constants(self):
        assert DEFAULT_THRESHOLDS == {"low": 20, "critical": 7, "stuck_minutes": 5}

    def test_five_minutes_is_five_readings(self):
        # Замір 08.11: між показами ~70 с, тож 5 хв це ті самі 5 показів, що й до порогів.
        assert stuck_readings(5) == 5

    def test_minimum_two_readings(self):
        # Поодинокий нуль на вході бував шумом (05.11, 06.11).
        assert stuck_readings(1) == 2


class TestLoad:
    def test_missing_file_gives_defaults(self, tmp_path):
        assert load_thresholds(tmp_path / "немає.json") == DEFAULT_THRESHOLDS

    def test_broken_json_gives_defaults(self, tmp_path):
        path = tmp_path / "t.json"
        path.write_text("{зламано", encoding="utf-8")
        assert load_thresholds(path) == DEFAULT_THRESHOLDS

    def test_valid_file(self, tmp_path):
        path = tmp_path / "t.json"
        path.write_text(
            json.dumps({"low": 30, "critical": 10, "stuck_minutes": 8}), encoding="utf-8"
        )
        assert load_thresholds(path) == {"low": 30, "critical": 10, "stuck_minutes": 8}

    @pytest.mark.parametrize(
        "data",
        [
            {"low": 30, "critical": 2, "stuck_minutes": 5},  # вимкнення надто пізно
            {"low": 30, "critical": 60, "stuck_minutes": 5},  # вимкнення надто рано
            {"low": 12, "critical": 10, "stuck_minutes": 5},  # попередження впритул до вимкнення
            {"low": 95, "critical": 10, "stuck_minutes": 5},
            {"low": 30, "critical": 10, "stuck_minutes": 0},
            {"low": 30, "critical": 10, "stuck_minutes": 600},
            {"low": "30", "critical": 10, "stuck_minutes": 5},
            {"low": True, "critical": 10, "stuck_minutes": 5},
            {"low": 30.5, "critical": 10, "stuck_minutes": 5},
            {"critical": 10, "stuck_minutes": 5},
            [30, 10, 5],
        ],
    )
    def test_invalid_file_gives_defaults(self, tmp_path, data):
        path = tmp_path / "t.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert load_thresholds(path) == DEFAULT_THRESHOLDS


class TestBoundaries:
    @pytest.mark.parametrize(
        "data",
        [
            {"low": 12, "critical": 7, "stuck_minutes": 5},  # рівно на 5 % вище
            {"low": 10, "critical": 5, "stuck_minutes": 1},
            {"low": 90, "critical": 50, "stuck_minutes": 60},
        ],
    )
    def test_edges_are_allowed(self, tmp_path, data):
        path = tmp_path / "t.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert load_thresholds(path) == data


class TestWrite:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "sub" / "t.json"
        write_thresholds(path, {"low": 25, "critical": 8, "stuck_minutes": 3})
        assert load_thresholds(path) == {"low": 25, "critical": 8, "stuck_minutes": 3}

    def test_invalid_is_refused_and_file_kept(self, tmp_path):
        path = tmp_path / "t.json"
        write_thresholds(path, {"low": 25, "critical": 8, "stuck_minutes": 3})
        with pytest.raises(ValueError):
            write_thresholds(path, {"low": 9, "critical": 8, "stuck_minutes": 3})
        assert load_thresholds(path) == {"low": 25, "critical": 8, "stuck_minutes": 3}

    def test_command_line(self, tmp_path):
        path = tmp_path / "t.json"
        assert (
            main_set_thresholds(["--low", "30", "--critical", "10", "--stuck-minutes", "7"], path)
            == 0
        )
        assert load_thresholds(path) == {"low": 30, "critical": 10, "stuck_minutes": 7}

    def test_command_line_refuses_bad_values(self, tmp_path, capsys):
        path = tmp_path / "t.json"
        assert (
            main_set_thresholds(["--low", "8", "--critical", "10", "--stuck-minutes", "7"], path)
            == 2
        )
        assert not path.exists()
        assert capsys.readouterr().err.strip() != ""


class TestMachineUsesThresholds:
    def test_low_and_critical_follow_thresholds(self):
        machine = Machine()
        machine.apply_thresholds({"low": 50, "critical": 30, "stuck_minutes": 5})
        events = feed(machine, battery(60), battery(60), battery(50), battery(30), battery(30))
        assert events == ["outage", "low", "critical"]

    def test_stuck_follows_thresholds(self):
        machine = Machine()
        machine.apply_thresholds({"low": 20, "critical": 7, "stuck_minutes": 2})
        assert feed(machine, stuck(), stuck()) == ["stuck"]

    def test_effective_thresholds_in_state_file(self, tmp_path):
        # Віджет показує, з якими порогами сторож працює НАСПРАВДІ.
        machine = Machine()
        machine.apply_thresholds({"low": 25, "critical": 8, "stuck_minutes": 3})
        path = tmp_path / "state.json"
        save_machine(machine, path)
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["thresholds"] == {"low": 25, "critical": 8, "stuck_minutes": 3}

    def test_default_machine_saves_default_thresholds(self, tmp_path):
        path = tmp_path / "state.json"
        save_machine(Machine(), path)
        assert json.loads(path.read_text(encoding="utf-8"))["thresholds"] == DEFAULT_THRESHOLDS
