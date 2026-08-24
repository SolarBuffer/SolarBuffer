"""BLE-provisioning voor Shelly Gen2/Gen3-apparaten.

Praat direct met de mOS RPC-over-BLE service die Shelly's eigen app ook gebruikt
(WiFi.SetConfig), zodat een net uitgepakte Shelly aan het thuisnetwerk gekoppeld
kan worden zonder de Shelly-app te installeren en zonder dat de hub zijn eigen
wlan0-verbinding hoeft te verlaten (BLE en WiFi zijn aparte radio's op de Pi).

UUIDs en framing geverifieerd tegen twee onafhankelijke implementaties:
- ALLTERCO/shelly-script-examples / shelly-api-docs (RPC-over-BLE, mongoose-os)
- diego-treitos/Shelly-Utilities (shelly-ble-rpc.py, bleak-gebaseerd)
"""
import asyncio
import json
import random
import struct

from bleak import BleakClient, BleakScanner

SHELLY_GATT_SERVICE_UUID = "5f6d4f53-5f52-5043-5f53-56435f49445f"
RPC_CHAR_DATA_UUID = "5f6d4f53-5f52-5043-5f64-6174615f5f5f"
RPC_CHAR_TX_CTL_UUID = "5f6d4f53-5f52-5043-5f74-785f63746c5f"
RPC_CHAR_RX_CTL_UUID = "5f6d4f53-5f52-5043-5f72-785f63746c5f"
ALLTERCO_MFID = 0x0BA9

DEFAULT_TIMEOUT = 8.0


class ShellyBleError(Exception):
    pass


async def _scan_shelly_devices(duration):
    found = {}

    def on_detect(device, adv):
        if not device.name or "Shelly" not in device.name:
            return
        if ALLTERCO_MFID not in adv.manufacturer_data:
            return
        found[device.address] = {
            "address": device.address,
            "name": device.name,
            "rssi": adv.rssi,
        }

    async with BleakScanner(detection_callback=on_detect):
        await asyncio.sleep(duration)

    return sorted(found.values(), key=lambda d: d["rssi"], reverse=True)


async def _call_rpc(address, method, params, timeout):
    async with BleakClient(address) as client:
        service = client.services.get_service(SHELLY_GATT_SERVICE_UUID)
        if service is None:
            raise ShellyBleError("Shelly BLE RPC-service niet gevonden op dit apparaat")

        data_char = service.get_characteristic(RPC_CHAR_DATA_UUID)
        tx_ctl_char = service.get_characteristic(RPC_CHAR_TX_CTL_UUID)
        rx_ctl_char = service.get_characteristic(RPC_CHAR_RX_CTL_UUID)
        if not all([data_char, tx_ctl_char, rx_ctl_char]):
            raise ShellyBleError("BLE RPC-characteristics ontbreken op dit apparaat")

        request_id = random.randint(1, 1_000_000_000)
        payload = {"id": request_id, "src": "solarbuffer", "method": method}
        if params:
            payload["params"] = params
        body = json.dumps(payload).encode("utf-8")

        await asyncio.wait_for(
            client.write_gatt_char(tx_ctl_char, struct.pack(">I", len(body)), response=True),
            timeout=timeout,
        )
        await asyncio.wait_for(
            client.write_gatt_char(data_char, body, response=True),
            timeout=timeout,
        )

        raw_len = await asyncio.wait_for(client.read_gatt_char(rx_ctl_char), timeout=timeout)
        frame_len = struct.unpack(">I", raw_len)[0]

        response = bytearray()
        while len(response) < frame_len:
            chunk = await asyncio.wait_for(client.read_gatt_char(data_char), timeout=timeout)
            if not chunk:
                break
            response.extend(chunk)

        result = json.loads(bytes(response).decode("utf-8")) if response else {}

    if result.get("id") != request_id:
        raise ShellyBleError("Onverwacht BLE-antwoord (id komt niet overeen)")
    if "error" in result:
        raise ShellyBleError(f"Shelly RPC-fout: {result['error']}")
    return result.get("result", {})


def scan(duration=6.0):
    """Zoekt naar nabije Shelly-apparaten die BLE adverteren (setup-modus)."""
    return asyncio.run(_scan_shelly_devices(duration))


def provision_wifi(address, ssid, password, timeout=DEFAULT_TIMEOUT):
    """Stuurt de thuisnetwerk-gegevens naar de Shelly via BLE (WiFi.SetConfig),
    zodat hij zelfstandig het WiFi-netwerk joint. Werkt alleen tijdens het
    onbevestigde setup-venster van het apparaat (voor koppeling is geen pairing
    nodig; daarna wel, dus dit moet meteen na uitpakken gebeuren)."""
    params = {"config": {"sta": {"enable": True, "ssid": ssid, "pass": password}}}
    return asyncio.run(_call_rpc(address, "WiFi.SetConfig", params, timeout))
