SolarBuffer
SolarBuffer is een Python-gebaseerde applicatie voor het beheren van slimme Shelly-apparaten en het uitlezen van P1-energiedata via een Raspberry Pi. De app regelt automatisch de helderheid van je apparaten op basis van je energieproductie en -consumptie met behulp van een PID-controller.
📦 Functies
Lees P1 energiemeter gegevens uit
Stuur Shelly slimme apparaten aan op basis van energiestromen
PID-gestuurde automatische regeling
Webgebaseerde configuratie via een Wizard
Real-time status en bediening van apparaten
Force-on/force-off overrides
Eenvoudige herconfiguratie van apparaten via webinterface
⚡ Installatie op een Raspberry Pi
Update je Raspberry Pi
sudo apt update && sudo apt upgrade -y
Clone de repository
cd ~
git clone https://github.com/SolarBuffer/SolarBuffer.git
cd SolarBuffer/solarbuffer
Update Python (optioneel, afhankelijk van je versie)
Controleer je Python-versie:
python3 --version
Update naar bijvoorbeeld Python 3.14 als nodig.
Installeer een virtual environment
sudo apt install python3-venv -y
python3 -m venv venv
source venv/bin/activate
Installeer vereiste Python-pakketten
pip install --upgrade pip
pip install simple-pid flask requests
