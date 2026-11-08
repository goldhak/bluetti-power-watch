#!/usr/bin/env python3
"""bluetti-power-watch: keeps a Linux PC going through a blackout on a BLUETTI station.

Once a minute it reads the station over Bluetooth (read-only). Power gone: power
saving mode. Power back: everything restored. Low battery: a warning. Critical
battery: the PC shuts down, but only when auto shutdown is armed.

The main rule: "the station does not answer" does NOT mean "there is no power".
Nothing is switched off for lack of data, only after two real readings in a row.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import math
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

__version__ = "0.1.0"
APP = "bluetti-power-watch"

log = logging.getLogger(APP)

# The AUR package ships bluetti-bt-lib and its Bluetooth helpers here: they are
# not in the Arch repositories, and their AUR packages are orphaned and broken.
VENDOR_DIR = Path("/usr/lib/bluetti-power-watch/vendor")


def use_vendor(path: Path) -> None:
    """Put the shipped libraries first, so an old system copy does not win."""
    folder = str(path)
    if Path(path).is_dir() and folder not in sys.path:
        sys.path.insert(0, folder)


def vendor_enabled(module_file: str) -> bool:
    """Only the installed copy uses the shipped libraries: the PKGBUILD check()
    runs the tests from the source tree and must test the new wheels."""
    return str(Path(module_file).resolve()).startswith("/usr/")


if vendor_enabled(__file__):
    use_vendor(VENDOR_DIR)


def _xdg_dir(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "")
    return Path(value) if os.path.isabs(value) else Path.home() / fallback


STATE_DIR = _xdg_dir("XDG_STATE_HOME", ".local/state") / APP
CONFIG_DIR = _xdg_dir("XDG_CONFIG_HOME", ".config") / APP
CONFIG_PATH = CONFIG_DIR / "config.json"
# The KDE widget writes the thresholds, the watchdog reads them on every reading.
THRESHOLDS_PATH = CONFIG_DIR / "thresholds.json"

POLL_SECONDS = 60
READ_TIMEOUT_SECONDS = 90

LOW_PERCENT = 20
CRITICAL_PERCENT = 7
CONFIRM_READINGS = 2
NO_DATA_LIMIT = 10
# Readings in a row with grid voltage present but no input power before warning.
# A single zero reading happened as noise.
STUCK_LIMIT = 5
STUCK_MINUTES = 5
# Measured on an Elite 320: ~70 s between readings (a minute of sleep + the read).
READING_SECONDS = 70

DEFAULT_THRESHOLDS = {
    "low": LOW_PERCENT,
    "critical": CRITICAL_PERCENT,
    "stuck_minutes": STUCK_MINUTES,
}
# Shutting down below 5 % leaves no margin for an inaccurate charge reading;
# the warning has to come at least 5 % earlier than the shutdown.
THRESHOLD_RANGES = {"low": (10, 90), "critical": (5, 50), "stuck_minutes": (1, 60)}
MIN_LOW_CRITICAL_GAP = 5

# The battery cannot charge without the grid: a rise by this much means the power
# came back in between (for example while the PC was off after a shutdown).
RECHARGE_MARGIN = 5

MIN_GRID_VOLTAGE = 100
MAX_VOLTAGE = 300
MAX_WATTS = 10000

# Fields the watchdog needs from a station profile of bluetti-bt-lib.
NEEDED_FIELDS = ("total_battery_percent", "ac_output_power", "ac_input_power", "ac_input_voltage")

# Elite 320 is not in bluetti-bt-lib 0.1.8. It answers with the EL100V2 profile
# over an encrypted link; these exact registers were tested on a real unit.
EL320_NAME_RE = re.compile(r"^EL320(\d+)$")
EL320_REGISTERS = ((102, 1), (140, 7), (1314, 2))

# ---------------------------------------------------------------- messages

MESSAGES = {
    "title": {"uk": "Сторож живлення", "en": "Power watch"},
    "unknown": {"uk": "невідомо", "en": "unknown"},
    "outage": {
        "uk": "Немає світла. Економний режим увімкнено, заряд {percent}.",
        "en": "Power outage. Power saving is on, battery {percent}.",
    },
    "restored": {
        "uk": "Світло повернулось. Повна потужність відновлена.",
        "en": "Power is back. Full performance restored.",
    },
    "low": {
        "uk": "Заряд низький: {percent}. Час зберегти роботу.",
        "en": "Battery low: {percent}. Time to save your work.",
    },
    "critical_poweroff": {
        "uk": "Заряд критичний: {percent}. Вимикаю ПК.",
        "en": "Battery critical: {percent}. Shutting down the PC.",
    },
    "critical_manual": {
        "uk": "Заряд критичний: {percent}. Автовимкнення не спрацює, вимкніть ПК самі.",
        "en": "Battery critical: {percent}. Auto shutdown is off, please shut down the PC yourself.",
    },
    "critical_done": {
        "uk": "Заряд критичний: {percent}. Автовимкнення вже спрацювало під час цього "
        "вимкнення світла, тож удруге не вимикаю. Вимкніть ПК самі.",
        "en": "Battery critical: {percent}. Auto shutdown already ran during this outage, "
        "so it will not run again. Please shut down the PC yourself.",
    },
    "poweroff_failed": {
        "uk": "Вимкнути ПК не вдалось, вимкніть його самі.",
        "en": "Could not shut down the PC, please do it yourself.",
    },
    "no_data": {
        "uk": "Станція не відповідає понад 10 хвилин. Я не знаю стану світла і заряду.",
        "en": "The station has not answered for over 10 minutes. Power and battery state are unknown.",
    },
    "data_back": {"uk": "Станція знову відповідає.", "en": "The station is answering again."},
    "stuck": {
        "uk": "Станція не бере струм із мережі, хоча напруга є ({voltage} В). "
        "ПК живиться від батареї, заряд {percent}. "
        "Перепідключіть вилку станції (на екрані, мабуть, E114).",
        "en": "The station is not taking power from the grid although voltage is present "
        "({voltage} V). The PC runs on battery, {percent}. "
        "Re-plug the station's AC cable (the screen probably shows E114).",
    },
    "unstuck": {
        "uk": "Станція знову бере струм із мережі.",
        "en": "The station is taking power from the grid again.",
    },
    "not_configured": {
        "uk": "Станцію не налаштовано. Виконайте: {app} scan, потім {app} set-station НАЗВА",
        "en": "No station configured. Run: {app} scan, then {app} set-station NAME",
    },
    "thresholds_dict": {"uk": "пороги мають бути словником", "en": "thresholds must be an object"},
    "thresholds_int": {"uk": "{key}: потрібне ціле число", "en": "{key}: an integer is required"},
    "thresholds_range": {"uk": "{key}: від {lo} до {hi}", "en": "{key}: from {lo} to {hi}"},
    "thresholds_gap": {
        "uk": "попередження має бути щонайменше на {gap} % вище за вимкнення",
        "en": "the warning must be at least {gap} % above the shutdown",
    },
    "thresholds_refused": {
        "uk": "пороги не збережено: {error}",
        "en": "thresholds not saved: {error}",
    },
    "thresholds_write_failed": {
        "uk": "пороги не записано: {error}",
        "en": "could not write thresholds: {error}",
    },
    "scan_none": {
        "uk": "Станцій BLUETTI поруч не знайдено. Увімкніть Bluetooth на станції й повторіть.",
        "en": "No BLUETTI stations found nearby. Turn on Bluetooth on the station and retry.",
    },
    "scan_header": {
        "uk": "Знайдено (назва, адреса, модель):",
        "en": "Found (name, address, model):",
    },
    "scan_hint": {
        "uk": "Виберіть СВОЮ станцію (номер на корпусі) і виконайте: {app} set-station НАЗВА",
        "en": "Pick YOUR station (serial on the case) and run: {app} set-station NAME",
    },
    "station_unknown_model": {
        "uk": "Невідома модель у назві «{name}».",
        "en": "Unknown model in the name '{name}'.",
    },
    "station_unsupported": {
        "uk": "Модель {model} не віддає напругу на вході, тож сторож не відрізнить вимкнення світла. Не підтримується.",
        "en": "Model {model} does not report input voltage, so a power outage cannot be detected. Not supported.",
    },
    "station_not_found": {
        "uk": "Станцію «{name}» не знайдено поруч.",
        "en": "Station '{name}' was not found nearby.",
    },
    "station_encryption_unknown": {
        "uk": "Не вдалось визначити, чи шифрує станція звʼязок. Вкажіть --encrypted yes або no.",
        "en": "Could not detect whether the station encrypts the link. Pass --encrypted yes or no.",
    },
    "station_saved": {
        "uk": "Збережено в {path}: {name}, модель {model}, шифрування {encrypted}.",
        "en": "Saved to {path}: {name}, model {model}, encryption {encrypted}.",
    },
    "config_broken": {
        "uk": "Файл налаштувань {path} зіпсований ({error}). Виправте або видаліть його й повторіть.",
        "en": "The config file {path} is broken ({error}). Fix or delete it and retry.",
    },
    "read_failed": {
        "uk": "Станцію «{name}» не прочитано. Перевірте, що Bluetooth увімкнено, станція поруч, "
        "назва точна ({app} scan) і її зараз не читає інша програма.",
        "en": "Station '{name}' was not read. Check that Bluetooth is on, the station is near, "
        "the name is exact ({app} scan) and no other program is reading it right now.",
    },
    "station_untested": {
        "uk": "Увага: сторож перевірено лише на Elite 320. Модель {model} має працювати за профілем бібліотеки, але не перевірена. Перевірте командою: {app} once",
        "en": "Note: the watchdog was tested only on an Elite 320. Model {model} should work via the library profile but is untested. Check with: {app} once",
    },
    "armed": {
        "uk": "Автовимкнення ПК увімкнено ({path}): при критичному заряді без світла ПК вимкнеться.",
        "en": "Auto shutdown is armed ({path}): on critical battery during an outage the PC shuts down.",
    },
    "disarmed": {
        "uk": "Автовимкнення ПК вимкнено ({path}).",
        "en": "Auto shutdown is disarmed ({path}).",
    },
}


def lang() -> str:
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(variable, "")
        if value:
            return "uk" if value.lower().startswith("uk") else "en"
    return "en"


def tr(message_id: str, /, **kwargs) -> str:
    return MESSAGES[message_id][lang()].format(app=APP, **kwargs)


# ---------------------------------------------------------------- readings


@dataclass(frozen=True)
class Reading:
    voltage_in: float
    watts_in: int
    watts_out: int
    percent: int

    @property
    def on_battery(self) -> bool:
        return self.voltage_in < MIN_GRID_VOLTAGE

    @property
    def stuck_on_battery(self) -> bool:
        """Grid voltage is present, but the battery feeds the load (seen as error E114)."""
        return not self.on_battery and self.watts_in == 0 and self.watts_out > 0


def parse_reading(raw: dict | None) -> Reading | None:
    """Station answer into a reading. Anything incomplete or impossible counts as no data."""
    if not raw:
        return None
    try:
        voltage = float(raw["ac_input_voltage"])
        watts_in = int(raw["ac_input_power"])
        watts_out = int(raw["ac_output_power"])
        percent = int(raw["total_battery_percent"])
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
    if not 0 <= voltage <= MAX_VOLTAGE:
        return None
    if not (0 <= watts_in <= MAX_WATTS and 0 <= watts_out <= MAX_WATTS):
        return None
    if not 0 <= percent <= 100:
        return None
    return Reading(voltage, watts_in, watts_out, percent)


# ---------------------------------------------------------------- thresholds


def stuck_readings(minutes: int) -> int:
    """Widget minutes into a number of readings. Never fewer than two: one zero was noise."""
    return max(2, math.ceil(minutes * 60 / READING_SECONDS))


def validate_thresholds(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError(tr("thresholds_dict"))
    result = {}
    for key, (lo, hi) in THRESHOLD_RANGES.items():
        value = data.get(key)
        if type(value) is not int:
            raise ValueError(tr("thresholds_int", key=key))
        if not lo <= value <= hi:
            raise ValueError(tr("thresholds_range", key=key, lo=lo, hi=hi))
        result[key] = value
    if result["low"] < result["critical"] + MIN_LOW_CRITICAL_GAP:
        raise ValueError(tr("thresholds_gap", gap=MIN_LOW_CRITICAL_GAP))
    return result


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict:
    """A broken or impossible file does not stop the watchdog: defaults are used then."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_THRESHOLDS)
    except (OSError, ValueError) as exc:
        log.warning("thresholds not read (%s), using defaults", exc)
        return dict(DEFAULT_THRESHOLDS)
    try:
        return validate_thresholds(data)
    except ValueError as exc:
        log.warning("thresholds rejected (%s), using defaults", exc)
        return dict(DEFAULT_THRESHOLDS)


