"""
ble_provision.py - Shelly BLE Wi-Fi provisioning voor SolarBuffer
Vereiste package: bleak  (pip install bleak)

Werking:
  1. Scan op Shelly BLE advertenties (naam begint met 'Shelly')
  2. Verbind via GATT
  3. Stuur WiFi.SetConfig RPC over de drie Shelly GATT characteristics
  4. Optioneel: stel apparaatnaam in via Sys.SetConfig
"""

import asyncio
import json
import struct
import threading
from typing import Optional

try:
    from bleak import BleakScanner, BleakClient
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False
    BleakScanner = None  # type: ignore
    BleakClient = None   # type: ignore — stub zodat functie-annotaties niet crashen

# Shelly GATT UUIDs (Gen2 / Gen3 identiek)
UUID_RW     = "5f6d4f53-5f52-5043-5f64-6174615f5f5f"  # data read/write
UUID_RX_CTL = "5f6d4f53-5f52-5043-5f72-785f63746c5f"  # RX control (notify)
UUID_TX_CTL = "5f6d4f53-5f52-5043-5f74-785f63746c5f"  # TX control (write length)

MTU = 256

# Globale scan-state
_scan_state = {
    "running": False,
    "found":   [],
    "error":   None,
}
_provision_state = {
    "running": False,
    "done":    False,
    "success": False,
    "message": "",
    "log":     [],
}
_provision_lock = threading.Lock()


# --- SCAN ---

async def _async_scan(duration: float = 8.0) -> list:
    found = {}

    def callback(device, adv):
        name = device.name or ""
        if name.lower().startswith("shelly"):
            found[device.address] = {
                "name":    name,
                "address": device.address,
                "rssi":    adv.rssi,
            }

    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()
    await asyncio.sleep(duration)
    await scanner.stop()
    return list(found.values())


def scan_shelly_ble(duration: float = 8.0) -> dict:
    if not BLEAK_AVAILABLE:
        return {"devices": [], "error": "bleak niet geïnstalleerd (pip install bleak)"}
    try:
        devices = asyncio.run(_async_scan(duration))
        return {"devices": devices, "error": None}
    except Exception as e:
        return {"devices": [], "error": str(e)}


def start_scan_background(duration: float = 8.0):
    global _scan_state
    _scan_state = {"running": True, "found": [], "error": None}

    def _run():
        result = scan_shelly_ble(duration)
        _scan_state["found"]   = result["devices"]
        _scan_state["error"]   = result["error"]
        _scan_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()


def get_scan_state() -> dict:
    return dict(_scan_state)


# --- GATT helpers ---

async def _gatt_write(client: BleakClient, char_uuid: str, data: bytes):
    # Write Without Response voorkomt dubbele verzending bij fallback én werkt op CoreBluetooth
    await client.write_gatt_char(char_uuid, data, response=False)


async def _rpc_call(client: BleakClient, method: str, params: dict, rpc_id: int = 1) -> dict:
    payload = json.dumps({
        "id":     rpc_id,
        "src":    "solarbuffer",
        "method": method,
        "params": params,
    }).encode("utf-8")

    response_length = [0]
    response_event = asyncio.Event()

    def on_rx_ctl(sender, data: bytearray):
        response_length[0] = struct.unpack_from("<I", bytes(data))[0]
        response_event.set()

    # Probeer notificaties in te schakelen
    use_notify = False
    try:
        try:
            await client.stop_notify(UUID_RX_CTL)
            await asyncio.sleep(0.15)
        except Exception:
            pass
        await client.start_notify(UUID_RX_CTL, on_rx_ctl)
        # Wacht tot CCCD-schrijf voltooid is (CoreBluetooth doet dit asynchroon)
        await asyncio.sleep(0.5)
        use_notify = True
    except Exception:
        pass

    try:
        # Stuur lengte naar TX control
        await _gatt_write(client, UUID_TX_CTL, struct.pack("<I", len(payload)))
        await asyncio.sleep(0.1)

        # Stuur payload in chunks
        for i in range(0, len(payload), MTU):
            await _gatt_write(client, UUID_RW, payload[i:i + MTU])
            await asyncio.sleep(0.05)

        total = 0

        if use_notify:
            try:
                await asyncio.wait_for(response_event.wait(), timeout=8.0)
                total = response_length[0]
            except asyncio.TimeoutError:
                pass  # CoreBluetooth leverde notificatie niet af — val terug op polling

        if total == 0:
            # Polling: lees UUID_RX_CTL totdat Shelly een antwoord heeft klaarstaan
            deadline = asyncio.get_event_loop().time() + 10.0
            while asyncio.get_event_loop().time() < deadline:
                raw = await client.read_gatt_char(UUID_RX_CTL)
                if len(raw) >= 4:
                    total = struct.unpack_from("<I", bytes(raw))[0]
                    if total > 0:
                        break
                await asyncio.sleep(0.15)
            if total == 0:
                raise TimeoutError(f"Geen antwoord van Shelly op methode '{method}'")

        # Lees response in chunks
        received = bytearray()
        while len(received) < total:
            chunk = await client.read_gatt_char(UUID_RW)
            received.extend(chunk)

        return json.loads(received.decode("utf-8"))
    finally:
        if use_notify:
            try:
                await client.stop_notify(UUID_RX_CTL)
            except Exception:
                pass


