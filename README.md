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

| Chip | BOOTSEL PID | Flash default |
|------|------------|---------------|
| RP2040 (Pico, Pico W) | `2e8a:0003` | 2MB |
| RP2350A/B (Pico 2) | `2e8a:000f` | 4MB |
| RP2354A/B (Pico 2 + PSRAM) | `2e8a:000f` | 4MB |

Application-mode VID:PID is never assumed — only ROM-fixed BOOTSEL PIDs are hardcoded.

## Future device families

- ESP32/ESP8266 → `esptool.py`
- SAMD51/SAMD21 → `bossac`
