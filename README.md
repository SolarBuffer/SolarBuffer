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
- FUTURE, batterij koppeling mogelijk

---
> [!IMPORTANT]
> Alle functionele toepassingen zijn van SolarBuffer. Zonder een SolarBuffer installatie, heeft deze repository geen toepassing.
---
> [!NOTE]
> Geïnteresseerd in SolarBuffer, vraag er één aan via [SolarBuffer](https://www.solarbuffer.nl)
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

## Expert Settings
Uitleg over alle beschikbare parameters in de Expert Settings:
test

## MQTT

## Tailscale

