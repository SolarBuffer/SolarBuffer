# SolarBuffer ☀️

Ondersteunings Python-app voor SolarBuffer besturing en het uitlezen van P1-energiemeterdata via een Raspberry Pi.  
Met een webgebaseerde configuratie-wizard kun je snel je apparaten instellen en het systeem automatisch regelen via PID.

---

## 📦 Functies

- Lees real-time P1-energiemeterdata uit  
- Stuur SolarBuffer-apparaten aan (aan/uit en dimmen)  
- PID-gestuurde automatische regeling voor verbruik
- Tijdschema's
- Anti-Legionella
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
<img width="280" height="340" alt="image" src="https://github.com/user-attachments/assets/5812057f-362b-431b-89b4-bf4803eb36a3" />

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
<img width="355" height="413" alt="image" src="https://github.com/user-attachments/assets/bb9e093c-aca7-4fd9-b443-1f18d6b86406" />

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

---

## SolarBuffer — Expert instellingen
De expertmodus biedt toegang tot de parameters die bepalen hoe de regellogica reageert op vermogensschommelingen. Alle waarden worden opgeslagen in `config.json` onder de sleutel `expert_settings`.

### Inschakellogica (teruglevering)

Deze instellingen bepalen wanneer SolarBuffer apparaten **inschakelt** omdat er overtollige zonne-energie teruggeleverd wordt aan het net.

| Instelling | Label | Standaard | Eenheid | Beschrijving |
|---|---|---|---|---|
| `EXPORT_THRESHOLD` | Inschakeldrempel | `-50` | W | Zodra het gemeten vermogen onder deze waarde daalt (negatief = teruglevering), start SolarBuffer met het inschakelen van apparaten. Maak de waarde negatiever om later in te schakelen. |
| `EXPORT_DELAY` | Inschakelvertraging | `15` | s | Het aantal seconden dat de exportdrempel ononderbroken overschreden moet zijn voordat er daadwerkelijk wordt ingeschakeld. Voorkomt flapperen bij korte pieken. |

### Bevriezingslogica

Als een apparaat (bijv. een boiler) een hoge stand bereikt, kan SolarBuffer het "bevriezen" — het wordt dan niet verder verhoogd totdat de situatie verandert.

| Instelling | Label | Standaard | Eenheid | Beschrijving |
|---|---|---|---|---|
| `FREEZE_AT` | Bevriezen bij | `95` | % | Standpercentage waarbij een apparaat wordt bevroren. Bij `95` betekent dit: zodra het apparaat op 95% of hoger staat, wordt verdere verhoging gestopt. |
| `FREEZE_CONFIRM` | Bevestiging bevriezen | `5` | s | Hoelang de bevriescondtie stabiel moet zijn voordat de bevriezing daadwerkelijk wordt toegepast. |
| `IMPORT_UNFREEZE_THRESHOLD` | Vrijgave importdrempel | `200` | W | Als de netafname boven deze waarde stijgt terwijl een apparaat bevroren is, wordt het vrijgegeven zodat de regelaar het kan dimmen. |
| `UNFREEZE_DELAY` | Vrijgavevertraging | `5` | s | Hoelang de importdrempel overschreden moet zijn voordat een bevriezing wordt losgelaten. |

### Uitschakellogica

Deze instellingen bepalen wanneer SolarBuffer apparaten **uitschakelt** omdat er te veel van het net wordt afgenomen.

| Instelling | Label | Standaard | Eenheid | Beschrijving |
|---|---|---|---|---|
| `IMPORT_OFF_THRESHOLD` | Uitschakeldrempel import | `250` | W | Als de netafname boven deze waarde stijgt, begint SolarBuffer apparaten uit te schakelen. |
| `OFF_DELAY` | Uitschakelvertraging | `120` | s | Hoelang de uitschakeldrempel continu overschreden moet zijn voordat apparaten worden uitgeschakeld. Een hogere waarde voorkomt onnodige uitschakelcycli. |

