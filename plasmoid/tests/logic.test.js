// Тести логіки віджета. Запуск: node --test plasmoid/tests/
// logic.js написаний для QML (.pragma library, без export), тож вантажимо його як текст.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const src = fs
  .readFileSync(path.join(__dirname, "../contents/code/logic.js"), "utf8")
  .replace(/^\.pragma library\s*$/m, "");
const L = {};
vm.runInNewContext(src, L);

const STATE = {
  state: "grid",
  low_sent: false,
  critical_sent: false,
  no_data_sent: false,
  stuck_sent: false,
  last: { voltage_in: 215.0, watts_in: 1133, watts_out: 357, percent: 98 },
  updated: "2026-11-08T17:34:49",
};

function out(state, { now = 1000, mtime = 990, armed = 1 } = {}) {
  return `${now}\n${mtime}\n${armed}\n${state === null ? "" : JSON.stringify(state)}\n`;
}

test("розбирає вивід команди", () => {
  const info = L.parse(out(STATE));
  assert.equal(info.now, 1000);
  assert.equal(info.mtime, 990);
  assert.equal(info.armed, true);
  assert.equal(info.state.last.percent, 98);
});

test("зламаний json не валить віджет", () => {
  const info = L.parse("1000\n990\n0\n{зламано");
  assert.equal(info.state, null);
  assert.equal(info.armed, false);
});

test("порожній вивід", () => {
  const info = L.parse("");
  assert.equal(info.state, null);
});

test("світло є", () => {
  assert.equal(L.status(L.parse(out(STATE)), "uk").kind, "grid");
});

test("немає світла", () => {
  const s = { ...STATE, state: "outage", last: { voltage_in: 0, watts_in: 0, watts_out: 400, percent: 80 } };
  assert.equal(L.status(L.parse(out(s)), "uk").kind, "outage");
});

test("станція застрягла (E114)", () => {
  const s = { ...STATE, stuck_sent: true, last: { voltage_in: 214.8, watts_in: 0, watts_out: 419, percent: 99 } };
  const st = L.status(L.parse(out(s)), "uk");
  assert.equal(st.kind, "stuck");
  assert.match(st.hint, /вилку/);
});

test("один нуль на вході без позначки сторожа ще не застрягання", () => {
  const s = { ...STATE, last: { voltage_in: 214.8, watts_in: 0, watts_out: 419, percent: 99 } };
  assert.equal(L.status(L.parse(out(s)), "uk").kind, "grid");
});

test("станція не відповідає", () => {
  const s = { ...STATE, last: null };
  assert.equal(L.status(L.parse(out(s)), "uk").kind, "station");
});

test("сторож мовчить понад 5 хвилин", () => {
  const st = L.status(L.parse(out(STATE, { now: 1000, mtime: 1000 - 301 })), "uk");
  assert.equal(st.kind, "watch");
});

test("файлу стану немає", () => {
  assert.equal(L.status(L.parse(out(null, { mtime: 0 })), "uk").kind, "watch");
});

test("свіжий файл не застарілий", () => {
  assert.equal(L.status(L.parse(out(STATE, { now: 1000, mtime: 1000 - 299 })), "uk").kind, "grid");
});

test("скільки вистачить: формула з інструкції Elite 320", () => {
  // 3200 Вт·год × 0,95 × 0,92 × 99 % ÷ (400 + 10) Вт = 6,75 год
  assert.equal(L.runtimeMinutes(99, 400), 405);
});

test("скільки вистачить без споживання не ділить на нуль", () => {
  assert.ok(Number.isFinite(L.runtimeMinutes(50, 0)));
});

test("формат тривалості", () => {
  assert.equal(L.formatDuration(405, "uk"), "6 год 45 хв");
  assert.equal(L.formatDuration(45, "uk"), "45 хв");
  assert.equal(L.formatDuration(120, "uk"), "2 год 0 хв");
});

test("значок батареї", () => {
  assert.equal(L.iconName(98, true), "battery-090-charging");
  assert.equal(L.iconName(100, false), "battery-100");
  assert.equal(L.iconName(7, false), "battery-000");
  assert.equal(L.iconName(null, false), "dialog-warning");
});

test("заряджається, коли з мережі бере більше, ніж віддає", () => {
  assert.equal(L.charging({ voltage_in: 200, watts_in: 1133, watts_out: 357, percent: 98 }), true);
  assert.equal(L.charging({ voltage_in: 227, watts_in: 420, watts_out: 420, percent: 100 }), false);
  assert.equal(L.charging(null), false);
});

