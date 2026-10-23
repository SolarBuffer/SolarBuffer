"""Toegang op afstand: de hub belt uit zichzelf naar de server van SolarBuffer.

Waarom dit bestaat
------------------
Tot nu toe ging toegang van buitenaf met Tailscale. Dat werkt, maar het vraagt
van de klant een account, een app op zijn telefoon en een naam die hij ergens
moet invullen. Bij elke installatie is dat een drempel, en bij de iPhone-app
werkte het bovendien niet eens, want Apple weigert gewoon http naar zo'n naam.

Hier belt de hub uit zichzelf naar één server en houdt die verbinding open. Dat
is uitgaand verkeer, dus er hoeft niets open in de router van de klant en zijn
wisselende IP-adres doet er niet toe. Van buitenaf praat de app met één naam over
https, en de server stuurt het verzoek door de tunnel naar deze hub.

Dit bestand staat los van app.py omdat het vooral met het systeem praat: sleutels
maken, een configuratiebestand schrijven, een dienst starten. Dat is apart te
beproeven, wat in een bestand van veertienduizend regels niet lukt.
"""

import os
import re
import subprocess

import requests

SERVER = os.environ.get("SOLARBUFFER_TOEGANG", "https://hub.solarbuffer.nl")
WG_INTERFACE = "wg0"
WG_MAP = "/etc/wireguard"
WG_CONF = f"{WG_MAP}/{WG_INTERFACE}.conf"
SLEUTEL_PAD = f"{WG_MAP}/{WG_INTERFACE}.key"
# Zonder dit valt de tunnel dicht zodra de router van de klant de uitgaande
# verbinding opruimt, en is de hub van buitenaf onbereikbaar tot hij zelf weer
# iets stuurt. Vijfentwintig seconden is wat WireGuard zelf aanraadt.
KEEPALIVE = 25


def _draai(opdracht, invoer=None, tijd=20):
    return subprocess.run(opdracht, input=invoer, capture_output=True,
                          text=True, timeout=tijd)


def wireguard_aanwezig():
    return _draai(["which", "wg"]).returncode == 0


def installeer_wireguard():
    """Installeert wireguard als het er nog niet is.

    Een hub die al jaren draait heeft dit niet, dus het hoort erbij in plaats van
    dat iemand het met de hand moet doen.
    """
    if wireguard_aanwezig():
        return True, "was al aanwezig"
    omgeving = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    uit = subprocess.run(["apt-get", "install", "-y", "wireguard-tools"],
                         capture_output=True, text=True, timeout=300,
                         env=omgeving)
    if uit.returncode != 0:
        return False, (uit.stderr or uit.stdout or "").strip()[:300]
    return True, "geinstalleerd"


def sleutels():
    """Het sleutelpaar van deze hub, aangemaakt als het er nog niet is.

    De private sleutel blijft hier en gaat nooit over de lijn; de server krijgt
    alleen de publieke helft.
    """
    os.makedirs(WG_MAP, exist_ok=True)
    if os.path.exists(SLEUTEL_PAD):
        with open(SLEUTEL_PAD) as f:
            prive = f.read().strip()
        if prive:
            publiek = _draai(["wg", "pubkey"], invoer=prive + "\n").stdout.strip()
            if publiek:
                return prive, publiek

    prive = _draai(["wg", "genkey"]).stdout.strip()
    if not prive:
        raise RuntimeError("kon geen sleutel maken; is wireguard-tools er?")
    publiek = _draai(["wg", "pubkey"], invoer=prive + "\n").stdout.strip()
    vorige = os.umask(0o077)
    try:
        with open(SLEUTEL_PAD, "w") as f:
            f.write(prive + "\n")
    finally:
        os.umask(vorige)
    os.chmod(SLEUTEL_PAD, 0o600)
    return prive, publiek


