"""BLE-provisioning voor Zendure legacy-apparaten (Hyper 2000, Hub1200, Hub2000,
Ace1500/AIO2400) — apparaten die zenSDK (lokale HTTP-API) niet ondersteunen en
alleen via Zendure's cloud-MQTT-broker werken.

In plaats van een DNS-omleiding op te zetten om die cloud-verbinding te
onderscheppen, stuurt dit de eigen lokale broker (SolarBuffer Hub) rechtstreeks
naar het apparaat via Bluetooth — hetzelfde commando dat het apparaat ook van de
Zendure-app krijgt bij het instellen van wifi.

UUIDs en payload-formaat geverifieerd tegen de officiële Zendure Home Assistant-
integratie (device.py, bleMqtt()/bleCommand()):
https://github.com/Zendure/Zendure-HA/blob/master/custom_components/zendure_ha/device.py
Zowel de command-characteristic (SF_COMMAND_CHAR) als het iotUrl/ssid/password-
commando komen daar 1-op-1 mee overeen. MQTT-topics (voor een latere fase) zijn
daar te vinden als iot/{prodkey}/{deviceId}/properties/(read|write) en
iot/{prodkey}/{deviceId}/function/invoke.
"""
import asyncio
import json
import time

from bleak import BleakClient, BleakScanner

ZENDURE_GATT_SERVICE_UUID = "0000a002-0000-1000-8000-00805f9b34fb"
ZENDURE_CMD_CHAR_UUID = "0000c304-0000-1000-8000-00805f9b34fb"

# Volledige supported-devices-lijst uit solarflow-bt-manager. Nuttig voor twee
# dingen: (1) referentie welke apparaten dit BLE-provisioningpad ondersteunen
# (SolarFlow 800 dus ook, naast de echte legacy-apparaten), en (2) later nodig
# voor het samenstellen van de MQTT-topics: iot/<PRODKEY>/<DEVICE_ID>/...
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


def _current_gmt_offset():
    """bv. 'GMT+02:00' — dynamisch berekend i.p.v. hardcoded, want anders fout
    zodra zomer-/wintertijd wisselt."""
    offset_sec = -time.timezone if time.localtime().tm_isdst == 0 else -time.altzone
    sign = "+" if offset_sec >= 0 else "-"
    h, m = divmod(abs(offset_sec) // 60, 60)
    return f"GMT{sign}{h:02d}:{m:02d}"


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


async def _ble_command(client, command):
    """Eén JSON-commando naar de command-characteristic, zonder op antwoord te
    wachten — zo doet de officiële Zendure-HA-integratie het ook (bleCommand()):
    write-without-response, geen notify-subscriptie nodig."""
    payload = json.dumps(command).encode("utf-8")
    await client.write_gatt_char(ZENDURE_CMD_CHAR_UUID, payload, response=False)


async def _provision(address, ssid, password, iot_url, timeout):
    async with BleakClient(address, timeout=timeout) as client:
        await _ble_command(client, {
            "iotUrl": iot_url,
            "messageId": 1002,
            "method": "token",
            "password": password,
            "ssid": ssid,
            "timeZone": _current_gmt_offset(),
            "token": "abcdefgh",
        })
        await _ble_command(client, {"messageId": 1003, "method": "station"})


def scan(duration=6.0):
    """Zoekt naar nabije Zendure-apparaten die BLE adverteren. Alleen lezen,
    geen verbinding, dus veilig om los te testen."""
    return asyncio.run(_scan_zendure_devices(duration))


def provision_broker(address, ssid, password, iot_url, timeout=DEFAULT_TIMEOUT):
    """Stuurt wifi-gegevens én het lokale broker-adres (iotUrl) naar het apparaat,
    zodat het voortaan met de SolarBuffer Hub praat in plaats van Zendure's cloud.
    Protocol geverifieerd tegen de officiële Zendure-HA-broncode."""
    try:
        asyncio.run(_provision(address, ssid, password, iot_url, timeout))
    except Exception as e:
        raise ZendureBleError(f"Zendure BLE-fout bij koppelen: {e}") from e
