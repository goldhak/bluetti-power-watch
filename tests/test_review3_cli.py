"""Findings of the 08.11 review outside the state machine."""

import json

import pytest
from bluetti_power_watch import (
    MESSAGES,
    main,
    model_profile,
    set_armed,
    set_station,
    vendor_enabled,
)

SUPPORTED = [
    "EL320",
    "AC180",
    "AC180P",
    "AC180T",
    "AC300",
    "AC500",
    "AC60",
    "AC60P",
    "AC70",
    "AC70P",
    "AP300",
    "EB3A",
    "EL10",
    "EL100V2",
    "EL30V2",
    "EP500",
    "EP500P",
    "Handsfree 1",
    "PR100V2",
    "PR30V2",
]


@pytest.mark.parametrize("model", SUPPORTED)
def test_reading_never_selects_battery_packs(model):
    # Selecting a battery pack is a WRITE to the station (AC300 had max_packs=4).
    device, _registers = model_profile(model)
    assert device.max_packs == 0


def test_once_fails_loudly_when_nothing_is_read(tmp_path, monkeypatch, capsys):
    import bluetti_power_watch as bpw

    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"station": {"name": "EL320000000000001", "model": "EL320", "encrypted": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(bpw, "CONFIG_PATH", config)

    async def nothing(_station):
        return None

    monkeypatch.setattr(bpw, "read_once", nothing)
    with pytest.raises(SystemExit) as exit_info:
        main(["once"])
    assert exit_info.value.code == 1
    assert "не прочитано" in capsys.readouterr().err


def test_set_station_keeps_a_broken_config(tmp_path, capsys):
    path = tmp_path / "config.json"
    broken = '{"brightness": 20, "power_saver": true,}'
    path.write_text(broken, encoding="utf-8")
    assert set_station("EL320000000000001", "auto", path) == 1
    assert path.read_text(encoding="utf-8") == broken
    assert str(path) in capsys.readouterr().err


def test_set_station_keeps_a_non_object_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert set_station("EL320000000000001", "auto", path) == 1
    assert path.read_text(encoding="utf-8") == "[1, 2]"


def test_set_station_says_where_it_wrote(tmp_path, capsys):
    path = tmp_path / "config.json"
    set_station("EL320000000000001", "auto", path)
    assert str(path) in capsys.readouterr().out


def test_arm_says_where_it_wrote(tmp_path, capsys):
    set_armed(True, tmp_path)
    assert str(tmp_path / "poweroff.armed") in capsys.readouterr().out


def test_save_command_is_gone():
    # It was undone by the running service within a minute.
    with pytest.raises(SystemExit):
        main(["save"])


@pytest.mark.parametrize(
    "module_file,enabled",
    [
        ("/usr/lib/python3.14/site-packages/bluetti_power_watch.py", True),
        ("/home/someone/src/bluetti-power-watch/bluetti_power_watch.py", False),
        ("/build/bluetti-power-watch/src/bluetti-power-watch-0.1.0/bluetti_power_watch.py", False),
    ],
)
def test_vendor_only_for_the_installed_module(module_file, enabled):
    # check() in the PKGBUILD must test the shipped wheels, not an older installed copy.
    assert vendor_enabled(module_file) is enabled


def test_new_messages_have_both_languages():
    for key in ("read_failed", "config_broken"):
        assert set(MESSAGES[key]) == {"uk", "en"}
