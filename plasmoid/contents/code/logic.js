.pragma library
// Widget logic without QML: what to show for the watchdog state. Tests: plasmoid/tests/.

// Elite 320 user manual, Q5: 3200 Wh, depth of discharge 95 %, inverter
// efficiency over 92 %, self-consumption about 10 W. Other models: a rough guess.
var CAPACITY_WH = 3200;
var USABLE = 0.95 * 0.92;
var SELF_WATTS = 10;

// The watchdog writes its state every ~70 s. Older than 5 min = it is not running.
var STALE_SECONDS = 300;

// The same rule as _xdg_dir() in bluetti_power_watch.py: only an absolute
// XDG_STATE_HOME counts, anything else means ~/.local/state.
var STATE_DIR_SHELL = 'case "$XDG_STATE_HOME" in /*) b="$XDG_STATE_HOME";; *) b="$HOME/.local/state";; esac; '
    + 'd="$b/bluetti-power-watch"; ';

// Time now, mtime of state.json, auto shutdown (0 off, 1 armed, 2 armed but already
// done during this outage), then state.json itself.
var READ_COMMAND = STATE_DIR_SHELL
    + "date +%s; "
    + 'stat -c %Y "$d/state.json" 2>/dev/null || echo 0; '
    + 'if [ -e "$d/poweroff.armed" ]; then if [ -e "$d/poweroff.done" ]; then echo 2; else echo 1; fi; else echo 0; fi; '
    + 'cat "$d/state.json" 2>/dev/null';

var T = {
    hours: { uk: "год", en: "h" },
    minutes: { uk: "хв", en: "min" },
    runtime: { uk: "Вистачить приблизно на {d}.", en: "Enough for about {d}." },
    watch_off: { uk: "Сторож не працює", en: "Watchdog is not running" },
    watch_off_hint: {
        uk: "Немає файлу стану. Перевірте службу: systemctl --user status bluetti-power-watch",
        en: "No state file. Check the service: systemctl --user status bluetti-power-watch",
    },
    watch_silent: { uk: "Сторож мовчить {m} хв", en: "Watchdog silent for {m} min" },
    watch_silent_hint: {
        uk: "Дані нижче застарілі. Перевірте службу bluetti-power-watch.",
        en: "The data below is stale. Check the bluetti-power-watch service.",
    },
    setup: { uk: "Станцію не налаштовано", en: "No station configured" },
    setup_hint: {
        uk: "У терміналі: bluetti-power-watch scan, потім bluetti-power-watch set-station НАЗВА",
        en: "In a terminal: bluetti-power-watch scan, then bluetti-power-watch set-station NAME",
    },
    station: { uk: "Станція не відповідає", en: "Station does not answer" },
    station_hint: {
        uk: "Стан світла і заряду невідомий. Можливо, завис Bluetooth.",
        en: "Power and battery state unknown. Bluetooth may be stuck.",
    },
    outage: { uk: "Немає світла", en: "Power outage" },
    outage_hint: { uk: "ПК живиться від батареї.", en: "The PC runs on battery." },
    stuck: { uk: "Станція не бере з мережі", en: "Station is not taking grid power" },
    stuck_hint: {
        uk: "Напруга є, але живить батарея (мабуть, E114). Перепідключіть вилку станції.",
        en: "Voltage is present but the battery feeds the load (probably E114). Re-plug the station's AC cable.",
    },
    grid: { uk: "Світло є", en: "Power is on" },
    charging: { uk: "Станція заряджається.", en: "The station is charging." },
    thresholds: {
        uk: "попередження на {low} %, вимкнення ПК на {critical} %, «не бере з мережі» через {stuck} хв",
        en: "warning at {low} %, PC shutdown at {critical} %, 'not taking grid power' after {stuck} min",
    },
    tooltip: { uk: "Станція Bluetti", en: "BLUETTI station" },
    charge: { uk: "Заряд", en: "Charge" },
    voltage: { uk: "Напруга в мережі", en: "Grid voltage" },
    input: { uk: "Бере з мережі", en: "Grid input" },
    output: { uk: "Віддає", en: "Output" },
    volts: { uk: "В", en: "V" },
    watts: { uk: "Вт", en: "W" },
    thresholds_label: { uk: "Пороги", en: "Thresholds" },
    pending: { uk: "Нові пороги застосуються з наступним заміром.", en: "New thresholds apply with the next reading." },
    not_saved: { uk: "Пороги не збережено", en: "Thresholds not saved" },
    bad_values: { uk: "неможливі значення порогів", en: "impossible threshold values" },
    write_failed: { uk: "помилка запису", en: "write error" },
    armed: { uk: "Автовимкнення ПК: увімкнене", en: "Auto shutdown: armed" },
    armed_done: {
        uk: "Автовимкнення ПК: увімкнене, у цьому вимкненні світла вже спрацювало",
        en: "Auto shutdown: armed, already ran during this outage",
    },
    disarmed: { uk: "Автовимкнення ПК: вимкнене", en: "Auto shutdown: off" },
    updated: { uk: "Оновлено {s} с тому", en: "Updated {s} s ago" },
    charge_short: { uk: "Заряд", en: "Charge" },
    cfg_low: { uk: "Попередження «заряд низький», %:", en: "'Battery low' warning, %:" },
    cfg_critical: { uk: "Вимкнення ПК, %:", en: "PC shutdown, %:" },
    cfg_stuck: { uk: "«Станція не бере з мережі» через, хв:", en: "'Not taking grid power' after, min:" },
    cfg_note: {
        uk: "Вимкнення ПК спрацьовує, лише коли немає світла і автовимкнення увімкнене (bluetti-power-watch arm). Попередження має бути щонайменше на 5 % вище за вимкнення. Сторож застосує нові пороги з наступним заміром, приблизно за хвилину.",
        en: "The PC shuts down only during an outage and only when auto shutdown is armed (bluetti-power-watch arm). The warning must be at least 5 % above the shutdown. The watchdog applies new thresholds with the next reading, in about a minute.",
    },
    cfg_page: { uk: "Пороги", en: "Thresholds" },
    err_low: { uk: "Попередження: від 10 до 90.", en: "Warning: from 10 to 90." },
    err_critical: { uk: "Вимкнення: від 5 до 50.", en: "Shutdown: from 5 to 50." },
    err_stuck: { uk: "«Не бере з мережі»: від 1 до 60.", en: "'Not taking grid power': from 1 to 60." },
    err_gap: {
        uk: "Попередження має бути щонайменше на 5 % вище за вимкнення.",
        en: "The warning must be at least 5 % above the shutdown.",
    },
    err_not_saved: { uk: "Такі пороги не буде збережено.", en: "These thresholds will not be saved." },
};

