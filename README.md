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
### 2. Repository clonen
```bash
git clone https://github.com/SolarBuffer/SolarBuffer.git
cd SolarBuffer/solarbuffer
```
