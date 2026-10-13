"""Hardware-free Maiman drivers using an in-memory serial transport.
FakeMaimanSerial emulates J/P commands, state register 0700, TEC register 0A1A,
serial numbers and plausible measurements. Real protocol, scaling and identity
validation remain unchanged; only the serial transport is replaced."""

from __future__ import annotations

from typing import Optional, Union

from .driver import MaimanDriver, SF6015, SF8150T


class FakeMaimanSerial:
    """Emulate a Maiman serial device entirely in memory."""

    def __init__(self, serial_number: int, has_tec: bool,
                 current_set_step: float, current_meas_step: float) -> None:
        self.is_open = True
        self._has_tec = has_tec
        self._set_step = current_set_step
        self._meas_step = current_meas_step
        self._pending = b""
        self._regs = {
            0x0701: serial_number & 0xFFFF,
            0x0702: serial_number & 0xFFFF,   # Model / version ID (SF6015)
            0x0710: 0x0640,                   # Firmware-Version (SF8xxx)
            0x0700: 0x0001,                   # only the powered-on bit is set
            0x0300: 0,                        # Current setpoint (raw)
            0x0100: 0,                        # Frequency
            0x0200: 0,                        # Pulse duration
            0x0800: 0,                        # Lock-Status
        }
        if has_tec:
            self._regs[0x0A1A] = 0x0000       # TEC state (OFF)
            self._regs[0x0A10] = 2500         # TEC setpoint 25.00 °C

    # -- pyserial-compatible API -------------------------------------- #
    def reset_input_buffer(self) -> None:
        self._pending = b""

    def reset_output_buffer(self) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False

    def write(self, data: bytes) -> int:
        text = data.decode("ascii", errors="replace").strip()
        if not text:
            return len(data)
        kind = text[0]
        if kind == "J":
            param = int(text[1:5], 16)
            self._pending = self._respond(param)
        elif kind == "P":
            param = int(text[1:5], 16)
            value = int(text[6:10], 16)
            self._apply(param, value)
            # Factory default: no response to P commands
        return len(data)

    def read_until(self, terminator: bytes = b"\r") -> bytes:
        out, self._pending = self._pending, b""
        return out

    # -- Emulation ---------------------------------------------------- #
    def _respond(self, param: int) -> bytes:
        state = self._regs[0x0700]
        started = bool(state & (1 << 1))
        tec_started = bool(self._regs.get(0x0A1A, 0) & (1 << 1))

        # TEC parameters exist only on TEC devices.
        if not self._has_tec and 0x0A00 <= param <= 0x0BFF:
            return b"K0000 0000\r"

        if param == 0x0307:        # Measured output current
            raw_set = self._regs.get(0x0300, 0)
            value = round(raw_set * self._set_step / self._meas_step) if started else 0
        elif param == 0x0407:      # Measured output voltage (0.1 V)
            value = 20 if started else 0
        elif param == 0x0A15:      # Measured TEC temperature (0.01 °C)
            value = self._regs.get(0x0A10, 2500)
        elif param == 0x0A16:      # TEC current (0.1 A)
            value = 5 if tec_started else 0
        elif param == 0x0A18:      # TEC voltage (0.1 V)
            value = 15 if tec_started else 0
        elif param == 0x0AE4:      # External NTC (0.1 °C)
            value = 250
        elif param == 0x0AF4:      # PCB temperature (0.1 °C)
            value = 300
        elif param in self._regs:
            value = self._regs[param]
        else:
            return b"K0000 0000\r"   # Unsupported parameter

        return f"K{param:04X} {value & 0xFFFF:04X}\r".encode("ascii")

    def _apply(self, param: int, value: int) -> None:
        if param == 0x0700:
            self._apply_driver_state(value)
        elif param == 0x0A1A and self._has_tec:
            self._apply_tec_state(value)
        else:
            self._regs[param] = value & 0xFFFF

    def _apply_driver_state(self, sub: int) -> None:
        s = self._regs[0x0700]
        if sub == 0x0008:      s |= (1 << 1)     # Start
        elif sub == 0x0010:    s &= ~(1 << 1)    # Stop
        elif sub == 0x0020:    s |= (1 << 2)     # Internal current source
        elif sub == 0x0040:    s &= ~(1 << 2)    # External current source
        elif sub == 0x0400:    s |= (1 << 4)     # Internal enable
        elif sub == 0x0200:    s &= ~(1 << 4)    # External enable
        elif sub == 0x1000:    s &= ~(1 << 7)    # Allow interlock
        elif sub == 0x2000:    s |= (1 << 7)     # Deny interlock
        elif sub == 0x8000:    s &= ~(1 << 6)    # Allow external NTC
        elif sub == 0x4000:    s |= (1 << 6)     # Deny external NTC
        # 0x0002 (save), 0x0060/0x0080 (standalone): no state change
        s |= 1                                   # powered-on bit remains set
        self._regs[0x0700] = s & 0xFFFF

    def _apply_tec_state(self, sub: int) -> None:
        t = self._regs.get(0x0A1A, 0)
        if sub == 0x0008:      t |= (1 << 1)     # TEC Start (Status: 1st bit)
        elif sub == 0x0010:    t &= ~(1 << 1)    # TEC Stop
        elif sub == 0x0020:    t |= (1 << 2)     # Internal temperature source
        elif sub == 0x0040:    t &= ~(1 << 2)    # External temperature source
        elif sub == 0x0400:    t |= (1 << 4)     # Internal enable
        elif sub == 0x0200:    t &= ~(1 << 4)    # External enable
        self._regs[0x0A1A] = t & 0xFFFF


class MockSF6015(SF6015):
    """Hardware-free SF6015 for tests and GUI demonstrations."""

    _SETTLE_SECONDS = 0.0      # Mock responds immediately; no wait needed

    def __init__(self, port: str = "MOCK-SF6015",
                 serial_number: Union[int, str] = 0x0001, **kwargs) -> None:
        self._mock_serial_number = MaimanDriver._parse_serial(serial_number) or 0
        super().__init__(port, **kwargs)

    def _open_transport(self) -> FakeMaimanSerial:
        return FakeMaimanSerial(
            self._mock_serial_number, has_tec=False,
            current_set_step=self.CURRENT_SET_STEP_A,
            current_meas_step=self.CURRENT_MEAS_STEP_A,
        )


class MockSF8150T(SF8150T):
    """Hardware-free SF8150-T with TEC for tests and GUI demonstrations."""

    _SETTLE_SECONDS = 0.0      # Mock responds immediately; no wait needed

    def __init__(self, port: str = "MOCK-SF8150T",
                 serial_number: Union[int, str] = 0x0002,
                 current_max_a: Optional[float] = None, **kwargs) -> None:
        self._mock_serial_number = MaimanDriver._parse_serial(serial_number) or 0
        super().__init__(port, current_max_a=current_max_a, **kwargs)

    def _open_transport(self) -> FakeMaimanSerial:
        return FakeMaimanSerial(
            self._mock_serial_number, has_tec=True,
            current_set_step=self.CURRENT_SET_STEP_A,
            current_meas_step=self.CURRENT_MEAS_STEP_A,
        )
