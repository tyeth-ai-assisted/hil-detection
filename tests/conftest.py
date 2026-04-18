"""
conftest.py — HIL test fixtures for Pico W boards connected to rpi-displays (192.168.1.234).

This conftest runs on the Tachyon host and issues commands to rpi-displays via SSH/sshpass.
Serial communication with the Pico W is also done through the rpi-displays host.
"""
import json
import subprocess
import time
import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
RPI_HOST = "pi@192.168.1.234"
RPI_PASSWORD = "sjahse98"
DEFAULT_USB_PATH = "1-1.1.4"

BOOTSEL_VID_PID = "2e8a:0003"           # RP2040 BOOTSEL
CP_VID_PID = "239a:8120"                # CircuitPython CDC + MSC
MP_VID_PID = "2e8a:0005"                # MicroPython FS mode

CIRCUITPY_DEV = "/dev/sdf1"
CIRCUITPY_MOUNT = "/tmp/circuitpy"
HIL_FLASH_SCRIPT = "/tmp/pico_hil_flash.sh"

# ──────────────────────────────────────────────────────────────────────────────
# Pytest options / markers
# ──────────────────────────────────────────────────────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        "--firmware-dir",
        default="/tmp",
        help="Directory on rpi-displays where UF2 firmware files are stored (default: /tmp)",
    )
    parser.addoption(
        "--rpi-host",
        default="192.168.1.234",
        help="IP or hostname of rpi-displays (default: 192.168.1.234)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "circuitpython: tests that target CircuitPython firmware")
    config.addinivalue_line("markers", "wippersnapper: tests that target WipperSnapper firmware")
    config.addinivalue_line("markers", "micropython: tests that target MicroPython firmware")


# ──────────────────────────────────────────────────────────────────────────────
# Reachability guard — skip entire session if rpi-displays is unreachable
# ──────────────────────────────────────────────────────────────────────────────

def _rpi_reachable(host: str) -> bool:
    result = subprocess.run(
        ["ping", "-c", "1", "-W", "3", host],
        capture_output=True,
    )
    return result.returncode == 0


def pytest_collection_modifyitems(config, items):
    host = config.getoption("--rpi-host", default="192.168.1.234")
    if not _rpi_reachable(host):
        skip_marker = pytest.mark.skip(
            reason=f"rpi-displays ({host}) is unreachable — skipping all HIL tests"
        )
        for item in items:
            item.add_marker(skip_marker)


# ──────────────────────────────────────────────────────────────────────────────
# Core SSH helper
# ──────────────────────────────────────────────────────────────────────────────

def _ssh(cmd: str, host: str = RPI_HOST, password: str = RPI_PASSWORD,
         timeout: int = 60, check: bool = False) -> subprocess.CompletedProcess:
    """Run *cmd* on rpi-displays via sshpass, return CompletedProcess."""
    full_cmd = [
        "sshpass", "-p", password,
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        host,
        cmd,
    ]
    return subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def rpi_host(request):
    """Return the rpi-displays host address."""
    return request.config.getoption("--rpi-host", default="192.168.1.234")


@pytest.fixture(scope="session")
def firmware_dir(request):
    """Return firmware directory on rpi-displays."""
    return request.config.getoption("--firmware-dir", default="/tmp")


@pytest.fixture(scope="session")
def ssh_cmd(rpi_host):
    """
    Session-scoped fixture that returns a callable::

        ssh_cmd(cmd, timeout=60, check=False) -> CompletedProcess

    The callable transparently tunnels commands to rpi-displays via sshpass.
    """
    host = f"pi@{rpi_host}"

    def _run(cmd: str, timeout: int = 60, check: bool = False) -> subprocess.CompletedProcess:
        return _ssh(cmd, host=host, timeout=timeout, check=check)

    return _run


@pytest.fixture(scope="session")
def usb_path(ssh_cmd):
    """
    Detect the USB bus path of the Pico W on rpi-displays.

    Priority:
    1. Device currently in BOOTSEL mode (VID:PID 2e8a:0003) — use its sysfs path.
    2. Device in application mode (239a:8120 or 2e8a:0005) — same.
    3. Fall back to the known-good anchor path ``1-1.1.4``.
    """
    for vid_pid in (BOOTSEL_VID_PID, CP_VID_PID, MP_VID_PID):
        vid, pid = vid_pid.split(":")
        result = ssh_cmd(
            f"lsusb -d {vid_pid} 2>/dev/null | head -1"
        )
        if result.returncode == 0 and result.stdout.strip():
            # Try to resolve to a bus path via sysfs
            path_result = ssh_cmd(
                f"find /sys/bus/usb/devices -maxdepth 1 -name '*' | "
                f"xargs -I{{}} sh -c 'cat {{}}/idVendor 2>/dev/null | grep -qi {vid} && "
                f"cat {{}}/idProduct 2>/dev/null | grep -qi {pid} && basename {{}}' 2>/dev/null | head -1"
            )
            if path_result.returncode == 0 and path_result.stdout.strip():
                return path_result.stdout.strip()
    return DEFAULT_USB_PATH


@pytest.fixture(scope="function")
def tty_port(ssh_cmd, usb_path):
    """
    Find the ttyACM* device for the Pico W at *usb_path* using udevadm.

    Returns the full device path, e.g. ``/dev/ttyACM0``.
    Raises pytest.skip if no port is found within 10 s.
    """
    deadline = time.time() + 10
    while time.time() < deadline:
        # Method 1: udevadm via DEVPATH
        result = ssh_cmd(
            f"for dev in /dev/ttyACM*; do "
            f"  udevadm info --query=path --name=$dev 2>/dev/null | grep -q '{usb_path}' && echo $dev; "
            f"done | head -1"
        )
        port = result.stdout.strip()
        if port:
            return port

        # Method 2: sysfs devpath search
        result2 = ssh_cmd(
            f"find /sys/bus/usb/devices/{usb_path} -name 'ttyACM*' 2>/dev/null | head -1"
        )
        if result2.returncode == 0 and result2.stdout.strip():
            tty_name = result2.stdout.strip().split("/")[-1]
            return f"/dev/{tty_name}"

        # Method 3: any ttyACM as fallback
        result3 = ssh_cmd("ls /dev/ttyACM* 2>/dev/null | head -1")
        if result3.returncode == 0 and result3.stdout.strip():
            return result3.stdout.strip()

        time.sleep(1)

    pytest.skip(f"No ttyACM port found for USB path {usb_path} — is the Pico W connected?")


@pytest.fixture(scope="function")
def circuitpy_mount(ssh_cmd):
    """
    Mount/unmount fixture for the CIRCUITPY mass-storage partition.

    Yields the remote mount-point path.  After the test, the partition is
    unmounted regardless of test outcome.
    """
    # Ensure mount point exists
    ssh_cmd(f"sudo mkdir -p {CIRCUITPY_MOUNT}")

    # Unmount if stale
    ssh_cmd(f"sudo umount {CIRCUITPY_MOUNT} 2>/dev/null; true")

    # Wait for block device to appear
    deadline = time.time() + 15
    while time.time() < deadline:
        result = ssh_cmd(f"test -b {CIRCUITPY_DEV} && echo ok")
        if "ok" in result.stdout:
            break
        time.sleep(1)
    else:
        pytest.skip(f"{CIRCUITPY_DEV} not found — is CIRCUITPY mass storage exposed?")

    mount_result = ssh_cmd(f"sudo mount {CIRCUITPY_DEV} {CIRCUITPY_MOUNT}")
    if mount_result.returncode != 0:
        pytest.skip(f"Failed to mount {CIRCUITPY_DEV}: {mount_result.stderr.strip()}")

    yield CIRCUITPY_MOUNT

    # Teardown: sync + unmount
    ssh_cmd(f"sync; sudo umount {CIRCUITPY_MOUNT} 2>/dev/null; true")


@pytest.fixture(scope="function")
def flash_firmware(ssh_cmd, firmware_dir):
    """
    Returns a callable that flashes firmware onto the Pico W via rpi-displays::

        flash_firmware(uf2_filename, extra_args="")

    *uf2_filename* is relative to *firmware_dir* on rpi-displays.
    *extra_args* are passed verbatim to pico_hil_flash.sh (e.g. ``"--erase"``).

    Raises pytest.skip if the firmware file is not present on rpi-displays.
    Raises AssertionError if flashing fails.
    """
    def _flash(uf2_filename: str, extra_args: str = "", timeout: int = 120) -> None:
        firmware_path = f"{firmware_dir}/{uf2_filename}"
        check_result = ssh_cmd(f"test -f {firmware_path} && echo ok")
        if "ok" not in check_result.stdout:
            pytest.skip(
                f"Firmware file not found on rpi-displays: {firmware_path}\n"
                f"Copy the UF2 to rpi-displays:{firmware_path} and re-run."
            )

        cmd = f"bash {HIL_FLASH_SCRIPT} {extra_args} {firmware_path}"
        result = ssh_cmd(cmd, timeout=timeout)
        assert result.returncode == 0, (
            f"Flash command failed (rc={result.returncode}):\n"
            f"  cmd : {cmd}\n"
            f"  stdout: {result.stdout}\n"
            f"  stderr: {result.stderr}"
        )
        # Give the board a moment to enumerate
        time.sleep(3)

    return _flash


# ──────────────────────────────────────────────────────────────────────────────
# Serial helper — reads via rpi-displays Python one-liner
# ──────────────────────────────────────────────────────────────────────────────

def serial_read_output(ssh_cmd, tty_port: str, duration: float = 10.0,
                       send_bytes: str = "") -> str:
    """
    Open *tty_port* on rpi-displays, optionally send *send_bytes* (escape sequences
    supported as \\x03 etc.), collect output for *duration* seconds, return as str.

    Uses a Python one-liner on rpi-displays to avoid needing pyserial installed here.
    """
    send_repr = repr(send_bytes.encode()).lstrip("b")  # e.g. b'\\x03\\x03' → "'\\x03\\x03'"
    script = (
        f"python3 -c \""
        f"import serial, time; "
        f"s = serial.Serial('{tty_port}', 115200, timeout=1); "
        f"time.sleep(0.5); "
        f"s.write({send_repr}); "
        f"s.flush(); "
        f"out = b''; "
        f"end = time.time() + {duration}; "
        f"[out := out + (s.read(s.in_waiting or 1) or b'') for _ in iter(lambda: time.time() < end, False)]; "
        f"print(out.decode('utf-8', errors='replace'))"
        f"\""
    )
    result = ssh_cmd(script, timeout=int(duration) + 30)
    return result.stdout


def serial_send_and_read(ssh_cmd, tty_port: str, commands: list,
                         read_duration: float = 10.0) -> str:
    """
    Send a list of command strings + newlines to *tty_port*, then read for
    *read_duration* seconds.  Returns combined output as str.
    """
    combined_cmds = "\r\n".join(commands) + "\r\n"
    send_repr = repr(combined_cmds.encode())
    script = (
        f"python3 -c \""
        f"import serial, time; "
        f"s = serial.Serial('{tty_port}', 115200, timeout=1); "
        f"time.sleep(0.5); "
        f"s.reset_input_buffer(); "
        f"s.write({send_repr}); "
        f"s.flush(); "
        f"out = b''; "
        f"end = time.time() + {read_duration}; "
        f"[out := out + (s.read(s.in_waiting or 1) or b'') for _ in iter(lambda: time.time() < end, False)]; "
        f"print(out.decode('utf-8', errors='replace'))"
        f"\""
    )
    result = ssh_cmd(script, timeout=int(read_duration) + 30)
    return result.stdout


def read_serial_until(ssh_cmd, tty_port: str, trigger: str,
                      timeout: float = 30.0, send_first: str = "") -> str:
    """
    Read from *tty_port* until *trigger* appears in the output or *timeout* expires.
    Optionally send *send_first* before reading.
    Returns accumulated output.
    """
    send_repr = repr((send_first.encode())) if send_first else "b''"
    trigger_repr = repr(trigger)
    script = (
        f"python3 -c \""
        f"import serial, time; "
        f"s = serial.Serial('{tty_port}', 115200, timeout=1); "
        f"time.sleep(0.5); "
        f"s.reset_input_buffer(); "
        f"s.write({send_repr}); "
        f"s.flush(); "
        f"out = b''; "
        f"end = time.time() + {timeout}; "
        f"trigger = {trigger_repr}.encode(); "
        f"while time.time() < end: "
        f"  chunk = s.read(s.in_waiting or 1); "
        f"  if chunk: out += chunk; "
        f"  if trigger in out: break; "
        f"print(out.decode('utf-8', errors='replace'))"
        f"\""
    )
    result = ssh_cmd(script, timeout=int(timeout) + 30)
    return result.stdout


# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers exposed for tests
# ──────────────────────────────────────────────────────────────────────────────

def is_circuitpython_running(ssh_cmd, circuitpy_mount_path: str = CIRCUITPY_MOUNT) -> bool:
    """Return True if boot_out.txt on CIRCUITPY contains 'CircuitPython'."""
    result = ssh_cmd(f"cat {circuitpy_mount_path}/boot_out.txt 2>/dev/null")
    return "CircuitPython" in result.stdout


def get_boot_out(ssh_cmd, mount_path: str = CIRCUITPY_MOUNT,
                 filename: str = "boot_out.txt") -> str:
    result = ssh_cmd(f"cat {mount_path}/{filename} 2>/dev/null")
    return result.stdout


def write_file_to_mount(ssh_cmd, mount_path: str, filename: str, content: str) -> None:
    """Write *content* to *filename* inside *mount_path* on rpi-displays."""
    escaped = content.replace("'", "'\\''")
    ssh_cmd(f"printf '%s' '{escaped}' | sudo tee {mount_path}/{filename} > /dev/null")
    ssh_cmd(f"sync")


def ensure_circuitpython(ssh_cmd, flash_firmware_fn, cp_uf2: str = "circuitpython-pico-w-10.1.4.uf2"):
    """
    Ensure the board is running CircuitPython 10.1.4.

    Checks boot_out.txt; if version mismatch or missing, reflashes.
    """
    # Try to read boot_out.txt — mount temporarily
    ssh_cmd(f"sudo mkdir -p {CIRCUITPY_MOUNT}")
    ssh_cmd(f"sudo umount {CIRCUITPY_MOUNT} 2>/dev/null; true")

    dev_present = ssh_cmd(f"test -b {CIRCUITPY_DEV} && echo ok")
    if "ok" in dev_present.stdout:
        ssh_cmd(f"sudo mount {CIRCUITPY_DEV} {CIRCUITPY_MOUNT} 2>/dev/null")
        boot = get_boot_out(ssh_cmd, CIRCUITPY_MOUNT)
        ssh_cmd(f"sudo umount {CIRCUITPY_MOUNT} 2>/dev/null; true")
        if "CircuitPython 10.1.4" in boot:
            return  # Already running correct version

    # Need to reflash
    flash_firmware_fn(cp_uf2)
    time.sleep(5)  # Wait for enumeration
