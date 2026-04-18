"""
test_wippersnapper.py — HIL tests for Pico W running WipperSnapper firmware.

Assumes WipperSnapper UF2 is at <firmware_dir>/wippersnapper-pico-w.uf2 on
rpi-displays.  All tests are skipped automatically if the file is absent.

Adafruit IO credentials used in tests are read from environment variables
(or a tests/credentials.py file) — see tests/credentials.example.py.
"""
import json
import os
import time
import pytest

# ──────────────────────────────────────────────────────────────────────────────
# Credentials — read from env or fall back to example values.
# Set HIL_AIO_KEY, HIL_AIO_USER, HIL_WIFI_SSID, HIL_WIFI_PASSWORD in the
# environment (or export them from a sourced credentials file) before running.
# ──────────────────────────────────────────────────────────────────────────────
try:
    from credentials import AIO_USER, AIO_KEY, WIFI_SSID, WIFI_PASSWORD  # type: ignore
except ImportError:
    AIO_USER = os.environ.get("HIL_AIO_USER", "playground_example")
    AIO_KEY = os.environ.get("HIL_AIO_KEY", "")
    WIFI_SSID = os.environ.get("HIL_WIFI_SSID", "free4all")
    WIFI_PASSWORD = os.environ.get("HIL_WIFI_PASSWORD", "password")

from conftest import (
    CIRCUITPY_DEV,
    CIRCUITPY_MOUNT,
    get_boot_out,
    read_serial_until,
    serial_read_output,
    write_file_to_mount,
)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

WS_UF2 = "wippersnapper-pico-w.uf2"

# WipperSnapper mounts its partition as WIPPER (or similar label); we use the
# same block device since there is only one Pico W connected.
WIPPER_DEV = "/dev/sdf1"
WIPPER_MOUNT = "/tmp/wipper"

AIO_URL = "io.adafruit.com"

pytestmark = pytest.mark.wippersnapper


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ws_flashed(ssh_cmd, flash_firmware, firmware_dir):
    """
    Module-scoped fixture: flash WipperSnapper once for all WS tests.
    Skips the entire module if the UF2 file is not present.
    """
    firmware_path = f"{firmware_dir}/{WS_UF2}"
    check = ssh_cmd(f"test -f {firmware_path} && echo ok")
    if "ok" not in check.stdout:
        pytest.skip(
            f"WipperSnapper UF2 not found on rpi-displays: {firmware_path}\n"
            f"Copy the UF2 to rpi-displays:{firmware_path} and re-run."
        )
    flash_firmware(WS_UF2, timeout=120)
    time.sleep(6)
    return True


@pytest.fixture(scope="function")
def wipper_mount(ssh_cmd, ws_flashed):
    """
    Mount the WipperSnapper mass-storage partition.
    Yields the mount-point path; unmounts on teardown.
    """
    ssh_cmd(f"sudo mkdir -p {WIPPER_MOUNT}")
    ssh_cmd(f"sudo umount {WIPPER_MOUNT} 2>/dev/null; true")

    deadline = time.time() + 15
    while time.time() < deadline:
        result = ssh_cmd(f"test -b {WIPPER_DEV} && echo ok")
        if "ok" in result.stdout:
            break
        time.sleep(1)
    else:
        pytest.skip(f"{WIPPER_DEV} not found — is WipperSnapper mass storage present?")

    mount_result = ssh_cmd(f"sudo mount {WIPPER_DEV} {WIPPER_MOUNT}")
    if mount_result.returncode != 0:
        pytest.skip(f"Failed to mount {WIPPER_DEV}: {mount_result.stderr.strip()}")

    yield WIPPER_MOUNT

    ssh_cmd(f"sync; sudo umount {WIPPER_MOUNT} 2>/dev/null; true")


