"""
Hardwarefreier Test der Laserketten-Logik.

Ausfuehren mit:  py test_laser_chains.py

Es wird KEIN echter Treiber verbunden -- stattdessen kommen die Mock-Treiber
:class:`MockSF6015` / :class:`MockSF8150T` zum Einsatz, die das Maiman-Protokoll
im Speicher emulieren.

Aufgebaut werden zwei Laserketten:

* Kette 1 "MOPA":  1x TEC-Treiber  ->  1x DC-Treiber (ohne TEC)
* Kette 2 "Array": 3x TEC-Treiber  ->  1x DC-Treiber (ohne TEC)

Geprueft werden:
  - Initialisierung (TECs laufen, DC-Strom = 0.0 A)
  - Reihenfolge-Abhaengigkeit beim Ein- und Ausschalten (beliebig lange Ketten)
  - Schutz: Laserdiode nur bei laufendem TEC aktivierbar
  - Stromsetzung der DC-Stufe
  - Seriennummer-Pruefung beim Verbinden
"""

import warnings

from maiman_drivers import SerialNumberMismatch
from maiman_drivers.mock import MockSF6015, MockSF8150T
from maiman_drivers.laser import (
    TecLaserStage, DcLaserStage, AdjustableTecLaserStage, LaserChain,
    ChainDependencyError, TecNotRunningError,
)


_passed = 0


def check(label: str, condition: bool) -> None:
    global _passed
    if not condition:
        raise AssertionError(f"FEHLGESCHLAGEN: {label}")
    _passed += 1
    print(f"  [ok] {label}")


def expect_raises(label: str, exc_type, func) -> None:
    try:
        func()
    except exc_type:
        check(label, True)
    else:
        raise AssertionError(f"FEHLGESCHLAGEN (keine Exception): {label}")


def build_chain_1() -> LaserChain:
    """MOPA: TEC-Seed -> DC-Verstaerker."""
    seed = MockSF8150T("MOCK-K1-TEC", serial_number=0x1001)
    amp = MockSF6015("MOCK-K1-DC", serial_number=0x1002)
    stages = [
        TecLaserStage("K1/Seed (TEC)", seed, diode_current_a=0.3),
        DcLaserStage("K1/Verstaerker (DC)", amp, initial_current_a=0.0),
    ]
    chain = LaserChain("Kette 1 (MOPA)", stages)
    chain.initialize()
    return chain


def build_chain_2() -> LaserChain:
    """Array: 3x TEC -> 1x DC."""
    stages = [
        TecLaserStage("K2/TEC-A", MockSF8150T("MOCK-K2-A", serial_number=0x2001),
                      diode_current_a=0.5),
        TecLaserStage("K2/TEC-B", MockSF8150T("MOCK-K2-B", serial_number=0x2002),
                      diode_current_a=0.5),
        TecLaserStage("K2/TEC-C", MockSF8150T("MOCK-K2-C", serial_number=0x2003),
                      diode_current_a=0.5),
        DcLaserStage("K2/DC-End", MockSF6015("MOCK-K2-D", serial_number=0x2004),
                     initial_current_a=0.0),
    ]
    chain = LaserChain("Kette 2 (Array)", stages)
    chain.initialize()
    return chain


def test_initialisierung(chain1: LaserChain, chain2: LaserChain) -> None:
    print("\n== Initialisierung ==")
    # Alle TEC-Stufen laufen, alle Ausgaenge sind aus.
    for chain in (chain1, chain2):
        for stage in chain.stages:
            check(f"{stage.name}: Ausgang aus nach init", stage.is_on is False)
            if isinstance(stage, TecLaserStage):
                check(f"{stage.name}: TEC laeuft", stage.tec_running() is True)
    dc = chain1.stages[1]
    check("K1/DC: Sollstrom 0.0 A nach init",
          abs(dc.get_current_setpoint() - 0.0) < 1e-9)


def test_reihenfolge_kette1(chain: LaserChain) -> None:
    print("\n== Kette 1: Reihenfolge-Abhaengigkeit ==")
    check("can_enable(0) anfangs", chain.can_enable(0) is True)
    check("can_enable(1) anfangs False", chain.can_enable(1) is False)

    expect_raises("enable(1) vor enable(0) -> Fehler",
                  ChainDependencyError, lambda: chain.enable(1))

    chain.enable(0)
    check("Stufe 0 an", chain.stages[0].is_on is True)
    check("can_enable(1) jetzt True", chain.can_enable(1) is True)

    chain.enable(1)
    check("Stufe 1 an", chain.stages[1].is_on is True)

    check("can_disable(0) False (Nachfolger an)", chain.can_disable(0) is False)
    expect_raises("disable(0) waehrend Stufe 1 an -> Fehler",
                  ChainDependencyError, lambda: chain.disable(0))

    chain.disable(1)
    check("Stufe 1 aus", chain.stages[1].is_on is False)
    chain.disable(0)
    check("Stufe 0 aus", chain.stages[0].is_on is False)


def test_tec_schutz(chain: LaserChain) -> None:
    print("\n== Kette 1: TEC-Schutz ==")
    seed = chain.stages[0]
    # TEC kuenstlich stoppen -> Diode darf nicht mehr eingeschaltet werden.
    seed.driver.tec_stop()
    check("TEC nun gestoppt", seed.tec_running() is False)
    expect_raises("turn_on bei gestopptem TEC -> TecNotRunningError",
                  TecNotRunningError, lambda: chain.enable(0))
    check("Diode blieb aus", seed.is_on is False)
    # TEC wieder starten -> einschalten klappt.
    seed.driver.tec_start()
    chain.enable(0)
    check("nach TEC-Neustart: Diode an", seed.is_on is True)
    chain.disable(0)


