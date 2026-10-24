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
# Zonder dit valt de tunnel dicht zodra de router van de klant de uitgaande
# verbinding opruimt, en is de hub van buitenaf onbereikbaar tot hij zelf weer
# iets stuurt. Vijfentwintig seconden is wat WireGuard zelf aanraadt.
KEEPALIVE = 25


def _draai(opdracht, invoer=None, tijd=20):
    return subprocess.run(opdracht, input=invoer, capture_output=True,
                          text=True, timeout=tijd)


def _root(opdracht, invoer=None, tijd=20):
    """Hetzelfde, maar met root-rechten.

    De webdienst draait als gebruiker solarbuffer en niet als root, terwijl dit
    allemaal het systeem raakt: een pakket installeren, in /etc/wireguard
    schrijven, een dienst starten, de firewall zetten. Zonder sudo ervoor
    mislukte elke schakelaar met een rechtenfout, en die kwam alleen in de
    browser terecht en niet in het logboek.
    """
    return subprocess.run(["sudo", "-n", *opdracht], input=invoer,
                          capture_output=True, text=True, timeout=tijd)


def wireguard_aanwezig():
    return _draai(["which", "wg"]).returncode == 0


def installeer_wireguard():
    """Installeert wireguard als het er nog niet is.

    Een hub die al jaren draait heeft dit niet, dus het hoort erbij in plaats van
    dat iemand het met de hand moet doen.
    """
    if wireguard_aanwezig():
        return True, "was al aanwezig"
    uit = subprocess.run(
        ["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive",
         "apt-get", "install", "-y", "wireguard-tools"],
        capture_output=True, text=True, timeout=300)
    if uit.returncode != 0:
        return False, (uit.stderr or uit.stdout or "").strip()[:300]
    return True, "geinstalleerd"


def sleutels():
    """Het sleutelpaar van deze hub, aangemaakt als het er nog niet is.

    De private sleutel staat in wg0.conf en nergens anders. Eerder hield ik hem
    daarnaast in een eigen bestand bij, maar dan staat hetzelfde geheim op twee
    plekken en kunnen die uit elkaar gaan lopen. Lezen en schrijven gaat met
    root-rechten, want /etc/wireguard is van root en de webdienst draait dat niet.
    """
    bestaand = _root(["cat", WG_CONF])
    if bestaand.returncode == 0:
        treffer = re.search(r"^PrivateKey\s*=\s*(\S+)", bestaand.stdout, re.M)
        if treffer:
            prive = treffer.group(1)
            publiek = _draai(["wg", "pubkey"], invoer=prive + "\n").stdout.strip()
            if publiek:
                return prive, publiek

    prive = _draai(["wg", "genkey"]).stdout.strip()
    if not prive:
        raise RuntimeError("kon geen sleutel maken; is wireguard-tools er?")
    publiek = _draai(["wg", "pubkey"], invoer=prive + "\n").stdout.strip()
    return prive, publiek


def versie():
    """Welke versie deze hub draait.

    Uit git en niet uit een nummer in de code, want dat laatste klopt alleen
    zolang iemand eraan denkt het bij te werken. Een hub die op een branch staat
    of een paar commits verder is, zegt dat hier dan ook eerlijk.
    """
    map_ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    uit = _draai(["git", "-C", map_, "describe", "--tags", "--always", "--dirty"])
    return (uit.stdout or "").strip()[:40]


def naam():
    uit = _draai(["hostname"])
    return (uit.stdout or "").strip()[:60]


def apparaat_id():
    """De aanduiding waarmee deze hub al in het hele systeem bekend staat.

    Dezelfde die Homey en Home Assistant gebruiken om een hub uit elkaar te
    houden, uit /etc/machine-id. Hiermee kun je een hub in de lijst terugvinden
    in je eigen administratie, zonder dat er iets over de klant zelf naar de
    server gaat; de naam die de hub meldt is bij iedereen "SolarBuffer".
    """
    try:
        with open("/etc/machine-id", encoding="utf-8") as f:
            waarde = f.read().strip()
        if waarde:
            return waarde[:40]
    except Exception:
        pass
    return ""


