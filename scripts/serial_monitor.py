#!/usr/bin/env python3
"""
serial_monitor.py — Fast serial capture for HIL testing.

Handles device re-enumeration gracefully: if the ttyACM/ttyUSB disappears and
reappears (common during firmware flash + reboot), we re-open automatically.

Design: Skip VID filtering in the hot loop — udevadm subprocess calls (50-100ms
each) cause missed output when devices enumerate briefly. For HIL benches where
we control what's plugged in, just grab the first available port.
"""

import argparse
import glob
import os
import sys
import termios
import time

BAUD_MAP = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
    230400: termios.B230400,
    460800: termios.B460800,
    921600: termios.B921600,
}


def vid_for_tty_fast(port):
    """Read VID directly from sysfs — no subprocess, ~0.1ms."""
    import re
    m = re.search(r'(ttyACM|ttyUSB)(\d+)', port)
    if not m:
        return None
    try:
        sysfs = f"/sys/class/tty/{os.path.basename(port)}/device"
        vendor_path = os.path.realpath(sysfs + "/../../idVendor")
        with open(vendor_path) as f:
            return f.read().strip()
    except Exception:
        return None


def find_ports(pattern=None):
    """Find available serial ports, optionally filtered by glob pattern."""
    if pattern:
        return sorted(glob.glob(pattern))
    return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))


def configure_port(fd, baud):
    """Configure serial port: baud rate, 8N1, raw mode."""
    baud_const = BAUD_MAP.get(baud)
    if baud_const is None:
        raise ValueError(f"Unsupported baud rate: {baud}")

    attrs = termios.tcgetattr(fd)
    attrs[4] = attrs[5] = baud_const
    attrs[3] &= ~(termios.ICANON | termios.ECHO | termios.ECHOE | termios.ISIG)
    attrs[2] |= termios.CS8
    attrs[2] &= ~(termios.PARENB | termios.CSTOPB)
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)


def monitor(port=None, timeout=60, baud=115200, log_file=None, vid=None):
    """
    Monitor serial port(s) with tight polling loop.

    Args:
        port: Specific port (e.g., /dev/ttyACM0) or None for auto-detect
        timeout: Max seconds to run (0 = indefinite)
        baud: Baud rate
        log_file: Optional file path to log output
        vid: Optional VID filter (e.g., "239a")

    Returns:
        0 on clean exit, 1 on error
    """
    fd = None
    current_tty = None
    start = time.time()
    log_fh = None

    if log_file:
        log_fh = open(log_file, 'ab')

    try:
        while timeout == 0 or (time.time() - start) < timeout:
            if port:
                ports = [port] if os.path.exists(port) else []
            else:
                ports = find_ports()
                if vid:
                    ports = [p for p in ports if vid_for_tty_fast(p) == vid.lower()]

            target = ports[0] if ports else None

            if target and target != current_tty:
                try:
                    if fd is not None:
                        try:
                            os.close(fd)
                        except Exception:
                            pass
                    fd = os.open(target, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
                    configure_port(fd, baud)
                    os.set_blocking(fd, False)
                    current_tty = target
                    msg = f"[+] {target} opened @ {baud} baud\n"
                    sys.stderr.write(msg)
                    sys.stderr.flush()
                except Exception as e:
                    sys.stderr.write(f"[!] {target}: {e}\n")
                    sys.stderr.flush()
                    fd = None
                    current_tty = None
                    time.sleep(0.05)
                    continue

            if fd is not None:
                try:
                    chunk = os.read(fd, 256)
                    if chunk:
                        sys.stdout.buffer.write(chunk)
                        sys.stdout.flush()
                        if log_fh:
                            log_fh.write(chunk)
                            log_fh.flush()
                except BlockingIOError:
                    pass
                except OSError as e:
                    msg = f"\n[-] {current_tty} closed ({e})\n"
                    sys.stderr.write(msg)
                    sys.stderr.flush()
                    try:
                        os.close(fd)
                    except Exception:
                        pass
                    fd = None
                    current_tty = None
            else:
                time.sleep(0.02)

        if timeout > 0:
            sys.stderr.write(f"[*] Timeout after {timeout}s\n")
            sys.stderr.flush()
        return 0

    except KeyboardInterrupt:
        sys.stderr.write("\n[*] Interrupted\n")
        sys.stderr.flush()
        return 0
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if log_fh:
            log_fh.close()


def main():
    parser = argparse.ArgumentParser(
        description="Fast serial monitor for HIL testing with device re-enumeration support"
    )
    parser.add_argument(
        "--port", "-p",
        help="Specific serial port (default: auto-detect first ttyACM*/ttyUSB*)"
    )
    parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=60,
        help="Timeout in seconds, 0 for indefinite (default: 60)"
    )
    parser.add_argument(
        "--baud", "-b",
        type=int,
        default=115200,
        help="Baud rate (default: 115200)"
    )
    parser.add_argument(
        "--log-file", "-l",
        help="Log output to file (in addition to stdout)"
    )
    parser.add_argument(
        "--vid",
        help="Filter by USB VID (e.g., 239a for Adafruit)"
    )

    args = parser.parse_args()

    sys.exit(monitor(
        port=args.port,
        timeout=args.timeout,
        baud=args.baud,
        log_file=args.log_file,
        vid=args.vid,
    ))


if __name__ == "__main__":
    main()
