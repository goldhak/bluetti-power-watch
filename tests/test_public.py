"""What exists only in the public version: station choice, languages, models, options."""

import asyncio
import json
import string

import pytest
from bluetti_power_watch import (
    EL320_REGISTERS,
    MESSAGES,
    NEEDED_FIELDS,
    Actions,
    Machine,
    Reading,
    Station,
    actions_from_config,
    guess_model,
    handle_events,
    lang,
    main_set_thresholds,
    merge_registers,
    model_profile,
    save_machine,
    set_armed,
    set_station,
    station_from_config,
    tr,
    watch_once,
)

GRID = Reading(voltage_in=227.0, watts_in=420, watts_out=420, percent=100)
EL320 = {"name": "EL320000000000001", "model": "EL320", "encrypted": True}


class Recorder:
    def __init__(self):
        self.calls = []

    def run(self, cmd):
        self.calls.append(cmd)
        if cmd[:2] == ["powerprofilesctl", "get"]:
            return 0, "balanced\n"
        if cmd[:2] == ["ddcutil", "getvcp"]:
            return 0, "VCP 10 C 80 100\n"
        return 0, ""

    def tools(self):
        return {cmd[0] for cmd in self.calls}


class TestLanguage:
    def test_ukrainian(self, monkeypatch):
        monkeypatch.setenv("LANG", "uk_UA.UTF-8")
        assert lang() == "uk"

    def test_english(self, monkeypatch):
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        assert lang() == "en"

    def test_other_language_falls_back_to_english(self, monkeypatch):
        monkeypatch.setenv("LANG", "de_DE.UTF-8")
        assert lang() == "en"

    def test_lc_all_wins(self, monkeypatch):
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        monkeypatch.setenv("LC_ALL", "uk_UA.UTF-8")
        assert lang() == "uk"

    def test_nothing_set(self, monkeypatch):
        monkeypatch.delenv("LANG", raising=False)
        assert lang() == "en"

    def test_english_text(self, monkeypatch):
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        assert tr("outage", percent="80 %") == "Power outage. Power saving is on, battery 80 %."

    @pytest.mark.parametrize("message_id", sorted(MESSAGES))
    def test_both_languages_have_the_same_placeholders(self, message_id):
        fields = {
            code: {f for _, f, _, _ in string.Formatter().parse(text) if f}
            for code, text in MESSAGES[message_id].items()
        }
        assert set(fields) == {"uk", "en"}
        assert fields["uk"] == fields["en"]

    def test_message_with_key_placeholder(self):
        # tr() itself has a positional-only first argument, so key= is a placeholder.
        assert "low" in tr("thresholds_int", key="low")

    def test_stuck_message_units(self, monkeypatch, tmp_path):
        reading = Reading(voltage_in=214.8, watts_in=0, watts_out=419, percent=99)
        told = []
        handle_events(
            ["stuck"], reading, Actions(run=lambda c: (0, ""), state_dir=tmp_path), told.append
        )
        assert "215 В" in told[0]
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        told.clear()
        handle_events(
            ["stuck"], reading, Actions(run=lambda c: (0, ""), state_dir=tmp_path), told.append
        )
        assert "215 V" in told[0] and "E114" in told[0]


class TestOptionsOffByDefault:
    def test_only_power_profile_by_default(self, tmp_path):
        rec = Recorder()
        Actions(run=rec.run, state_dir=tmp_path).apply_save()
        assert rec.tools() == {"powerprofilesctl"}

    def test_power_saver_can_be_off(self, tmp_path):
        rec = Recorder()
        Actions(run=rec.run, state_dir=tmp_path, power_saver=False).apply_save()
        assert rec.calls == []

    def test_config_enables_brightness(self, tmp_path):
        rec = Recorder()
        actions = actions_from_config({"brightness": 20}, run=rec.run, state_dir=tmp_path)
        actions.apply_save()
        assert rec.tools() == {"powerprofilesctl", "ddcutil"}
        assert ["ddcutil", "setvcp", "10", "20"] in rec.calls

    @pytest.mark.parametrize("brightness", ["20", 150, -1, True, 20.5, None])
    def test_bad_brightness_is_ignored(self, brightness, tmp_path):
        assert (
            actions_from_config({"brightness": brightness}, state_dir=tmp_path).save_brightness
            is None
        )

    def test_power_saver_only_off_when_false(self, tmp_path):
        assert actions_from_config({"power_saver": 0}, state_dir=tmp_path).power_saver is True
        assert actions_from_config({"power_saver": False}, state_dir=tmp_path).power_saver is False


