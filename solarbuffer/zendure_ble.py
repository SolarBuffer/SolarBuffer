"""BLE-provisioning voor Zendure legacy-apparaten (Hyper 2000, Hub1200, Hub2000,
Ace1500/AIO2400) — apparaten die zenSDK (lokale HTTP-API) niet ondersteunen en
alleen via Zendure's cloud-MQTT-broker werken.

In plaats van een DNS-omleiding op te zetten om die cloud-verbinding te
onderscheppen, stuurt dit de eigen lokale broker (SolarBuffer Hub) rechtstreeks
naar het apparaat via Bluetooth — hetzelfde commando dat het apparaat ook van de
Zendure-app krijgt bij het instellen van wifi.

UUIDs en payload-formaat overgenomen uit de community-tool solarflow-bt-manager:
https://github.com/reinhard-brandstaedter/solarflow-bt-manager
(src/solarflow-bt-manager.py). Dit is UNGEVERIFIEERD tegen een echte Hyper 2000 —
de scan-functie (alleen lezen, geen verbinding) is veilig te testen; de
schrijffunctie (provision_broker) stuurt een write-commando naar het apparaat en
moet eerst voorzichtig tegen een los/niet-actief apparaat getest worden.
"""
import asyncio

from bleak import BleakClient, BleakScanner

ZENDURE_GATT_SERVICE_UUID = "0000a002-0000-1000-8000-00805f9b34fb"
ZENDURE_CMD_CHAR_UUID = "0000c304-0000-1000-8000-00805f9b34fb"
ZENDURE_NOTIFY_CHAR_UUID = "0000c305-0000-1000-8000-00805f9b34fb"

# Volledige supported-devices-lijst uit solarflow-bt-manager. Nuttig voor twee
# dingen: (1) referentie welke apparaten dit BLE-provisioningpad ondersteunen
# (SolarFlow 800 dus ook, naast de echte legacy-apparaten), en (2) later nodig
# voor het samenstellen van de MQTT-topics, die volgens LibreZen de vorm
# /<PRODUCT_ID>/<DEVICE_ID>/# hebben.
ZENDURE_PRODUCT_IDS = {
    "73bkTV": "Hub1200",
    "A8yh63": "Hub2000",
    "yWF7hV": "AIO2400",
    "ja72U0ha": "Hyper2000",
    "8bM93H": "ACE 1500",
    "B1NHMC": "SolarFlow 800",
}
ZENDURE_HYPER2000_PRODUCT_ID = "ja72U0ha"

# Fallback voor het geval een apparaat wél een leesbare naam uitzendt (niet
# bevestigd voor de Hyper 2000 zelf — die adverteert alleen zijn MAC-adres als
# naam, en is te herkennen aan ZENDURE_GATT_SERVICE_UUID hierboven).
ZENDURE_KNOWN_NAMES = ("zendure", "solarflow", "hyper", "hub1200", "hub2000", "ace1500", "aio2400")

DEFAULT_TIMEOUT = 10.0


class ZendureBleError(Exception):
    pass


async def _scan_zendure_devices(duration):
    found = {}

    def on_detect(device, adv):
        name = (device.name or adv.local_name or "").lower()
        has_known_name = any(known in name for known in ZENDURE_KNOWN_NAMES)
        has_zendure_service = ZENDURE_GATT_SERVICE_UUID in (adv.service_uuids or [])
        if not has_known_name and not has_zendure_service:
            return
        found[device.address] = {
            "address": device.address,
            "name": device.name or adv.local_name,
            "rssi": adv.rssi,
        }

    async with BleakScanner(detection_callback=on_detect):
        await asyncio.sleep(duration)

    return sorted(found.values(), key=lambda d: d["rssi"], reverse=True)


async def _send_command(address, payload, timeout):
    """Schrijft één JSON-commando naar de command-characteristic en verzamelt
    het notify-antwoord, indien het apparaat er een stuurt. Geen bevestigde
    multi-frame lengte-header zoals bij Shelly — dit is een simpel schema van
    één write gevolgd door eventuele notify-berichten."""
    import json

    response_chunks = []
    response_event = asyncio.Event()

    def on_notify(_char, data):
        response_chunks.append(bytes(data))
        response_event.set()

    async with BleakClient(address) as client:
        await client.start_notify(ZENDURE_NOTIFY_CHAR_UUID, on_notify)
        try:
            body = json.dumps(payload).encode("utf-8")
            await asyncio.wait_for(
                client.write_gatt_char(ZENDURE_CMD_CHAR_UUID, body, response=True),
                timeout=timeout,
            )
            try:
                await asyncio.wait_for(response_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass  # niet elk commando geeft per se een notify terug
        finally:
            try:
                await client.stop_notify(ZENDURE_NOTIFY_CHAR_UUID)
            except Exception:
                pass

    if response_chunks:
        try:
            return json.loads(b"".join(response_chunks).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {"raw": b"".join(response_chunks).hex()}
    return {}


def scan(duration=6.0):
    """Zoekt naar nabije Zendure-apparaten die BLE adverteren. Alleen lezen,
    geen verbinding, dus veilig om los te testen."""
    return asyncio.run(_scan_zendure_devices(duration))


def provision_broker(address, ssid, password, iot_url, timeout=DEFAULT_TIMEOUT):
    """Stuurt wifi-gegevens én het lokale broker-adres (iotUrl) naar het apparaat,
    zodat het voortaan met de SolarBuffer Hub praat in plaats van Zendure's cloud.

    LET OP: dit is een schrijfcommando naar echte apparaathardware, gebaseerd op
    een ongeverifieerd (community-gereverse-engineerd) protocol. Test dit eerst
    tegen een apparaat dat niet actief aan het laden/ontladen is."""
    config_payload = {
        "iotUrl": iot_url,
        "messageId": "1002",
        "method": "token",
        "password": password,
        "ssid": ssid,
        "timeZone": "GMT+02:00",
        "token": "abcdefgh",
    }
    result = asyncio.run(_send_command(address, config_payload, timeout))
    if isinstance(result, dict) and result.get("error"):
        raise ZendureBleError(f"Zendure BLE-fout bij configureren: {result['error']}")

    # Vervolgcommando dat de wifi-verbinding daadwerkelijk laat starten.
    station_payload = {"messageId": "1003", "method": "station"}
    asyncio.run(_send_command(address, station_payload, timeout))

    return result