def test_lange_kette(chain: LaserChain) -> None:
    print("\n== Kette 2: lange Kette (4 Stufen) ==")
    n = len(chain)
    check("Kette hat 4 Stufen", n == 4)

    # Aus dem komplett ausgeschalteten Zustand darf nur Stufe 0 starten.
    for i in range(1, n):
        expect_raises(f"enable({i}) aus Grundzustand -> Fehler",
                      ChainDependencyError, lambda i=i: chain.enable(i))

    # Schrittweise hochfahren; dabei darf man nie eine Stufe ueberspringen.
    for i in range(n):
        chain.enable(i)
        check(f"Stufe {i} an", chain.stages[i].is_on is True)
        if i + 2 < n:
            check(f"can_enable({i+2}) noch False (kein Ueberspringen)",
                  chain.can_enable(i + 2) is False)

    check("alle Stufen an", all(chain.state()))

    # Mitten in der Kette darf nichts abgeschaltet werden.
    for i in range(n - 1):
        expect_raises(f"disable({i}) bei aktivem Nachfolger -> Fehler",
                      ChainDependencyError, lambda i=i: chain.disable(i))

    # Sauberes Herunterfahren in umgekehrter Reihenfolge.
    chain.shutdown()
    check("alle Stufen aus nach shutdown", not any(chain.state()))


def test_dc_stromsetzung(chain: LaserChain) -> None:
    print("\n== Kette 2: DC-Stromsetzung ==")
    dc = chain.stages[-1]
    for value in (0.1, 0.5, 5.0, 12.3):
        dc.set_current(value)
        got = dc.get_current_setpoint()
        check(f"DC set_current({value}) -> {got:.2f} A", abs(got - value) < 1e-6)


def test_seriennummer() -> None:
    print("\n== Seriennummer-Pruefung beim Verbinden ==")
    # Richtige Seriennummer -> Verbindung bleibt.
    drv = MockSF8150T("MOCK", serial_number=0x1A2F, expected_serial="1A2F")
    check("passende Seriennummer -> verbunden", drv.is_open is True)
    check("gelesene Seriennummer korrekt", drv.serial_number == 0x1A2F)
    drv.close()

    # Falsche Seriennummer -> Warnung + Verbindung verworfen + Exception.
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        expect_raises("falsche Seriennummer -> SerialNumberMismatch",
                      SerialNumberMismatch,
                      lambda: MockSF6015("MOCK", serial_number=0xBEEF,
                                         expected_serial="1A2F"))
        check("Warnung wurde ausgegeben",
              any(issubclass(x.category, UserWarning) for x in w))


def test_tec_solltemperatur() -> None:
    print("\n== TEC-Solltemperatur (15..40 C) ==")
    drv = MockSF8150T("MOCK-TEMP", serial_number=0x3001)
    stage = TecLaserStage("Seed 17 C", drv, diode_current_a=0.1, tec_temperature_c=17)
    stage.initialize()
    check("TecLaserStage setzt 17 C", abs(drv.get_tec_temperature_setpoint() - 17.0) < 1e-9)
    check("TEC laeuft nach initialize", stage.tec_running())

    plain = MockSF8150T("MOCK-TEMP2", serial_number=0x3002)
    TecLaserStage("ohne Angabe", plain, diode_current_a=0.1).initialize()
    check("ohne tec_temperature_c bleibt der Sollwert unveraendert (25 C)",
          abs(plain.get_tec_temperature_setpoint() - 25.0) < 1e-9)

    adj = MockSF8150T("MOCK-TEMP3", serial_number=0x3003)
    adj_stage = AdjustableTecLaserStage("adjustable", adj, 0.0)
    adj_stage.initialize()
    check("AdjustableTecLaserStage Standard 25 C",
          abs(adj.get_tec_temperature_setpoint() - 25.0) < 1e-9)
    adj2 = MockSF8150T("MOCK-TEMP4", serial_number=0x3004)
    AdjustableTecLaserStage("adjustable 17", adj2, 0.0, tec_temperature_c=17.5).initialize()
    check("AdjustableTecLaserStage 17.5 C",
          abs(adj2.get_tec_temperature_setpoint() - 17.5) < 1e-9)

    for bad in (14.99, 40.01, float("nan")):
        expect_raises(f"Stufe mit {bad} C -> ValueError", ValueError,
                      lambda bad=bad: TecLaserStage("x", MockSF8150T("M"), 0.1,
                                                    tec_temperature_c=bad))
    before = drv.get_tec_temperature_setpoint()
    expect_raises("set_tec_temperature(10) -> ValueError", ValueError,
                  lambda: drv.set_tec_temperature(10))
    check("ungueltiger Wert wird nicht gesendet",
          drv.get_tec_temperature_setpoint() == before)


def main() -> None:
    print("Baue zwei Laserketten (ohne Hardware, Mock-Treiber) ...")
    chain1 = build_chain_1()
    chain2 = build_chain_2()
    print(f"  Kette 1: {[s.name for s in chain1.stages]}")
    print(f"  Kette 2: {[s.name for s in chain2.stages]}")

    test_initialisierung(chain1, chain2)
    test_reihenfolge_kette1(chain1)
    test_tec_schutz(chain1)
    test_lange_kette(chain2)
    test_dc_stromsetzung(chain2)
    test_seriennummer()
    test_tec_solltemperatur()

    print(f"\nAlle {_passed} Pruefungen bestanden.")


if __name__ == "__main__":
    main()
