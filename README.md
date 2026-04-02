# SolarBuffer ☀️

Ondersteunigs Python-app voor SolarBuffer besturing en het uitlezen van P1-energiemeterdata via een Raspberry Pi.  
Met een webgebaseerde configuratie-wizard kun je snel je apparaten instellen en het systeem automatisch regelen via PID.

---

## 📦 Functies

- Lees real-time P1-energiemeterdata uit  
- Stuur SolarBuffer-apparaten aan (aan/uit en dimmen)  
- PID-gestuurde automatische regeling van verbruik  
- Webgebaseerde configuratie wizard  
- Real-time status dashboard  
- Forceer aan/uit modus voor apparaten   

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
pip install simple-pid flask requests
```

### 5. Config.json aanmaken
Handmatig moet er een config.json file komen om configuratie in op te slaan.
```bash
cd /home/{jouw_pi}/SolarBuffer
cp config.voorbeeld.json config.json
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
User={jouw_pi}
WorkingDirectory=/home/{jouw_pi}/SolarBuffer/solarbuffer
ExecStart=/home/{jouw_pi}/venv/bin/python3 app.py
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
0 3 * * * * cd /home/pi/SolarBuffer && git pull && sudo systemctl restart solarbuffer
```
Nu wordt er elke nacht om 03:00 uur 's nachts controleert voor nieuwe updates en uitgevoerd indien er een update heeft plaats gevonden.

---

## 🧙‍♂️ Wizard
Als de SolarBuffer service draait dienen de parameters via de webbrowser geconfigureerd te worden.
```bash
solarbuffer.local:5001
of
IP-ADRESS:5001
```
Login op de webbrowser
```bash
Username: solarbuffer
Password: solarbuffer
```
Na het inloggen kom je in het configuratiescherm, hierin worden de IP-adressen van de HomeWizard P1-meter ingevuld en de IP-adressen van de Shelly-devices (5 max). Later kan de configuratie altijd nog gewijzigd worden.
