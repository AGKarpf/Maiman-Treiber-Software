"""
Startet die PyQt6-Laser-GUI mit simulierten Treibern (keine Hardware noetig).

Ausfuehren mit:  py demo_gui.py

Aufgebaut werden zwei Laserketten:
  * Kette 1 "MOPA":  1x TEC-Treiber  ->  1x DC-Treiber
  * Kette 2 "Array": 3x TEC-Treiber  ->  1x DC-Treiber

Bedienung:
  - Der EIN/AUS-Knopf einer Stufe ist nur aktiv, wenn die Kettenregel es erlaubt
    (Vorgaenger an zum Einschalten, Nachfolger aus zum Ausschalten).
  - DC-Stufen haben zusaetzlich eine Strom-SpinBox (0.1-A-Schritte / Direkteingabe).
  - Fuer echte Hardware einfach die MockSF*-Treiber gegen SF6015 / SF8150T mit
    echtem COM-Port und (optional) erwarteter Seriennummer tauschen.
"""

import sys

from maiman_drivers.mock import MockSF6015, MockSF8150T
from maiman_drivers.laser import TecLaserStage, DcLaserStage, LaserChain
from maiman_drivers.gui import run


def build_chains():
    # --- Kette 1: TEC-Seed -> DC-Verstaerker ---
    chain1 = LaserChain("Kette 1 (MOPA)", [
        TecLaserStage("Seed (TEC)",
                      MockSF8150T("MOCK-K1-TEC", serial_number=0x1001),
                      diode_current_a=0.3),
        DcLaserStage("Verstaerker (DC)",
                     MockSF6015("MOCK-K1-DC", serial_number=0x1002),
                     initial_current_a=0.0),
    ])

    # --- Kette 2: 3x TEC -> 1x DC ---
    chain2 = LaserChain("Kette 2 (Array)", [
        TecLaserStage("TEC-A", MockSF8150T("MOCK-K2-A", serial_number=0x2001),
                      diode_current_a=0.5),
        TecLaserStage("TEC-B", MockSF8150T("MOCK-K2-B", serial_number=0x2002),
                      diode_current_a=0.5),
        TecLaserStage("TEC-C", MockSF8150T("MOCK-K2-C", serial_number=0x2003),
                      diode_current_a=0.5),
        DcLaserStage("DC-End", MockSF6015("MOCK-K2-D", serial_number=0x2004),
                     initial_current_a=0.0),
    ])

    for chain in (chain1, chain2):
        chain.initialize()   # TECs anschalten, Ausgaenge aus, DC-Strom = 0.0 A
    return [chain1, chain2]


if __name__ == "__main__":
    sys.exit(run(build_chains()))
