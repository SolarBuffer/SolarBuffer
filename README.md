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
pip install simple-pid flask requests zeroconf
```

### 5. Config.json aanmaken
Handmatig moet er een config.json file komen om configuratie in op te slaan.
```bash
cd /home/solarbuffer/SolarBuffer
cp solarbuffer/config.voorbeeld.json solarbuffer/config.json
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

```bash
from flask import Flask, request, render_template_string
import subprocess
import threading
import time

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WiFi Setup - SolarBuffer</title>

<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap');

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'Inter', sans-serif;
    background: hsl(30, 25%, 97%);
    color: hsl(220, 20%, 14%);
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
}

.container {
    width: 100%;
    max-width: 420px;
    background: white;
    border: 1px solid hsl(30, 15%, 88%);
    border-radius: 0.75rem;
    box-shadow: 0 10px 40px -10px hsla(32, 95%, 52%, 0.15);
    padding: 2rem;
}

.header {
    text-align: center;
    margin-bottom: 2rem;
}

.header .icon {
    font-size: 2rem;
    color: hsl(32, 95%, 52%);
}

.header h1 {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 700;
    font-size: 1.6rem;
    margin-top: 0.25rem;
}

.header h1 .solar {
    background: linear-gradient(135deg, hsl(32, 95%, 52%), hsl(40, 100%, 60%));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.header p {
    color: hsl(220, 10%, 46%);
    font-size: 0.85rem;
    margin-top: 0.25rem;
}

form {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
}

form div {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
}

label {
    font-weight: 600;
    font-size: 0.95rem;
}

input {
    width: 100%;
    padding: 0.65rem 0.75rem;
    border: 1px solid hsl(30, 15%, 88%);
    border-radius: 0.5rem;
    font-size: 1rem;
    font-family: 'Inter', sans-serif;
    transition: all 0.2s ease;
}

input:focus {
    border-color: hsl(32, 95%, 52%);
    box-shadow: 0 0 0 3px hsla(32, 95%, 52%, 0.15);
    outline: none;
}

button {
    width: 100%;
    background: hsl(32, 95%, 52%);
    color: white;
    font-weight: 600;
    font-size: 1rem;
    padding: 0.75rem;
    border: none;
    border-radius: 0.5rem;
    cursor: pointer;
    transition: all 0.2s ease;
}

button:hover {
    background: hsl(32, 85%, 45%);
    transform: translateY(-1px);
}

button:active {
    transform: translateY(0);
}

.message {
    text-align: center;
    font-size: 0.9rem;
    min-height: 1.2rem;
    white-space: pre-wrap;
    word-break: break-word;
}

.success {
    color: hsl(140, 60%, 40%);
}

.error {
    color: hsl(0, 75%, 60%);
}
</style>
</head>

<body>

<div class="container">
    <div class="header">
        <div class="icon">📡</div>
        <h1><span class="solar">Solar</span>Buffer</h1>
        <p>Configureer uw WiFi netwerk</p>
    </div>

    <div class="message {{ status_class }}">
        {{ message or "" }}
    </div>

    <form method="POST">
        <div>
            <label>WiFi naam (SSID)</label>
            <input name="ssid" required>
        </div>

        <div>
            <label>WiFi wachtwoord</label>
            <input name="password" type="password">
        </div>

        <button type="submit">Verbinden met netwerk</button>
    </form>
</div>

</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SolarBuffer</title>

<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap');

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'Inter', sans-serif;
    background: hsl(30, 25%, 97%);
    color: hsl(220, 20%, 14%);
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
}

.container {
    width: 100%;
    max-width: 420px;
    background: white;
    border: 1px solid hsl(30, 15%, 88%);
    border-radius: 0.75rem;
    box-shadow: 0 10px 40px -10px hsla(32, 95%, 52%, 0.15);
    padding: 2rem;
    text-align: center;
}

.icon {
    font-size: 2rem;
    color: hsl(32, 95%, 52%);
    margin-bottom: 0.75rem;
}

h1 {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 700;
    font-size: 1.6rem;
    margin-bottom: 0.5rem;
}

h1 .solar {
    background: linear-gradient(135deg, hsl(32, 95%, 52%), hsl(40, 100%, 60%));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

h3 {
    margin-top: 1rem;
    margin-bottom: 0.5rem;
    color: hsl(140, 60%, 40%);
}

p {
    color: hsl(220, 10%, 46%);
    font-size: 0.95rem;
}
</style>
</head>

<body>
<div class="container">
    <div class="icon">✅</div>
    <h1><span class="solar">Solar</span>Buffer</h1>
    <h3>WiFi opgeslagen</h3>
    <p>SolarBuffer herstart over enkele seconden...</p>
</div>
</body>
</html>
"""

def delayed_reboot(delay=3):
    def _reboot():
        time.sleep(delay)
        subprocess.Popen(["systemctl", "reboot"])
    threading.Thread(target=_reboot, daemon=True).start()

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        ssid = request.form["ssid"].strip()
        password = request.form["password"]

        try:
            subprocess.run(
                ["nmcli", "connection", "delete", "customer-wifi"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            subprocess.run(
                [
                    "nmcli", "connection", "add",
                    "type", "wifi",
                    "ifname", "wlan0",
                    "con-name", "customer-wifi",
                    "ssid", ssid
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "wifi-sec.key-mgmt",
                    "wpa-psk"
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "wifi-sec.psk",
                    password
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "connection.autoconnect",
                    "yes"
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "connection.autoconnect-priority",
                    "100"
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "connection.autoconnect-retries",
                    "0"
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "PI-SETUP",
                    "connection.autoconnect",
                    "no"
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "PI-SETUP",
                    "connection.autoconnect-priority",
                    "-100"
                ],
                check=True
            )

            subprocess.run(
                ["nmcli", "connection", "down", "PI-SETUP"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            result = subprocess.run(
                ["nmcli", "connection", "up", "customer-wifi"],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                delayed_reboot(3)
                return SUCCESS_HTML

            return render_template_string(
                HTML,
                message=f"WiFi opgeslagen, maar verbinden mislukte:\n{result.stderr}",
                status_class="error"
            )

        except Exception as e:
            return render_template_string(
                HTML,
                message=f"Fout:\n{e}",
                status_class="error"
            )

    return render_template_string(HTML, message="", status_class="")

app.run(host="0.0.0.0", port=80)
```
