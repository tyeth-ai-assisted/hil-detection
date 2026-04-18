---
name: pico-hil-flash
description: "Hardware-in-the-loop (HIL) flash and test workflow for Raspberry Pi Pico (RP2040/RP2350) and compatible boards on a display test bench. Use when a user wants to identify a Pico-class device via lsusb, reset it into BOOTSEL mode via 1200-baud stty sentinel, optionally erase flash with picotool, flash new firmware (UF2 or ELF), reboot into the application, and capture serial output until pass/fail criteria are matched or a timeout expires. Also use for any hardware-in-the-loop testing workflow involving USB-controlled DUTs on the rpi-displays bench (Pi Zero 2W at 192.168.1.234) with solenoid-controlled USB power switching. NOT for ESP32 targets, Wi-Fi OTA updates, or non-USB boards."
---

# pico-hil-flash

Flash and test a Pico-class device in one command, with real hardware on the bench.

## Quick Start

```bash
# Flash and run the default test (pass/fail on serial "PASS"/"FAIL" within 60s)
bash scripts/pico_hil_flash.sh firmware.uf2

# Full options: specific port, erase first, custom patterns, 120s timeout
bash scripts/pico_hil_flash.sh \
  --serial /dev/ttyACM2 \
  --erase \
  --pass-pattern "All tests passed" \
  --fail-pattern "ASSERT|ERROR|panic" \
  --timeout 120 \
  firmware.uf2
```

Exit codes: `0` = PASS, `1` = FAIL, `2` = TIMEOUT.

## Workflow (what the script does)

1. **lsusb -v** — enumerate USB, confirm device is visible with the expected VID (`2e8a` by default)
2. **Auto-detect serial port** — finds the first ttyACM* whose USB VID matches; or use `--serial`
3. **stty -F \<port\> 1200** — sends the 1200-baud CDC sentinel on the serial port (`stty -F /dev/ttyACM0 1200`), triggering BOOTSEL reset; waits ~3 s
4. **picotool erase** *(optional, `--erase`)* — erases all flash sectors before programming
5. **picotool load** — programs the firmware file (UF2 or ELF)
6. **picotool reboot** — reboots device into application; waits ~3 s for ttyACM to reappear
7. **Serial capture** — streams serial output at `--baud` (default 115200); evaluates each line against pass/fail regex patterns in real time; exits as soon as a pattern matches or timeout fires

## Key Options

| Flag | Default | Purpose |
|------|---------|---------|
| `--serial <port>` | auto-detect | Force a specific ttyACM/ttyUSB port |
| `--erase` | off | Erase flash before programming |
| `--pass-pattern <re>` | `PASS` | Regex that signals test success |
| `--fail-pattern <re>` | `FAIL` | Regex that signals test failure |
| `--timeout <s>` | `60` | Max wait for pass/fail |
| `--baud <rate>` | `115200` | Serial baud rate for log capture |
| `--no-reboot` | off | Skip the reboot step (useful for ELF debugging) |
| `--dry-run` | off | Print commands without executing |
| `--vid / --pid` | `2e8a` / unset | Filter lsusb device by VID:PID |

## Solenoid Power Cycling (bench-specific)

When a device won't enumerate or accept the 1200-baud reset, power-cycle its USB port via the solenoid controller. SSH to `rpi-displays` (192.168.1.234):

```bash
# Power cycle Metro ESP32-S2 (solenoid ch6) — adapt channel for other boards
ssh pi@192.168.1.234 "python3 ~/solenoid_power_cycle.py 6"
```

Then retry the flash script. See `references/hardware.md` for the full solenoid channel map and timing sequences.

## Hardware Reference

See `references/hardware.md` for:
- Full bench hardware map (rpi-displays, ProtoMQ Pi 5, USB hub, solenoid wiring)
- Pico VID/PID table for application vs BOOTSEL mode
- Tool installation (picotool, udev rules)
- Common failure modes and fixes

## Agent Notes

- **Never simulate hardware** — always use the real device; if it's not responding, troubleshoot the physical setup (power cycle, check lsusb, try a different port)
- Run the script on `rpi-displays` (192.168.1.234) via SSH, or adapt for the local host if the DUT is connected directly
- The script is self-contained; copy it to rpi-displays with `scp` if needed
- Check `lsusb` output before and after each step to confirm enumeration changes
- If `picotool` requires root, add the udev rule in `references/hardware.md` to avoid sudo
