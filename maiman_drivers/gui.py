"""PyQt6 widgets for ordered Maiman laser chains.
Fixed TEC stages have an output switch and temperature/current telemetry.
DC stages add current adjustment. Adjustable TEC stages combine a current
control, serial/port identification, current limit and TEC setpoint/telemetry.
LaserChainWidget enforces seed-before-pump and reverse shutdown dependencies."""

from __future__ import annotations

from typing import List, Sequence

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .laser import (
    LaserChain, LaserStage, TecLaserStage, DcLaserStage, AdjustableTecLaserStage,
    ChainDependencyError, TecNotRunningError,
)


_ON_STYLE = "background-color:#2e7d32;color:white;font-weight:bold;"
_OFF_STYLE = "background-color:#b71c1c;color:white;font-weight:bold;"


# ---------------------------------------------------------------------------
# Base stage widget
# ---------------------------------------------------------------------------
class LaserStageWidget(QGroupBox):
    """Output switch and status line for a chain stage."""

    def __init__(self, stage: LaserStage) -> None:
        super().__init__(stage.name)
        self.stage = stage
        self._chain_widget: "LaserChainWidget | None" = None
        self._index = -1

        self.button = QPushButton("OFF")
        self.button.setMinimumHeight(40)
        self.button.clicked.connect(self._on_click)

        self.status = QLabel("-")
        self.status.setTextFormat(Qt.TextFormat.PlainText)

        self._build_layout()
        self.refresh()

    # -- extended by subclasses --
    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self.button)
        layout.addWidget(self.status)

    def attach(self, chain_widget: "LaserChainWidget", index: int) -> None:
        self._chain_widget = chain_widget
        self._index = index

    def _on_click(self) -> None:
        if self._chain_widget is not None:
            self._chain_widget.request_toggle(self._index)

    def refresh(self, can_on: bool = True, can_off: bool = True) -> None:
        on = self.stage.is_on
        self.button.setText("ON" if on else "OFF")
        self.button.setStyleSheet(_ON_STYLE if on else _OFF_STYLE)
        # Enable the button only if chain dependency rules permit the action.
        self.button.setEnabled((not on and can_on) or (on and can_off))
        self._refresh_status()

    def _refresh_status(self) -> None:
        self.status.setText("ON" if self.stage.is_on else "OFF")


# ---------------------------------------------------------------------------
# TEC stage
# ---------------------------------------------------------------------------
class TecStageWidget(LaserStageWidget):
    """Fixed-current TEC stage: output switch and telemetry."""

    stage: TecLaserStage

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        text = f"Diode current (fixed): {self.stage.diode_current_a:.3f} A"
        if self.stage.tec_temperature_c is not None:
            text += f"  |  TEC target: {self.stage.tec_temperature_c:g} °C"
        layout.addWidget(QLabel(text))
        self.tec_label = QLabel("TEC: -")
        layout.addWidget(self.tec_label)
        layout.addWidget(self.button)
        layout.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(500)

    def _refresh_status(self) -> None:
        try:
            running = self.stage.tec_running()
            temp = self.stage.driver.get_tec_temperature()
            current = self.stage.driver.get_measured_current()
        except Exception as exc:                      # pragma: no cover
            self.tec_label.setText(f"TEC: Error ({exc})")
            return
        self.tec_label.setText(
            f"TEC: {'running' if running else 'OFF'}   |   {temp:.2f} °C")
        self.status.setText(
            f"Laser diode: {'ON' if self.stage.is_on else 'OFF'}"
            f"   |   {current * 1000:.1f} mA")


