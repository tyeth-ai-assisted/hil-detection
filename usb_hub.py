"""
usb_hub.py — USB hub port control via Adafruit 8-channel solenoid driver.
Parameterized version of solenoid_hub_control.py — timing parameters are
exposed on port_off() so callers can tune for device-specific reset sequences.

Use case: SAMD51/bossac requires a double-tap reset within ~500ms.
With soft-latching power buttons, the on/off pulse timing must be tuned
to hit the bossac window. The default timings match the hub's standard
ON/OFF sequences; override sleep_between and off_duration for SAMD51.

Hardware:
  - Adafruit I2C to 8 Channel Solenoid Driver (product #6318)
  - MCP23017 GPIO expander at I2C address 0x20
  - Solenoid channels 0-7 = MCP23017 port A pins A0-A7
  - Each channel drives a soft-latching button on the USB hub port
  - Pi Zero 2W at 192.168.1.234, I2C on bus 1

Timing sequences (soft-latching toggle buttons):
  ON:  200ms HIGH -> LOW (single pulse)
  OFF: 200ms HIGH -> 500ms LOW -> 1000ms HIGH -> LOW

Usage:
    from usb_hub import SolenoidHubController
    hub = SolenoidHubController()
    hub.port_on(1)   # turn on USB hub port 1 (channel 1)
    hub.port_off(1)  # turn off USB hub port 1
    # SAMD51 double-tap: tighter timing
    hub.port_off(2, sleep_between=0.1, off_duration=0.3)
    hub.cleanup()
"""

import time
import board
import busio
import digitalio
from adafruit_mcp230xx.mcp23017 import MCP23017


class SolenoidHubController:
    """
    Controls USB hub ports 0-6 via Adafruit 8-channel solenoid driver.
    Each channel maps to one USB hub port's soft-latching power button.
    """

    def __init__(self, i2c_address: int = 0x20):
        self.i2c = busio.I2C(board.SCL, board.SDA)
        self.mcp = MCP23017(self.i2c, address=i2c_address)
        self._pins = {}

    def _get_pin(self, channel: int):
        if channel not in self._pins:
            pin = self.mcp.get_pin(channel)
            pin.direction = digitalio.Direction.OUTPUT
            pin.value = False
            self._pins[channel] = pin
        return self._pins[channel]

    def port_on(self, channel: int, on_duration: float = 0.200) -> None:
        """
        Power ON sequence for USB hub port.
        Single pulse: on_duration HIGH -> LOW
        """
        pin = self._get_pin(channel)
        pin.value = True
        time.sleep(on_duration)
        pin.value = False

    def port_off(self, channel: int, on_first: bool = True,
                 on_duration: float = 0.2, sleep_between: float = 0.5,
                 off_duration: float = 1.0) -> None:
        """
        Power OFF sequence for USB hub port.

        Default timing: 200ms HIGH -> 500ms LOW -> 1000ms HIGH -> LOW
        The longer final pulse distinguishes OFF from ON for the hub logic.

        Parameters:
            on_first:       Send a short ON pulse before the OFF pulse (default True).
            on_duration:    Duration of the initial ON pulse in seconds (default 0.2).
            sleep_between:  Gap between ON and OFF pulses in seconds (default 0.5).
                            Reduce to ~0.1 for SAMD51 double-tap reset sequences.
            off_duration:   Duration of the OFF pulse in seconds (default 1.0).
                            Reduce to ~0.3 for SAMD51 double-tap reset sequences.
        """
        pin = self._get_pin(channel)
        if on_first:
            pin.value = True
            time.sleep(on_duration)
            pin.value = False
            time.sleep(sleep_between)
        pin.value = True
        time.sleep(off_duration)
        pin.value = False

    def all_off(self) -> None:
        """Send OFF sequence to all 7 hub ports (channels 0-6)."""
        for ch in range(7):
            self.port_off(ch)
            time.sleep(0.1)

    def cleanup(self) -> None:
        """Ensure all channels are LOW before releasing."""
        for pin in self._pins.values():
            pin.value = False