def meld_aan(publieke_sleutel, tijd=20):
    """Schrijft deze hub in bij de server en krijgt een plek in de tunnel."""
    r = requests.post(f"{SERVER}/v1/aanmelden",
                      json={"publieke_sleutel": publieke_sleutel}, timeout=tijd)
    if r.status_code != 200:
        reden = ""
        try:
            reden = (r.json() or {}).get("error", "")
        except Exception:
            reden = r.text[:200]
        raise RuntimeError(f"server weigerde het aanmelden: {reden or r.status_code}")
    return r.json()


def schrijf_conf(prive, adres, server_publieke_sleutel, endpoint):
    """Zet de tunnel op papier.

    AllowedIPs staat met opzet op alleen het adres van de server en niet op
    0.0.0.0/0: deze tunnel is er om bereikbaar te zijn, niet om al het verkeer
    van de klant door onze server te sturen.
    """
    net = adres.rsplit(".", 1)[0] + ".1/32"
    inhoud = (
        "# Door SolarBuffer geschreven, niet met de hand aanpassen.\n"
        "[Interface]\n"
        f"Address = {adres}/32\n"
        f"PrivateKey = {prive}\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {server_publieke_sleutel}\n"
        f"Endpoint = {endpoint}\n"
        f"AllowedIPs = {net}\n"
        f"PersistentKeepalive = {KEEPALIVE}\n"
    )
    vorige = os.umask(0o077)
    try:
        with open(WG_CONF, "w") as f:
            f.write(inhoud)
    finally:
        os.umask(vorige)
    os.chmod(WG_CONF, 0o600)
    return inhoud


def zet_aan():
    """Alles achter elkaar: installeren, sleutels, aanmelden, tunnel starten.

    Geeft het antwoord van de server terug, waar ook de toegangssleutel in zit
    die de app nodig heeft om te koppelen.
    """
    ok, wat = installeer_wireguard()
    if not ok:
        raise RuntimeError(f"wireguard installeren mislukte: {wat}")

    prive, publiek = sleutels()
    gegevens = meld_aan(publiek)
    schrijf_conf(prive, gegevens["adres"],
                 gegevens["server_publieke_sleutel"], gegevens["server_endpoint"])

    _draai(["systemctl", "enable", f"wg-quick@{WG_INTERFACE}"], tijd=30)
    # Eerst stoppen: draait hij al met een oude configuratie, dan neemt een
    # enkele start de nieuwe niet over.
    _draai(["systemctl", "stop", f"wg-quick@{WG_INTERFACE}"], tijd=30)
    uit = _draai(["systemctl", "start", f"wg-quick@{WG_INTERFACE}"], tijd=30)
    if uit.returncode != 0:
        raise RuntimeError((uit.stderr or uit.stdout or "").strip()[:300])
    return gegevens


def zet_uit():
    _draai(["systemctl", "disable", "--now", f"wg-quick@{WG_INTERFACE}"], tijd=30)
    return True


def stand():
    """Of de tunnel staat, en of de server er werkelijk doorheen antwoordt.

    Alleen kijken of de dienst draait is niet genoeg: hij draait ook prima als er
    aan de andere kant niets gebeurt. De laatste handshake is het enige dat zegt
    of er werkelijk contact is.
    """
    actief = _draai(["systemctl", "is-active", f"wg-quick@{WG_INTERFACE}"]).stdout.strip()
    uit = _draai(["wg", "show", WG_INTERFACE, "latest-handshakes"]).stdout.strip()
    laatste = 0
    if uit:
        deel = uit.split()
        if len(deel) >= 2 and deel[-1].isdigit():
            laatste = int(deel[-1])
    adres = ""
    if os.path.exists(WG_CONF):
        with open(WG_CONF) as f:
            treffer = re.search(r"^Address = ([0-9.]+)", f.read(), re.M)
            if treffer:
                adres = treffer.group(1)
    return {"actief": actief == "active", "adres": adres,
            "laatste_handshake": laatste}
