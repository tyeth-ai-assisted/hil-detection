#!/usr/bin/env bash
# pico_hil_flash.sh — Hardware-in-the-loop flash + test for RP2040/RP2350 devices
#
# Usage:
#   pico_hil_flash.sh [OPTIONS] <firmware.uf2|firmware.elf>
#
# Options:
#   --serial <port>       Serial port, e.g. /dev/ttyACM0 — used to identify the
#                         physical USB port path; script tracks the device across
#                         VID:PID changes using that path, not the port number
#   --vid <vid>           USB Vendor ID to filter initial device scan (optional).
#                         If omitted, scans for BOOTSEL PIDs (2e8a:0003/000f) first,
#                         then falls back to the first available ttyACM device.
#                         NOTE: application-mode VID:PID varies by board vendor and
#                         cannot be assumed. Only BOOTSEL PIDs are ROM-fixed.
#   --pid <pid>           USB Product ID to pair with --vid (optional)
#   --erase               Erase flash before programming (requires picotool)
#   --pass-pattern <re>   Regex that serial output must match to PASS (default: "PASS")
#   --fail-pattern <re>   Regex that triggers an immediate FAIL  (default: "FAIL")
#   --timeout <secs>      Max seconds to wait for pass/fail      (default: 60)
#   --baud <rate>         Serial baud rate for log capture        (default: 115200)
#   --no-reboot           Skip the final reboot step
#   --dry-run             Show commands without executing
#   -h, --help            Show this help

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
VID=""   # No default: application-mode VID:PID varies by board vendor
PID=""
SERIAL_PORT=""
FIRMWARE=""
ERASE=false
PASS_PATTERN="PASS"
FAIL_PATTERN="FAIL"
TIMEOUT=60
BAUD=115200
NO_REBOOT=false
DRY_RUN=false

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "[pico-hil] $*"; }
run() {
  if $DRY_RUN; then echo "[dry-run] $*"; else eval "$@"; fi
}

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial)       SERIAL_PORT="$2";   shift 2 ;;
    --vid)          VID="$2";           shift 2 ;;
    --pid)          PID="$2";           shift 2 ;;
    --erase)        ERASE=true;         shift   ;;
    --pass-pattern) PASS_PATTERN="$2";  shift 2 ;;
    --fail-pattern) FAIL_PATTERN="$2";  shift 2 ;;
    --timeout)      TIMEOUT="$2";       shift 2 ;;
    --baud)         BAUD="$2";          shift 2 ;;
    --no-reboot)    NO_REBOOT=true;     shift   ;;
    --dry-run)      DRY_RUN=true;       shift   ;;
    -h|--help)      grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
    -*)             die "Unknown option: $1" ;;
    *)              FIRMWARE="$1";      shift   ;;
  esac
done

[[ -n "$FIRMWARE" ]] || die "No firmware file specified. Usage: $0 [OPTIONS] <firmware.uf2|firmware.elf>"
[[ -f "$FIRMWARE" ]] || die "Firmware file not found: $FIRMWARE"

# ── USB path tracking helpers ─────────────────────────────────────────────────
# The physical USB port path (e.g. "1-1.3") never changes regardless of
# VID:PID transitions (application → BOOTSEL → new firmware → application).
# We anchor the entire session to this path and use it to find ttyACM at each step.

# Get the USB port path for a ttyACM device
# Extracts stable physical port path from DEVPATH, e.g. "1-1.1.4"
get_usb_path_for_tty() {
  local tty="$1"
  # DEVPATH=/devices/.../usb1/1-1/1-1.1/1-1.1.4/1-1.1.4:1.0/tty/ttyACM2
  # Extract the last "N-N.N.N" segment before the colon (interface specifier)
  udevadm info "$tty" 2>/dev/null \
    | grep '^E: DEVPATH=' \
    | grep -oP '\d+-[\d.]+(?=/)' \
    | tail -1 || true
}

