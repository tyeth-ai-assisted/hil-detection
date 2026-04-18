"""
test_circuitpython.py — HIL tests for Pico W running CircuitPython 10.1.4.

Each test ensures CircuitPython 10.1.4 is installed before running (reflashing
if necessary).  Tests exercise the REPL, code.py auto-reload, and on-board LED.
"""
import time
import pytest

from conftest import (
    CIRCUITPY_DEV,
    CIRCUITPY_MOUNT,
    ensure_circuitpython,
    get_boot_out,
    is_circuitpython_running,
    read_serial_until,
    serial_read_output,
    serial_send_and_read,
    write_file_to_mount,
)

CP_UF2 = "circuitpython-pico-w-10.1.4.uf2"

pytestmark = pytest.mark.circuitpython


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_cp(ssh_cmd, flash_firmware):
    """Shorthand: guarantee CP 10.1.4 is running before each test."""
    ensure_circuitpython(ssh_cmd, flash_firmware, CP_UF2)
    time.sleep(3)


def _ctrl_c():
    """Return two Ctrl-C bytes as a string suitable for serial_read_output."""
    return "\x03\x03"


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestCircuitPythonREPL:
    """Tests focused on the CircuitPython REPL."""

    def test_repl_detected(self, ssh_cmd, flash_firmware, tty_port):
        """
        Open serial port, send Ctrl-C twice, assert '>>>' prompt appears.
        """
        _ensure_cp(ssh_cmd, flash_firmware)

        output = serial_read_output(
            ssh_cmd, tty_port,
            duration=10.0,
            send_bytes=_ctrl_c(),
        )
        assert ">>>" in output, (
            f"CircuitPython REPL prompt '>>>' not found in serial output.\n"
            f"Output was:\n{output}"
        )

    def test_ctrl_c_clears_running_code(self, ssh_cmd, flash_firmware, tty_port, circuitpy_mount):
        """
        Write an infinite-loop code.py that prints 'running', confirm it starts,
        send Ctrl-C, assert REPL prompt appears and 'running' output stops.
        """
        _ensure_cp(ssh_cmd, flash_firmware)

        code = (
            "import time\n"
            "while True:\n"
            "    print('running')\n"
            "    time.sleep(0.2)\n"
        )
        write_file_to_mount(ssh_cmd, circuitpy_mount, "code.py", code)

        # Unmount so CircuitPython picks up the new file via auto-reload
        ssh_cmd(f"sync; sudo umount {CIRCUITPY_MOUNT}")
        time.sleep(3)  # Allow auto-reload

        # Read output — 'running' should appear
        running_output = serial_read_output(
            ssh_cmd, tty_port,
            duration=5.0,
            send_bytes="",
        )
        assert "running" in running_output, (
            f"Expected 'running' in serial output before Ctrl-C.\nOutput: {running_output}"
        )

        # Send Ctrl-C to interrupt
        prompt_output = serial_read_output(
            ssh_cmd, tty_port,
            duration=5.0,
            send_bytes=_ctrl_c(),
        )
        assert ">>>" in prompt_output, (
            f"Expected '>>>' after Ctrl-C but did not find it.\nOutput: {prompt_output}"
        )

        # Confirm 'running' no longer appearing (give it a few seconds)
        after_output = serial_read_output(
            ssh_cmd, tty_port,
            duration=3.0,
            send_bytes="",
        )
        # The 'running' could appear once more in the buffer; we just need >>> present
        assert ">>>" in after_output or "running" not in after_output, (
            f"Board still printing 'running' after Ctrl-C.\nOutput: {after_output}"
        )

    def test_supervisor_reload(self, ssh_cmd, flash_firmware, tty_port, circuitpy_mount):
        """
        Write a code.py that prints 'hello reload', invoke supervisor.reload()
        from REPL, assert 'hello reload' appears in serial output.
        """
        _ensure_cp(ssh_cmd, flash_firmware)

        code = "print('hello reload')\n"
        write_file_to_mount(ssh_cmd, circuitpy_mount, "code.py", code)
        ssh_cmd(f"sync; sudo umount {CIRCUITPY_MOUNT}")
        time.sleep(2)

        # First, interrupt any running code to reach REPL
        serial_read_output(ssh_cmd, tty_port, duration=3.0, send_bytes=_ctrl_c())

        # Send supervisor.reload() — this will cause code.py to run again
        output = read_serial_until(
            ssh_cmd, tty_port,
            trigger="hello reload",
            timeout=20.0,
            send_first="import supervisor; supervisor.reload()\r\n",
        )
        assert "hello reload" in output, (
            f"Expected 'hello reload' after supervisor.reload() but not found.\n"
            f"Output was:\n{output}"
        )

    def test_led_after_erase(self, ssh_cmd, flash_firmware, tty_port, circuitpy_mount):
        """
        Perform full flash erase, re-flash CP 10.1.4, write code.py that turns
        on the on-board LED, assert no Traceback and LED commands execute.
        """
        # Full erase then flash
        flash_firmware(CP_UF2, extra_args="--erase", timeout=180)
        time.sleep(5)

        led_code = (
            "import board\n"
            "import digitalio\n"
            "led = digitalio.DigitalInOut(board.LED)\n"
            "led.direction = digitalio.Direction.OUTPUT\n"
            "led.value = True\n"
            "print('LED is ON')\n"
        )
        write_file_to_mount(ssh_cmd, circuitpy_mount, "code.py", led_code)
        ssh_cmd(f"sync; sudo umount {CIRCUITPY_MOUNT}")
        time.sleep(4)  # Wait for auto-reload

        output = serial_read_output(ssh_cmd, tty_port, duration=10.0)
        assert "Traceback" not in output, (
            f"Unexpected Traceback in serial output after LED code:\n{output}"
        )
        assert "LED is ON" in output, (
            f"Expected 'LED is ON' but not found in output:\n{output}"
        )

    def test_led_via_code_py(self, ssh_cmd, flash_firmware, tty_port, circuitpy_mount):
        """
        Write a blink loop code.py that prints 'blink', assert 'blink' appears
        in serial output and no errors are present.
        """
        _ensure_cp(ssh_cmd, flash_firmware)

        blink_code = (
            "import board\n"
            "import digitalio\n"
            "import time\n"
            "led = digitalio.DigitalInOut(board.LED)\n"
            "led.direction = digitalio.Direction.OUTPUT\n"
            "for i in range(5):\n"
            "    led.value = True\n"
            "    print('blink')\n"
            "    time.sleep(0.3)\n"
            "    led.value = False\n"
            "    time.sleep(0.3)\n"
            "print('blink done')\n"
        )
        write_file_to_mount(ssh_cmd, circuitpy_mount, "code.py", blink_code)
        ssh_cmd(f"sync; sudo umount {CIRCUITPY_MOUNT}")
        time.sleep(4)

        output = serial_read_output(ssh_cmd, tty_port, duration=10.0)
        assert "blink" in output, (
            f"Expected 'blink' in serial output.\nOutput was:\n{output}"
        )
        assert "Traceback" not in output and "Error" not in output.split("blink")[0], (
            f"Error found in serial output before blink:\n{output}"
        )


class TestCircuitPythonBootOut:
    """Tests that inspect boot_out.txt on the CIRCUITPY mass storage."""

    def test_boot_out_version(self, ssh_cmd, flash_firmware, circuitpy_mount):
        """boot_out.txt should report CircuitPython 10.1.4."""
        _ensure_cp(ssh_cmd, flash_firmware)
        boot = get_boot_out(ssh_cmd, circuitpy_mount)
        assert "CircuitPython" in boot, (
            f"'CircuitPython' not found in boot_out.txt:\n{boot}"
        )
        assert "10.1.4" in boot, (
            f"Version '10.1.4' not found in boot_out.txt:\n{boot}"
        )

    def test_boot_out_board_id(self, ssh_cmd, flash_firmware, circuitpy_mount):
        """boot_out.txt should identify the board as a Pico W variant."""
        _ensure_cp(ssh_cmd, flash_firmware)
        boot = get_boot_out(ssh_cmd, circuitpy_mount)
        pico_variants = ["pico_w", "Pico W", "raspberry_pi_pico_w"]
        assert any(v.lower() in boot.lower() for v in pico_variants), (
            f"Board ID (pico_w variant) not found in boot_out.txt:\n{boot}"
        )
