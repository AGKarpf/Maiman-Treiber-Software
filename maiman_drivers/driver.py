"""Serial control of Maiman Electronics laser diode drivers.
SF6015: up to 15 A, no TEC. SF8150T: integrated TEC, SF8025/8075/8150/8300-T
family for type-2 butterfly diodes. Shared ASCII protocol: 115200 baud, 8N1,
no flow control, PC-initiated transactions. Write P<pppp> <vvvv><CR> (hex);
factory defaults send no reply. Read J<pppp><CR> returns K<pppp> <vvvv><CR>.
E<code><CR> indicates an error (E0001: unknown command); K0000 0000 indicates
an unsupported parameter. Scaling and parameter addresses are model-specific.
Never ground LD+ or LD- or attach grounded oscilloscope probes to the output:
this can destroy the diode and driver. Power-on defaults use analog parameters,
external enable and active interlock; other modes require digital commands."""

from __future__ import annotations

import time
import warnings
from typing import Dict, Optional, Union

import serial  # pyserial


__all__ = [
    "MaimanDriver", "SF6015", "SF8150T",
    "MaimanError", "SerialNumberMismatch",
]


class MaimanError(Exception):
    """Communication error from a Maiman driver."""


class SerialNumberMismatch(MaimanError):
    """A port returned an unexpected serial number; the connection was closed."""

    def __init__(self, port: str, expected: int, actual: int) -> None:
        self.port = port
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Wrong driver on {port}: expected serial number "
            f"0x{expected:04X}, received 0x{actual:04X}. Connection closed."
        )


