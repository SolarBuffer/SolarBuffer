# SolarBuffer ☀️

Ondersteunings Python-app voor SolarBuffer besturing en het uitlezen van P1-energiemeterdata via een Raspberry Pi.  
Met een webgebaseerde configuratie-wizard kun je snel je apparaten instellen en het systeem automatisch regelen via PID.

---

## 📦 Functies

- Lees real-time P1-energiemeterdata uit  
- Stuur SolarBuffer-apparaten aan (aan/uit en dimmen)  
- PID-gestuurde automatische regeling voor verbruik  
- Webgebaseerde configuratie wizard  
- Real-time status dashboard  

---
> [!IMPORTANT]
> Alle functionele toepassingen zijn van SolarBuffer. Zonder een SolarBuffer installatie, heeft deze repository geen toepassing.
---
> [!NOTE]
> Geïnteresseerd in SolarBuffer, vraag er één aan via [SolarBuffer](https://www.solarbuffer.nl)
---

## ⚡ Installatie

### 1. Raspberry Pi updaten
Update eerst je Raspberry Pi zodat alle pakketten up-to-date zijn. Dit zorgt ervoor dat je systeem stabiel draait en de nieuwste beveiligingsupdates heeft.
```bash
sudo apt update && sudo apt upgrade -y
```
#### 1.1 Check Python
Controleer of je Python 3.14 of hoger hebt.
```bash
python3 --version
```
Zo niet, update Python dan naar een recente versie.

### 2. Repository clonen
Download de SolarBuffer-code van GitHub en ga naar de juiste map.
```bash
git clone https://github.com/SolarBuffer/SolarBuffer.git
```
Hier staat zowel app.py als config.json.

### 3. Python virtual environment aanmaken
Om te zorgen dat alle Python-pakketten netjes geïsoleerd zijn, maken we een virtuele omgeving:
```bash
python3 -m venv venv
source venv/bin/activate
```

### 4. Dependencies installeren
Upgrade pip en installeer de benodigde pakketten.
```bash
pip install --upgrade pip
pip install simple-pid flask requests zeroconf
```

### 5. Config.json & audit.log aanmaken
Handmatig moet er een config.json en audit.log file komen om configuratie in op te slaan.
```bash
cd /home/solarbuffer/SolarBuffer
cp solarbuffer/tempfiles/config.voorbeeld.json solarbuffer/config.json
cp solarbuffer/tempfiles/audit.voorbeeld.log solarbuffer/audit.log
```
---


## Auto Boot
### 1. Maak nieuw service bestand
Configureer een service bestand in de system files om automatisch de python script te starten tijdens boot.
```bash
sudo nano /etc/systemd/system/solarbuffer.service
```

### 2. Voeg de volgende gegevens toe
```bash
[Unit]
Description=SolarBuffer Service
After=network.target

[Service]
User=solarbuffer
WorkingDirectory=/home/solarbuffer/SolarBuffer/solarbuffer
ExecStart=/home/solarbuffer/venv/bin/python3 app.py
Restart=always

[Install]
WantedBy=multi-user.target
```
### 3. Sla het bestand op Cntrl+O, Enter, Cntrl+X, Enter

### 4. Herlaad systemd en start de service
```bash
sudo systemctl daemon-reload
sudo systemctl enable solarbuffer.service
sudo systemctl start solarbuffer.service
```

### 5. Check de status
```bash
sudo systemctl status solarbuffer.service
```
---
> [!TIP]
> Om volledig up to date te blijven voer het volgende uit

## Auto Git Pull
### 1. Open cron
```bash
crontab -e
```
### 2. Auto update command
Elke 24 uur wordt er gecontroleerd of er nieuwe updates beschikbaar zijn via de command
```bash
0 3 * * * cd /home/solarbuffer/SolarBuffer && git pull && sudo systemctl restart solarbuffer
```
Nu wordt er elke nacht om 03:00 uur 's nachts controleert voor nieuwe updates en uitgevoerd indien er een update heeft plaats gevonden.

---

## Hotspot voor WiFi 
Om zonder beeldscherm automatisch WiFi in te stellen word tijdens de eerste boot een Hotspot gestart om de WiFi te configureren via een browser op adres 10.42.0.1
### 1. Controleer of deze paden aanwezig zijn.
```bash
whoami
ls -l /home/solarbuffer/SolarBuffer/solarbuffer/wifi.py
ls -l /home/solarbuffer/venv/bin/python3
```

### 2. Maak een portal service aan in de system files
```bash
sudo nano /etc/systemd/system/solarbuffer-portal.service
```

Hierin moeten de volgende parameters
```bash
[Unit]
Description=SolarBuffer provisioning portal
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
User=root
WorkingDirectory=/home/solarbuffer
ExecStart=/home/solarbuffer/venv/bin/python3 /home/solarbuffer/SolarBuffer/solarbuffer/wifi.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```
Opslaan met Cntrl + O, Enter, Cntrl + X, Enter

### 3. Maak de provisioning manager
Creeer een nieuw bestand
```bash
sudo nano /usr/local/bin/provisioning-manager.sh
```
Hierin moeten de volgende parameters
```bash
#!/usr/bin/env bash
set -e

WIFI_IF="wlan0"
CUSTOMER_CONN="customer-wifi"
SETUP_CONN="PI-SETUP"

customer_profile_exists() {
    nmcli -t -f NAME connection show | grep -qx "${CUSTOMER_CONN}"
}

is_customer_connected() {
    nmcli -t -f NAME,DEVICE,STATE connection show --active | grep -q "^${CUSTOMER_CONN}:${WIFI_IF}:activated$"
}

try_customer_wifi() {
    nmcli connection up "${CUSTOMER_CONN}" >/dev/null 2>&1 || true
    sleep 10
}

start_setup_mode() {
    nmcli connection up "${SETUP_CONN}" >/dev/null 2>&1 || true
    systemctl start solarbuffer-portal.service
}

stop_setup_mode() {
    systemctl stop solarbuffer-portal.service >/dev/null 2>&1 || true
    nmcli connection down "${SETUP_CONN}" >/dev/null 2>&1 || true
}

main() {
    if customer_profile_exists; then
        try_customer_wifi
    fi

    if is_customer_connected; then
        stop_setup_mode
    else
        start_setup_mode
    fi
}

main
```
Daarna exectutable maken:
``` bash
sudo chmod +x /usr/local/bin/provisioning-manager.sh
```
### 4. Maak provisioning service
``` bash
sudo nano /etc/systemd/system/solarbuffer-provisioning.service
```

Hierin moeten de volgende parameters:
``` bash
[Unit]
Description=SolarBuffer provisioning manager
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/provisioning-manager.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

### 5. Maak het hotspotprofiel aan PI-SETUP
```bash
sudo nmcli connection add type wifi ifname wlan0 con-name PI-SETUP ssid PI-SETUP
sudo nmcli connection modify PI-SETUP 802-11-wireless.mode ap
sudo nmcli connection modify PI-SETUP 802-11-wireless.band bg
sudo nmcli connection modify PI-SETUP wifi-sec.key-mgmt wpa-psk
sudo nmcli connection modify PI-SETUP wifi-sec.psk "SolarBuffer"
sudo nmcli connection modify PI-SETUP ipv4.method shared
sudo nmcli connection modify PI-SETUP ipv6.method ignore
sudo nmcli connection modify PI-SETUP connection.autoconnect no
sudo nmcli connection modify PI-SETUP connection.autoconnect-priority -100
```

### 6. Services laden en provisioning aanzetten
``` bash
sudo systemctl daemon-reload
sudo systemctl enable solarbuffer-provisioning.service
```
### 7. Handmatig testen
```bash
sudo systemctl start solarbuffer-portal.service
sudo systemctl status solarbuffer-portal.service
```
Als dit faalt check de logs
```bash
sudo journalctl -u solarbuffer-portal.service -n 50 --no-pager
```

### 8. Provisioning testen
Zorg dat de ''customer-wifi nog niet bestaat
```bash
sudo nmcli connection delete customer-wifi
```
Start nu provisioning
```bash
sudo systemctl restart solarbuffer-provisioning.service
```
Controleer
```bash

nmcli connection show --active
sudo systemctl status solarbuffer-provisioning.service
sudo systemctl status solarbuffer-portal.service
```
Nu moet te zien zijn dat PI-SETUP en solarbuffer-portal.service actief zijn

### 9. Reboot test
Als alles goed doorlopen is kan de PI een reboot krijgen
```bash
sudo reboot
```
Na de reboot kan je dit checken
```bash
nmcli connection show --active
systemctl status solarbuffer-provisioning.service
systemctl status solarbuffer-portal.service
```

### 10. Setup
Als je verbonden bent met de PI-SETUP hotspot kan je de WiFi instellingen doen via het voglende adres
```bash
10.42.0.1:80
of
solarbuffer.local:80
```

---

## 🧙‍♂️ Beginnen met SolarBuffer
Installatiehandleiding SolarBuffer Hub

Volg onderstaande stappen om de SolarBuffer Hub correct te installeren en te configureren.
### Stap 1 - Koppel de SolarBuffer aan het netwerk
De SolarBuffer kan via een app koppeld worden aan het thuisnetwerk. Ga naar de App Store [IOS]([https://www.solarbuffer.nl](https://apps.apple.com/nl/app/shelly-smart-control/id1660045967)) of [Android]([https://www.solarbuffer.nl](https://play.google.com/store/apps/details?id=cloud.shelly.smartcontrol&hl=nl&pli=1)) en download de Shelly app.

Maak een account aan en koppel.

Sluit de SolarBuffer aan op een voeding en koppel via de Shelly App de SolarBuffer aan het netwerk. Na het configureren zorg ervoor dat de shelly een static ip krijgt. De app zou hierna verwijderd kunnen worden, want deze heeft geen verdere toepassing meer.

### Stap 2 – Hub aansluiten op voeding
Sluit de SolarBuffer Hub aan op de stroomvoorziening.
Wacht vervolgens ongeveer 2 minuten totdat de hub volledig is opgestart.

### Stap 3 – Verbinden met het setup-wifi netwerk
Maak verbinding met het wifi-netwerk:
```bash
Netwerknaam: PI-SETUP
Wachtwoord: SolarBuffer
```

### Stap 4 – Thuisnetwerk instellen
Open een willekeurige webbrowser en ga naar:
```bash
solarbuffer.local:80
```
Vul hier de wifi-gegevens van je thuisnetwerk in.
Na het opslaan start de hub automatisch opnieuw op.
Wacht opnieuw ongeveer 2 minuten tot de hub weer online is.

### Stap 5 – Controleren of de hub verbonden is
Open opnieuw een webbrowser en ga naar:
http://solarbuffer.local:5001
Verschijnt er een loginpagina? Dan is de hub correct verbonden met je netwerk ✅
(Als er geen login pagina verschijnt, controleer op de hotspot weer beschikbaar is, dit kan betekenen dat er een fout 
is gemaakt bij het instellen van de WiFi)

Log in met:
```bash
Gebruikersnaam: solarbuffer
Wachtwoord: solarbuffer
```
### Stap 6 – Configuratie via de wizard
Na het inloggen kom je in de configuratiewizard.
De hub voert automatisch een autoscan uit om compatibele apparaten te vinden.
Indien nodig kun je apparaten ook handmatig toevoegen via het IP-adres.

### Stap 7 – Optioneel: Power Module koppelen
Je kunt optioneel een Power Module koppelen aan een apparaat, bijvoorbeeld:
- HomeWizard Energy Socket
- Shelly Smart Plug
- Shelly PM Mini Gen3

### Stap 8 – Prioriteiten instellen
Wanneer meerdere apparaten worden gevonden:
Stel de prioriteit correct in.
Apparaten met prioriteit 1 start als eerste
Daarna volgen prioriteit 2, 3, enz.
Dit bepaalt de volgorde van inschakelen en uitschakelen.

### Stap 9 – Configuratie opslaan
Sla de configuratie op.
De installatie is nu voltooid en je kunt direct starten met het gebruik van de SolarBuffer Hub 🚀


