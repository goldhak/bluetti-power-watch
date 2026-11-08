# bluetti-power-watch

Keeps a Linux PC going through a blackout on a BLUETTI power station.

[Українською](README.uk.md)

Made in Ukraine, where blackouts are part of everyday life. The PC sits behind a
BLUETTI station; this small service watches the station over Bluetooth and makes
the battery last longer while the power is out.

## What it does

- Reads the station over Bluetooth LE about once a minute. **Read-only**: it never
  writes anything to the station.
- **Power outage** (input voltage below 100 V on two readings in a row): switches
  to the `power-saver` profile, optionally dims the monitor, shows a
  notification.
- **Power back**: restores the power profile and the brightness.
- **Battery low**: a notification. **Battery critical**: a clean shutdown of the
  PC, but only if you armed it (`bluetti-power-watch arm`).
- **Station stuck**: after a voltage dip some stations stop taking grid power and
  keep running on battery even when the voltage is back (error E114 on the
  screen). The watchdog notices it and asks you to re-plug the station. While
  it is stuck, the low and critical warnings and the automatic shutdown work
  as in an outage.
- "The station does not answer" is **not** treated as an outage. Nothing is
  switched off for lack of data.
- A KDE Plasma 6 widget: charge, grid voltage, input and output power, estimated
  runtime, threshold settings.

## Supported stations

- **Tested:** BLUETTI Elite 320.
- **Should work, untested:** models whose
  [bluetti-bt-lib](https://github.com/Patrick762/bluetti-bt-lib) profile reports
  AC input voltage: AC60, AC60P, AC70, AC70P, AC180, AC180P, AC180T, AC300,
  AC500, AP300, EB3A, EL10, EL30V2, EL100V2, EP500, EP500P, PR30V2, PR100V2,
  Handsfree 1. If you try one, please open an issue with the output of
  `bluetti-power-watch once`.
- **Not supported:** models that do not report input voltage (AC2A, AC2P, AC50B,
  AC200L, AC200M, AC200PL, EP600, EP760, EP800, EP2000): an outage cannot be told
  apart from a full battery there.

## Install

From the AUR, with any helper (the package is on its way there: new AUR
accounts are paused at the moment):

```sh
paru -S bluetti-power-watch
```

Without the AUR, from this repository (the same recipe):

```sh
git clone https://github.com/goldhak/bluetti-power-watch.git
cd bluetti-power-watch/packaging/aur/bluetti-power-watch
makepkg -si
```

## Setup

1. Find your station:
   ```sh
   bluetti-power-watch scan
   ```
2. Choose it by its **full name** (model + serial). Check the serial on the case
   or in the BLUETTI app: two stations of the same model in one flat is a real
   case, and the watchdog connects only to the exact name you give it.
   ```sh
   bluetti-power-watch set-station EL3201234567890123
   ```
3. Check that it reads:
   ```sh
   bluetti-power-watch once
   ```
4. Start the service:
   ```sh
   systemctl --user enable --now bluetti-power-watch
   ```
5. Optional: right-click the panel → *Add Widgets…* → **BLUETTI power watch**.
6. Optional: arm the automatic shutdown (see below).

## Configuration

`~/.config/bluetti-power-watch/config.json` (the `station` part is written by
`set-station`):

```json
{
  "station": {"name": "EL3201234567890123", "model": "EL320", "encrypted": true},
  "power_saver": true,
  "brightness": 20
}
```

- `power_saver`: switch to the `power-saver` profile during an outage (default on,
  needs `power-profiles-daemon`).
- `brightness`: lower the monitor brightness to this value with `ddcutil`
  (default off).

**Thresholds** are set in the widget (*Configure…*), or from a terminal:

```sh
bluetti-power-watch set-thresholds --low 20 --critical 7 --stuck-minutes 5
```

Set them in one place: the widget saves its values whenever you apply them in
its settings. Changes apply with the next reading, no restart needed.

State and a monthly log of readings are kept in
`~/.local/state/bluetti-power-watch/`.

## Automatic shutdown

Off by default.

```sh
bluetti-power-watch arm      # shut down at critical battery during an outage
bluetti-power-watch disarm
```

It uses `systemctl poweroff`, which polkit allows for the active local session.
It happens at most once per outage: if you turn the PC on again during the same
outage, it will not be shut down again, and the notification says so. If the
station got charged meanwhile (the power was back while the PC was off), the
next outage counts as a new one.

To be honest: the logic is covered by tests, but a real shutdown at critical
charge has not happened on the author's machine yet.

## Known limitations

- A USB Bluetooth adapter once hung on the author's PC. The watchdog then cannot
  read the station and only notifies once, after about 10 minutes. Only a full
  power cycle of the PC helped.
- The runtime estimate in the widget uses the Elite 320 figures (3200 Wh); for
  other models it is rough.
- One station only, and one reader at a time: two programs reading the same
  station at once (for example two copies of this service) break each other's
  reading.
- The BLUETTI phone app can be used at the same time: the author watches the
  station in the app while the service runs, and it does not interfere.

## Privacy

Nothing leaves your PC: no network access, no telemetry.

## License

MIT. Uses [bluetti-bt-lib](https://github.com/Patrick762/bluetti-bt-lib) by
Patrick762 (MIT). The AUR package ships it together with the Bluetooth helpers
it needs (bleak-retry-connector, aiooui, usb-devices, uart-devices: MIT;
bluetooth-adapters: Apache-2.0) in `/usr/lib/bluetti-power-watch/vendor`,
because they are not in the Arch repositories. BLUETTI is a trademark of its
owner; this project is not affiliated with it.