### PID-regelaar neutrale zone

De PID-regelaar stuurt het vermogen van apparaten bij. Waarden binnen de neutrale zone worden als nul beschouwd, zodat de regelaar niet continu kleine aanpassingen maakt.

| Instelling | Label | Standaard | Eenheid | Beschrijving |
|---|---|---|---|---|
| `PID_NEUTRAL_LOW` | PID-neutraal laag | `-5` | W | Ondergrens van de neutrale zone. Vermogenswaarden boven deze grens én onder `PID_NEUTRAL_HIGH` worden genegeerd door de PID. |
| `PID_NEUTRAL_HIGH` | PID-neutraal hoog | `45` | W | Bovengrens van de neutrale zone. Vergroot het venster om stabielere aansturing te krijgen ten koste van nauwkeurigheid. |

> Voorbeeld: met standaardwaarden (`-5` tot `45`) geldt een gemeten vermogen van `20 W` als neutraal — de PID past niets aan.

### Power socket

Instellingen voor apparaten die via een schakelbare stekker (bijv. Shelly Plug) worden aangestuurd.

| Instelling | Label | Standaard | Eenheid | Beschrijving |
|---|---|---|---|---|
| `POWER_SOCKET_DELAY` | Power socket startvertraging | `5` | s | Wachttijd nadat een socket is ingeschakeld voordat het systeem het apparaat als actief beschouwt. Geeft het aangesloten apparaat tijd om op te starten. |
| `POWER_SOCKET_HOLD_SECONDS` | Power socket nalooptijd | `600` | s | Hoelang een socket actief blijft nadat de regelaar voor het laatst heeft bepaald dat het apparaat nodig is. Voorkomt kort na elkaar in- en uitschakelen. |

### Boost

| Instelling | Label | Standaard | Eenheid | Beschrijving |
|---|---|---|---|---|
| `BOOST_DURATION` | Boost duur | `900` | s | Hoelang een apparaat op 100% vermogen draait wanneer de boostknop wordt ingedrukt. Na deze tijd keert het apparaat terug naar normaal geregeld gedrag. |

---

## MQTT / Home Assistant

Optionele integratie met een MQTT broker, bijvoorbeeld voor gebruik met Home Assistant auto-discovery.

| Instelling | Label | Standaard | Beschrijving |
|---|---|---|---|
| `mqtt_enabled` | MQTT inschakelen | `false` | Schakel de MQTT-integratie in of uit. |
| `mqtt_broker` | MQTT Broker | *(leeg)* | IP-adres of hostnaam van de MQTT broker (bijv. het adres van Home Assistant). |
| `mqtt_port` | Poort | `1883` | Poortnummer van de MQTT broker. Standaard `1883` (TCP). |
| `mqtt_username` | Gebruikersnaam | *(leeg)* | Gebruikersnaam voor authenticatie. Laat leeg als de broker geen authenticatie vereist. |
| `mqtt_password` | Wachtwoord | *(leeg)* | Wachtwoord voor authenticatie. Laat leeg als de broker geen authenticatie vereist. |
| `mqtt_topic_prefix` | Topic prefix | `solarbuffer` | Basisnaam voor alle MQTT topics. Bijv. `solarbuffer/status`. |
| `mqtt_ha_discovery` | HA Auto-discovery | `true` | Registreert SolarBuffer-entiteiten automatisch in Home Assistant via het MQTT discovery-protocol. |
| `mqtt_publish_interval` | Publiceer-interval | `30` | s — Hoe vaak (in seconden) de status wordt gepubliceerd op de MQTT topics. Minimaal `5`, maximaal `3600`. |

## Tailscale
Koppel SolarBuffer met TailScale om remote toegang te krijgen tot het dashboard. TailScale is een gratis VPN.

---

## Zonnevoorspelling
Zonnevoorspelling wordt gedaan op basis van locatie