def klop(hub_id, support_tot=0, tijd=10):
    """Laat de server weten dat deze hub er is, welke versie hij draait en of de
    onderhoudsdeur openstaat.

    Dat laatste is wat je bij veertig hubs wilt zien: wie heeft er om hulp
    gevraagd. Daarom wordt dit ook meteen gestuurd als die schakelaar omgaat en
    niet pas bij het volgende kwartier.
    """
    try:
        requests.post(f"{SERVER}/v1/klop", timeout=tijd, json={
            "hub_id": hub_id, "versie": versie(), "naam": naam(),
            "apparaat_id": apparaat_id(), "support_tot": support_tot})
        return True
    except Exception:
        return False


def meld_aan(publieke_sleutel, tijd=20):
    """Schrijft deze hub in bij de server en krijgt een plek in de tunnel."""
    r = requests.post(f"{SERVER}/v1/aanmelden", timeout=tijd,
                      json={"publieke_sleutel": publieke_sleutel,
                            "versie": versie(), "naam": naam(),
                            "apparaat_id": apparaat_id()})
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
    uit = _root(["tee", WG_CONF], invoer=inhoud)
    if uit.returncode != 0:
        raise RuntimeError((uit.stderr or "").strip()[:200] or "kon de tunnel niet opschrijven")
    _root(["chmod", "600", WG_CONF])
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

    _root(["systemctl", "enable", f"wg-quick@{WG_INTERFACE}"], tijd=30)
    # Eerst stoppen: draait hij al met een oude configuratie, dan neemt een
    # enkele start de nieuwe niet over.
    _root(["systemctl", "stop", f"wg-quick@{WG_INTERFACE}"], tijd=30)
    uit = _root(["systemctl", "start", f"wg-quick@{WG_INTERFACE}"], tijd=30)
    if uit.returncode != 0:
        raise RuntimeError((uit.stderr or uit.stdout or "").strip()[:300])

    # Meteen dichtzetten. Zou dit pas bij de volgende controle gebeuren, dan
    # staat ssh in de tussentijd open op de tunnel.
    firewall_opbouwen()
    return gegevens


def zet_uit():
    _root(["systemctl", "disable", "--now", f"wg-quick@{WG_INTERFACE}"], tijd=30)
    return True


def meld_af(toegangssleutel, tijd=10):
    """Schrijft deze hub uit bij de server en wist de tunnel van de schijf.

    Hoort bij een fabrieksreset, om dezelfde reden als het uitloggen bij
    Tailscale: zonder dit blijft de hub als dode regel in het overzicht staan en
    blijft zijn adres bezet. En belangrijker: de private sleutel staat in
    wg0.conf, dus een kaart die gekopieerd wordt levert twee hubs met dezelfde
    sleutel op, die allebei hetzelfde tunneladres claimen. Daarom gaat dat bestand
    hier weg, net zoals machine-id en de ssh-hostsleutels dat doen.
    """
    gemeld = False
    if toegangssleutel:
        try:
            r = requests.post(f"{SERVER}/v1/afmelden", timeout=tijd,
                              json={"toegangssleutel": toegangssleutel})
            gemeld = r.status_code == 200
        except Exception:
            # Geen netwerk meer, of de server is weg. De tunnel gaat er lokaal
            # hoe dan ook af; een achtergebleven regel in het overzicht is dan
            # hooguit rommel en geen storing.
            pass
    _root(["systemctl", "disable", "--now", f"wg-quick@{WG_INTERFACE}"], tijd=30)
    _root(["rm", "-f", WG_CONF], tijd=15)
    return gemeld


# ================= WAT ER OP DE TUNNEL BEREIKBAAR IS =================
# Standaard alleen poort 5001, waar het dashboard achter een inlog zit. Ssh staat
# op de tunnel dicht, want daarmee is iemand root op deze machine en kan hij ook
# het wachtwoord van de klant omzetten. Dat gaat pas open als de klant zelf om
# hulp vraagt, en dan tijdelijk.
#
# De regels staan in een eigen keten, zodat ze in één keer opnieuw op te bouwen
# zijn zonder aan de rest van de firewall te komen. Ze overleven een herstart
# niet, dus app.py zet ze bij elke start opnieuw.
KETEN = "SOLARBUFFER-WG"
HUB_POORT = 5001
SSH_POORT = 22


def _iptables(*argumenten, controleer=False):
    uit = _root(["iptables", *argumenten])
    if controleer and uit.returncode != 0:
        raise RuntimeError((uit.stderr or uit.stdout or "").strip()[:200])
    return uit.returncode == 0


NOODLUIK_OPEN = "/run/solarbuffer-noodluik-open"