def write_thresholds(path: Path, data: dict) -> None:
    _write_json(Path(path), validate_thresholds(data))


def main_set_thresholds(argv: list[str], path: Path = THRESHOLDS_PATH) -> int:
    """Called by the widget. Exit 0 saved, 2 values rejected, 1 could not write."""
    parser = argparse.ArgumentParser(prog=f"{APP} set-thresholds")
    parser.add_argument("--low", type=int, required=True)
    parser.add_argument("--critical", type=int, required=True)
    parser.add_argument("--stuck-minutes", type=int, required=True)
    args = parser.parse_args(argv)
    data = {"low": args.low, "critical": args.critical, "stuck_minutes": args.stuck_minutes}
    try:
        write_thresholds(path, data)
    except ValueError as exc:
        print(tr("thresholds_refused", error=exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(tr("thresholds_write_failed", error=exc), file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- state machine


class Machine:
    """Grid / outage state. Pure logic, no hardware."""

    def __init__(
        self,
        low: int = LOW_PERCENT,
        critical: int = CRITICAL_PERCENT,
        confirm: int = CONFIRM_READINGS,
        no_data_limit: int = NO_DATA_LIMIT,
        stuck_limit: int = STUCK_LIMIT,
    ):
        self.low = low
        self.critical = critical
        self.confirm = confirm
        self.no_data_limit = no_data_limit
        self.stuck_limit = stuck_limit
        self.stuck_minutes = STUCK_MINUTES
        self.state = "grid"
        self.low_sent = False
        self.critical_sent = False
        self.no_data_sent = False
        self.stuck_sent = False
        # Lowest charge seen while running on battery (outage or stuck station).
        self.min_percent: int | None = None
        self._battery_run = 0
        self._recharge_run = 0
        self._grid_run = 0
        self._critical_run = 0
        self._no_data_run = 0
        self._stuck_run = 0

    def apply_thresholds(self, thresholds: dict) -> None:
        self.low = thresholds["low"]
        self.critical = thresholds["critical"]
        self.stuck_minutes = thresholds["stuck_minutes"]
        self.stuck_limit = stuck_readings(thresholds["stuck_minutes"])

    def step(self, reading: Reading | None) -> list[str]:
        if reading is None:
            return self._step_no_data()
        events: list[str] = []
        self._no_data_run = 0
        if self.no_data_sent:
            self.no_data_sent = False
            events.append("data_back")
        if self.state == "grid":
            events.extend(self._step_grid(reading))
        else:
            events.extend(self._step_outage(reading))
        return events

    def _step_no_data(self) -> list[str]:
        # A failed read is not evidence of anything, so the counters keep their
        # values: otherwise every other read failing would block the shutdown.
        # Only a real reading that says otherwise resets them.
        self._no_data_run += 1
        if self._no_data_run >= self.no_data_limit and not self.no_data_sent:
            self.no_data_sent = True
            return ["no_data"]
        return []

    def _step_grid(self, reading: Reading) -> list[str]:
        if not reading.on_battery:
            self._battery_run = 0
            return self._check_stuck(reading)
        self._stuck_run = 0
        self._battery_run += 1
        if self._battery_run < self.confirm:
            return []
        self._battery_run = 0
        self.state = "outage"
        if self.stuck_sent:
            # It was already running on battery: the same drain goes on.
            self.stuck_sent = False
        else:
            self._start_battery_period()
        # The reading that confirmed the outage does not count toward critical.
        return ["outage", *self._check_battery(reading, count_critical=False)]

    def _start_battery_period(self) -> None:
        self.min_percent = None
        self._critical_run = 0
        self._recharge_run = 0

    def _end_battery_period(self) -> None:
        self.low_sent = False
        self.critical_sent = False
        self.min_percent = None
        self._critical_run = 0
        self._recharge_run = 0

    def _check_stuck(self, reading: Reading) -> list[str]:
        if not reading.stuck_on_battery:
            self._stuck_run = 0
            if self.stuck_sent:
                self.stuck_sent = False
                self._end_battery_period()
                return ["unstuck"]
            return []
        self._stuck_run += 1
        if self.stuck_sent:
            # A stuck station drains the battery exactly like an outage.
            return self._check_battery(reading)
        if self._stuck_run >= self.stuck_limit:
            self.stuck_sent = True
            self._start_battery_period()
            return ["stuck", *self._check_battery(reading, count_critical=False)]
        return []

    def _step_outage(self, reading: Reading) -> list[str]:
        if not reading.on_battery:
            self._critical_run = 0
            self._grid_run += 1
            if self._grid_run < self.confirm:
                return []
            self._grid_run = 0
            self.state = "grid"
            self._end_battery_period()
            return ["restored"]
        self._grid_run = 0
        return self._check_battery(reading)

    def _check_battery(self, reading: Reading, count_critical: bool = True) -> list[str]:
        """Low and critical charge while the battery feeds the PC."""
        events: list[str] = []
        if self.min_percent is None or reading.percent < self.min_percent:
            self.min_percent = reading.percent
            self._recharge_run = 0
        elif reading.percent >= self.min_percent + RECHARGE_MARGIN:
            self._recharge_run += 1
            if self._recharge_run >= self.confirm:
                # Charged meanwhile, so the power was back: warnings and the
                # once-per-outage shutdown start anew.
                self.min_percent = reading.percent
                self.low_sent = False
                self.critical_sent = False
                self._critical_run = 0
                self._recharge_run = 0
                events.append("recharged")
        else:
            self._recharge_run = 0
        events.extend(self._check_low(reading))
        if not count_critical:
            return events
        if reading.percent > self.critical:
            self._critical_run = 0
            return events
        self._critical_run += 1
        if self._critical_run >= self.confirm:
            # Repeated while the charge stays critical: the shutdown may have failed
            # or auto shutdown may have been armed later.
            events.append("critical_again" if self.critical_sent else "critical")
            self.critical_sent = True
        return events

    def on_grid_power(self, reading: Reading | None) -> bool:
        """The grid really feeds the PC: a real reading, not a stuck station."""
        return (
            self.state == "grid"
            and not self.stuck_sent
            and reading is not None
            and not reading.on_battery
            and not reading.stuck_on_battery
        )

    def _check_low(self, reading: Reading) -> list[str]:
        if reading.percent <= self.low and not self.low_sent:
            self.low_sent = True
            return ["low"]
        return []


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_machine(
    machine: Machine, path: Path, reading: Reading | None = None, configured: bool = True
) -> None:
    _write_json(
        path,
        {
            "configured": configured,
            "state": machine.state,
            "low_sent": machine.low_sent,
            "critical_sent": machine.critical_sent,
            "no_data_sent": machine.no_data_sent,
            "stuck_sent": machine.stuck_sent,
            "min_percent": machine.min_percent,
            "thresholds": {
                "low": machine.low,
                "critical": machine.critical,
                "stuck_minutes": machine.stuck_minutes,
            },
            "last": asdict(reading) if reading else None,
            "updated": datetime.now().isoformat(timespec="seconds"),
        },
    )


def load_machine(path: Path, **kwargs) -> Machine:
    machine = Machine(**kwargs)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return machine
    if not isinstance(data, dict) or data.get("state") not in ("grid", "outage"):
        return machine
    machine.state = data["state"]
    machine.low_sent = bool(data.get("low_sent"))
    # critical_sent is NOT restored: after a restart the first critical reading
    # must speak again (shut down, or tell the person why it will not).
    machine.no_data_sent = bool(data.get("no_data_sent"))
    machine.stuck_sent = bool(data.get("stuck_sent"))
    min_percent = data.get("min_percent")
    if type(min_percent) is int and 0 <= min_percent <= 100:
        machine.min_percent = min_percent
    return machine


# ---------------------------------------------------------------- PC actions


def run_command(cmd: list[str]) -> tuple[int, str]:
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    return res.returncode, res.stdout


class Actions:
    """Actions on the PC. One failing does not stop the others.

    Only the power profile is on by default. Monitor brightness (ddcutil) is
    opt-in through config.json.
    """

    def __init__(
        self,
        run=run_command,
        state_dir: Path = STATE_DIR,
        save_brightness: int | None = None,
        power_saver: bool = True,
    ):
        self.run = run
        self.state_dir = Path(state_dir)
        self.save_brightness = save_brightness
        self.power_saver = power_saver
        self.snapshot_path = self.state_dir / "before_save.json"
        self.armed_path = self.state_dir / "poweroff.armed"
        self.poweroff_done_path = self.state_dir / "poweroff.done"

    def _try(self, cmd: list[str]) -> str | None:
        try:
            code, out = self.run(cmd)
        except Exception as exc:
            log.warning("command %s failed: %s", cmd[0], exc)
            return None
        if code != 0:
            log.warning("command %s returned %s", cmd[0], code)
            return None
        return out

    def _read_profile(self) -> str | None:
        if not self.power_saver:
            return None
        out = self._try(["powerprofilesctl", "get"])
        return out.strip() or None if out is not None else None

    def _read_brightness(self) -> int | None:
        if self.save_brightness is None:
            return None
        out = self._try(["ddcutil", "getvcp", "10", "--brief"])
        found = re.search(r"VCP 10 C (\d+) \d+", out or "")
        return int(found.group(1)) if found else None

    def _set_brightness(self, value: int) -> bool:
        return self._try(["ddcutil", "setvcp", "10", str(value)]) is not None

    def _set_profile(self, value: str) -> bool:
        return self._try(["powerprofilesctl", "set", value]) is not None

    def apply_save(self) -> None:
        profile = self._read_profile()
        brightness = self._read_brightness()
        snapshot = self._read_snapshot() or {}
        # The snapshot is only extended: what is already in it is older and correct.
        merged = {"profile": profile, "brightness": brightness, **snapshot}
        if merged != snapshot:
            self._write_snapshot(merged)
        if self.power_saver:
            self._set_profile("power-saver")
        if (
            self.save_brightness is not None
            and brightness is not None
            and brightness > self.save_brightness
        ):
            self._set_brightness(self.save_brightness)

    def restore(self) -> None:
        """Restores the snapshot. Only what could not be restored stays in it."""
        snapshot = self._read_snapshot()
        if snapshot is None:
            return
        left = {}
        if snapshot.get("profile") and not self._set_profile(snapshot["profile"]):
            left["profile"] = snapshot["profile"]
        if snapshot.get("brightness") is not None and not self._set_brightness(
            snapshot["brightness"]
        ):
            left["brightness"] = snapshot["brightness"]
        if not left:
            self.snapshot_path.unlink(missing_ok=True)
        elif left != snapshot:
            self._write_snapshot(left)

    def _read_snapshot(self) -> dict | None:
        try:
            text = self.snapshot_path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return data
        log.warning("settings snapshot is broken, removing it")
        self.snapshot_path.unlink(missing_ok=True)
        return None

    def _write_snapshot(self, snapshot: dict) -> None:
        try:
            _write_json(self.snapshot_path, snapshot)
        except OSError as exc:
            log.warning("settings snapshot not written: %s", exc)

    def is_armed(self) -> bool:
        return self.armed_path.exists()

    def will_poweroff(self) -> bool:
        return self.armed_path.exists() and not self.poweroff_done_path.exists()

    def poweroff_if_armed(self) -> bool:
        """Once per outage: if a person turns the PC on again, that is their decision.

        The service starts before login, so without this mark the PC would power
        off in a loop two minutes after every boot.
        """
        if not self.will_poweroff():
            return False
        try:
            self.poweroff_done_path.touch()
        except OSError as exc:
            log.warning("shutdown mark not written: %s", exc)
        if self._try(["systemctl", "poweroff"]) is not None:
            return True
        self.clear_poweroff_done()
        return False

    def clear_poweroff_done(self) -> None:
        try:
            self.poweroff_done_path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("shutdown mark not removed: %s", exc)


def _tell(notify, text: str) -> None:
    log.info("%s", text)
    try:
        notify(text)
    except Exception as exc:
        log.warning("notification failed: %s", exc)


def _poweroff(actions: Actions, notify) -> None:
    if actions.will_poweroff() and not actions.poweroff_if_armed():
        _tell(notify, tr("poweroff_failed"))


def handle_events(events: list[str], reading: Reading | None, actions: Actions, notify) -> None:
    percent = f"{reading.percent} %" if reading else tr("unknown")
    for event in events:
        if event == "outage":
            actions.apply_save()
            _tell(notify, tr("outage", percent=percent))
        elif event == "restored":
            actions.clear_poweroff_done()
            actions.restore()
            _tell(notify, tr("restored"))
        elif event == "low":
            _tell(notify, tr("low", percent=percent))
        elif event == "critical":
            if actions.will_poweroff():
                key = "critical_poweroff"
            elif actions.is_armed():
                key = "critical_done"
            else:
                key = "critical_manual"
            _tell(notify, tr(key, percent=percent))
            _poweroff(actions, notify)
        elif event == "recharged":
            actions.clear_poweroff_done()
        elif event == "critical_again":
            _poweroff(actions, notify)
        elif event == "no_data":
            _tell(notify, tr("no_data"))
        elif event == "data_back":
            _tell(notify, tr("data_back"))
        elif event == "stuck":
            # Emitted only together with a real reading (see Machine._check_stuck).
            _tell(notify, tr("stuck", voltage=f"{reading.voltage_in:.0f}", percent=percent))
        elif event == "unstuck":
            # The shutdown mark is cleared by tick(): this reading is real grid power.
            _tell(notify, tr("unstuck"))


def tick(machine: Machine, reading: Reading | None, actions: Actions, notify) -> None:
    handle_events(machine.step(reading), reading, actions, notify)
    if machine.on_grid_power(reading):
        actions.clear_poweroff_done()
        actions.restore()


def resume(machine: Machine, actions: Actions) -> None:
    """After a restart in the middle of an outage."""
    if machine.state == "outage":
        actions.apply_save()


def append_reading(state_dir: Path, reading: Reading | None, state: str, now: datetime) -> None:
    path = Path(state_dir) / f"readings-{now:%Y-%m}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["time", "state", "voltage_in", "watts_in", "watts_out", "percent"])
        values = (
            [reading.voltage_in, reading.watts_in, reading.watts_out, reading.percent]
            if reading
            else ["", "", "", ""]
        )
        writer.writerow([now.isoformat(timespec="seconds"), state, *values])


def cycle(
    machine: Machine,
    reading: Reading | None,
    actions: Actions,
    notify,
    state_dir: Path,
    now: datetime,
) -> None:
    """One pass. Nothing here may stop the station from being read."""
    try:
        tick(machine, reading, actions, notify)
    except Exception:
        log.exception("watchdog actions failed")
    try:
        save_machine(machine, Path(state_dir) / "state.json", reading)
    except OSError as exc:
        log.warning("state not written: %s", exc)
    try:
        append_reading(state_dir, reading, machine.state, now)
    except OSError as exc:
        log.warning("readings log not written: %s", exc)


# ---------------------------------------------------------------- configuration


@dataclass(frozen=True)
class Station:
    name: str
    model: str
    encrypted: bool


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("config not read (%s)", exc)
        return {}
    return data if isinstance(data, dict) else {}


def station_from_config(config: dict) -> Station | None:
    """The full station name (model + serial) is required: in one flat there can be
    two stations of the same model, and connecting to someone else's is not allowed."""
    station = config.get("station")
    if not isinstance(station, dict):
        return None
    name, model, encrypted = station.get("name"), station.get("model"), station.get("encrypted")
    if not (isinstance(name, str) and isinstance(model, str) and isinstance(encrypted, bool)):
        return None
    if guess_model(name) != model:
        return None
    return Station(name, model, encrypted)


def actions_from_config(config: dict, run=run_command, state_dir: Path = STATE_DIR) -> Actions:
    brightness = config.get("brightness")
    if type(brightness) is not int or not 0 <= brightness <= 100:
        brightness = None
    return Actions(
        run=run,
        state_dir=state_dir,
        save_brightness=brightness,
        power_saver=config.get("power_saver") is not False,
    )


def guess_model(name: str) -> str | None:
    if EL320_NAME_RE.match(name):
        return "EL320"
    from bluetti_bt_lib.devices import DEVICE_NAME_RE

    found = DEVICE_NAME_RE.match(name)
    return found[1] if found else None


def merge_registers(fields: list[tuple[int, int]], max_gap: int = 4) -> list[tuple[int, int]]:
    """(address, size) of the fields into as few reads as possible."""
    ranges: list[list[int]] = []
    for address, size in sorted(fields):
        end = address + size
        if ranges and address <= ranges[-1][1] + max_gap:
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([address, end])
    return [(start, end - start) for start, end in ranges]


def model_profile(model: str):
    """(bluetti-bt-lib device, registers to read) or None if the model is unusable."""
    from bluetti_bt_lib.devices import DEVICES
    from bluetti_bt_lib.devices.el100v2 import EL100V2

    if model == "EL320":
        device = EL100V2()
        # Selecting a battery pack is a write to the station; never do it.
        device.max_packs = 0
        return device, list(EL320_REGISTERS)
    station_class = DEVICES.get(model)
    if station_class is None:
        return None
    device = station_class()
    # Selecting a battery pack is a write to the station (AC300 has 4 packs); never do it.
    device.max_packs = 0
    fields = {str(field.name): field for field in device.fields}
    if not all(name in fields for name in NEEDED_FIELDS):
        return None
    return device, merge_registers([(fields[n].address, fields[n].size) for n in NEEDED_FIELDS])


# ---------------------------------------------------------------- Bluetooth (read-only)


async def _find(name: str, timeout: int = 20):
    from bleak import BleakScanner

    return await BleakScanner.find_device_by_filter(
        lambda device, _adv: device.name == name, timeout=timeout
    )


async def read_station(station: Station) -> dict | None:
    """Read only. There is no writing to the station here and there must never be."""
    from bluetti_bt_lib.bluetooth import DeviceReader, DeviceReaderConfig
    from bluetti_bt_lib.registers.ReadableRegisters import ReadableRegisters

    profile = model_profile(station.model)
    if profile is None:
        return None
    device, registers = profile
    found = await _find(station.name)
    if found is None:
        return None
    reader = DeviceReader(
        found.address,
        device,
        asyncio.Future,
        DeviceReaderConfig(timeout=30, use_encryption=station.encrypted),
    )
    return await reader.read([ReadableRegisters(a, n) for a, n in registers])


async def read_once(station: Station) -> Reading | None:
    try:
        return parse_reading(await asyncio.wait_for(read_station(station), READ_TIMEOUT_SECONDS))
    except Exception as exc:
        log.warning("station not read: %s", exc)
        return None


async def scan(timeout: int = 15) -> list[tuple[str, str, str]]:
    from bleak import BleakScanner

    found = {}
    for device in await BleakScanner.discover(timeout=timeout):
        model = guess_model(device.name) if device.name else None
        if model:
            found[device.name] = (device.name, device.address, model)
    return sorted(found.values())


async def detect_encryption(address: str) -> bool | None:
    from bluetti_bt_lib.bluetooth.device_recognizer import recognize_device

    result = await recognize_device(address, asyncio.Future)
    return None if result is None else bool(result.encrypted)


def desktop_notify(text: str) -> None:
    run_command(["notify-send", "-u", "critical", "-a", tr("title"), tr("title"), text])


async def watch_once(
    machine: Machine,
    state_dir: Path = STATE_DIR,
    config_path: Path = CONFIG_PATH,
    thresholds_path: Path = THRESHOLDS_PATH,
    reader=None,
    notify=desktop_notify,
    run=run_command,
) -> Station | None:
    """One iteration of the service. Config and thresholds are re-read every time,
    so set-station and the widget take effect without a restart."""
    config = load_config(config_path)
    station = station_from_config(config)
    machine.apply_thresholds(load_thresholds(thresholds_path))
    state_path = Path(state_dir) / "state.json"
    if station is None:
        try:
            save_machine(machine, state_path, None, configured=False)
        except OSError as exc:
            log.warning("state not written: %s", exc)
        return None
    reading = await (reader or read_once)(station)
    actions = actions_from_config(config, run=run, state_dir=state_dir)
    cycle(machine, reading, actions, notify, state_dir, datetime.now())
    log.info("state %s, reading %s", machine.state, reading)
    return station


async def run_forever() -> None:
    machine = load_machine(STATE_DIR / "state.json")
    resume(machine, actions_from_config(load_config()))
    log.info("watchdog started, state %s", machine.state)
    warned = False
    while True:
        station = await watch_once(machine)
        if station is None and not warned:
            log.warning("%s", tr("not_configured"))
        warned = station is None
        await asyncio.sleep(POLL_SECONDS)


# ---------------------------------------------------------------- command line


def _load_config_strict(path: Path) -> dict:
    """Like load_config, but a broken file is an error, so it is never overwritten."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    return data


def set_station(name: str, encrypted: str, config_path: Path = CONFIG_PATH) -> int:
    model = guess_model(name)
    if model is None:
        print(tr("station_unknown_model", name=name), file=sys.stderr)
        return 2
    if model_profile(model) is None:
        print(tr("station_unsupported", model=model), file=sys.stderr)
        return 2
    try:
        config = _load_config_strict(config_path)
    except (OSError, ValueError) as exc:
        print(tr("config_broken", path=config_path, error=exc), file=sys.stderr)
        return 1
    if encrypted in ("yes", "no"):
        use_encryption = encrypted == "yes"
    elif model == "EL320":
        use_encryption = True
    else:
        found = asyncio.run(_find(name))
        if found is None:
            print(tr("station_not_found", name=name), file=sys.stderr)
            return 1
        use_encryption = asyncio.run(detect_encryption(found.address))
        if use_encryption is None:
            print(tr("station_encryption_unknown"), file=sys.stderr)
            return 1
    config["station"] = {"name": name, "model": model, "encrypted": use_encryption}
    _write_json(Path(config_path), config)
    print(tr("station_saved", path=config_path, name=name, model=model, encrypted=use_encryption))
    if model != "EL320":
        print(tr("station_untested", model=model))
    return 0


def set_armed(armed: bool, state_dir: Path = STATE_DIR) -> int:
    path = Path(state_dir) / "poweroff.armed"
    if armed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)
    print(tr("armed" if armed else "disarmed", path=path))
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog=APP, description="Keeps a Linux PC going through a blackout on a BLUETTI power station.")
    parser.add_argument("--version", action="version", version=f"{APP} {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="watch the station (what the systemd service runs)")
    sub.add_parser("scan", help="list BLUETTI stations nearby")
    p_station = sub.add_parser("set-station", help="choose your station by its full name")
    p_station.add_argument("name")
    p_station.add_argument("--encrypted", choices=["auto", "yes", "no"], default="auto")
    sub.add_parser("once", help="read the station once and print the reading")
    sub.add_parser("arm", help="arm auto shutdown at critical battery")
    sub.add_parser("disarm", help="disarm auto shutdown")
    sub.add_parser("restore", help="restore saved settings now")
    sub.add_parser("set-thresholds", help="used by the widget", add_help=False)
    args, rest = parser.parse_known_args(argv)
    if args.command == "set-thresholds":
        sys.exit(main_set_thresholds(rest))
    if rest:
        parser.error(f"unrecognized arguments: {' '.join(rest)}")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("bleak").setLevel(logging.WARNING)
    logging.getLogger("bluetti_bt_lib").setLevel(logging.ERROR)

    if args.command == "run":
        asyncio.run(run_forever())
    elif args.command == "scan":
        stations = asyncio.run(scan())
        if not stations:
            print(tr("scan_none"))
            sys.exit(1)
        print(tr("scan_header"))
        for name, address, model in stations:
            print(f"  {name}  {address}  {model}")
        print(tr("scan_hint"))
    elif args.command == "set-station":
        sys.exit(set_station(args.name, args.encrypted))
    elif args.command == "once":
        station = station_from_config(load_config(CONFIG_PATH))
        if station is None:
            print(tr("not_configured"), file=sys.stderr)
            sys.exit(2)
        reading = asyncio.run(read_once(station))
        if reading is None:
            print(tr("read_failed", name=station.name), file=sys.stderr)
            sys.exit(1)
        print(reading)
    elif args.command in ("arm", "disarm"):
        sys.exit(set_armed(args.command == "arm"))
    elif args.command == "restore":
        actions_from_config(load_config()).restore()


if __name__ == "__main__":
    main()
