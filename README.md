# SolarBuffer ☀️

Ondersteunigs Python-app voor SolarBuffer besturing en het uitlezen van P1-energiemeterdata via een Raspberry Pi.  
Met een webgebaseerde configuratie-wizard kun je snel je apparaten instellen en het systeem automatisch regelen via PID.

---

## 📦 Functies

- Lees real-time P1-energiemeterdata uit  
- Stuur Shelly-apparaten aan (aan/uit en dimmen)  
- PID-gestuurde automatische regeling van verbruik  
- Webgebaseerde configuratie wizard  
- Real-time status dashboard  
- Forceer aan/uit modus voor apparaten  
- Logging en foutafhandeling  

---

## ⚡ Installatie

### 1. Raspberry Pi updaten
Update eerst je Raspberry Pi zodat alle pakketten up-to-date zijn. Dit zorgt ervoor dat je systeem stabiel draait en de nieuwste beveiligingsupdates heeft.
```bash
sudo apt update && sudo apt upgrade -y
```
#### Check Python
Controleer of je Python 3.14 of hoger hebt:
```bash
python3 --version
```
Zo niet, update Python dan naar een recente versie.

### 2. Repository clonen
Download de SolarBuffer-code van GitHub en ga naar de juiste map
```bash
git clone https://github.com/SolarBuffer/SolarBuffer.git
cd SolarBuffer/solarbuffer
```
Hier staat zowel app.py als config.json.

### 3. Python virtual environment aanmaken
Om te zorgen dat alle Python-pakketten netjes geïsoleerd zijn, maken we een virtuele omgeving:
```bash
python3 -m venv venv
source venv/bin/activate
```

### 4. Dependencies installeren
Upgrade pip en installeer de benodigde pakketten:
```bash
pip install --upgrade pip
pip install simple-pid flask requests
```
### 5. Automatisch starten bij opstart
#### Maak nieuw service bestand
```bash
sudo nano /etc/systemd/system/solarbuffer.service
```

#### Voeg de volgende gegevens toe
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
#### Sla het bestand op Cntrl+O, Enter, Cntrl+X

#### Herlaad systemd en start de service
```bash
sudo systemctl daemon-reload
sudo systemctl enable solarbuffer.service
sudo systemctl start solarbuffer.service
```

#### Check de status
```bash
sudo systemctl status solarbuffer.service
```
