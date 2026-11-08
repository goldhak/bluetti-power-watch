"""Тести головного циклу: один крок сторожа, продовження після перезапуску, журнал."""

import csv
import json
from datetime import datetime

from bluetti_power_watch import (
    Actions,
    Machine,
    Reading,
    append_reading,
    resume,
    save_machine,
    tick,
)
from test_power_watch import GRID, FakeHost, battery


def make(tmp_path, host=None):
    host = host or FakeHost()
    return host, Actions(run=host.run, state_dir=tmp_path, save_brightness=20)


class TestTick:
    def test_outage_applies_save(self, tmp_path):
        host, actions = make(tmp_path)
        machine = Machine()
        sent = []
        tick(machine, battery(), actions, sent.append)
        tick(machine, battery(), actions, sent.append)
        assert host.profile == "power-saver"
        assert len(sent) == 1

    def test_failed_restore_is_retried_on_next_tick(self, tmp_path):
        host, actions = make(tmp_path)
        machine = Machine()
        for _ in range(2):
            tick(machine, battery(), actions, lambda _t: None)
        host.broken.add("ddcutil")
        for _ in range(2):
            tick(machine, GRID, actions, lambda _t: None)
        assert host.brightness == 20
        host.broken.clear()
        tick(machine, GRID, actions, lambda _t: None)
        assert host.brightness == 30

    def test_no_restore_while_outage_lasts(self, tmp_path):
        host, actions = make(tmp_path)
        machine = Machine()
        for _ in range(3):
            tick(machine, battery(), actions, lambda _t: None)
        assert host.profile == "power-saver"

    def test_no_answer_during_outage_keeps_save(self, tmp_path):
        host, actions = make(tmp_path)
        machine = Machine()
        for _ in range(2):
            tick(machine, battery(), actions, lambda _t: None)
        tick(machine, None, actions, lambda _t: None)
        assert host.profile == "power-saver"


class TestResume:
    def test_outage_after_restart_applies_save_again(self, tmp_path):
        host, actions = make(tmp_path)
        actions.apply_save()
        host.profile = "balanced"
        machine = Machine()
        machine.state = "outage"
        resume(machine, actions)
        assert host.profile == "power-saver"
        actions.restore()
        assert host.profile == "balanced"

    def test_grid_after_restart_touches_nothing(self, tmp_path):
        host, actions = make(tmp_path)
        host.profile = "performance"
        resume(Machine(), actions)
        assert host.profile == "performance"


class TestStateFile:
    def test_file_tells_last_reading(self, tmp_path):
        path = tmp_path / "state.json"
        save_machine(Machine(), path, reading=Reading(227.0, 420, 415, 97))
        last = json.loads(path.read_text(encoding="utf-8"))["last"]
        assert last["percent"] == 97
        assert last["watts_out"] == 415

    def test_no_answer_keeps_no_last_reading(self, tmp_path):
        path = tmp_path / "state.json"
        save_machine(Machine(), path, reading=None)
        assert json.loads(path.read_text(encoding="utf-8"))["last"] is None


class TestReadingsLog:
    NOW = datetime(2026, 11, 3, 21, 5, 0)

    def rows(self, tmp_path):
        with (tmp_path / "readings-2026-11.csv").open(encoding="utf-8") as f:
            return list(csv.reader(f))

    def test_header_is_written_once(self, tmp_path):
        append_reading(tmp_path, GRID, "grid", self.NOW)
        append_reading(tmp_path, GRID, "grid", self.NOW)
        rows = self.rows(tmp_path)
        assert rows[0][0] == "time"
        assert len(rows) == 3

    def test_row_has_values(self, tmp_path):
        append_reading(tmp_path, GRID, "grid", self.NOW)
        assert self.rows(tmp_path)[1] == [
            "2026-11-03T21:05:00",
            "grid",
            "227.0",
            "420",
            "420",
            "100",
        ]

    def test_no_answer_leaves_empty_cells(self, tmp_path):
        append_reading(tmp_path, None, "outage", self.NOW)
        assert self.rows(tmp_path)[1] == [
            "2026-11-03T21:05:00",
            "outage",
            "",
            "",
            "",
            "",
        ]
