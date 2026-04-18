# Hardware Reference — pico-hil-flash

## Display Bench (rpi-displays)

| Component | Detail |
|-----------|--------|
| Host Pi | Pi Zero 2W — `rpi-displays` — `192.168.1.234` (SSH: pi/sjahse98) |
| ProtoMQ/MQTT | Pi 5 — `192.168.1.210`, MQTT port 1884, web UI port 5173 |
| USB Hub | Genesys Logic (05e3:0610), dual-cascaded on rpi-displays |
| Solenoid controller | MCP23017 at I2C 0x20 — 8-channel solenoid driver (Adafruit) |
| Camera | Arducam 16MP IMX519 on Pi Zero 2W CSI — `dtoverlay=imx519` |

### Solenoid → USB Port Map

The solenoid controller can power-cycle individual USB ports. Use the `solenoid_power_cycle.py` helper (on rpi-displays) or SSH + i2cset directly.

- **ch6** → Metro ESP32-S2 power control
- Other channels → remaining DUT ports (update this table as you wire boards)

**Power cycle OFF sequence:** 200 ms ON → 500 ms OFF → 1000 ms ON → OFF  
**Power cycle ON sequence:** 200 ms ON → 500 ms OFF → 200 ms ON → OFF

### Known Boards on rpi-displays

| Port | USB ID | /dev | Board |
|------|--------|------|-------|
| 1-1.3 | 239a:80df | /dev/ttyACM2 | Metro ESP32-S2 (WipperSnapper V1) |
| — | — | /dev/ttyACM0 | QT Py ESP32-S3 |
| — | — | /dev/ttyACM1 | Feather ESP32-S3 TFT |

13 boards total on bench; each has a unique QR code (adafru.it URLs): 398, 1028, 2900, 3129, 4116, 4313, 4440, 4650, 4777, 4868, 5300, 5483, 5691.

---

## Pico USB IDs

| Mode | VID | PID | Notes |
|------|-----|-----|-------|
| CircuitPython application (Pico W) | 239a | 8120 | CP running — ttyACM present |
| CircuitPython UF2 bootloader (Pico W) | 239a | 8120 | Same PID! ttyACM present, also exposes mass storage |
| WipperSnapper application (Pico W) | 239a | cafe | Adafruit WipperSnapper firmware |
| Native BOOTSEL (RP2040) | 2e8a | 0003 | No ttyACM; picotool can flash here |
| Native BOOTSEL (RP2350) | 2e8a | 000f | No ttyACM; picotool can flash here |
| MicroPython application (RP2040) | 2e8a | 000a | MicroPython REPL mode |
| MicroPython FS mode (RP2040) | 2e8a | 0005 | MicroPython Board in FS mode (Pico W v1.28+) |

### CircuitPython USB device modes (Pico W, confirmed 2026-04-18)

CircuitPython runs as a **composite USB device** in application mode — it presents both CDC serial (REPL) and mass storage (CIRCUITPY drive) simultaneously, all under `239a:8120`.

| State | VID:PID | ttyACM | Mass storage | Notes |
|-------|---------|--------|--------------|-------|
| CP application | 239a:8120 | ✅ REPL here | ✅ CIRCUITPY mounted | Normal running state |
| Native BOOTSEL | 2e8a:0003 | ❌ | ✅ RPI-RP2 drive | picotool flashes here |
| WipperSnapper app | 239a:cafe | ✅ | ❌ | Separate compiled firmware |

**Key insight:** `239a:8120` with CIRCUITPY mounted = CP application running. The REPL is on the ttyACM port for that device. There is no separate "CP UF2 bootloader" state visible from the host — it's always composite when running.

### CircuitPython reset chain (Pico W)

```
CP application (239a:8120, ttyACM + CIRCUITPY)
  → stty -F <ttyACM> 1200
Native BOOTSEL (2e8a:0003, RPI-RP2 drive, no ttyACM)
  → picotool load <firmware>
  → picotool reboot -a
CP application (239a:8120, ttyACM + CIRCUITPY) ← REPL + code.py output here
```

- One `stty 1200` kick is sufficient from CP application to native BOOTSEL
- After `picotool reboot -a`, find the ttyACM port with VID `239a` PID `8120` — that is the REPL
- Serial output (including `print()` from `code.py`) appears on that port at 115200 baud
- Look for `Auto-reload is on` to confirm CP is running, then match your pass/fail pattern

The 1200-baud reset trick works on **any** RP2040/RP2350 running firmware that implements the USB CDC 1200-baud sentinel (standard in MicroPython, CircuitPython, and the Pico C SDK tinyusb stack).

---

## Tool Prerequisites

| Tool | Install | Purpose |
|------|---------|---------|
| `picotool` | `sudo apt install picotool` or build from source | Erase, load, reboot |
| `lsusb` | `sudo apt install usbutils` | USB enumeration |
| `stty` | coreutils (always present) | 1200-baud reset |
| `udevadm` | udev (always present) | Port VID matching |

### picotool udev rule (avoids sudo)

```
# /etc/udev/rules.d/99-pico.rules
SUBSYSTEM=="usb", ATTRS{idVendor}=="2e8a", MODE="0666"
SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", MODE="0666"
```

Reload with: `sudo udevadm control --reload-rules && sudo udevadm trigger`

---

## Timing Notes

- After 1200-baud reset: wait **~3 s** before expecting BOOTSEL enumeration
- After `picotool reboot`: wait **~3 s** before expecting ttyACM to reappear
- AE settling on Arducam: ~1.5 s (skip first 1.5 s of any video capture)
- If the device doesn't enter BOOTSEL: try a solenoid power cycle first, then retry

---

## Common Failure Modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `stty: cannot open /dev/ttyACMx` | Device not enumerated / wrong port | Check lsusb, solenoid power cycle |
| `stty -F <port> 1200` has no effect | Firmware doesn't honour CDC sentinel | Power cycle the device via solenoid instead |
| BOOTSEL device not found after reset | Firmware doesn't honour 1200-baud sentinel | Power cycle the device instead |
| `picotool load` fails — no device found | Device still in application mode | Increase sleep after stty reset |
| Serial output stops immediately | Wrong baud rate | Adjust `--baud` |
| Timeout with no PASS/FAIL | Pattern mismatch or test hung | Check `--pass-pattern` / `--fail-pattern` regex |
