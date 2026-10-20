#!/usr/bin/env python3
"""Kopieert release-notities van de gesloten repo naar de openbare.

De software staat in een gesloten repository, dus links naar de release-notities
lopen daar voor een klant dood. De notities schrijf je nog steeds gewoon bij de
release in de gesloten repo; dit script zet er een publieke kopie van, zodat de
links op de updatepagina van de Hub blijven werken.

    python3 kopieer_release.py            kopieert alles wat er nog niet staat
    python3 kopieer_release.py V0.8.2     kopieert één versie
    python3 kopieer_release.py --lijst    laat zien wat er mist

Vraagt gh om te zijn ingelogd met toegang tot allebei de repositories.
"""
import json
import re
import subprocess
import sys
import tempfile

BRON = "SolarBuffer/SolarBuffer"
DOEL = "SolarBuffer/solarbuffer-releases"


def gh(*args, tekst=True):
    r = subprocess.run(["gh", *args], capture_output=True, text=tekst)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"gh {' '.join(args)} mislukte")
    return r.stdout


def tags(repo):
    uit = gh("release", "list", "--repo", repo, "--limit", "200",
             "--json", "tagName")
    return [r["tagName"] for r in json.loads(uit)]


def nummer(tag):
    return tuple(int(x) for x in re.findall(r"\d+", tag))


def kopieer(tag):
    d = json.loads(gh("release", "view", tag, "--repo", BRON,
                      "--json", "tagName,name,body"))
    body = d.get("body") or ""
    if not body.strip():
        print(f"  {tag}: geen tekst in de bron, overgeslagen")
        return False
    # Via een bestand, want de tekst bevat opmaak en regeleindes.
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body)
        pad = f.name
    gh("release", "create", tag, "--repo", DOEL,
       "--title", d.get("name") or tag, "--notes-file", pad)
    print(f"  {tag}: gekopieerd")
    return True


def main():
    argumenten = [a for a in sys.argv[1:] if not a.startswith("--")]
    alleen_lijst = "--lijst" in sys.argv

    bron, doel = tags(BRON), set(tags(DOEL))
    mist = sorted([t for t in bron if t not in doel], key=nummer)

    if alleen_lijst:
        print(f"{len(bron)} in de bron, {len(doel)} openbaar")
        print("mist:", ", ".join(mist) if mist else "niets")
        return

    tewerk = argumenten or mist
    if not tewerk:
        print("Niets te doen, alles staat al openbaar.")
        return

    for tag in tewerk:
        if tag in doel:
            print(f"  {tag}: stond er al")
            continue
        if tag not in bron:
            print(f"  {tag}: bestaat niet in {BRON}")
            continue
        kopieer(tag)


if __name__ == "__main__":
    main()
