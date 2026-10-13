"""Maiman Electronics serial diode drivers.
SF6015 has no TEC; SF8150T supports the SF8 TEC family.
Select internal enable/current sources before output.
expected_serial accepts an integer or a hexadecimal string and rejects
identity mismatches by closing the connection and raising SerialNumberMismatch."""

from .driver import (
    MaimanDriver, SF6015, SF8150T, MaimanError, SerialNumberMismatch,
)
from .laser import (
    LaserStage, TecLaserStage, DcLaserStage, LaserChain,
    LaserError, ChainDependencyError, TecNotRunningError,
)
from .mock import MockSF6015, MockSF8150T, FakeMaimanSerial

# maiman_drivers.gui is not imported automatically, so the
# core package can be used without PyQt6. Import the GUI explicitly if needed:
#     from maiman_drivers.gui import run, LaserChainWidget

__all__ = [
    # Drivers
    "MaimanDriver", "SF6015", "SF8150T",
    "MaimanError", "SerialNumberMismatch",
    # Laser chains
    "LaserStage", "TecLaserStage", "DcLaserStage", "LaserChain",
    "LaserError", "ChainDependencyError", "TecNotRunningError",
    # Simulation
    "MockSF6015", "MockSF8150T", "FakeMaimanSerial",
]
__version__ = "1.2.0"