# ---------------------------------------------------------------------------
# DC stage (no TEC)
# ---------------------------------------------------------------------------
class DcStageWidget(LaserStageWidget):
    """Adjustable-current DC stage: current input and output switch."""

    stage: DcLaserStage

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Current:"))
        self.spin = QDoubleSpinBox()
        self.spin.setRange(0.0, self.stage.current_max_a)
        self.spin.setSingleStep(0.1)          # 0.1 A steps
        self.spin.setDecimals(2)
        self.spin.setSuffix(" A")
        # Apply on Enter, focus loss or arrow clicks, rather than every keystroke.
        self.spin.setKeyboardTracking(False)
        self.spin.setValue(self.stage.get_current_setpoint())
        self.spin.valueChanged.connect(self._on_current_changed)
        row.addWidget(self.spin)
        layout.addLayout(row)

        layout.addWidget(self.button)
        layout.addWidget(self.status)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start(500)

    def _on_current_changed(self, value: float) -> None:
        # Triggered on Enter confirmation or current-input changes.
        try:
            self.stage.set_current(value)
        except Exception as exc:                      # pragma: no cover
            QMessageBox.warning(self, "Error", str(exc))

    def _refresh_status(self) -> None:
        try:
            current = self.stage.driver.get_measured_current()
        except Exception as exc:                      # pragma: no cover
            self.status.setText(f"Error: {exc}")
            return
        self.status.setText(
            f"Output: {'ON' if self.stage.is_on else 'OFF'}"
            f"   |   {current:.2f} A")


class AdjustableTecStageWidget(DcStageWidget):
    """Current control, output switch and TEC telemetry for an SF8 diode."""

    stage: AdjustableTecLaserStage

    def _build_layout(self) -> None:
        super()._build_layout()
        self.spin.setDecimals(4)
        self.spin.setSingleStep(0.001)
        self.layout().insertWidget(0, QLabel(
            f"Serial: {self.stage.driver.serial_number}  |  "
            f"Port: {self.stage.driver.port_name}\n"
            f"Maximum current: {self.stage.current_max_a:g} A  |  "
            f"TEC target: {self.stage.tec_temperature_c:g} °C"))
        self.tec_label = QLabel("TEC: --")
        self.layout().addWidget(self.tec_label)

    def _refresh_status(self) -> None:
        super()._refresh_status()
        if not hasattr(self, "tec_label"):
            return
        try:
            running = self.stage.tec_running()
            temperature = self.stage.driver.get_tec_temperature()
            self.tec_label.setText(
                f"TEC: {'running' if running else 'OFF'}  |  {temperature:.2f} °C")
        except Exception as exc:
            self.tec_label.setText(f"TEC error: {exc}")


def make_stage_widget(stage: LaserStage) -> LaserStageWidget:
    """Construct the appropriate controls for the stage type."""
    if isinstance(stage, AdjustableTecLaserStage):
        return AdjustableTecStageWidget(stage)
    if isinstance(stage, TecLaserStage):
        return TecStageWidget(stage)
    if isinstance(stage, DcLaserStage):
        return DcStageWidget(stage)
    return LaserStageWidget(stage)


# ---------------------------------------------------------------------------
# Chain widget
# ---------------------------------------------------------------------------
class LaserChainWidget(QGroupBox):
    """Group stage controls and enforce their ordering dependencies."""

    def __init__(self, chain: LaserChain) -> None:
        super().__init__(chain.name)
        self.chain = chain
        self.stage_widgets: List[LaserStageWidget] = []

        layout = QVBoxLayout(self)
        for i, stage in enumerate(chain.stages):
            w = make_stage_widget(stage)
            w.attach(self, i)
            self.stage_widgets.append(w)
            layout.addWidget(w)

        self.refresh()

    def request_toggle(self, index: int) -> None:
        try:
            self.chain.toggle(index)
        except (ChainDependencyError, TecNotRunningError) as exc:
            QMessageBox.warning(self, "Action unavailable", str(exc))
        self.refresh()

    def refresh(self) -> None:
        for i, w in enumerate(self.stage_widgets):
            w.refresh(self.chain.can_enable(i), self.chain.can_disable(i))


# ---------------------------------------------------------------------------
# Window for multiple chains
# ---------------------------------------------------------------------------
class MultiLaserWindow(QWidget):
    """Show multiple diode chains side by side."""

    def __init__(self, chains: Sequence[LaserChain],
                 title: str = "Maiman laser controls") -> None:
        super().__init__()
        self.setWindowTitle(title)
        layout = QHBoxLayout(self)
        self.chain_widgets = [LaserChainWidget(c) for c in chains]
        for w in self.chain_widgets:
            layout.addWidget(w)


def run(chains: Sequence[LaserChain],
        title: str = "Maiman laser controls") -> int:
    """Start the Qt application with the supplied devices or chains."""
    app = QApplication.instance() or QApplication([])
    window = MultiLaserWindow(chains, title)
    window.show()
    return app.exec()
