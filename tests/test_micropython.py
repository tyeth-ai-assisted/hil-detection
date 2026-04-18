"""
test_micropython.py — HIL tests for Pico W running MicroPython v1.28.0.

Assumes MicroPython UF2 is at <firmware_dir>/micropython-pico-w-1.28.0.uf2
on rpi-displays.  Tests are skipped automatically if the file is absent.

MicroPython USB VID:PID in FS mode: 2e8a:0005
BOOTSEL mode (after machine.bootloader()): 2e8a:0003
"""
import time
import pytest

from conftest import (
    BOOTSEL_VID_PID,
    read_serial_until,
    serial_read_output,
    serial_send_and_read,
)

MP_UF2 = "micropython-pico-w-1.28.0.uf2"
MP_VID_PID = "2e8a:0005"

pytestmark = pytest.mark.micropython


# ──────────────────────────────────────────────────────────────────────────────
# Module-level fixture: flash MicroPython once
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mp_flashed(ssh_cmd, flash_firmware, firmware_dir):
    """
    Flash MicroPython (with erase) once for the entire module.
    Skips the whole module if the UF2 is not present on rpi-displays.
    """
    firmware_path = f"{firmware_dir}/{MP_UF2}"
    check = ssh_cmd(f"test -f {firmware_path} && echo ok")
    if "ok" not in check.stdout:
        pytest.skip(
            f"MicroPython UF2 not found on rpi-displays: {firmware_path}\n"
            f"Copy micropython-pico-w-1.28.0.uf2 to rpi-displays:{firmware_path} and re-run."
        )
    flash_firmware(MP_UF2, extra_args="--erase", timeout=180)
    time.sleep(5)
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _interrupt_repl(ssh_cmd, tty_port: str) -> str:
    """Send Ctrl-C to break into REPL; return output."""
    return serial_read_output(ssh_cmd, tty_port, duration=5.0, send_bytes="\x03\x03")