def _write_secrets(ssh_cmd, mount: str, wifi_ssid: str, wifi_pass: str,
                   aio_user: str, aio_key: str, io_url: str = AIO_URL) -> None:
    """Write a WipperSnapper secrets.json to *mount*."""
    secrets = {
        "network_type_wifi": {
            "ssid": wifi_ssid,
            "password": wifi_pass,
        },
        "aio_username": aio_user,
        "aio_key": aio_key,
        "status_pixel_brightness": 0.2,
        "io_url": io_url,
    }
    content = json.dumps(secrets, indent=2)
    write_file_to_mount(ssh_cmd, mount, "secrets.json", content)


def _flash_ws_with_secrets(ssh_cmd, flash_firmware, wipper_mount_path: str,
                            wifi_ssid: str, wifi_pass: str,
                            aio_user: str, aio_key: str,
                            io_url: str = AIO_URL) -> None:
    """Flash WS and write secrets so the board reboots with them."""
    flash_firmware(WS_UF2, timeout=120)
    time.sleep(6)

    # Mount, write, unmount
    ssh_cmd(f"sudo mkdir -p {wipper_mount_path}")
    ssh_cmd(f"sudo umount {wipper_mount_path} 2>/dev/null; true")

    deadline = time.time() + 15
    while time.time() < deadline:
        if "ok" in ssh_cmd(f"test -b {WIPPER_DEV} && echo ok").stdout:
            break
        time.sleep(1)

    ssh_cmd(f"sudo mount {WIPPER_DEV} {wipper_mount_path}")
    _write_secrets(ssh_cmd, wipper_mount_path, wifi_ssid, wifi_pass, aio_user, aio_key, io_url)
    ssh_cmd(f"sync; sudo umount {wipper_mount_path}")
    time.sleep(3)


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestWipperSnapperBoot:
    """Basic boot-log validation tests."""

    def test_no_secrets_boot_log(self, ssh_cmd, flash_firmware, tty_port, wipper_mount):
        """
        Flash WipperSnapper, write an empty/invalid secrets.json, read serial,
        assert boot log contains hardware name and WS version, and that a
        credentials error is logged.
        """
        # Write a minimal blank secrets to trigger credential error
        blank_secrets = "{}"
        write_file_to_mount(ssh_cmd, wipper_mount, "secrets.json", blank_secrets)
        ssh_cmd(f"sync; sudo umount {WIPPER_MOUNT}")

        # Re-flash to get a fresh boot with the blank secrets
        flash_firmware(WS_UF2, timeout=120)
        time.sleep(4)

        output = serial_read_output(ssh_cmd, tty_port, duration=30.0)

        # Check hardware name appears
        hw_names = ["pico_w", "pico w", "raspberry_pi_pico_w", "Pico W"]
        assert any(hw.lower() in output.lower() for hw in hw_names), (
            f"Hardware name not found in boot log.\nOutput:\n{output}"
        )

        # Check WipperSnapper version string
        assert any(kw in output for kw in ["WipperSnapper", "wippersnapper", "Adafruit.io"]), (
            f"WipperSnapper identifier not found in boot log.\nOutput:\n{output}"
        )

        # Check for credentials error
        error_keywords = ["Invalid", "invalid", "missing", "Missing", "credentials",
                          "No such file", "KeyError", "secrets"]
        assert any(kw in output for kw in error_keywords), (
            f"Expected credentials error in boot log but none found.\nOutput:\n{output}"
        )

    def test_boot_log_hw_name(self, ssh_cmd, ws_flashed, wipper_mount):
        """
        Parse wipper_boot_out.txt (or boot_out.txt) from mass storage,
        assert hardware identifier is present.
        """
        # Try wipper_boot_out.txt first, fall back to boot_out.txt
        boot = ssh_cmd(f"cat {WIPPER_MOUNT}/wipper_boot_out.txt 2>/dev/null").stdout
        if not boot.strip():
            boot = get_boot_out(ssh_cmd, WIPPER_MOUNT)

        if not boot.strip():
            pytest.skip("No boot_out file found on WipperSnapper mass storage partition.")

        hw_names = ["pico_w", "pico w", "raspberry_pi_pico_w", "pico"]
        assert any(hw.lower() in boot.lower() for hw in hw_names), (
            f"Hardware identifier not found in boot log:\n{boot}"
        )

    def test_boot_log_version(self, ssh_cmd, ws_flashed, wipper_mount, tty_port):
        """
        Assert WipperSnapper version string is parseable from boot log or serial.
        """
        import re

        boot = ssh_cmd(f"cat {WIPPER_MOUNT}/wipper_boot_out.txt 2>/dev/null").stdout
        if not boot.strip():
            boot = get_boot_out(ssh_cmd, WIPPER_MOUNT)

        # Version patterns: "v1.0.0-beta.5", "1.0.0", "WipperSnapper 1.x.x"
        version_pattern = re.compile(r"v?\d+\.\d+[\.\d\-\w]*")
        ws_pattern = re.compile(r"[Ww]ipper[Ss]napper", re.IGNORECASE)

        if boot.strip() and ws_pattern.search(boot) and version_pattern.search(boot):
            return  # Found in boot log

        # Fall back to serial output
        serial_out = serial_read_output(ssh_cmd, tty_port, duration=15.0)
        combined = boot + "\n" + serial_out

        assert ws_pattern.search(combined), (
            f"WipperSnapper identifier not found in boot log or serial.\nCombined output:\n{combined}"
        )
        assert version_pattern.search(combined), (
            f"Version string not found in boot log or serial.\nCombined output:\n{combined}"
        )

    def test_airlift_modem_firmware(self, ssh_cmd, ws_flashed, tty_port):
        """
        If an AirLift/ESP32 co-processor is present, assert modem firmware version
        is logged during boot.  Skipped if no AirLift is detected.
        """
        output = serial_read_output(ssh_cmd, tty_port, duration=20.0)

        # AirLift indicators in boot log
        airlift_keywords = ["AirLift", "ESP32", "NINA", "airlift", "nina"]
        airlift_present = any(kw in output for kw in airlift_keywords)

        if not airlift_present:
            pytest.skip("No AirLift/ESP32 co-processor detected in boot log — skipping modem test.")

        fw_keywords = ["Firmware", "firmware", "version", "Version", "NINA-W10"]
        assert any(kw in output for kw in fw_keywords), (
            f"AirLift detected but modem firmware version not found.\nOutput:\n{output}"
        )


