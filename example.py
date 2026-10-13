"""
Beispiel: Verbinden und Steuern der Maiman-Treiber.

Ausfuehren mit:  py example.py
(listet zunaechst die verfuegbaren COM-Ports auf)
"""

import serial.tools.list_ports as list_ports

from maiman_drivers import SF6015, SF8150T, SerialNumberMismatch


def list_serial_ports() -> None:
    print("Verfuegbare serielle Ports:")
    ports = list(list_ports.comports())
    if not ports:
        print("  (keine gefunden)")
    for p in ports:
        print(f"  {p.device:10s} {p.description}")


def demo_sf6015(port: str) -> None:
    """SF6015 -- Laserdiodentreiber ohne TEC."""
    with SF6015(port) as ld:
        print("Seriennummer:", ld.get_serial_number())

        ld.set_current_source(internal=True)   # digitaler Sollwert statt Poti
        ld.set_enable_source(internal=True)     # Enable per Software
        ld.set_current(5.0)                     # 5 A
        ld.start()

        print("Zustand:", ld.read_state())
        print("Strom :", ld.get_measured_current(), "A")
        print("Span. :", ld.get_measured_voltage(), "V")
        print("Lock  :", ld.read_lock_status())

        ld.stop()


def demo_sf8150t(port: str) -> None:
    """SF8150-T -- Laserdiodentreiber mit TEC."""
    with SF8150T(port) as drv:
        print("Seriennummer:", drv.get_serial_number())
        print("Firmware    :", drv.get_firmware_version())

        # TEC zuerst einregeln
        drv.tec_set_source(internal=True)
        drv.tec_set_enable_source(internal=True)
        drv.set_tec_temperature(25.0)           # 25.00 Grad C
        drv.tec_start()

        # Laserdiodentreiber
        drv.set_current_source(internal=True)
        drv.set_enable_source(internal=True)
        drv.set_current(1.2)                    # 1.2 A
        drv.start()

        print("TEC-Zustand :", drv.read_tec_state())
        print("TEC-Temp    :", drv.get_tec_temperature(), "Grad C")
        print("LD-Strom    :", drv.get_measured_current(), "A")

        drv.stop()
        drv.tec_stop()


def demo_komplexer_laser() -> None:
    """Mehrere Treiber sicher ueber ihre Seriennummer zuordnen.

    In einem komplexen Aufbau koennen sich COM-Nummern zwischen Neustarts
    aendern. Mit ``expected_serial`` wird beim Verbinden geprueft, dass am Port
    wirklich der gewuenschte Treiber haengt -- sonst Warnung + Abbruch.
    """
    treiber = {
        "COM4": ("Seed", "1A2F"),
        "COM5": ("Verstaerker", "0B7C"),
    }
    for port, (name, seriennr) in treiber.items():
        try:
            drv = SF8150T(port, expected_serial=seriennr)
        except SerialNumberMismatch as e:
            print(f"[{name}] {e}")
            continue
        print(f"[{name}] verbunden an {port}, "
              f"Seriennummer 0x{drv.serial_number:04X}")
        drv.close()


if __name__ == "__main__":
    list_serial_ports()
    print()
    print("Zum Testen die passende Funktion mit dem richtigen Port aufrufen,")
    print("z.B.:  demo_sf6015('COM4')  oder  demo_sf8150t('COM5')")
    print("Mehrere Treiber per Seriennummer: demo_komplexer_laser()")