# ---------------------------------------------------------------------------
# Base class: serial protocol and shared controls
# ---------------------------------------------------------------------------
class MaimanDriver:
    """Shared serial protocol. Use SF6015 or SF8150T for model-specific scaling.
    port: COM4 (Windows) or /dev/ttyUSB0 (Linux). baudrate defaults to 115200;
    timeout is the response-line timeout in seconds. connect=True opens immediately.
    expected_serial is optional: a 16-bit integer or hexadecimal string such as
    1A2F, 0x1A2F or #1A2F. A mismatch warns, closes and raises
    SerialNumberMismatch. None disables identity checking."""

    # ---- shared parameters / subcommands (parameter 0700) ----
    _P_STATE = 0x0700
    _P_SERIAL = 0x0701

    # After start/stop, the device saves parameters and temporarily stops responding
    # (datasheet: about 300 ms). Wait after start/stop rather than issuing reads
    # during this interval; subsequent reads can verify state.
    _SETTLE_SECONDS = 0.4
    # Retry transient read errors elsewhere.
    _READ_RETRIES = 3
    _READ_RETRY_DELAY = 0.1

    _CMD_SAVE = 0x0002
    _CMD_START = 0x0008
    _CMD_STOP = 0x0010
    _CMD_CURRENT_INTERNAL = 0x0020
    _CMD_CURRENT_EXTERNAL = 0x0040
    _CMD_ENABLE_EXTERNAL = 0x0200
    _CMD_ENABLE_INTERNAL = 0x0400
    _CMD_ALLOW_INTERLOCK = 0x1000
    _CMD_DENY_INTERLOCK = 0x2000

    # ---- model-specific (overridden by subclasses) ----
    #: Current setpoint (0300): amperes per LSB
    CURRENT_SET_STEP_A: float = 0.0
    #: Measured current (0307): amperes per LSB
    CURRENT_MEAS_STEP_A: float = 0.0
    #: Maximum software setpoint in amperes (model limit)
    CURRENT_MAX_A: float = 0.0

    def __init__(self, port: str, baudrate: int = 115200,
                 timeout: float = 0.3, connect: bool = True,
                 expected_serial: Optional[Union[int, str]] = None) -> None:
        self.port_name = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.expected_serial: Optional[int] = self._parse_serial(expected_serial)
        #: Serial number read after a successful connection.
        self.serial_number: Optional[int] = None
        #: Cached output state (last commanded value). The display
        #: uses this value instead of an unreliable live read. The device powers up
        #: with output stopped.
        self._running: bool = False
        self.ser: Optional[serial.Serial] = None
        if connect:
            self.connect()

    @staticmethod
    def _parse_serial(value: Optional[Union[int, str]]) -> Optional[int]:
        """Parse None, a 16-bit integer or a hexadecimal serial string
        (1A2F, 0x1A2F, #1A2F). Integer values are already numeric, not hex strings."""
        if value is None:
            return None
        if isinstance(value, int):
            parsed = value
        else:
            text = value.strip().lstrip("#")
            if text.lower().startswith("0x"):
                text = text[2:]
            try:
                parsed = int(text, 16)
            except ValueError:
                raise MaimanError(
                    f"Invalid expected serial number: {value!r} "
                    f"(expected 4 hex digits, e.g. '1A2F')."
                )
        if not 0 <= parsed <= 0xFFFF:
            raise MaimanError(
                f"Expected Serial number 0x{parsed:X} is outside the "
                f"16-bit range (0000..FFFF)."
            )
        return parsed

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #
    def _open_transport(self):
        """Open the serial transport.
        Mocks override this method while retaining connection and identity checks."""
        return serial.Serial(
            port=self.port_name,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.timeout,
            write_timeout=self.timeout,
        )

    def connect(self) -> None:
        """Open a 115200 8N1 connection and optionally verify its serial number."""
        if self.ser is not None and self.ser.is_open:
            return
        self.ser = self._open_transport()
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

        # Verify the serial number when an expected value is supplied.
        if self.expected_serial is not None:
            try:
                actual = self.get_serial_number()
            except MaimanError as exc:
                # Cannot read serial number -> close the connection.
                self.close()
                self.ser = None
                warnings.warn(
                    f"Could not read the serial number of the driver on {self.port_name} "
                    f"({exc}). Connection closed.",
                    stacklevel=2,
                )
                raise
            self.serial_number = actual
            if actual != self.expected_serial:
                expected = self.expected_serial
                self.close()
                self.ser = None
                warnings.warn(
                    f"Wrong driver on {self.port_name}: expected "
                    f"Serial number 0x{expected:04X}, received 0x{actual:04X}. "
                    f"Connection closed.",
                    stacklevel=2,
                )
                raise SerialNumberMismatch(self.port_name, expected, actual)

    def close(self) -> None:
        """Close the serial port."""
        if self.ser is not None and self.ser.is_open:
            self.ser.close()

    @property
    def is_open(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def __enter__(self) -> "MaimanDriver":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Raw protocol transactions
    # ------------------------------------------------------------------ #
    def get_raw(self, param: int, signed: bool = False) -> int:
        """Read a J parameter and return its 16-bit raw value.
        param is a hexadecimal parameter address such as 0x0300. signed=True
        interprets two's-complement values, e.g. for temperatures below 0 °C."""
        if not self.is_open:
            raise MaimanError("Serial port is not open.")

        # Retry timeouts / E errors: after start/stop
        # the device is briefly busy saving and may respond too late.
        last_text = ""
        for attempt in range(self._READ_RETRIES + 1):
            self.ser.reset_input_buffer()
            self.ser.write(f"J{param:04X}\r".encode("ascii"))
            line = self.ser.read_until(b"\r")
            text = line.decode("ascii", errors="replace").strip()

            if text.startswith("K"):
                parts = text[1:].split()          # "Kpppp vvvv"
                if len(parts) != 2:
                    last_text = f"Unreadable response: {text!r}"
                else:
                    ret_param, ret_value = parts
                    if (ret_param.upper() == "0000" and ret_value == "0000"
                            and param != 0):
                        # Unsupported parameter -> do not retry.
                        raise MaimanError(f"Parameter 0x{param:04X} does "
                                          f"not exist on this device (K0000 0000).")
                    value = int(ret_value, 16)
                    if signed and value >= 0x8000:
                        value -= 0x10000
                    return value
            elif not text:
                last_text = "no response (timeout)"
            elif text.startswith("E"):
                last_text = f"Error {text}"
            else:
                last_text = f"Unexpected response {text!r}"

            # Wait briefly before retrying; the device may still be saving.
            if attempt < self._READ_RETRIES:
                time.sleep(self._READ_RETRY_DELAY)

        raise MaimanError(f"J{param:04X} failed after "
                          f"{self._READ_RETRIES + 1} attempts: {last_text}.")

    def set_raw(self, param: int, value: int, verify: bool = False) -> None:
        """Write a P parameter. Factory defaults send no acknowledgement.
        verify=True reads the value back and checks it after the write."""
        if not self.is_open:
            raise MaimanError("Serial port is not open.")

        value &= 0xFFFF  # 16-bit; negative values in two's complement
        self.ser.reset_input_buffer()
        self.ser.write(f"P{param:04X} {value:04X}\r".encode("ascii"))
        self.ser.flush()
        time.sleep(0.2)
        if verify:
            time.sleep(0.02)
            read_back = self.get_raw(param)
            if read_back != value:
                raise MaimanError(
                    f"Verification failed for 0x{param:04X}: "
                    f"set 0x{value:04X}, read 0x{read_back:04X}."
                )

    # ------------------------------------------------------------------ #
    # Shared controls (parameter 0700)
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Enable diode current output."""
        self.set_raw(self._P_STATE, self._CMD_START)
        self._running = True                 # cache the commanded state
        if self._SETTLE_SECONDS > 0:         # allow the device to process the command
            time.sleep(self._SETTLE_SECONDS)

    def stop(self) -> None:
        """Disable diode current output."""
        self.set_raw(self._P_STATE, self._CMD_STOP)
        self._running = False
        if self._SETTLE_SECONDS > 0:
            time.sleep(self._SETTLE_SECONDS)

    def set_enable_source(self, internal: bool) -> None:
        """True selects internal/digital enable; False selects the external pin."""
        self.set_raw(self._P_STATE,
                     self._CMD_ENABLE_INTERNAL if internal
                     else self._CMD_ENABLE_EXTERNAL)

    def set_current_source(self, internal: bool) -> None:
        """True selects a digital current setpoint; False selects the analog
        potentiometer or external input."""
        self.set_raw(self._P_STATE,
                     self._CMD_CURRENT_INTERNAL if internal
                     else self._CMD_CURRENT_EXTERNAL)

    def allow_interlock(self) -> None:
        """Enable the interlock pin."""
        self.set_raw(self._P_STATE, self._CMD_ALLOW_INTERLOCK)

    def deny_interlock(self) -> None:
        """Ignore the interlock pin."""
        self.set_raw(self._P_STATE, self._CMD_DENY_INTERLOCK)

    def get_serial_number(self) -> int:
        """Read the device serial number (parameter 0701)."""
        return self.get_raw(self._P_SERIAL)

    def read_state(self) -> Dict[str, object]:
        """Read and decode the driver state bitmask (parameter 0700)."""
        m = self.get_raw(self._P_STATE)
        return {
            "raw": m,
            "powered_on": bool(m & (1 << 0)),
            "started": bool(m & (1 << 1)),
            "current_internal": bool(m & (1 << 2)),   # 1 = internal, 0 = external
            "enable_internal": bool(m & (1 << 4)),     # 1 = internal, 0 = external
            "ext_ntc_interlock_denied": bool(m & (1 << 6)),
            "interlock_denied": bool(m & (1 << 7)),
        }

    @property
    def is_running(self) -> bool:
        """Return the last commanded output state, not a live measurement.
        Serial state reads can be unreliable during approximately 300 ms of saving
        after start/stop. Use read_state for the actual device state."""
        return self._running

    def read_is_running(self) -> bool:
        """Read live output state (0700, bit 1) and update the cached state.
        Use only when the device is not busy saving parameters."""
        self._running = bool(self.read_state()["started"])
        return self._running

    def save_parameters(self) -> None:
        """Save parameters persistently.
        The device does not respond to commands for approximately 300 ms."""
        self.set_raw(self._P_STATE, self._CMD_SAVE)
        time.sleep(0.35)

    # ------------------------------------------------------------------ #
    # Current (model-specific scaling)
    # ------------------------------------------------------------------ #
    def set_current(self, amperes: float, verify: bool = False) -> None:
        """Set the digital current in amperes, clamped to 0..CURRENT_MAX_A.
        Select internal current source first for this setpoint to take effect.
        CURRENT_MAX_A is a software bound, not a programmed hardware interlock."""
        amperes = max(0.0, min(amperes, self.CURRENT_MAX_A))
        raw = round(amperes / self.CURRENT_SET_STEP_A)
        self.set_raw(0x0300, raw, verify=verify)

    def get_current_setpoint(self) -> float:
        """Read the diode current setpoint in amperes."""
        return self.get_raw(0x0300) * self.CURRENT_SET_STEP_A

    def get_measured_current(self) -> float:
        """Read measured diode output current in amperes."""
        return self.get_raw(0x0307) * self.CURRENT_MEAS_STEP_A

    def get_measured_voltage(self) -> float:
        """Read measured output voltage in volts (0.1 V resolution)."""
        return self.get_raw(0x0407) * 0.1

    # ------------------------------------------------------------------ #
    # Pulse / QCW operation
    # ------------------------------------------------------------------ #
    def set_frequency(self, hertz: float, verify: bool = False) -> None:
        """Set pulse frequency in Hz (0.1 Hz resolution).
        Zero selects CW; positive values select QCW. Datasheet range: 0.1–100 Hz."""
        self.set_raw(0x0100, round(hertz * 10), verify=verify)

    def get_frequency(self) -> float:
        return self.get_raw(0x0100) * 0.1

    def set_pulse_duration(self, milliseconds: float, verify: bool = False) -> None:
        """Set pulse duration in milliseconds (0.1 ms resolution; 1–5000 ms range)."""
        self.set_raw(0x0200, round(milliseconds * 10), verify=verify)

    def get_pulse_duration(self) -> float:
        return self.get_raw(0x0200) * 0.1

    # ------------------------------------------------------------------ #
    # Lock / error status (parameter 0800); details in subclasses
    # ------------------------------------------------------------------ #
    def get_lock_status_raw(self) -> int:
        return self.get_raw(0x0800)


# ---------------------------------------------------------------------------
# SF6015: laser diode driver without TEC
# ---------------------------------------------------------------------------
class SF6015(MaimanDriver):
    """Maiman SF6015 laser diode driver, up to 15 A, without TEC.
    Select internal current and enable sources, set current, then start;
    stop output before closing the connection."""

    CURRENT_SET_STEP_A = 0.01     # Current setpoint (0300), 0.01 A steps
    CURRENT_MEAS_STEP_A = 0.1     # Measured current (0307), 0.1 A steps
    CURRENT_MAX_A = 15.0

    _CMD_DENY_NTC_INTERLOCK = 0x4000
    _CMD_ALLOW_NTC_INTERLOCK = 0x8000

    def get_device_id(self) -> int:
        """Read model/version ID (parameter 0702)."""
        return self.get_raw(0x0702)

    def allow_ntc_interlock(self) -> None:
        self.set_raw(self._P_STATE, self._CMD_ALLOW_NTC_INTERLOCK)

    def deny_ntc_interlock(self) -> None:
        self.set_raw(self._P_STATE, self._CMD_DENY_NTC_INTERLOCK)

    def get_ntc_temperature(self) -> float:
        """Read external NTC temperature in °C (0.1 °C resolution)."""
        return self.get_raw(0x0AE4, signed=True) * 0.1

    def get_pcb_temperature(self) -> float:
        """Read PCB temperature in °C (0.1 °C resolution)."""
        return self.get_raw(0x0AF4, signed=True) * 0.1

    def read_lock_status(self) -> Dict[str, bool]:
        """Decode the lock/error bitmask (parameter 0800)."""
        m = self.get_lock_status_raw()
        return {
            "any": bool(m),
            "interlock": bool(m & (1 << 1)),
            "over_current": bool(m & (1 << 3)),
            "overheat_warning": bool(m & (1 << 4)),
            "external_ntc_interlock": bool(m & (1 << 5)),
        }


# ---------------------------------------------------------------------------
# SF8150-T: laser diode driver with TEC controller
# ---------------------------------------------------------------------------
class SF8150T(MaimanDriver):
    """Maiman SF8150-T diode driver with TEC for type-2 butterfly diodes.
    SF8025-T/SF8075-T/SF8300-T share the protocol but have different maximum
    currents. Configure internal diode and TEC sources, temperature and current
    before starting the TEC and diode. Stop diode output before closing."""

    CURRENT_SET_STEP_A = 0.0001   # Current setpoint (0300), 0.1 mA steps
    CURRENT_MEAS_STEP_A = 0.0001  # Measured current (0307), 0.1 mA steps
    CURRENT_MAX_A = 1.5           # SF8150 = 1500 mA
    # TEC setpoint range ("temperature change range", SF8xxx datasheet)
    TEC_TEMPERATURE_MIN_C = 15.0
    TEC_TEMPERATURE_MAX_C = 40.0

    # TEC state (parameter 0A1A)
    _P_TEC_STATE = 0x0A1A
    _TEC_START = 0x0008
    _TEC_STOP = 0x0010
    _TEC_TEMP_INTERNAL = 0x0020
    _TEC_TEMP_EXTERNAL = 0x0040
    _TEC_ENABLE_EXTERNAL = 0x0200
    _TEC_ENABLE_INTERNAL = 0x0400

    # Additional driver subcommands (parameter 0700)
    _CMD_STANDALONE_ON = 0x0060
    _CMD_STANDALONE_OFF = 0x0080
    _CMD_DENY_EXT_NTC_INTERLOCK = 0x4000
    _CMD_ALLOW_EXT_NTC_INTERLOCK = 0x8000

    def __init__(self, port: str, current_max_a: Optional[float] = None,
                 **kwargs) -> None:
        """current_max_a overrides the model/software current bound.
        Family ratings: SF8025=0.25, SF8075=0.75, SF8150=1.5, SF8300=3.0 A.
        This does not program a separate hardware current-limit register."""
        super().__init__(port, **kwargs)
        if current_max_a is not None:
            self.CURRENT_MAX_A = current_max_a

    def get_firmware_version(self) -> int:
        """Read firmware version (parameter 0710)."""
        return self.get_raw(0x0710)

    # ---- Standalone mode / external NTC interlock ----
    def set_standalone(self, on: bool) -> None:
        """Enable or disable autonomous operation without a host."""
        self.set_raw(self._P_STATE,
                     self._CMD_STANDALONE_ON if on else self._CMD_STANDALONE_OFF)

    def allow_ext_ntc_interlock(self) -> None:
        self.set_raw(self._P_STATE, self._CMD_ALLOW_EXT_NTC_INTERLOCK)

    def deny_ext_ntc_interlock(self) -> None:
        self.set_raw(self._P_STATE, self._CMD_DENY_EXT_NTC_INTERLOCK)

    # ---- TEC controls ----
    def tec_start(self) -> None:
        """Enable the TEC controller."""
        self.set_raw(self._P_TEC_STATE, self._TEC_START)

    def tec_stop(self) -> None:
        """Disable the TEC controller."""
        self.set_raw(self._P_TEC_STATE, self._TEC_STOP)

    def tec_set_source(self, internal: bool) -> None:
        """True selects internal/digital temperature setpoint; False selects external."""
        self.set_raw(self._P_TEC_STATE,
                     self._TEC_TEMP_INTERNAL if internal
                     else self._TEC_TEMP_EXTERNAL)

    def tec_set_enable_source(self, internal: bool) -> None:
        """True selects internal TEC enable; False selects external."""
        self.set_raw(self._P_TEC_STATE,
                     self._TEC_ENABLE_INTERNAL if internal
                     else self._TEC_ENABLE_EXTERNAL)

    def set_tec_temperature(self, celsius: float, verify: bool = False) -> None:
        """Set TEC temperature in °C (0.01 °C resolution, 15-40 °C).
        Raises ValueError outside the range without sending a command."""
        self.check_tec_temperature(celsius)
        self.set_raw(0x0A10, round(celsius * 100), verify=verify)

    @classmethod
    def check_tec_temperature(cls, celsius: float) -> float:
        """Return celsius as float if it is a valid TEC setpoint, else raise ValueError."""
        import math
        try:
            value = float(celsius)
        except (TypeError, ValueError):
            value = math.nan
        if not math.isfinite(value) or not (
                cls.TEC_TEMPERATURE_MIN_C <= value <= cls.TEC_TEMPERATURE_MAX_C):
            raise ValueError(
                f"TEC temperature must be between {cls.TEC_TEMPERATURE_MIN_C:g} and "
                f"{cls.TEC_TEMPERATURE_MAX_C:g} °C (got {celsius!r}).")
        return value

    def set_tec_temperature_limits(self, minimum_c: float,
                                   maximum_c: float) -> None:
        """Set lower and upper TEC setpoint bounds in °C."""
        self.set_raw(0x0A12, round(minimum_c * 100))   # Minimum
        self.set_raw(0x0A11, round(maximum_c * 100))   # Maximum

    def get_tec_temperature_setpoint(self) -> float:
        """Read TEC temperature setpoint in °C."""
        return self.get_raw(0x0A10, signed=True) * 0.01

    def get_tec_temperature(self) -> float:
        """Read measured TEC temperature in °C."""
        return self.get_raw(0x0A15, signed=True) * 0.01

    def get_tec_current(self) -> float:
        """Read measured TEC current in amperes (0.1 A resolution)."""
        return self.get_raw(0x0A16, signed=True) * 0.1

    def get_tec_voltage(self) -> float:
        """Read measured TEC voltage in volts (0.1 V resolution)."""
        return self.get_raw(0x0A18, signed=True) * 0.1

    def set_tec_current_limit(self, amperes: float, verify: bool = False) -> None:
        """Set TEC output current limit in amperes (0.1 A resolution)."""
        self.set_raw(0x0A17, round(amperes * 10), verify=verify)

    def read_tec_state(self) -> Dict[str, object]:
        """Read and decode TEC state bitmask (parameter 0A1A)."""
        m = self.get_raw(self._P_TEC_STATE)
        return {
            "raw": m,
            # Datasheet TEC state bitmask: bit 1 = started,
            # bit 2 = internal temperature source, bit 4 = internal enable (like driver state).
            "started": bool(m & (1 << 1)),
            "temp_internal": bool(m & (1 << 2)),   # 1 = internal, 0 = external
            "enable_internal": bool(m & (1 << 4)),
        }

    # ---- TEC PID coefficients ----
    def set_pid(self, p: int, i: int, d: int) -> None:
        """Set TEC PID coefficients (parameters 0A21..0A23)."""
        self.set_raw(0x0A21, p)
        self.set_raw(0x0A22, i)
        self.set_raw(0x0A23, d)

    def get_pid(self) -> Dict[str, int]:
        return {
            "P": self.get_raw(0x0A21),
            "I": self.get_raw(0x0A22),
            "D": self.get_raw(0x0A23),
        }

    # ---- External NTC sensor ----
    def get_ext_ntc_temperature(self) -> float:
        """Read external NTC temperature in °C (0.1 °C resolution)."""
        return self.get_raw(0x0AE4, signed=True) * 0.1

    # ---- Lock / error status ----
    def read_lock_status(self) -> Dict[str, bool]:
        """Decode the lock/error bitmask (parameter 0800)."""
        m = self.get_lock_status_raw()
        return {
            "any": bool(m),
            "interlock": bool(m & (1 << 1)),
            "ld_over_current": bool(m & (1 << 3)),
            "ld_overheat": bool(m & (1 << 4)),
            "external_ntc_interlock": bool(m & (1 << 5)),
            "tec_error": bool(m & (1 << 6)),
            "tec_self_heat": bool(m & (1 << 7)),
        }
