# SolarBuffer rescue-mechanisme

Zelfherstel voor SolarBuffer-kastjes in het veld. Als de app na een update
blijft crashen, kan de klant de fix niet meer via de webinterface binnenhalen
— de update-knop zit immers in de app zelf. Dit mechanisme draait **buiten**
de app om en lost dat op zonder tussenkomst van de klant.

## Hoe het werkt

Een systemd-timer draait elke 10 minuten `solarbuffer-rescue.sh`:

1. **App draait normaal** → het script onthoudt de huidige git-commit als
   "laatst bekende goede versie" (pas nadat de app minimaal 5 minuten
   stabiel draait) en doet verder niets.
2. **App-service staat op `failed`** (crash-loop opgegeven door systemd) →
   het script doet `git pull` en herstart de service. Staat de fix al op
   GitHub, dan herstelt het kastje zichzelf dus binnen ±10 minuten na je push.
3. **Nog steeds kapot na de pull** → rollback (`git reset --hard`) naar de
   laatst bekende goede versie en opnieuw herstarten. De klant draait dan
   weer op de oude, werkende versie totdat jij de fix gepusht hebt.

Alles wordt gelogd in `/var/log/solarbuffer-rescue.log`.

## Installatie (op de Pi)

```bash
cd /home/solarbuffer/SolarBuffer/deploy
sudo bash install-rescue.sh
```

## Vereiste in solarbuffer.service

De rescue grijpt pas in wanneer systemd de service als `failed` markeert.
Met alleen `Restart=always` blijft een crash-loop eindeloos doorgaan en
gebeurt dat nooit. Zet daarom in `/etc/systemd/system/solarbuffer.service`
(zie `solarbuffer.service.example`):

```ini
[Unit]
StartLimitIntervalSec=120
StartLimitBurst=6

[Service]
Restart=always
RestartSec=5
```

Na aanpassen: `sudo systemctl daemon-reload && sudo systemctl restart solarbuffer`.

## Testen

```bash
# Forceer een failed-status (6 keer snel achter elkaar laten crashen kan ook):
sudo systemctl stop solarbuffer
sudo systemctl start solarbuffer-rescue.service   # rescue direct draaien
cat /var/log/solarbuffer-rescue.log
```

Let op: een gestopte (`inactive`) service is geen `failed` service — de
rescue herstart alleen wat écht gecrasht is, dus een bewuste
`systemctl stop` blijft gewoon uit staan.
