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
- Batterij koppeling mogelijk

---
> [!IMPORTANT]
> Alle functionele toepassingen zijn van SolarBuffer. Zonder een SolarBuffer installatie, heeft deze repository geen toepassing.
---
> [!NOTE]
> Geïnteresseerd in SolarBuffer, vraag er één aan via [SolarBuffer](https://www.solarbuffer.nl)
---

## 🧙‍♂️ Beginnen met SolarBuffer

Installatiehandleiding SolarBuffer Hub. Volg onderstaande stappen om SolarBuffer in te stellen. De installatie duurt gemiddeld **10–15 minuten**.

### 1.1 Hub verbinden met het wifi

De SolarBuffer-hub is een kleine computer (Raspberry Pi) die de besturing uitvoert. Bij eerste opstart zet hij een eigen wifi-netwerk op zodat jij hem kunt configureren.

**Hub opstarten**
1. Verbind de hub met een USB-C kabel (5V voeding). De overige aansluitingen op de hub zijn bedoeld voor de ontwikkelaar of toekomstige uitbreidingen en kunnen worden genegeerd.
2. Wacht ±30 seconden totdat de hub volledig is opgestart.

**Verbinden met het setup-wifi**

3. Ga op je telefoon of laptop naar de wifi-instellingen.
4. Maak verbinding met het wifi-netwerk:
   ```bash
   Netwerknaam: PI-SETUP
   Wachtwoord: SolarBuffer
   ```

**Wifi instellen via de browser**

5. Open een browser en ga naar:
   ```bash
   http://solarbuffer.local:80
   ```
6. Vul de naam en het wachtwoord van je eigen wifi-netwerk in en bevestig.
7. De hub probeert verbinding te maken en herstart automatisch. Je kunt deze pagina nu sluiten.

**Verbinding controleren**

Gelukt? Sluit je telefoon/laptop aan op hetzelfde wifi-netwerk dat je net hebt ingesteld en open:
```bash
http://solarbuffer.local:5001
```
Mislukt? Het wifi-netwerk **PI-SETUP** verschijnt opnieuw in de wifi-instellingen — begin dan opnieuw bij punt 3.

### 1.2 Welkomswizard & SolarBuffer koppelen

Na het openen van `solarbuffer.local:5001` start de installatie-wizard. Doorloop de volgende stappen:

1. **Account aanmaken** — kies een gebruikersnaam en wachtwoord.
2. **P1-meter koppelen** — druk op *Scannen* om de HomeWizard P1-meter automatisch te vinden in het wifi-netwerk. Zorg dat de **Lokale API** aanstaat in de HomeWizard-app (apparaat → tandwiel → Lokale API), anders wordt de P1-meter niet gevonden.
3. **SolarBuffer koppelen via Bluetooth** — stop de SolarBuffer in het stopcontact, druk op *Zoek via Bluetooth* en kies je SolarBuffer uit de gevonden lijst. Vul de naam en het wachtwoord van je wifi-netwerk in en klik op *Verbind SolarBuffer met netwerk*. Na ongeveer 15 seconden verschijnt de SolarBuffer automatisch in de lijst.
4. Sla de configuratie op en ga naar het dashboard.

> [!TIP]
> Houd de SolarBuffer dicht bij de hub tijdens het koppelen — Bluetooth heeft een beperkt bereik en dit is een eenmalige actie bij het installeren.

> [!TIP]
> Vul bij het koppelen de gegevens van je 2,4 GHz wifi-netwerk in — de SolarBuffer ondersteunt geen 5 GHz.

**Lukt Bluetooth niet?**
1. Download de Shelly-app — beschikbaar in de [App Store](https://apps.apple.com/nl/app/shelly-smart-control/id1660045967) (iPhone) of [Play Store](https://play.google.com/store/apps/details?id=cloud.shelly.smartcontrol&hl=nl&pli=1) (Android) — en maak een account aan.
2. Voeg de SolarBuffer toe als nieuw apparaat in de Shelly-app en volg de stappen daar om hem met je wifi-netwerk te verbinden.
3. Ga terug naar de SolarBuffer-wizard en druk op *Zoek op IP-adres* om de SolarBuffer alsnog automatisch te vinden.

### 1.3 Slimme stekker koppelen (optioneel, aanbevolen)

Een slimme stekker (power socket) geeft extra inzicht: je ziet live het vermogen van de boiler en de app kan de SolarBuffer nog beter aansturen. Ondersteunde merken: **HomeWizard** en **Shelly**.

Zonder slimme stekker heeft de SolarBuffer een sluipverbruik van ~3 W als de buffer niet actief is. De ingebouwde ventilator schakelt automatisch op basis van temperatuur — hij draait dus niet constant — maar de elektronica blijft wel stroom trekken zolang de stekker in het stopcontact zit. Met een slimme stekker schakelt de app de volledige stroom naar de SolarBuffer uit wanneer hij niet nodig is, waardoor ook dit sluipverbruik wegvalt.

**IP-adres vinden**
- **HomeWizard**: open de HomeWizard Energy-app → apparaat → tandwiel → LAN API → noteer het IP-adres.
- **Shelly**: open de Shelly-app → apparaat → instellingen → apparaatinfo → noteer het IP-adres.

**Koppelen in SolarBuffer**
1. Ga naar **Instellingen → Configuratie → SolarBuffers** en open het apparaat.
2. Vul het IP-adres van de slimme stekker handmatig in.
3. Sla de configuratie op.

> [!TIP]
> Geef de slimme stekker een vast IP-adres in je router zodat dit adres nooit verandert.

### 1.4 Toegang op afstand via Tailscale VPN (aanbevolen)

SolarBuffer draait lokaal op jouw wifi en is standaard niet bereikbaar van buiten je wifi-netwerk. Met **Tailscale** maak je een gratis en veilige VPN-verbinding zodat je de app ook onderweg kunt gebruiken.

**Hub koppelen**
1. Ga in SolarBuffer naar **Instellingen > Verbinding > Remote Toegang**.
2. Klik op **Verbinden** — er verschijnt een link.
3. Open de link en koppel de SolarBuffer Hub aan een Tailscale-account. Maak gratis een account aan als je er nog geen hebt.

**App op je telefoon**
4. Download de **Tailscale**-app via de App Store of Play Store.
5. Log in met hetzelfde account als in stap 3.
6. Bij het eerste gebruik vraagt je telefoon toestemming voor VPN-certificaten. Dit is veilig en noodzakelijk voor de werking.

**Verbinding gebruiken**

Zet Tailscale aan in de app en open SolarBuffer via:
```bash
http://solarbuffer:5001
```
Deze URL werkt overal ter wereld, zolang de Tailscale VPN-verbinding actief staat — ook onderweg of op een ander wifi-netwerk.

**Thuis vs. onderweg**

`http://solarbuffer.local:5001` werkt altijd, maar alleen thuis op hetzelfde wifi-netwerk als de Hub. `http://solarbuffer:5001` werkt overal, maar alleen zolang Tailscale actief is. Thuis werken beide adressen naast elkaar zodra Tailscale aanstaat.

Laat je Tailscale altijd aanstaan, dan kun je gewoon overal `http://solarbuffer:5001` gebruiken — ook thuis. Zet je Tailscale regelmatig uit, gebruik dan `http://solarbuffer.local:5001` als vaste link thuis.

> [!TIP]
> Als de SolarBuffer het enige apparaat is in jouw Tailscale-netwerk kun je de VPN gewoon altijd aan laten staan.

### 1.5 SolarBuffer op het beginscherm zetten (aanbevolen)

SolarBuffer werkt als een webapp. Door hem op het beginscherm te zetten opent de app volledig scherm, net als een gewone app.

Heb je Tailscale ingesteld (zie 1.4) en laat je de VPN-verbinding altijd aanstaan? Gebruik dan overal hieronder `http://solarbuffer:5001` in plaats van `solarbuffer.local:5001`, zodat de snelkoppeling ook buitenshuis werkt.

**iPhone (Safari)**
1. Open `solarbuffer.local:5001` in **Safari**.
2. Tik op het deel-icoon onderaan (vierkantje met pijl omhoog).
3. Kies **Zet op beginscherm**.
4. Bevestig met **Voeg toe**.

**Android (Chrome)**
1. Open `solarbuffer.local:5001` in **Chrome**.
2. Tik op de drie puntjes rechtsboven.
3. Kies **Toevoegen aan startscherm** of **App installeren**.
4. Bevestig met **Toevoegen**.

> [!TIP]
> Gebruik op iPhone altijd Safari — andere browsers ondersteunen het toevoegen aan het beginscherm niet.

---

✅ **Je bent klaar!** Sluit de stekker van de boiler in de SolarBuffer en open het dashboard op `solarbuffer.local:5001`. SolarBuffer begint nu automatisch te sturen op zonne-energie.

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
| `IMPORT_OFF_THRESHOLD` | Uitschakeldrempel import | `275` | W | Als de netafname boven deze waarde stijgt, begint SolarBuffer apparaten uit te schakelen. |
| `OFF_DELAY` | Uitschakelvertraging | `120` | s | Hoelang de uitschakeldrempel continu overschreden moet zijn voordat apparaten worden uitgeschakeld. Een hogere waarde voorkomt onnodige uitschakelcycli. |

### PID-regelaar neutrale zone

De PID-regelaar stuurt het vermogen van apparaten bij. Waarden binnen de neutrale zone worden als nul beschouwd, zodat de regelaar niet continu kleine aanpassingen maakt.

| Instelling | Label | Standaard | Eenheid | Beschrijving |
|---|---|---|---|---|
| `PID_NEUTRAL_LOW` | PID-neutraal laag | `-5` | W | Ondergrens van de neutrale zone. Vermogenswaarden boven deze grens én onder `PID_NEUTRAL_HIGH` worden genegeerd door de PID. |
| `PID_NEUTRAL_HIGH` | PID-neutraal hoog | `45` | W | Bovengrens van de neutrale zone. Vergroot het venster om stabielere aansturing te krijgen ten koste van nauwkeurigheid. |

> Voorbeeld: met standaardwaarden (`-5` tot `45`) geldt een gemeten vermogen van `20 W` als neutraal — de PID past niets aan.

| Instelling | Label | Standaard | Eenheid | Beschrijving |
|---|---|---|---|---|
| `PID_KI_ADJUST` | Regelsnelheid (Ki) | `0` | % | Past de Ki (integraalversterking) van de PID-regelaar procentueel aan, tussen `-20` en `20`. Hoger = sneller bijregelen, lager = rustiger. |

### Power socket

Instellingen voor apparaten die via een schakelbare stekker (bijv. Shelly Plug) worden aangestuurd.

| Instelling | Label | Standaard | Eenheid | Beschrijving |
|---|---|---|---|---|
| `POWER_SOCKET_DELAY` | Power socket startvertraging | `5` | s | Wachttijd nadat een socket is ingeschakeld voordat het systeem het apparaat als actief beschouwt. Geeft het aangesloten apparaat tijd om op te starten. |
| `POWER_SOCKET_HOLD_SECONDS` | Power socket nalooptijd | `60` | s | Hoelang een socket actief blijft nadat de regelaar voor het laatst heeft bepaald dat het apparaat nodig is. Voorkomt kort na elkaar in- en uitschakelen. |

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

---

## ☀️ Zonnevoorspelling

Op het dashboard staat een zonne-verwachtingskaart met een uurbalkengrafiek voor vandaag en morgen, gebaseerd op de verwachte zoninstraling (via [Open-Meteo](https://open-meteo.com)) op jouw locatie.

Locatie instellen via **Instellingen → Configuratie → Locatie**, op drie manieren:
- **Automatisch invullen** — bepaalt je locatie via je IP-adres.
- **Zoek op plaatsnaam of adres** — zoek je woonplaats of adres op.
- **Handmatig** — vul zelf de coördinaten in (bijv. `52.3676` en `4.9041`).

Zonder ingestelde locatie toont het dashboard een link om dit alsnog te doen in plaats van de verwachting.

## 🔌 Verbruikers

Naast de SolarBuffer zelf kun je losse energieverbruikers (denk aan een droger, wasmachine of laadpaal) toevoegen om hun vermogen op het dashboard te zien.

**Ondersteunde hardware**
- **Shelly** Plug S / Plug US / PM Mini / Pro PM — lokaal aangestuurd via de Shelly HTTP-API, geen cloud nodig.
- **HomeWizard** Wi-Fi Energy Socket / HWE-SDM — lokaal aangestuurd via de HomeWizard API v1.

**Toevoegen**
1. Ga naar **Instellingen → Accessories → Voeg verbruiker toe**.
2. Geef de verbruiker een herkenbare naam (bijv. *Boiler*).
3. Selecteer het type: **Shelly** of **HomeWizard**.
4. Vul het lokale IP-adres van de vermogensmeter in.
5. Kies een passend symbool en sla op.

**Zonnestroom meter**

Eén verbruiker kan worden gemarkeerd als zonnestroom meter. Zodra dat is gekoppeld, berekent SolarBuffer twee extra statistieken op het dashboard:
- **Zelfverbruik** — het deel van de opgewekte zonnestroom dat je zelf direct gebruikt.
- **Zelfvoorziening** — het deel van je totale verbruik dat door zonnepanelen is gedekt.

> [!TIP]
> Geef elke slimme stekker een vast (statisch) IP-adres in je router. SolarBuffer communiceert rechtstreeks op IP; wijzigt het adres, dan is het apparaat niet meer bereikbaar.

## 📈 Geschiedenisgrafieken

Ga naar het menu (drie streepjes linksboven op het dashboard) en kies **Grafieken** voor historische data van het netvermogen en de aangesloten verbruikers.

- **Netvermogen** — het verloop van import en teruglevering over de geselecteerde periode.
- **Per verbruiker** — vermogen of temperatuur per apparaat, afhankelijk van wat is ingesteld bij het toevoegen van de verbruiker.

Kies via de knoppen bovenin tussen **Dag**, **Week** en **Maand**.

## 🔋 Batterij koppelingen

SolarBuffer ondersteunt drie soorten thuisbatterijen, allemaal volledig lokaal aangestuurd zonder cloud-account: **HomeWizard HWE-BAT**, **Marstek Venus** en **Zendure SolarFlow**. SolarBuffer stuurt automatisch de laad- en ontlaadrechten zodat de batterij en de boiler samenwerken.

Instellen via **Instellingen → Configuratie → P1 & Batterij**: batterij koppelen aanzetten en het type kiezen.

**HomeWizard HWE-BAT**
1. Vul het IP-adres van de HWE-BAT in (maximaal 4 accu's).
2. Klik op **Koppelen** naast de accu en druk binnen 30 seconden op de knop op de HWE-BAT.
3. Klik op **Koppelen** bij **API-token P1 meter** en druk op de knop op de P1-meter — hiermee stuurt SolarBuffer de accu aan.

**Marstek Venus**
1. Zet de **Open API** aan in de Marstek-app en noteer de UDP-poort (standaard 30000).
2. Geef de accu een statisch IP-adres en vul dit in.
3. Stel het maximale vermogen in (standaard 2000 W).

De Marstek blijft normaal in zijn eigen Auto-modus draaien; alleen om het vermogen vast te zetten (boiler-vrijgave of stilstand) schrijft SolarBuffer een tijdschema in slot 0. Ontlaadt de accu terwijl hij zou moeten laden, zet dan **Omgekeerde +/−** uit.

**Zendure SolarFlow**
1. Zorg voor de nieuwste firmware. Ondersteund: SolarFlow 800 (Plus/Pro), 1600 AC+ en 2400 AC (Pro).
2. Geef de accu een statisch IP-adres en vul dit in.
3. Stel het maximale vermogen in (bijv. 800 W voor een SolarFlow 800).

SolarBuffer regelt de Zendure zelf op nul-op-de-meter via de P1-meting. Gebruik je een eigen meter met *slim matchen* in de Zendure-app, zet die functie dan uit, anders werken twee regelaars elkaar tegen.

**Prioriteit**
- **Batterij eerst** — de accu laadt ongestoord tot de ingestelde SoC-drempel (standaard 95%) voordat de SolarBuffer mag starten.
- **SolarBuffer eerst** — de boiler heeft voorrang; de batterij mag pas laden als de regelaar op 100% staat.

> [!TIP]
> Tijdens een legionellarun of een actief tijdschema blokkeert SolarBuffer het ontladen van de batterij automatisch.
