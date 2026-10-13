"""Ordered laser stages with enforced dependencies.
Enable seed before pump/amplifiers; disable stages in reverse order. A stage
requires its predecessor ON before enabling and its successor OFF before
disabling, so enabled stages always form a contiguous prefix.
TecLaserStage has a fixed current and requires a running TEC before enabling.
AdjustableTecLaserStage adds bounded current adjustment and a 25 °C setpoint.
DcLaserStage has adjustable current and no TEC guard. This module contains no Qt."""

from __future__ import annotations

from typing import List, Sequence


class LaserError(Exception):
    """Base error for laser chain operations."""


class ChainDependencyError(LaserError):
    """An action violates stage ordering dependencies."""


class TecNotRunningError(LaserError):
    """A diode was requested ON while its TEC was stopped."""


# ---------------------------------------------------------------------------
# Individual stages
# ---------------------------------------------------------------------------
class LaserStage:
    """Abstract driver stage in an ordered laser chain."""

    def __init__(self, name: str, driver) -> None:
        self.name = name
        self.driver = driver

    def initialize(self) -> None:
        """Prepare the stage with diode output OFF."""
        raise NotImplementedError

    def turn_on(self) -> None:
        """Enable this stage's diode output."""
        raise NotImplementedError

    def turn_off(self) -> None:
        """Disable this stage's diode output."""
        raise NotImplementedError

    @property
    def is_on(self) -> bool:
        """True if this stage's diode output is enabled."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r} on={self.is_on}>"


class TecLaserStage(LaserStage):
    """Stage with a continuously running TEC and a fixed diode current.
    initialize prepares diode_current_a; turn_on/turn_off switch the diode."""

    def __init__(self, name: str, driver, diode_current_a: float) -> None:
        super().__init__(name, driver)
        self.diode_current_a = diode_current_a

    def initialize(self) -> None:
        d = self.driver
        # Keep TEC enabled continuously.
        d.tec_set_source(internal=True)
        d.tec_set_enable_source(internal=True)
        d.tec_start()
        # Prepare diode current without enabling output.
        d.set_current_source(internal=True)
        d.set_enable_source(internal=True)
        d.set_current(self.diode_current_a)

    def tec_running(self) -> bool:
        """True if the TEC controller is running."""
        return bool(self.driver.read_tec_state()["started"])

    def turn_on(self) -> None:
        # Check that TEC is running before enabling the diode.
        if not self.tec_running():
            raise TecNotRunningError(
                f"{self.name}: TEC is not running; laser diode will not be "
                f"enabled."
            )
        self.driver.start()

    def turn_off(self) -> None:
        self.driver.stop()

    @property
    def is_on(self) -> bool:
        return self.driver.is_running


class AdjustableTecLaserStage(TecLaserStage):
    """SF8 stage with adjustable diode current and a fixed 25 °C TEC setpoint.

    Current is bounded by the configured software limit. Enabling the diode
    requires a running TEC and the normal chain dependency checks.
    """

    def initialize(self) -> None:
        self.driver.stop()
        self.driver.set_tec_temperature(25.0)
        super().initialize()

    def set_current(self, amperes: float) -> None:
        import math
        if not math.isfinite(amperes) or not 0 <= amperes <= self.current_max_a:
            raise ValueError(f"Current must be between 0 and {self.current_max_a:g} A.")
        self.driver.set_current(amperes)
        self.diode_current_a = amperes

    def get_current_setpoint(self) -> float:
        return self.driver.get_current_setpoint()

    @property
    def current_max_a(self) -> float:
        return self.driver.CURRENT_MAX_A


class DcLaserStage(LaserStage):
    """Adjustable-current stage without a TEC guard.
    Initialize at initial_current_a (default 0 A); set_current changes it later."""

    def __init__(self, name: str, driver, initial_current_a: float = 0.0) -> None:
        super().__init__(name, driver)
        self.initial_current_a = initial_current_a

    def initialize(self) -> None:
        d = self.driver
        d.set_current_source(internal=True)
        d.set_enable_source(internal=True)
        d.set_current(self.initial_current_a)

    def set_current(self, amperes: float) -> None:
        """Set diode current in amperes."""
        self.driver.set_current(amperes)

    def get_current_setpoint(self) -> float:
        return self.driver.get_current_setpoint()

    @property
    def current_max_a(self) -> float:
        return self.driver.CURRENT_MAX_A

    def turn_on(self) -> None:
        self.driver.start()

    def turn_off(self) -> None:
        self.driver.stop()

    @property
    def is_on(self) -> bool:
        return self.driver.is_running


# ---------------------------------------------------------------------------
# Chain
# ---------------------------------------------------------------------------
class LaserChain:
    """Ordered stages, index 0 first. enable/disable enforce dependencies."""

    def __init__(self, name: str, stages: Sequence[LaserStage]) -> None:
        if not stages:
            raise LaserError("A laser chain needs at least one stage.")
        self.name = name
        self.stages: List[LaserStage] = list(stages)

    def __len__(self) -> int:
        return len(self.stages)

    def initialize(self) -> None:
        """Initialize every stage; TECs run and diode outputs remain OFF."""
        for stage in self.stages:
            stage.initialize()

    # -- Dependency rules ---------------------------------------- #
    def can_enable(self, index: int) -> bool:
        """True if this is the first stage or its predecessor is ON."""
        if index == 0:
            return True
        return self.stages[index - 1].is_on

    def can_disable(self, index: int) -> bool:
        """True if this is the last stage or its successor is OFF."""
        if index == len(self.stages) - 1:
            return True
        return not self.stages[index + 1].is_on

    # -- Actions ----------------------------------------------------- #
    def enable(self, index: int) -> None:
        """Enable a stage after checking its predecessor."""
        if not self.can_enable(index):
            prev = self.stages[index - 1]
            raise ChainDependencyError(
                f"'{self.stages[index].name}' cannot be enabled "
                f"while '{prev.name}' is OFF."
            )
        self.stages[index].turn_on()

    def disable(self, index: int) -> None:
        """Disable a stage after checking its successor."""
        if not self.can_disable(index):
            nxt = self.stages[index + 1]
            raise ChainDependencyError(
                f"'{self.stages[index].name}' cannot be disabled "
                f"while '{nxt.name}' is ON."
            )
        self.stages[index].turn_off()

    def toggle(self, index: int) -> None:
        """Toggle a stage with dependency checks."""
        if self.stages[index].is_on:
            self.disable(index)
        else:
            self.enable(index)

    def enable_all(self) -> None:
        """Enable all stages in forward order (0, 1, 2, ...)."""
        for i in range(len(self.stages)):
            self.enable(i)

    def shutdown(self) -> None:
        """Disable all enabled stages in reverse order."""
        for i in reversed(range(len(self.stages))):
            if self.stages[i].is_on:
                self.disable(i)

    def state(self) -> List[bool]:
        """Return the ON/OFF state of every stage."""
        return [s.is_on for s in self.stages]