# --- PROVISION ---

async def _async_provision(
    address: str,
    ssid: str,
    password: str,
    device_name: Optional[str] = None,
    log_cb=None,
) -> dict:
    def log(msg):
        if log_cb:
            log_cb(msg)

    log(f"Verbinden met {address} …")
    async with BleakClient(address, timeout=20.0) as client:
        log("Verbonden ✓")

        # Service discovery afronden (CoreBluetooth doet dit asynchroon na connect)
        try:
            await client.get_services()
        except Exception:
            pass
        await asyncio.sleep(3.0)

        # Pairing — sommige Shelly-modellen eisen bonding voor GATT-toegang
        try:
            await client.pair()
            log("Beveiligde verbinding ✓")
        except Exception as e:
            log(f"Pairing overgeslagen ({e})")

        await asyncio.sleep(1.0)

        try:
            info_resp = await _rpc_call(client, "Shelly.GetDeviceInfo", {}, rpc_id=1)
            result = info_resp.get("result", {})
            log(f"Apparaat: {result.get('id', 'onbekend')}  model: {result.get('model', '?')}")
        except Exception as e:
            log(f"GetDeviceInfo mislukt: {e}")

        log(f"WiFi instellen: SSID={ssid} …")
        wifi_params = {
            "config": {
                "sta": {
                    "ssid":   ssid,
                    "pass":   password,
                    "enable": True,
                }
            }
        }
        wifi_resp = None
        for poging in range(1, 4):
            try:
                wifi_resp = await _rpc_call(client, "WiFi.SetConfig", wifi_params, rpc_id=2)
                break
            except Exception as e:
                if poging < 3:
                    log(f"WiFi.SetConfig poging {poging} mislukt ({e}), opnieuw over {poging} s …")
                    await asyncio.sleep(poging)
                else:
                    return {"success": False, "message": f"WiFi.SetConfig mislukt: {e}"}

        if wifi_resp is None:
            return {"success": False, "message": "WiFi.SetConfig: geen antwoord"}
        if "error" in wifi_resp:
            return {"success": False, "message": f"WiFi.SetConfig fout: {wifi_resp['error']}"}
        log("WiFi config verstuurd ✓")

        if device_name:
            log(f"Apparaatnaam instellen: {device_name} …")
            try:
                await _rpc_call(client, "Sys.SetConfig",
                                {"config": {"device": {"name": device_name}}}, rpc_id=3)
                log("Apparaatnaam ingesteld ✓")
            except Exception as e:
                log(f"Sys.SetConfig waarschuwing: {e} (niet kritiek)")

        log("Apparaat herstarten …")
        try:
            await _rpc_call(client, "Shelly.Reboot", {}, rpc_id=4)
        except Exception:
            pass  # verbinding valt weg na reboot

        log("Klaar ✓ Shelly maakt nu verbinding met je WiFi-netwerk.")
        return {
            "success": True,
            "message": f"Shelly succesvol geconfigureerd voor netwerk '{ssid}'.",
        }


def provision_shelly_wifi(
    address: str,
    ssid: str,
    password: str,
    device_name: Optional[str] = None,
) -> dict:
    if not BLEAK_AVAILABLE:
        return {
            "success": False,
            "message": "bleak niet geïnstalleerd (pip install bleak)",
            "log": [],
        }
    log_lines = []

    try:
        result = asyncio.run(
            _async_provision(address, ssid, password, device_name, log_lines.append)
        )
        result["log"] = log_lines
        return result
    except Exception as e:
        log_lines.append(f"Onverwachte fout: {e}")
        return {"success": False, "message": str(e), "log": log_lines}


def start_provision_background(
    address: str,
    ssid: str,
    password: str,
    device_name: Optional[str] = None,
):
    global _provision_state
    with _provision_lock:
        _provision_state = {
            "running": True,
            "done":    False,
            "success": False,
            "message": "",
            "log":     [],
        }

    def _run():
        result = provision_shelly_wifi(address, ssid, password, device_name)
        with _provision_lock:
            _provision_state.update({
                "running": False,
                "done":    True,
                "success": result["success"],
                "message": result["message"],
                "log":     result["log"],
            })

    threading.Thread(target=_run, daemon=True).start()


def get_provision_state() -> dict:
    with _provision_lock:
        return dict(_provision_state)