function language(localeName) {
    return (localeName || "").toLowerCase().indexOf("uk") === 0 ? "uk" : "en";
}

function tr(id, lang, values) {
    var text = T[id][lang === "uk" ? "uk" : "en"];
    for (var key in values || {}) {
        text = text.split("{" + key + "}").join(String(values[key]));
    }
    return text;
}

// Output of READ_COMMAND.
function parse(stdout) {
    var lines = (stdout || "").split("\n");
    var mark = (lines[2] || "").trim();
    var info = {
        now: parseInt(lines[0], 10) || 0,
        mtime: parseInt(lines[1], 10) || 0,
        armed: mark === "1" || mark === "2",
        done: mark === "2",
        state: null,
    };
    var text = lines.slice(3).join("\n").trim();
    if (text) {
        try {
            var data = JSON.parse(text);
            if (data && typeof data === "object") {
                info.state = data;
            }
        } catch (e) {
            info.state = null;
        }
    }
    return info;
}

function armedText(info, lang) {
    if (!info.armed) {
        return tr("disarmed", lang);
    }
    return tr(info.done ? "armed_done" : "armed", lang);
}

function runtimeMinutes(percent, wattsOut) {
    var watts = Math.max(0, wattsOut || 0) + SELF_WATTS;
    return Math.round((CAPACITY_WH * USABLE * (percent / 100) / watts) * 60);
}

function formatDuration(minutes, lang) {
    var h = Math.floor(minutes / 60);
    var m = minutes % 60;
    var mm = m + " " + tr("minutes", lang);
    return h > 0 ? h + " " + tr("hours", lang) + " " + mm : mm;
}