class TestWipperSnapperConnectivity:
    """Connectivity and credential error-path tests."""

    def test_wrong_wifi_no_crash(self, ssh_cmd, flash_firmware, tty_port):
        """
        Write secrets with wrong WiFi credentials.  Assert board attempts WiFi,
        logs a WiFi error, and does NOT hard-crash (no watchdog reset).
        """
        _flash_and_mount(ssh_cmd, flash_firmware)

        # Write wrong WiFi secrets
        _write_secrets_direct(
            ssh_cmd,
            wifi_ssid="wrongssid",
            wifi_pass="wrongpassword",
            aio_user="dummy_user",
            aio_key="dummy_key_12345678901234567890",
        )
        ssh_cmd(f"sync; sudo umount {WIPPER_MOUNT}")
        time.sleep(3)

        output = serial_read_output(ssh_cmd, tty_port, duration=40.0)

        # Assert WiFi attempt
        wifi_attempt = ["Connecting to WiFi", "WiFi", "SSID", "wifi", "network"]
        assert any(kw in output for kw in wifi_attempt), (
            f"No WiFi connection attempt found in output.\nOutput:\n{output}"
        )

        # Assert WiFi error logged
        wifi_err = ["failed", "Failed", "error", "Error", "timeout", "Timeout",
                    "not found", "disconnected", "Disconnected"]
        assert any(kw in output for kw in wifi_err), (
            f"Expected WiFi error but none found.\nOutput:\n{output}"
        )

        # Assert no watchdog reset (hard crash indicator)
        crash_keywords = ["watchdog", "Watchdog", "hard fault", "Hard Fault", "HARD_RESET"]
        assert not any(kw in output for kw in crash_keywords), (
            f"Watchdog/hard-crash detected in serial output (should not crash).\nOutput:\n{output}"
        )

    def test_wrong_aio_reaches_mqtt(self, ssh_cmd, flash_firmware, tty_port):
        """
        Write secrets with correct WiFi but wrong AIO credentials.
        Assert serial output reaches MQTT connection attempt.
        """
        _flash_and_mount(ssh_cmd, flash_firmware)

        _write_secrets_direct(
            ssh_cmd,
            wifi_ssid=WIFI_SSID,
            wifi_pass=WIFI_PASSWORD,
            aio_user="test_user",
            aio_key="test_key_00000000000000000000000",
        )
        ssh_cmd(f"sync; sudo umount {WIPPER_MOUNT}")
        time.sleep(3)

        output = read_serial_until(
            ssh_cmd, tty_port,
            trigger="MQTT",
            timeout=60.0,
        )

        mqtt_keywords = ["MQTT", "mqtt", "Connecting to AIO", "AIO MQTT", "broker"]
        assert any(kw in output for kw in mqtt_keywords), (
            f"Expected MQTT connection attempt but not found.\nOutput:\n{output}"
        )

    def test_correct_aio_waiting_registration(self, ssh_cmd, flash_firmware, tty_port):
        """
        Write correct AIO credentials and WiFi.  Assert serial output reaches
        'Waiting for registration' or 'registered' within 60 s.
        """
        _flash_and_mount(ssh_cmd, flash_firmware)

        _write_secrets_direct(
            ssh_cmd,
            wifi_ssid=WIFI_SSID,
            wifi_pass=WIFI_PASSWORD,
            aio_user=AIO_USER,
            aio_key=AIO_KEY,
            io_url=AIO_URL,
        )
        ssh_cmd(f"sync; sudo umount {WIPPER_MOUNT}")
        time.sleep(3)

        # Wait for registration keyword
        output = read_serial_until(
            ssh_cmd, tty_port,
            trigger="registr",  # matches 'registration', 'registered', etc.
            timeout=75.0,
        )

        reg_keywords = [
            "Waiting for registration",
            "registered",
            "Registration",
            "registration",
        ]
        assert any(kw in output for kw in reg_keywords), (
            f"Expected registration message within 60 s but not found.\nOutput:\n{output}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Module-level helpers (not fixtures — called directly in tests)
# ──────────────────────────────────────────────────────────────────────────────

def _flash_and_mount(ssh_cmd, flash_firmware) -> None:
    """Flash WS and prepare WIPPER mount point (unmounted)."""
    flash_firmware(WS_UF2, timeout=120)
    time.sleep(6)
    ssh_cmd(f"sudo mkdir -p {WIPPER_MOUNT}")
    ssh_cmd(f"sudo umount {WIPPER_MOUNT} 2>/dev/null; true")

    deadline = time.time() + 15
    while time.time() < deadline:
        if "ok" in ssh_cmd(f"test -b {WIPPER_DEV} && echo ok").stdout:
            break
        time.sleep(1)
    else:
        pytest.skip(f"{WIPPER_DEV} not available after flash.")

    result = ssh_cmd(f"sudo mount {WIPPER_DEV} {WIPPER_MOUNT}")
    if result.returncode != 0:
        pytest.skip(f"Could not mount {WIPPER_DEV}: {result.stderr.strip()}")


def _write_secrets_direct(ssh_cmd, wifi_ssid: str, wifi_pass: str,
                          aio_user: str, aio_key: str,
                          io_url: str = AIO_URL) -> None:
    """Write secrets.json to already-mounted WIPPER_MOUNT."""
    secrets = {
        "network_type_wifi": {
            "ssid": wifi_ssid,
            "password": wifi_pass,
        },
        "aio_username": aio_user,
        "aio_key": aio_key,
        "status_pixel_brightness": 0.2,
        "io_url": io_url,
    }
    content = json.dumps(secrets, indent=2)
    write_file_to_mount(ssh_cmd, WIPPER_MOUNT, "secrets.json", content)
