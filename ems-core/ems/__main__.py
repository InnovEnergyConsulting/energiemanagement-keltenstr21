"""ems-core – ein Image, mehrere Rollen (Umsetzungskonzept 2.4): python -m ems <rolle>"""
import sys

ROLES = {
    "controme-adapter": "ems.adapters.controme",
    "normalizer": "ems.normalizer.service",
    "writer": "ems.writer.service",
    "leitstand": "ems.leitstand.service",
}
# Werkzeuge mit Argumenten (einmalig aufgerufen, kein Dauerdienst)
TOOLS = {"kosten-rechnung": "ems.kosten.nachrechnung"}


def main():
    if len(sys.argv) >= 2 and sys.argv[1] in TOOLS:
        __import__(TOOLS[sys.argv[1]], fromlist=["run"]).run(sys.argv[2:])
        return
    if len(sys.argv) != 2 or sys.argv[1] not in ROLES:
        sys.exit(f"Aufruf: python -m ems <{'|'.join(ROLES)}>")
    module = __import__(ROLES[sys.argv[1]], fromlist=["run"])
    module.run()


if __name__ == "__main__":
    main()