class TestStationConfig:
    def test_valid(self):
        assert station_from_config({"station": EL320}) == Station(**EL320)

    @pytest.mark.parametrize(
        "station",
        [
            None,
            "EL320000000000001",
            {**EL320, "encrypted": "yes"},
            {**EL320, "model": "AC180"},  # the name says otherwise
            {**EL320, "name": "EL320"},  # no serial: could be any station
            {"name": "EL320000000000001", "model": "EL320"},
        ],
    )
    def test_invalid_means_not_configured(self, station):
        assert station_from_config({"station": station}) is None

    def test_no_config(self):
        assert station_from_config({}) is None


class TestModels:
    @pytest.mark.parametrize(
        "name,model",
        [
            ("EL320000000000001", "EL320"),
            ("AC180123456789", "AC180"),
            ("AC300123456789", "AC300"),
            ("EB3A123456789", "EB3A"),
            ("EL320", None),
            ("EL320ABC", None),
            ("Phone of someone", None),
            ("XEL320000000000001", None),
        ],
    )
    def test_guess_model(self, name, model):
        assert guess_model(name) == model

    def test_el320_uses_the_tested_registers(self):
        device, registers = model_profile("EL320")
        assert registers == list(EL320_REGISTERS)
        assert type(device).__name__ == "EL100V2"

    def test_other_model_registers_cover_the_needed_fields(self):
        device, registers = model_profile("AC180")
        fields = {str(f.name): f for f in device.fields}
        for name in NEEDED_FIELDS:
            field = fields[name]
            assert any(
                a <= field.address and field.address + field.size <= a + n for a, n in registers
            )

    def test_model_without_input_voltage_is_not_supported(self):
        assert model_profile("AC200M") is None

    def test_unknown_model(self):
        assert model_profile("XYZ") is None

    def test_merge_registers(self):
        assert merge_registers([(146, 1), (102, 1), (142, 1), (1314, 1)]) == [
            (102, 1),
            (142, 5),
            (1314, 1),
        ]

    def test_merge_overlapping(self):
        assert merge_registers([(10, 4), (12, 4)]) == [(10, 6)]


class TestSetStation:
    def test_el320_needs_no_bluetooth(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"brightness": 20}), encoding="utf-8")
        assert set_station("EL320000000000001", "auto", path) == 0
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved == {"brightness": 20, "station": EL320}

    def test_explicit_encryption(self, tmp_path):
        path = tmp_path / "config.json"
        assert set_station("AC180123456789", "no", path) == 0
        assert json.loads(path.read_text(encoding="utf-8"))["station"]["encrypted"] is False

    def test_unknown_model(self, tmp_path, capsys):
        path = tmp_path / "config.json"
        assert set_station("Something123", "yes", path) == 2
        assert not path.exists()

    def test_unsupported_model(self, tmp_path):
        path = tmp_path / "config.json"
        assert set_station("AC200M123456789", "yes", path) == 2
        assert not path.exists()


class TestArm:
    def test_arm_and_disarm(self, tmp_path):
        set_armed(True, tmp_path)
        assert Actions(state_dir=tmp_path).will_poweroff()
        set_armed(False, tmp_path)
        assert not Actions(state_dir=tmp_path).will_poweroff()

    def test_disarm_when_not_armed(self, tmp_path):
        assert set_armed(False, tmp_path) == 0


class TestWatchOnce:
    def test_not_configured_reads_nothing(self, tmp_path):
        calls = []

        async def reader(station):
            calls.append(station)
            return GRID

        result = asyncio.run(
            watch_once(
                Machine(),
                state_dir=tmp_path,
                config_path=tmp_path / "config.json",
                thresholds_path=tmp_path / "t.json",
                reader=reader,
                notify=lambda _t: None,
                run=lambda _c: (0, ""),
            )
        )
        assert result is None and calls == []
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state["configured"] is False

    def test_configured_reads_its_station(self, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({"station": EL320}), encoding="utf-8")
        (tmp_path / "t.json").write_text(
            json.dumps({"low": 30, "critical": 10, "stuck_minutes": 5}), encoding="utf-8"
        )
        calls = []

        async def reader(station):
            calls.append(station)
            return GRID

        machine = Machine()
        asyncio.run(
            watch_once(
                machine,
                state_dir=tmp_path,
                config_path=tmp_path / "config.json",
                thresholds_path=tmp_path / "t.json",
                reader=reader,
                notify=lambda _t: None,
                run=lambda _c: (0, ""),
            )
        )
        assert calls == [Station(**EL320)]
        assert machine.low == 30
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state["configured"] is True and state["last"]["percent"] == 100

    def test_configured_flag_default(self, tmp_path):
        save_machine(Machine(), tmp_path / "s.json")
        assert json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["configured"] is True


def test_threshold_errors_in_english(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert (
        main_set_thresholds(
            ["--low", "8", "--critical", "10", "--stuck-minutes", "5"], tmp_path / "t.json"
        )
        == 2
    )
    assert "thresholds not saved" in capsys.readouterr().err