test("пороги сторожа з файлу стану", () => {
  const s = { ...STATE, thresholds: { low: 25, critical: 8, stuck_minutes: 3 } };
  // Обʼєкт з іншого контексту vm: порівнюємо копію, інакше різні прототипи.
  assert.deepEqual({ ...L.thresholds(L.parse(out(s))) }, { low: 25, critical: 8, stuck_minutes: 3 });
});

test("старий файл стану без порогів", () => {
  assert.equal(L.thresholds(L.parse(out(STATE))), null);
});

test("текст порогів", () => {
  assert.equal(
    L.thresholdsText({ low: 20, critical: 7, stuck_minutes: 5 }, "uk"),
    "попередження на 20 %, вимкнення ПК на 7 %, «не бере з мережі» через 5 хв",
  );
});

test("чи збігаються пороги сторожа з налаштуваннями віджета", () => {
  const t = { low: 20, critical: 7, stuck_minutes: 5 };
  assert.equal(L.sameThresholds(t, 20, 7, 5), true);
  assert.equal(L.sameThresholds(t, 25, 7, 5), false);
  assert.equal(L.sameThresholds(null, 20, 7, 5), false);
});

test("команда запису порогів", () => {
  assert.equal(
    L.setCommand(30, 10, 7, 123),
    "bluetti-power-watch set-thresholds --low 30 --critical 10 --stuck-minutes 7 # 123",
  );
});

test("команда не збирається з нечисел", () => {
  assert.equal(L.setCommand("30; rm -rf ~", 10, 7, 1), "");
  assert.equal(L.setCommand(30.5, 10, 7, 1), "");
  assert.equal(L.setCommand(30, undefined, 7, 1), "");
  assert.equal(L.setCommand(30, 10, 7, "1; reboot"), "");
});

test("колір низького заряду йде за порогом сторожа", () => {
  assert.equal(L.isLow(25, { low: 30, critical: 10, stuck_minutes: 5 }), true);
  assert.equal(L.isLow(25, null), false);
  assert.equal(L.isLow(20, null), true);
});

test("усі тексти мають обидві мови", () => {
  for (const [id, entry] of Object.entries(L.T)) {
    assert.ok(entry.uk && entry.en, id);
  }
});

test("мова з назви локалі", () => {
  assert.equal(L.language("uk_UA"), "uk");
  assert.equal(L.language("en_US"), "en");
  assert.equal(L.language("de_DE"), "en");
  assert.equal(L.language(""), "en");
});

test("англійською", () => {
  const s = { ...STATE, state: "outage", last: { voltage_in: 0, watts_in: 0, watts_out: 400, percent: 99 } };
  const st = L.status(L.parse(out(s)), "en");
  assert.equal(st.title, "Power outage");
  assert.match(st.hint, /6 h 45 min/);
});

test("станцію не налаштовано", () => {
  const s = { ...STATE, configured: false, last: null };
  const st = L.status(L.parse(out(s)), "uk");
  assert.equal(st.kind, "setup");
  assert.match(st.hint, /set-station/);
});

test("підстановка значень у текст", () => {
  assert.equal(L.tr("updated", "en", { s: 5 }), "Updated 5 s ago");
  assert.equal(L.tr("watch_silent", "uk", { m: 7 }), "Сторож мовчить 7 хв");
});

test("перевірка порогів перед записом", () => {
  assert.equal(L.thresholdsError(20, 8, 5, "uk"), "");
  assert.equal(L.thresholdsError(12, 7, 5, "uk"), "");
  assert.match(L.thresholdsError(12, 8, 5, "uk"), /на 5 %/);
  assert.match(L.thresholdsError(12, 8, 5, "en"), /at least 5 %/);
  assert.match(L.thresholdsError(95, 8, 5, "uk"), /10 до 90/);
  assert.match(L.thresholdsError(20, 4, 5, "uk"), /5 до 50/);
  assert.match(L.thresholdsError(20, 8, 0, "uk"), /1 до 60/);
  assert.notEqual(L.thresholdsError(20.5, 8, 5, "uk"), "");
});

test("автовимкнення вже спрацювало в цьому вимкненні світла", () => {
  const info = L.parse("1000\n990\n2\n" + JSON.stringify(STATE));
  assert.equal(info.armed, true);
  assert.equal(info.done, true);
  assert.equal(L.armedText(info, "uk"), "Автовимкнення ПК: увімкнене, у цьому вимкненні світла вже спрацювало");
  assert.equal(L.armedText(L.parse(out(STATE)), "en"), "Auto shutdown: armed");
  assert.equal(L.armedText(L.parse(out(STATE, { armed: 0 })), "uk"), "Автовимкнення ПК: вимкнене");
});

test("тека стану за тим самим правилом, що й у сторожа", () => {
  assert.match(L.STATE_DIR_SHELL, /case "\$XDG_STATE_HOME" in \/\*\)/);
});
