# hil-detection

Hardware-in-the-loop (HIL) flash and test tooling for RP2040/RP2350 devices.

## Overview

`scripts/pico_hil_flash.sh` — single-script HIL test runner for Pico-class boards:

1. Identifies device by physical USB port path (stable across VID:PID transitions)
2. Resets to BOOTSEL via 3-stage strategy: `picotool -f` → `stty 1200` → `machine.bootloader()`
3. Queries flash size via `picotool info -a` (chip-type-aware defaults for RP2040/RP2350/RP2354)
4. Erases with `picotool erase -r <from> <to>` (exact range, no guessing)
5. Flashes firmware (UF2 or ELF)
6. Reboots into application
7. Streams serial output and evaluates pass/fail regex patterns

## Usage

```bash
bash scripts/pico_hil_flash.sh --erase --pass-pattern "PASS" firmware.uf2
```

See `SKILL.md` for full options, and `references/hardware.md` for bench hardware details.

## Device support

| Chip | Flash tool | Notes |
|------|-----------|-------|
| RP2040 (Pico, Pico W) | picotool | BOOTSEL PID 2e8a:0003 |
| RP2350A/B (Pico 2) | picotool | BOOTSEL PID 2e8a:000f |
| RP2354A/B (Pico 2 + PSRAM) | picotool | BOOTSEL PID 2e8a:000f |
| ESP32 / ESP8266 (CP2104 UART bridge) | esptool.py | Huzzah32, Huzzah ESP8266 on bench |
| ESP32-Sx native USB | esptool.py | Metro ESP32-S2, Feather ESP32-S3, QT Py ESP32-S3 |
| SAMD51 / SAMD21 | bossac | PyPortal M4 on bench (currently broken) |

Application-mode VID:PID is never assumed — only ROM-fixed BOOTSEL PIDs are hardcoded.

Use `usb_hub.py` for parameterized solenoid control — supports SAMD51 double-tap reset sequences.

## Future device families

- nRF52 → `nrfjprog` / `adafruit-nrfutil`
- STM32 → `dfu-util` / `stm32flash`