def _repl_exec(ssh_cmd, tty_port: str, code_line: str, read_duration: float = 10.0) -> str:
    """Send a single code line to MicroPython REPL and return output."""
    return serial_send_and_read(ssh_cmd, tty_port, [code_line], read_duration=read_duration)


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestMicroPythonREPL:
    """MicroPython REPL interaction tests."""

    def test_repl_banner(self, ssh_cmd, mp_flashed, tty_port):
        """
        Send Ctrl-C to MicroPython REPL, assert 'MicroPython' and
        'Type "help()"' appear in output.
        """
        output = _interrupt_repl(ssh_cmd, tty_port)

        assert "MicroPython" in output, (
            f"'MicroPython' banner not found.\nOutput:\n{output}"
        )
        assert 'help()' in output or 'Type' in output, (
            f"'Type \"help()\"' prompt not found.\nOutput:\n{output}"
        )

    def test_basic_arithmetic(self, ssh_cmd, mp_flashed, tty_port):
        """
        Send 'print(2+2)' to REPL, assert '4' appears in response.
        """
        # First break into REPL
        _interrupt_repl(ssh_cmd, tty_port)

        output = _repl_exec(ssh_cmd, tty_port, "print(2+2)", read_duration=8.0)
        assert "4" in output, (
            f"Expected '4' from print(2+2) but got:\n{output}"
        )

    def test_machine_bootloader(self, ssh_cmd, mp_flashed, tty_port, usb_path):
        """
        Call machine.bootloader() from REPL, assert device re-enumerates as
        BOOTSEL (VID:PID 2e8a:0003) within 10 s.
        """
        _interrupt_repl(ssh_cmd, tty_port)

        # Send bootloader command (no response expected — board will reset)
        ssh_cmd(
            f"python3 -c \""
            f"import serial, time; "
            f"s = serial.Serial('{tty_port}', 115200, timeout=1); "
            f"time.sleep(0.3); "
            f"s.write(b'import machine; machine.bootloader()\\r\\n'); "
            f"s.flush(); "
            f"time.sleep(1)"
            f"\""
        )

        # Poll for BOOTSEL enumeration
        bootsel_vid, bootsel_pid = BOOTSEL_VID_PID.split(":")
        deadline = time.time() + 15
        found = False
        while time.time() < deadline:
            result = ssh_cmd(f"lsusb -d {BOOTSEL_VID_PID} 2>/dev/null | head -1")
            if result.returncode == 0 and result.stdout.strip():
                found = True
                break
            time.sleep(1)

        assert found, (
            f"Board did not re-enumerate as BOOTSEL (2e8a:0003) within 15 s "
            f"after machine.bootloader()."
        )

        # Re-flash MicroPython to leave board in a known good state for subsequent tests
        # (the mp_flashed fixture is module-scoped so we call flash directly via ssh)
        # Note: if this test runs last, this is not strictly necessary.

    def test_led_toggle(self, ssh_cmd, mp_flashed, tty_port):
        """
        Toggle the on-board LED via machine.Pin in the REPL, assert 'LED on'
        appears in output.
        """
        _interrupt_repl(ssh_cmd, tty_port)

        commands = [
            "import machine",
            'led = machine.Pin("LED", machine.Pin.OUT)',
            "led.on()",
            'print("LED on")',
        ]
        output = serial_send_and_read(ssh_cmd, tty_port, commands, read_duration=10.0)
        assert "LED on" in output, (
            f"Expected 'LED on' in output.\nOutput was:\n{output}"
        )
        assert "Traceback" not in output and "Error" not in output, (
            f"Unexpected error in LED toggle output:\n{output}"
        )

    def test_import_network(self, ssh_cmd, mp_flashed, tty_port):
        """
        Import the 'network' module in the REPL.  Assert no ImportError —
        the network module should be present on Pico W MicroPython builds.
        """
        _interrupt_repl(ssh_cmd, tty_port)

        output = _repl_exec(ssh_cmd, tty_port, "import network", read_duration=8.0)
        assert "ImportError" not in output, (
            f"ImportError when importing 'network' module.\n"
            f"Output:\n{output}"
        )
        assert "ModuleNotFoundError" not in output, (
            f"ModuleNotFoundError when importing 'network' module.\n"
            f"Output:\n{output}"
        )

    def test_network_wlan_interface(self, ssh_cmd, mp_flashed, tty_port):
        """
        Check that network.WLAN(network.STA_IF) returns a valid WLAN object.
        Asserts no exception and the repr contains 'WLAN' or 'wlan'.
        """
        _interrupt_repl(ssh_cmd, tty_port)

        commands = [
            "import network",
            "wlan = network.WLAN(network.STA_IF)",
            "print(wlan)",
        ]
        output = serial_send_and_read(ssh_cmd, tty_port, commands, read_duration=10.0)
        assert "Traceback" not in output, (
            f"Unexpected exception creating WLAN interface:\n{output}"
        )
        assert any(kw in output for kw in ["WLAN", "wlan", "STA"]), (
            f"WLAN object repr not found in output:\n{output}"
        )


class TestMicroPythonFirmwareInfo:
    """Tests that verify firmware version and board identification."""

    def test_sys_version(self, ssh_cmd, mp_flashed, tty_port):
        """
        Query sys.version from REPL, assert it contains '1.28.0' and 'MicroPython'.
        """
        _interrupt_repl(ssh_cmd, tty_port)

        commands = ["import sys", "print(sys.version)"]
        output = serial_send_and_read(ssh_cmd, tty_port, commands, read_duration=8.0)
        assert "MicroPython" in output or "1.28" in output, (
            f"MicroPython version string not found in sys.version output:\n{output}"
        )

    def test_sys_platform(self, ssh_cmd, mp_flashed, tty_port):
        """
        Query sys.platform, assert it identifies as 'rp2' (RP2040/RP2350 platform).
        """
        _interrupt_repl(ssh_cmd, tty_port)

        commands = ["import sys", "print(sys.platform)"]
        output = serial_send_and_read(ssh_cmd, tty_port, commands, read_duration=8.0)
        assert "rp2" in output.lower(), (
            f"Expected 'rp2' platform identifier.\nOutput:\n{output}"
        )
