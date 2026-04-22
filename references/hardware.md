# Hardware Reference — hil-detection bench

## Bench Hosts

| Host | Role | IP | SSH |
|------|------|----|-----|
| Pi Zero 2W (rpi-displays) | DUT hub controller, USB switch | 192.168.1.234 | pi/sjahse98 (also root — both have passwordless tachyon keys) |
| Pi 5 (ProtoMQ) | MQTT broker | 192.168.1.210 | MQTT port 1884, web UI port 5173 |

## USB Hub

Genesys Logic (05e3:0610), dual-cascaded on rpi-displays.

## Solenoid Controller

MCP23017 at I2C address 0x20 — Adafruit 8-channel solenoid driver (product #6318).

Control scripts on rpi-displays:
- `~/usb_hub.py` — parameterized timings, use for SAMD51 double-tap
- `~/solenoid_hub_control.py` — standard fixed timings

## Confirmed Channel Map (verified 2026-04-21)

| Channel | Device | USB VID:PID | USB Serial | tty | Notes |
|---------|--------|-------------|------------|-----|-------|
| 0 | Adafruit QT Py ESP32-S3 (4MB Flash 2MB PSRAM) | 239a:8143 | — | ttyACM3 | Native USB CDC |
| 1 | Adafruit Feather ESP32-S3 TFT | 239a:811d | — | ttyACM0 | Native USB CDC |
| 2 | Adafruit Metro ESP32-S2 | 239a:80df | — | ttyACM1 | Native USB CDC |
| 3 | UNCONFIRMED | — | — | — | Does not produce unique device on toggle test |
| 4 | Adafruit PyPortal M4 Titano (ATSAMD51J20) | 239a:8053 (WS) / 239a:8054 (CP) / 239a:0035 (UF2) | F1DF00AE5346513551202020FF171730 | ttyACM0 | See PyPortal section below |
| 5 | Adafruit Huzzah32 ESP32 Feather v1 | 10c4:ea60 (CP2104) | 022AF71E | ttyUSB0 | UART bridge; no native USB |
| 6 | Raspberry Pi Pico W (RP2040) | 239a:8120 (CP app) | E6614104030F7A24 | ttyACM2 | CircuitPython composite mode |

> Note: ttyACM/ttyUSB assignments can shift on re-enumeration. Use `/dev/serial/by-id/` for stable references.

13 QR-coded boards on bench: adafru.it URLs 398, 1028, 2900, 3129, 4116, 4313, 4440, 4650, 4777, 4868, 5300, 5483, 5691.

## Solenoid Timing

Soft-latching toggle buttons. The timing encodes intent:

| Sequence | Purpose | Timing |
|----------|---------|--------|
| ON | Power port on | 200ms HIGH -> LOW |
| OFF (standard) | Power port off | 200ms HIGH -> 500ms LOW -> 1000ms HIGH -> LOW |
| OFF (SAMD51 double-tap) | Enter UF2 bootloader | 200ms HIGH -> 100ms LOW -> 300ms HIGH -> LOW |

Use `usb_hub.py` with `sleep_between=0.1, off_duration=0.3` for SAMD51 double-tap.

## RP2040 / RP2350 (Pico W)

### USB Device Modes

| Mode | VID:PID | ttyACM | Mass Storage | Notes |
|------|---------|--------|--------------|-------|
| CircuitPython application | 239a:8120 | YES (REPL) | YES (CIRCUITPY) | Composite; serial + storage together |
| Native BOOTSEL (RP2040) | 2e8a:0003 | NO | YES (RPI-RP2) | ROM-fixed; picotool flashes here |
| Native BOOTSEL (RP2350) | 2e8a:000f | NO | YES (RPI-RP2) | ROM-fixed |
| WipperSnapper application | 239a:cafe | YES | NO | Compiled firmware |
| MicroPython application | 2e8a:000a | YES (REPL) | — | Does NOT honour 1200-baud sentinel |
| MicroPython FS mode | 2e8a:0005 | YES | YES | Pico W v1.28+ |

### Reset Chain (CircuitPython -> BOOTSEL -> flash -> application)

```
CP application (239a:8120, ttyACM + CIRCUITPY)
  -> stty -F <ttyACM> 1200          # 1200-baud CDC sentinel
  -> wait ~3s
Native BOOTSEL (2e8a:0003, RPI-RP2, no ttyACM)
  -> picotool load <firmware.uf2>
  -> picotool reboot -a
  -> wait ~3s
CP application (239a:8120, ttyACM + CIRCUITPY)  <- REPL + code.py output here
```

Key: `239a:8120` with CIRCUITPY mounted = CP running. One stty kick is sufficient.

### Three-Stage Reset Strategy (pico_hil_flash.sh)

1. `picotool reboot -u -f` — force to BOOTSEL (works from most RP-series app firmware)
2. `stty -F <port> 1200` — CDC sentinel (CircuitPython, WipperSnapper, C SDK tinyusb)
3. `machine.bootloader()` via serial REPL — MicroPython fallback

## ESP32 / ESP8266 (Huzzah32, Huzzah ESP8266)

These boards use a CP2104 UART bridge (10c4:ea60) — there is no native USB.

### Reset Approach

- No 1200-baud CDC sentinel — CP2104 is a dumb UART bridge; SET_LINE_CODING is ignored
- `esptool.py` handles reset automatically via DTR/RTS toggling
- Basic check: `esptool.py --port /dev/ttyUSBx --chip auto flash_id`
- Manual bootloader entry: hold BOOT/GPIO0 button, press RESET, release RESET, release BOOT
- esptool.py auto-reset sequence: DTR->RST low, RTS->GPIO0 low, release RST, release GPIO0
  - Works reliably on Huzzah32
  - May need manual assist on Huzzah ESP8266 depending on board revision

### Identifying by CP2104 Serial

| CP2104 Serial | tty | Board |
|---------------|-----|-------|
| 0283D3AB | ttyUSB1 | Huzzah ESP8266 (channel TBD — verify assignment) |
| 022AF71E | ttyUSB0 | Huzzah32 ESP32 Feather v1 (ch5) |

Use `/dev/serial/by-id/usb-Silicon_Labs_CP2104_USB_to_UART_Bridge_Controller_<SERIAL>-if00-port0` for stable device references.

### esptool.py Location

Already installed on rpi-displays: `/home/pi/.local/bin/esptool.py`
Also: `espsecure.py`, `espefuse.py` in same directory.

## ESP32-Sx Native USB (Metro ESP32-S2, Feather ESP32-S3, QT Py ESP32-S3)

These have native USB (no CP210x bridge) and enumerate directly as CDC ACM devices.

- CDC 1200-baud sentinel works when running CircuitPython or WipperSnapper
- For esptool.py bootloader mode: hold BOOT button -> press RESET (or power-cycle with BOOT held)
- esptool.py auto-reset may work depending on firmware

No USB serial numbers on these boards (iSerial empty).

## SAMD51 (PyPortal M4 Titano — channel 4)

Board: Adafruit PyPortal M4 Titano (ATSAMD51J20), USB serial F1DF00AE5346513551202020FF171730.

### USB Device Modes

| Mode | VID:PID | ttyACM | Mass Storage | Notes |
|------|---------|--------|--------------|-------|
| WipperSnapper application | 239a:8053 | YES | YES (briefly) | "PyPortal M4 Titano" |
| CircuitPython application | 239a:8054 | YES (REPL) | YES (CIRCUITPY) | "Adafruit PyPortal Titano" |
| UF2 bootloader | 239a:0035 | YES (SAM-BA) | YES (8MB UF2 drive) | "PyPortal M4 Express" — same bootloader PID for Express and Titano |

### WipperSnapper Firmware Compatibility

| Version | Status |
|---------|--------|
| beta.78 | WORKING — enumerates stably, connects to WiFi |
| beta.126 | BROKEN — crash-loops with error -110 (USB enumeration timeout) |

The beta.126 crash is at the USB driver level (before serial output), not a secrets.json issue.

### Flashing SAMD51 — UF2 Only (bossac fails)

**bossac 1.9.1 (Debian repo) cannot write SAMD51 flash.** Erase succeeds but write fails with "SAM-BA operation failed".

**Use UF2 drag-and-drop instead** (must run as root on rpi-displays for stty permissions):

```bash
# UF2 flash sequence — run as root on rpi-displays
stty -F /dev/ttyACM0 1200 cs8 -cstopb -parenb   # kick to bootloader
sleep 3                                          # wait for /dev/sda
mount -t vfat /dev/sda /mnt/pyportal
cp firmware.uf2 /mnt/pyportal/flash.uf2
sync && sync
sleep 5                                          # bootloader flashes then reboots
# FAT errors during umount are EXPECTED — bootloader drops the drive mid-flash
```

> **Note:** `stty` on /dev/ttyACM* requires root on rpi-displays. SSH as `root@192.168.1.234` (passwordless key auth from tachyon).

### Double-tap timing with solenoid

The SAMD51 double-tap window is ~500ms. Use `usb_hub.py`:

```python
from usb_hub import SolenoidHubController
hub = SolenoidHubController()
# First tap
hub.port_off(4, sleep_between=0.1, off_duration=0.3)
# Second tap immediately after (within 500ms)
hub.port_on(4)
```

## Camera

Arducam 16MP IMX519 on Pi Zero 2W CSI. Device tree: `dtoverlay=imx519`.
AE settling: ~1.5s. Skip first 1.5s of any video capture.

## Tool Prerequisites

| Tool | Install / Location | Purpose |
|------|--------------------|---------|
| `picotool` | `/home/pi/picotool-2.2.0-a4/` (built from source) | Erase, load, reboot RP2040/RP2350 |
| `esptool.py` | `/home/pi/.local/bin/esptool.py` | Flash ESP32/ESP8266 |
| `bossac` | `sudo apt install bossac` | Flash SAMD21 only — **v1.9.1 fails on SAMD51**, use UF2 instead |
| `lsusb` | `sudo apt install usbutils` | USB enumeration |
| `stty` | coreutils (always present) | 1200-baud CDC reset |
| `udevadm` | udev (always present) | Port path resolution |

### picotool udev rule (avoids sudo)

```
SUBSYSTEM=="usb", ATTRS{idVendor}=="2e8a", MODE="0666"
SUBSYSTEM=="tty", ATTRS{idVendor}=="2e8a", MODE="0666"
```

Reload: `sudo udevadm control --reload-rules && sudo udevadm trigger`

## Common Failure Modes

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `error -110` on lsusb for a port | USB enumeration timeout — device crash-looping | Power cycle via solenoid; may need firmware fix |
| `stty: cannot open /dev/ttyACMx` | Device not enumerated or wrong port | Check lsusb, solenoid power cycle |
| `esptool.py` cannot connect | Wrong port, or not in bootloader | Manual BOOT+RESET; check CP2104 serial number |
| BOOTSEL not entered after stty 1200 | MicroPython ignores CDC sentinel | Power cycle via solenoid instead |
| `picotool load` fails | Still in application mode | Increase sleep after stty reset |
| bossac fails on PyPortal | Missing double-tap within 500ms window | Tune usb_hub.py sleep_between/off_duration |
| Serial output stops | Wrong baud rate | Adjust --baud |
| ttyACM/ttyUSB number wrong | Re-enumeration reordered ports | Use /dev/serial/by-id/ instead |

## Timing Notes

| Event | Wait |
|-------|------|
| After 1200-baud reset | ~3s before BOOTSEL enumeration |
| After picotool reboot | ~3s before ttyACM reappears |
| After solenoid power cycle | ~2s minimum (some devices longer) |
| SAMD51 double-tap window | ~500ms — timing critical |
| Arducam AE settling | ~1.5s — skip first 1.5s of video |