# Get the USB port path for a lsusb Bus/Device entry (integer match via sysfs)
get_usb_path_for_busdev() {
  local target_bus="$((10#$1))" target_dev="$((10#$2))"
  for sysdev in /sys/bus/usb/devices/*/; do
    local b d
    b=$(cat "${sysdev}busnum" 2>/dev/null || true)
    d=$(cat "${sysdev}devnum" 2>/dev/null || true)
    if [[ "${b:-x}" == "$target_bus" && "${d:-x}" == "$target_dev" ]]; then
      basename "$sysdev"
      return
    fi
  done
}

# Find ttyACM port currently associated with a given USB port path
find_tty_for_usb_path() {
  local target_path="$1"
  for tty in /dev/ttyACM* /dev/ttyUSB*; do
    [[ -e "$tty" ]] || continue
    local p
    p=$(get_usb_path_for_tty "$tty")
    if [[ "$p" == "$target_path" ]]; then
      echo "$tty"
      return
    fi
  done
}

# Get current VID:PID for a USB port path (via sysfs)
get_vidpid_for_usb_path() {
  local target_path="$1"
  local sysdev="/sys/bus/usb/devices/${target_path}"
  if [[ -d "$sysdev" ]]; then
    local vid pid
    vid=$(cat "${sysdev}/idVendor" 2>/dev/null | tr -d '[:space:]' || true)
    pid=$(cat "${sysdev}/idProduct" 2>/dev/null | tr -d '[:space:]' || true)
    echo "${vid}:${pid}"
  fi
}

# Wait for a device on our USB path to re-enumerate (any VID:PID)
wait_for_reenumeration() {
  local usb_path="$1" label="$2" max_wait="${3:-10}"
  log "  Waiting for device to re-enumerate on path ${usb_path} (${label})..."
  for i in $(seq 1 "$max_wait"); do
    local vp
    vp=$(get_vidpid_for_usb_path "$usb_path")
    if [[ -n "$vp" && "$vp" != ":" ]]; then
      log "  Re-enumerated as ${vp} (after ${i}s)"
      echo "$vp"
      return 0
    fi
    sleep 1
  done
  log "  WARNING: device did not re-enumerate on ${usb_path} within ${max_wait}s"
  echo ""
}

# ── Step 1: lsusb -v — identify device and anchor USB port path ───────────────
log "Step 1: Scanning USB with lsusb -v for Pico-class device..."
LSUSB_VERBOSE=$(lsusb -v 2>/dev/null || true)

# Only BOOTSEL PIDs are ROM-fixed and can be reliably assumed:
#   2e8a:0003 = RP2040 BOOTSEL,  2e8a:000f = RP2350 BOOTSEL
# Application-mode VID:PID varies by board vendor — never hardcode these.
# Device identity is anchored by physical USB port path throughout.
USB_PATH=""

if [[ -n "$SERIAL_PORT" && -e "$SERIAL_PORT" ]]; then
  # Port given and present — anchor by its physical USB path regardless of VID:PID
  USB_PATH=$(get_usb_path_for_tty "$SERIAL_PORT")
  CURRENT_VIDPID=$(get_vidpid_for_usb_path "$USB_PATH")
  log "  Specified port $SERIAL_PORT → USB path: ${USB_PATH}, VID:PID: ${CURRENT_VIDPID}"
elif [[ -n "$SERIAL_PORT" && ! -e "$SERIAL_PORT" ]]; then
  # Port given but absent — device is probably in BOOTSEL (no ttyACM in that mode)
  log "  Port $SERIAL_PORT not present — scanning lsusb for BOOTSEL device..."
  while IFS= read -r line; do
    if echo "$line" | grep -qiE "2e8a:0003|2e8a:000f"; then
      BUS=$(echo "$line" | grep -oP 'Bus \K\d+')
      DEV=$(echo "$line" | grep -oP 'Device \K\d+')
      USB_PATH=$(get_usb_path_for_busdev "$BUS" "$DEV")
      log "  Found BOOTSEL on bus $BUS dev $DEV → USB path: ${USB_PATH}"
      break
    fi
  done < <(lsusb)
elif [[ -n "$VID" ]]; then
  # User supplied a VID (and optional PID) — use it for initial scan only
  log "  Scanning lsusb for VID=${VID}${PID:+ PID=${PID}}..."
  while IFS= read -r line; do
    VP_MATCH="${VID}${PID:+:${PID}}"
    if echo "$line" | grep -qi "$VP_MATCH"; then
      BUS=$(echo "$line" | grep -oP 'Bus \K\d+')
      DEV=$(echo "$line" | grep -oP 'Device \K\d+')
      USB_PATH=$(get_usb_path_for_busdev "$BUS" "$DEV")
      log "  Found ${VP_MATCH} on bus $BUS dev $DEV → USB path: ${USB_PATH}"
      break
    fi
  done < <(lsusb)
else
  # No hints — check for BOOTSEL first (ROM-fixed, reliable), then first ttyACM
  log "  Auto-detecting: checking for BOOTSEL device first..."
  while IFS= read -r line; do
    if echo "$line" | grep -qiE "2e8a:0003|2e8a:000f"; then
      BUS=$(echo "$line" | grep -oP 'Bus \K\d+')
      DEV=$(echo "$line" | grep -oP 'Device \K\d+')
      USB_PATH=$(get_usb_path_for_busdev "$BUS" "$DEV")
      log "  Found BOOTSEL on bus $BUS dev $DEV → USB path: ${USB_PATH}"
      break
    fi
  done < <(lsusb)
  if [[ -z "$USB_PATH" ]]; then
    log "  No BOOTSEL found — anchoring to first available ttyACM..."
    for tty in /dev/ttyACM* /dev/ttyUSB*; do
      [[ -e "$tty" ]] || continue
      USB_PATH=$(get_usb_path_for_tty "$tty")
      if [[ -n "$USB_PATH" ]]; then
        CURRENT_VIDPID=$(get_vidpid_for_usb_path "$USB_PATH")
        log "  Using $tty → USB path: ${USB_PATH}, VID:PID: ${CURRENT_VIDPID}"
        SERIAL_PORT="$tty"
        break
      fi
    done
  fi
fi

[[ -n "$USB_PATH" ]] || die "Could not determine USB port path for device. Is it plugged in?"

# Print lsusb -v block matched by Bus/Device number (not by assumed VID)
log "lsusb -v detail for device on path ${USB_PATH}:"
set +o pipefail
_SYSDEV="/sys/bus/usb/devices/${USB_PATH}"
_BUS=$(cat "${_SYSDEV}/busnum" 2>/dev/null | tr -d '[:space:]' || true)
_DEV=$(cat "${_SYSDEV}/devnum" 2>/dev/null | tr -d '[:space:]' || true)
if [[ -n "$_BUS" && -n "$_DEV" ]]; then
  _BUS_PAD=$(printf '%03d' "$_BUS"); _DEV_PAD=$(printf '%03d' "$_DEV")
  echo "$LSUSB_VERBOSE" | awk "/^Bus ${_BUS_PAD} Device ${_DEV_PAD}/{found=1} found{print; if(/^$/ && found>1)found=0; found++}" | head -25 | sed 's/^/  /' || true
fi
set -o pipefail

CURRENT_VIDPID=$(get_vidpid_for_usb_path "$USB_PATH")
log "  Current VID:PID on path ${USB_PATH}: ${CURRENT_VIDPID}"

# ── Step 2: resolve or confirm serial port ────────────────────────────────────
log "Step 2: Resolving serial port for USB path ${USB_PATH}..."
RESOLVED_PORT=$(find_tty_for_usb_path "$USB_PATH")
if [[ -n "$RESOLVED_PORT" ]]; then
  log "  Serial port: $RESOLVED_PORT"
  SERIAL_PORT="$RESOLVED_PORT"
else
  CURRENT_VP=$(get_vidpid_for_usb_path "$USB_PATH")
  case "$CURRENT_VP" in
    2e8a:0003|2e8a:000f)
      log "  No ttyACM — device is in native BOOTSEL (${CURRENT_VP}), no CDC serial expected."
      ;;
    *)
      # Application firmware with CDC — ttyACM may need a moment to appear
      log "  ttyACM not yet visible for ${CURRENT_VP} on path ${USB_PATH} — waiting..."
      for i in $(seq 1 5); do
        sleep 1
        RESOLVED_PORT=$(find_tty_for_usb_path "$USB_PATH")
        [[ -n "$RESOLVED_PORT" ]] && break
      done
      if [[ -n "$RESOLVED_PORT" ]]; then
        log "  Serial port: $RESOLVED_PORT"
        SERIAL_PORT="$RESOLVED_PORT"
      else
        log "  WARNING: no ttyACM found for ${CURRENT_VP} on ${USB_PATH}"
        SERIAL_PORT=""
      fi
      ;;
  esac
fi

# ── Step 3: 1200-baud reset into BOOTSEL (if not already there) ───────────────
log "Step 3: Checking device state before reset..."
CURRENT_VIDPID=$(get_vidpid_for_usb_path "$USB_PATH")
log "  VID:PID on path ${USB_PATH}: ${CURRENT_VIDPID}"

case "$CURRENT_VIDPID" in
  2e8a:0003|2e8a:000f)
    log "  Already in native BOOTSEL — skipping reset."
    ;;
  ""|":")
    die "Device on path ${USB_PATH} has disappeared. Check USB connection."
    ;;
  *)
    # Three-stage reset strategy, in order of preference:
    # 1. picotool reboot -u -f  (force-resets any RP-series app, incl. MicroPython)
    # 2. stty -F <port> 1200    (CDC 1200-baud sentinel — CircuitPython, WipperSnapper, C SDK)
    #    NOTE: MicroPython does NOT implement this sentinel; its USB stack ignores SET_LINE_CODING
    # 3. machine.bootloader()   (MicroPython REPL fallback if stty didn't work)
    log "  Strategy 1: picotool reboot -u -f (force to BOOTSEL)..."
    if ! $DRY_RUN; then
      picotool reboot -u -f 2>/dev/null || true  # non-zero OK; not all firmware supports this
      sleep 2
      NEW_VP=$(wait_for_reenumeration "$USB_PATH" "BOOTSEL" 6)
    fi
    if echo "$NEW_VP" | grep -qiE "2e8a:0003|2e8a:000f"; then
      log "  BOOTSEL confirmed via picotool -f: ${NEW_VP}"
    elif [[ -n "$SERIAL_PORT" ]]; then
      log "  Strategy 2: stty 1200 on $SERIAL_PORT..."
      run "stty -F \"$SERIAL_PORT\" 1200 cs8 -cstopb -parenb"
      sleep 2
      NEW_VP=$(wait_for_reenumeration "$USB_PATH" "BOOTSEL" 6)
      if echo "$NEW_VP" | grep -qiE "2e8a:0003|2e8a:000f"; then
        log "  BOOTSEL confirmed via stty 1200: ${NEW_VP}"
      else
        log "  Strategy 3: machine.bootloader() via serial REPL..."
        REPL_PORT=$(find_tty_for_usb_path "$USB_PATH")
        if [[ -n "$REPL_PORT" ]] && ! $DRY_RUN; then
          python3 -c "
import serial, time, sys
try:
    s = serial.Serial('$REPL_PORT', 115200, timeout=2)
    s.write(b'\x03\x03')
    time.sleep(0.3)
    s.write(b'import machine; machine.bootloader()\r\n')
    time.sleep(1)
    s.close()
    print('sent machine.bootloader()')
except Exception as e:
    print(f'WARN: {e}', file=sys.stderr)
" 2>&1 | sed 's/^/  /'
          NEW_VP=$(wait_for_reenumeration "$USB_PATH" "BOOTSEL" 8)
          log "  After machine.bootloader(): VID:PID = ${NEW_VP}"
        fi
        if ! echo "$NEW_VP" | grep -qiE "2e8a:0003|2e8a:000f"; then
          die "Could not enter BOOTSEL (got: ${NEW_VP:-none}). Try power-cycling with BOOTSEL button held."
        fi
      fi
    else
      die "Device is not in BOOTSEL and no serial port available for reset strategies."
    fi
    ;;
esac

# ── Step 4: optional erase ────────────────────────────────────────────────────
# NOTE: This erase function is RP2040/RP2350-specific (uses picotool).
# Future device families will need different tools:
#   ESP32/ESP8266 → esptool.py --chip auto erase_flash
#   SAMD51/SAMD21 → bossac --erase
if $ERASE; then
  log "Step 4: Querying flash size via picotool info -a..."
  command -v picotool >/dev/null 2>&1 || die "picotool not found."
  INFO_OUT=$(picotool info -a 2>&1 || true)
  echo "$INFO_OUT" | sed 's/^/  [info] /'
  # Parse "flash size: NNNNk" or "flash size: N.NM" from picotool info output
  FLASH_SIZE_K=$(echo "$INFO_OUT" | grep -iP 'flash size' | grep -oP '\d+(?=K)' | head -1 || true)
  FLASH_SIZE_M=$(echo "$INFO_OUT" | grep -iP 'flash size' | grep -oP '[\d.]+(?=M)' | head -1 || true)
  if [[ -n "$FLASH_SIZE_K" ]]; then
    FLASH_BYTES=$(( FLASH_SIZE_K * 1024 ))
  elif [[ -n "$FLASH_SIZE_M" ]]; then
    FLASH_BYTES=$(python3 -c "print(int(float('$FLASH_SIZE_M') * 1024 * 1024))")
  else
    log "  WARNING: could not parse flash size from picotool info — falling back to full erase without size"
    FLASH_BYTES=""
  fi
  # Flash base is always 0x10000000 on all RP2-family chips.
  # Default flash sizes by chip type when picotool info cannot determine size:
  #   RP2040         (Pico, Pico W)               → 2MB
  #   RP2350A        (Pico 2, standard)            → 4MB
  #   RP2350B        (larger pin count variant)    → 4MB
  #   RP2354A/B      (with PSRAM)                  → 4MB flash
  # Source: picotool info -a "type:" field
  FLASH_BASE="0x10000000"

  # Parse chip type from picotool info to select correct default
  CHIP_TYPE=$(echo "$INFO_OUT" | grep -iP '^\s*type:' | grep -oP 'RP\d+[A-Z]?' | head -1 || true)
  log "  Chip type: ${CHIP_TYPE:-unknown}"

  if [[ -n "$FLASH_BYTES" ]]; then
    log "  Flash size: ${FLASH_BYTES} bytes (from picotool info)"
  else
    # Select default by chip type
    case "${CHIP_TYPE^^}" in
      RP2040)
        FLASH_BYTES=$((2 * 1024 * 1024))
        log "  Flash size unknown — RP2040 default: 2MB"
        ;;
      RP2350|RP2350A|RP2350B|RP2354|RP2354A|RP2354B)
        FLASH_BYTES=$((4 * 1024 * 1024))
        log "  Flash size unknown — ${CHIP_TYPE} default: 4MB"
        ;;
      *)
        # Unknown chip — conservative 2MB
        FLASH_BYTES=$((2 * 1024 * 1024))
        log "  Flash size unknown, chip type unknown — conservative default: 2MB"
        ;;
    esac
  fi

  FLASH_END=$(python3 -c "print(hex(0x10000000 + $FLASH_BYTES))")
  log "  Erasing ${FLASH_BASE} to ${FLASH_END} (${FLASH_BYTES} bytes)..."
  run "picotool erase -r ${FLASH_BASE} ${FLASH_END} --force"
  log "  Erase complete."
else
  log "Step 4: Skipping erase (use --erase to enable)."
fi

# ── Step 5: flash firmware ────────────────────────────────────────────────────
log "Step 5: Flashing firmware: $FIRMWARE"
command -v picotool >/dev/null 2>&1 || die "picotool not found on PATH."
run "picotool load \"$FIRMWARE\" --force"
log "  Flash complete."

# ── Step 6: reboot into application ──────────────────────────────────────────
if ! $NO_REBOOT; then
  log "Step 6: Rebooting device into application (picotool reboot -a)..."
  run "picotool reboot -a"
  # Wait for device to drop and re-enumerate as application firmware
  sleep 1
  NEW_VP=$(wait_for_reenumeration "$USB_PATH" "application" 10)
  log "  After reboot: VID:PID = ${NEW_VP}"
else
  log "Step 6: Skipping reboot (--no-reboot set)."
fi

# ── Step 7: find serial port by USB path and capture output ──────────────────
log "Step 7: Locating serial port and capturing output..."
log "  Pass pattern : \"$PASS_PATTERN\""
log "  Fail pattern : \"$FAIL_PATTERN\""
log "  Timeout      : ${TIMEOUT}s"

RESULT="TIMEOUT"
LOG_FILE=$(mktemp /tmp/pico_hil_XXXXXX.log)

if ! $DRY_RUN; then
  # Locate the ttyACM that now lives on our anchored USB path
  SERIAL_PORT=""
  for i in $(seq 1 15); do
    SERIAL_PORT=$(find_tty_for_usb_path "$USB_PATH")
    [[ -n "$SERIAL_PORT" ]] && break
    sleep 1
  done
  [[ -n "$SERIAL_PORT" ]] || die "No serial port found on USB path ${USB_PATH} after reboot. Current VID:PID: $(get_vidpid_for_usb_path "$USB_PATH")"
  log "  Serial port: $SERIAL_PORT (VID:PID: $(get_vidpid_for_usb_path "$USB_PATH"))"

  stty -F "$SERIAL_PORT" "$BAUD" raw -echo cs8 -cstopb -parenb 2>/dev/null || true

  DEADLINE=$(( $(date +%s) + TIMEOUT ))
  log "  Serial output:"
  echo "──────────────────────────────────────"
  while IFS= read -r line; do
    echo "  $line"
    echo "$line" >> "$LOG_FILE"
    if echo "$line" | grep -qE "$PASS_PATTERN"; then RESULT="PASS"; break; fi
    if echo "$line" | grep -qE "$FAIL_PATTERN"; then RESULT="FAIL"; break; fi
    [[ $(date +%s) -ge $DEADLINE ]] && break
  done < <(timeout "$TIMEOUT" cat "$SERIAL_PORT" 2>/dev/null || true)
  echo "──────────────────────────────────────"
else
  log "  [dry-run] skipping serial capture"
  RESULT="DRY_RUN"
fi

log "Log saved to: $LOG_FILE"

echo ""
case "$RESULT" in
  PASS)    log "✅ RESULT: PASS"; exit 0 ;;
  FAIL)    log "❌ RESULT: FAIL"; exit 1 ;;
  DRY_RUN) log "🔍 RESULT: DRY_RUN (no hardware touched)"; exit 0 ;;
  TIMEOUT) log "⏱  RESULT: TIMEOUT after ${TIMEOUT}s — no pass/fail pattern matched"; exit 2 ;;
esac
