# SolarBuffer ☀️

SolarBuffer is een Python-app voor het aansturen van Shelly-apparaten en het uitlezen van P1-energiemeterdata via een Raspberry Pi.  
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
```bash
sudo apt update && sudo apt upgrade -y
```
#### Check Python
```bash
python3 --version
```
#### Installeer pip
```bash
sudo apt install python3-pip -y
pip3 --version
```

### 2. Repository clonen
```bash
git clone https://github.com/SolarBuffer/SolarBuffer.git
cd SolarBuffer/solarbuffer
```

### 3. Python virtual environment aanmaken
```bash
python3 -m venv venv
source venv/bin/activate
```

### 4. Dependencies installeren
```bash
pip3 install --upgrade pip
pip3 install simple-pid flask requests
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