function charging(last) {
    return !!last && last.watts_in > last.watts_out;
}

function iconName(percent, isCharging) {
    if (percent === null || percent === undefined) {
        return "dialog-warning";
    }
    var level = Math.max(0, Math.min(100, Math.floor(percent / 10) * 10));
    var padded = ("00" + level).slice(-3);
    return "battery-" + padded + (isCharging ? "-charging" : "");
}

// The thresholds the watchdog really uses (it writes them into state.json).
function thresholds(info) {
    var t = info.state ? info.state.thresholds : null;
    if (!t || typeof t !== "object") {
        return null;
    }
    return { low: t.low, critical: t.critical, stuck_minutes: t.stuck_minutes };
}

function thresholdsText(t, lang) {
    return tr("thresholds", lang, { low: t.low, critical: t.critical, stuck: t.stuck_minutes });
}

function sameThresholds(t, low, critical, stuckMinutes) {
    return !!t && t.low === low && t.critical === critical && t.stuck_minutes === stuckMinutes;
}

function isLow(percent, t) {
    return percent !== null && percent !== undefined && percent <= (t ? t.low : 20);
}

function _isInt(value) {
    return typeof value === "number" && Number.isInteger(value);
}

// Same limits as THRESHOLD_RANGES in bluetti_power_watch.py. The settings fields
// do NOT limit each other (mutual limits clipped values while the page was
// opening: a saved 8 % showed up and got saved as 7 %), so the impossible is
// caught here and never written.
function thresholdsError(low, critical, stuckMinutes, lang) {
    var checks = [
        ["err_low", low, 10, 90],
        ["err_critical", critical, 5, 50],
        ["err_stuck", stuckMinutes, 1, 60],
    ];
    for (var i = 0; i < checks.length; i++) {
        var c = checks[i];
        if (!_isInt(c[1]) || c[1] < c[2] || c[1] > c[3]) {
            return tr(c[0], lang);
        }
    }
    if (low < critical + 5) {
        return tr("err_gap", lang);
    }
    return "";
}

// sh runs this command, so only integers get into it. The "# nonce" tail makes
// each run a separate source (the engine would not run the same command twice).
function setCommand(low, critical, stuckMinutes, nonce) {
    if (!_isInt(low) || !_isInt(critical) || !_isInt(stuckMinutes) || !_isInt(nonce)) {
        return "";
    }
    return "bluetti-power-watch set-thresholds --low " + low + " --critical " + critical
        + " --stuck-minutes " + stuckMinutes + " # " + nonce;
}

function _runtimeHint(last, lang) {
    return tr("runtime", lang, { d: formatDuration(runtimeMinutes(last.percent, last.watts_out), lang) });
}

// kind: watch (watchdog silent), setup (no station), station (no answer),
// outage, stuck (not taking grid power), grid.
function status(info, lang) {
    if (!info.state || !info.mtime) {
        return { kind: "watch", title: tr("watch_off", lang), hint: tr("watch_off_hint", lang) };
    }
    var age = info.now - info.mtime;
    if (age > STALE_SECONDS) {
        return {
            kind: "watch",
            title: tr("watch_silent", lang, { m: Math.round(age / 60) }),
            hint: tr("watch_silent_hint", lang),
        };
    }
    var st = info.state;
    if (st.configured === false) {
        return { kind: "setup", title: tr("setup", lang), hint: tr("setup_hint", lang) };
    }
    var last = st.last;
    if (!last) {
        return { kind: "station", title: tr("station", lang), hint: tr("station_hint", lang) };
    }
    if (st.state === "outage") {
        return { kind: "outage", title: tr("outage", lang), hint: tr("outage_hint", lang) + " " + _runtimeHint(last, lang) };
    }
    if (st.stuck_sent) {
        return { kind: "stuck", title: tr("stuck", lang), hint: tr("stuck_hint", lang) + " " + _runtimeHint(last, lang) };
    }
    return { kind: "grid", title: tr("grid", lang), hint: charging(last) ? tr("charging", lang) : "" };
}
