#!/usr/bin/env python3
"""Erzeugt die App-Symbole für den Startbildschirm.

**Werkzeug, keine Laufzeitabhängigkeit.** Läuft einmal auf dem Entwicklungsrechner
und legt fertige PNG-Dateien ab. Der Server braucht dafür nichts: er liefert nur
die erzeugten Dateien aus. Deshalb darf hier PIL benutzt werden, im Hallenbuch
selbst nicht.

Ohne Symbole nimmt das Handy beim Ablegen auf dem Startbildschirm einen
Ausschnitt der Seite. Das sieht auf dem Telefon eines Mitarbeiters billig aus.

  python3 werkzeuge/symbole.py
"""
from __future__ import annotations

import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Dieses Werkzeug braucht Pillow: python3 -m pip install pillow")

NACHT = (12, 17, 22)
CYAN = (63, 191, 223)
SCHRIFT = "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf"
ZIEL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "hallenbuch", "statisch", "symbole")

# (Dateiname, Kantenlänge, Anteil der Bildbreite für den Schriftzug)
# Beim maskierbaren Symbol schneiden Android-Geräte die Ecken weg, deshalb
# sitzt der Schriftzug dort deutlich kleiner in der sicheren Mitte.
SYMBOLE = [
    ("symbol-192.png", 192, 0.78),
    ("symbol-512.png", 512, 0.78),
    ("symbol-maskabel-512.png", 512, 0.54),
    ("apple-touch-icon.png", 180, 0.78),
]


def zeichnen(kante: int, anteil: float) -> "Image.Image":
    gross = kante * 4                      # erst gross zeichnen, dann verkleinern
    bild = Image.new("RGB", (gross, gross), NACHT)
    stift = ImageDraw.Draw(bild)

    groesse = int(gross * 0.42)
    schrift = ImageFont.truetype(SCHRIFT, groesse)
    while True:
        links, oben, rechts, unten = stift.textbbox((0, 0), "TD", font=schrift)
        if rechts - links <= gross * anteil or groesse <= 10:
            break
        groesse -= 4
        schrift = ImageFont.truetype(SCHRIFT, groesse)

    stift.text(((gross - (rechts - links)) / 2 - links,
                (gross - (unten - oben)) / 2 - oben),
               "TD", font=schrift, fill=CYAN)
    return bild.resize((kante, kante), Image.LANCZOS)


def main():
    if not os.path.exists(SCHRIFT):
        sys.exit("Schrift nicht gefunden: %s" % SCHRIFT)
    os.makedirs(ZIEL, exist_ok=True)
    for name, kante, anteil in SYMBOLE:
        pfad = os.path.join(ZIEL, name)
        zeichnen(kante, anteil).save(pfad, "PNG", optimize=True)
        print("  %-26s %3d x %3d  %6d B" % (name, kante, kante, os.path.getsize(pfad)))


if __name__ == "__main__":
    main()