def firewall_opbouwen():
    """Zet vast wat er op de tunnel bereikbaar is: alleen het dashboard.

    Dit gaat met iptables-restore en niet met losse regels, en dat is geen
    nettigheid. Eerder leegde deze functie de keten en bouwde hem daarna regel
    voor regel opnieuw op. Wordt de app precies daartussen doodgeschoten, dan
    blijft er een lege keten achter, en een lege keten laat álles door: niet
    alleen ssh maar ook poort 80 met de wifi-pagina zonder wachtwoord. Dat is bij
    een echte crashproef op hardware ook gebeurd.

    Met iptables-restore wordt de hele keten in één keer vervangen. Er is dus
    geen moment waarop hij half gevuld is.

    Staat het noodluik open, dan hoort ssh erbij te blijven. Dat staat hier en
    niet alleen bij het opstarten van de app: zit de dienst in een herstartlus,
    dan wordt deze keten bij elke poging vervangen en zou de regel van het luik
    anders telkens verdwijnen.
    """
    regels = [
        "*filter",
        f":{KETEN} - [0:0]",
        # Antwoorden op verbindingen die wij zelf zijn begonnen moeten erdoor,
        # anders werkt de keepalive naar de server niet meer.
        f"-A {KETEN} -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        f"-A {KETEN} -p tcp --dport {HUB_POORT} -j ACCEPT",
    ]
    if os.path.exists(NOODLUIK_OPEN):
        regels.append(f"-A {KETEN} -p tcp --dport {SSH_POORT} -j ACCEPT")
    regels += [
        # Pingen mag, dat helpt bij het zoeken naar een storing en geeft niets prijs.
        f"-A {KETEN} -p icmp -j ACCEPT",
        f"-A {KETEN} -j DROP",
        "COMMIT",
        "",
    ]
    uit = _root(["iptables-restore", "--noflush"], invoer="\n".join(regels))
    if uit.returncode != 0:
        raise RuntimeError((uit.stderr or "").strip()[:200] or "firewall niet te zetten")

    # De keten aan de tunnel hangen, precies één keer. Dit is één regel en dus
    # niet half uit te voeren.
    _iptables("-D", "INPUT", "-i", WG_INTERFACE, "-j", KETEN)
    _iptables("-I", "INPUT", "1", "-i", WG_INTERFACE, "-j", KETEN, controleer=True)
    return True


def support_aan():
    """Zet ssh open op de tunnel. Vóór de DROP, anders heeft het geen zin."""
    firewall_opbouwen()
    _iptables("-I", KETEN, "2", "-p", "tcp", "--dport", str(SSH_POORT),
              "-j", "ACCEPT", controleer=True)
    return True


def support_uit():
    """Haalt die ene regel er weer uit. Meermaals aanroepen kan geen kwaad."""
    for _ in range(5):
        if not _iptables("-D", KETEN, "-p", "tcp", "--dport", str(SSH_POORT),
                         "-j", "ACCEPT"):
            break
    return True


def support_staat_open():
    uit = _root(["iptables", "-n", "-L", KETEN])
    if uit.returncode != 0:
        return False
    for regel in uit.stdout.splitlines():
        if "ACCEPT" in regel and f"dpt:{SSH_POORT}" in regel:
            return True
    return False


def stand():
    """Of de tunnel staat, en of de server er werkelijk doorheen antwoordt.

    Alleen kijken of de dienst draait is niet genoeg: hij draait ook prima als er
    aan de andere kant niets gebeurt. De laatste handshake is het enige dat zegt
    of er werkelijk contact is.
    """
    actief = _draai(["systemctl", "is-active", f"wg-quick@{WG_INTERFACE}"]).stdout.strip()
    uit = _root(["wg", "show", WG_INTERFACE, "latest-handshakes"]).stdout.strip()
    laatste = 0
    if uit:
        deel = uit.split()
        if len(deel) >= 2 and deel[-1].isdigit():
            laatste = int(deel[-1])
    adres = ""
    inhoud = _root(["cat", WG_CONF])
    if inhoud.returncode == 0:
        treffer = re.search(r"^Address = ([0-9.]+)", inhoud.stdout, re.M)
        if treffer:
            adres = treffer.group(1)
    return {"actief": actief == "active", "adres": adres,
            "laatste_handshake": laatste,
            "support_open": support_staat_open()}
