# Maiman-Treiber-Software

Python-Treiber für die **Maiman Electronics** Laserdioden-Treiber

* **SF6015** – Diodentreiber ohne TEC
* **SF8150-T** (SF8-Familie) – Diodentreiber mit TEC

plus hardwareunabhängige Laserketten-Logik, Mock-Geräte und PyQt6-Widgets.

## Installation

```bash
pip install "maiman-drivers @ git+https://github.com/AGKarpf/Maiman-Treiber-Software.git"
# mit GUI-Widgets (PyQt6):
pip install "maiman-drivers[gui] @ git+https://github.com/AGKarpf/Maiman-Treiber-Software.git"
```

Für die Entwicklung im ausgecheckten Repo: `pip install -e .[gui]`

## Struktur

```
maiman_drivers/
├─ driver.py   # MaimanDriver, SF6015, SF8150T, SerialNumberMismatch
├─ laser.py    # LaserStage, TecLaserStage, DcLaserStage, AdjustableTecLaserStage, LaserChain
├─ mock.py     # MockSF6015, MockSF8150T, FakeMaimanSerial (ohne Hardware)
└─ gui.py      # PyQt6: Stage-Widgets, LaserChainWidget, MultiLaserWindow, run()
example.py            # Treiber-Beispiel
demo_gui.py           # GUI-Demo mit Mock-Geräten
test_laser_chains.py  # hardwarefreie Tests (py test_laser_chains.py)
SF6015.pdf, FS8150.pdf  # Herstellerdokumentation
```

## Beispiel

```python
from maiman_drivers import SF8150T

with SF8150T("COM5", expected_serial=0x1379) as drv:   # Seriennummer optional
    drv.set_tec_temperature(25.0)
    drv.tec_start()
    drv.set_current(0.3)        # Ampere
    drv.start()
```

`expected_serial` (int oder Hex-String) prüft die Geräteidentität; bei
Abweichung wird die Verbindung geschlossen und `SerialNumberMismatch` geworfen.

Laserketten erzwingen die Einschaltreihenfolge (Stufe *n* nur, wenn Stufe *n-1*
läuft) und die TEC-Abhängigkeit:

```python
from maiman_drivers.laser import LaserChain, TecLaserStage, DcLaserStage
from maiman_drivers.mock import MockSF8150T, MockSF6015

chain = LaserChain("Laser 1", [
    TecLaserStage("Seed", MockSF8150T("MOCK1"), diode_current_a=0.3),
    DcLaserStage("Amplifier", MockSF6015("MOCK2"), initial_current_a=0.0),
])
chain.initialize()
```

Die GUI wird nicht automatisch importiert, damit der Kern ohne PyQt6 nutzbar
bleibt: `from maiman_drivers.gui import LaserChainWidget, run`.

## Tests / Demo

```bash
py test_laser_chains.py
py demo_gui.py
```
