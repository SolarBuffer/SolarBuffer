from simple_pid import PID
import requests
import time
import json
import os
import socket
import ipaddress
import subprocess
import uuid
import re
import traceback
import secrets
import shutil
import sqlite3
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, jsonify, request, redirect, session, send_file, Response
import io
import threading
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import paho.mqtt.client as _mqtt_lib
    MQTT_AVAILABLE = True
except ImportError:
    _mqtt_lib = None
    MQTT_AVAILABLE = False

try:
    import broadlink as _broadlink_lib
    import base64 as _b64
    BROADLINK_AVAILABLE = True
except ImportError:
    _broadlink_lib = None
    _b64 = None
    BROADLINK_AVAILABLE = False

_broadlink_learn_lock = threading.Lock()
_broadlink_learn_state = {}  # learn_id -> {status, code, error}

# ================= CONFIG =================
BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
AUDIT_LOG_FILE = os.path.join(BASE_DIR, "audit.log")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
ENERGY_BASELINES_FILE = os.path.join(BASE_DIR, "energy_baselines.json")
_last_state_save = 0
_energy_baselines_lock = threading.Lock()

DEFAULT_EXPERT_SETTINGS = {
    "EXPORT_THRESHOLD": -50,
    "EXPORT_DELAY": 15,
    "FREEZE_AT": 95,
    "FREEZE_CONFIRM": 5,
    "IMPORT_UNFREEZE_THRESHOLD": 200,
    "UNFREEZE_DELAY": 5,
    "IMPORT_OFF_THRESHOLD": 250,
    "OFF_DELAY": 120,
    "PID_NEUTRAL_LOW": -5,
    "PID_NEUTRAL_HIGH": 45,
    "POWER_SOCKET_DELAY": 5,
    "POWER_SOCKET_HOLD_SECONDS": 60,
    "BOOST_DURATION": 900
}

# ===== DYNAMIC PRICING =====
_price_cache = {}          # {datetime(hour, utc): price_eur_kwh}
_price_cache_lock = threading.Lock()
_current_price_ct = None   # float ct/kWh, updated every hour


def _update_price_cache():
    global _current_price_ct
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    from_dt = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    till_dt = from_dt + timedelta(days=2) - timedelta(seconds=1)
    try:
        r = requests.get(
            "https://api.energyzero.nl/v1/energyprices",
            params={
                "fromDate": from_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "tillDate": till_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "interval": 4,
                "usageType": 1,
                "inclBtw": "true",
            },
            timeout=10,
        )
        if r.status_code == 200:
            prices = r.json().get("Prices", [])
            new_cache = {}
            for p in prices:
                ts = p.get("readingDate", "")
                price = p.get("price")
                if ts and price is not None:
                    try:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        hour_key = dt.replace(minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
                        new_cache[hour_key] = float(price)
                    except Exception:
                        pass
            with _price_cache_lock:
                _price_cache.clear()
                _price_cache.update(new_cache)
    except Exception:
        pass
    # update current price
    _current_price_ct = get_current_price_ct()


def get_current_price_ct():
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    with _price_cache_lock:
        price_eur = _price_cache.get(now_utc)
    if price_eur is None:
        return None
    return round(price_eur * 100, 2)


def _price_fetch_loop():
    while True:
        _update_price_cache()
        time.sleep(3600)


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}

    if "shelly_devices" not in cfg:
        cfg["shelly_devices"] = []
    if "p1_ip" not in cfg:
        cfg["p1_ip"] = ""
    if "expert_mode" not in cfg:
        cfg["expert_mode"] = False
    if "expert_settings" not in cfg or not isinstance(cfg["expert_settings"], dict):
        cfg["expert_settings"] = {}
    if "schedules" not in cfg or not isinstance(cfg["schedules"], list):
        cfg["schedules"] = []
    for sched in cfg["schedules"]:
        if "device_ips" not in sched:
            sched["device_ips"] = []
    if "anti_legionella_enabled" not in cfg:
        cfg["anti_legionella_enabled"] = False
    if "pid_enabled" not in cfg:
        cfg["pid_enabled"] = True
    if "schedules_enabled" not in cfg:
        cfg["schedules_enabled"] = True
    if "vacation_mode" not in cfg:
        cfg["vacation_mode"] = False
    if "vacation_until" not in cfg:
        cfg["vacation_until"] = None
    if "vacation_legionella" not in cfg:
        cfg["vacation_legionella"] = False
    if "gas_enabled" not in cfg:
        cfg["gas_enabled"] = False

    # Migreer oude solaredge_* keys naar generieke inverter_* keys
    if "solaredge_enabled" in cfg and "inverter_enabled" not in cfg:
        cfg["inverter_enabled"] = cfg.pop("solaredge_enabled")
        cfg["inverter_ip"] = cfg.pop("solaredge_ip", "")
        cfg["inverter_type"] = "solaredge"

    if "inverter_enabled" not in cfg:
        cfg["inverter_enabled"] = False
    if "inverter_ip" not in cfg:
        cfg["inverter_ip"] = ""
    if "inverter_type" not in cfg:
        cfg["inverter_type"] = "solaredge"

    if "mqtt_enabled" not in cfg:
        cfg["mqtt_enabled"] = False
    if "mqtt_broker" not in cfg:
        cfg["mqtt_broker"] = ""
    if "mqtt_port" not in cfg:
        cfg["mqtt_port"] = 1883
    if "mqtt_username" not in cfg:
        cfg["mqtt_username"] = ""
    if "mqtt_password" not in cfg:
        cfg["mqtt_password"] = ""
    if "mqtt_topic_prefix" not in cfg:
        cfg["mqtt_topic_prefix"] = "solarbuffer"
    if "mqtt_ha_discovery" not in cfg:
        cfg["mqtt_ha_discovery"] = True
    if "mqtt_publish_interval" not in cfg:
        cfg["mqtt_publish_interval"] = 30

    if "latitude" not in cfg:
        cfg["latitude"] = ""
    if "longitude" not in cfg:
        cfg["longitude"] = ""

    if "accessories" not in cfg or not isinstance(cfg["accessories"], list):
        cfg["accessories"] = []
    for acc in cfg["accessories"]:
        if "id" not in acc:
            acc["id"] = str(uuid.uuid4())
        if "acc_type" not in acc:
            acc["acc_type"] = "temperature" if acc.get("temp_ip") else "power"
        if acc["acc_type"] == "temperature":
            if "temp_ip" not in acc:
                acc["temp_ip"] = ""
            # migreer temp_channels (lijst) → enkelvoudig temp_channel
            if "temp_channel" not in acc:
                old_list = acc.pop("temp_channels", None)
                if isinstance(old_list, list) and old_list:
                    acc["temp_channel"] = int(old_list[0])
                else:
                    acc["temp_channel"] = 100
            else:
                acc["temp_channel"] = int(acc["temp_channel"])
            acc.pop("temp_channels", None)
            if "linked_device_ip" not in acc:
                acc["linked_device_ip"] = ""
        else:
            if "power_meter_type" not in acc:
                acc["power_meter_type"] = "shelly"
            if "power_ips" not in acc:
                old_ip = (acc.get("power_ip") or "").strip()
                acc["power_ips"] = [old_ip] if old_ip else []
            acc["power_ips"] = [ip for ip in acc["power_ips"] if ip.strip()]
            if "power_ip" not in acc:
                acc["power_ip"] = acc["power_ips"][0] if acc["power_ips"] else ""
        if "icon" not in acc:
            acc["icon"] = "mdi-thermometer" if acc["acc_type"] == "temperature" else "mdi-power-plug"
        if "record_history" not in acc:
            acc["record_history"] = False
        if acc.get("acc_type") == "power" and "is_solar" not in acc:
            acc["is_solar"] = False

    # Migreer oud formaat (enkele gebruiker) naar gebruikerslijst
    if "users" not in cfg:
        old_username = cfg.pop("username", "solarbuffer")
        old_hash = cfg.pop("password_hash", "")
        cfg["users"] = [{"username": old_username, "password_hash": old_hash}]

    # Migreer globale ntfy-instellingen naar eerste gebruiker
    if "ntfy_enabled" in cfg or "ntfy_url" in cfg:
        first = cfg["users"][0] if cfg["users"] else None
        if first is not None:
            for key in ("ntfy_enabled", "ntfy_url", "ntfy_notify_start",
                        "ntfy_notify_legionella", "ntfy_notify_schedule", "ntfy_notify_offline"):
                if key not in first:
                    first[key] = cfg.pop(key, True if key.startswith("ntfy_notify") else (False if key == "ntfy_enabled" else ""))
        for key in ("ntfy_enabled", "ntfy_url", "ntfy_notify_start",
                    "ntfy_notify_legionella", "ntfy_notify_schedule", "ntfy_notify_offline"):
            cfg.pop(key, None)

    for user in cfg["users"]:
        if "dark_mode" not in user:
            user["dark_mode"] = False
        if "ntfy_enabled" not in user:
            user["ntfy_enabled"] = False
        if "ntfy_url" not in user:
            user["ntfy_url"] = ""
        if "ntfy_notify_start" not in user:
            user["ntfy_notify_start"] = True
        if "ntfy_notify_legionella" not in user:
            user["ntfy_notify_legionella"] = True
        if "ntfy_notify_schedule" not in user:
            user["ntfy_notify_schedule"] = True
        if "ntfy_notify_offline" not in user:
            user["ntfy_notify_offline"] = True
        if "role" not in user:
            user["role"] = "admin"

    for key, value in DEFAULT_EXPERT_SETTINGS.items():
        if key not in cfg["expert_settings"]:
            cfg["expert_settings"][key] = value

    normalized_devices = []
    for d in cfg.get("shelly_devices", []):
        dev = dict(d)
        if "power_socket_type" not in dev:
            dev["power_socket_type"] = ""
        if "power_socket_ip" not in dev:
            dev["power_socket_ip"] = ""
        if "boiler_volume" not in dev:
            dev["boiler_volume"] = 100
        normalized_devices.append(dev)

    cfg["shelly_devices"] = normalized_devices

    if "broadlink_devices" not in cfg or not isinstance(cfg["broadlink_devices"], list):
        cfg["broadlink_devices"] = []
    for bl in cfg["broadlink_devices"]:
        if "id" not in bl:
            bl["id"] = str(uuid.uuid4())
        if "name" not in bl:
            bl["name"] = "Broadlink"
        if "ip" not in bl:
            bl["ip"] = ""
        if "mac" not in bl:
            bl["mac"] = ""
        if "devtype" not in bl:
            bl["devtype"] = 0
        if "ir_devices" not in bl or not isinstance(bl["ir_devices"], list):
            bl["ir_devices"] = []
        for ir in bl["ir_devices"]:
            if "id" not in ir:
                ir["id"] = str(uuid.uuid4())
            if "name" not in ir:
                ir["name"] = "IR-apparaat"
            if "icon" not in ir:
                ir["icon"] = "mdi-remote"
            if "show_on_dashboard" not in ir:
                ir["show_on_dashboard"] = True
            if "linked_accessory_id" not in ir:
                ir["linked_accessory_id"] = ""
            if "commands" not in ir or not isinstance(ir["commands"], list):
                ir["commands"] = []
            for cmd in ir["commands"]:
                if "id" not in cmd:
                    cmd["id"] = str(uuid.uuid4())
                if "name" not in cmd:
                    cmd["name"] = "Commando"
                if "code" not in cmd:
                    cmd["code"] = ""

    if "battery_enabled" not in cfg:
        cfg["battery_enabled"] = False
    if "battery_type" not in cfg:
        cfg["battery_type"] = "homewizard"
    # Migreer oud battery_ip (string) → battery_ips (lijst)
    if "battery_ips" not in cfg:
        old_ip = cfg.pop("battery_ip", "").strip()
        cfg["battery_ips"] = [old_ip] if old_ip else []
    else:
        cfg.pop("battery_ip", None)
    cfg["battery_ips"] = [ip for ip in cfg["battery_ips"] if ip.strip()]
    # Migreer battery_token (enkelvoud) → battery_tokens (lijst, één per accu)
    if "battery_tokens" not in cfg:
        old_token = cfg.pop("battery_token", "")
        cfg["battery_tokens"] = [old_token] if old_token else []
    else:
        cfg.pop("battery_token", None)
    # Sync lengte met battery_ips
    _n_bats = max(len(cfg.get("battery_ips", [])), 1)
    while len(cfg["battery_tokens"]) < _n_bats:
        cfg["battery_tokens"].append("")
    cfg["battery_tokens"] = cfg["battery_tokens"][:_n_bats]
    if "battery_control_token" not in cfg:
        cfg["battery_control_token"] = ""
    if "marstek_port" not in cfg:
        cfg["marstek_port"] = 30000
    if "marstek_max_power" not in cfg:
        cfg["marstek_max_power"] = 2000
    if "battery_priority" not in cfg:
        cfg["battery_priority"] = "boiler"
    if "battery_soc_threshold" not in cfg:
        cfg["battery_soc_threshold"] = 95
    if "battery_force_tofull" not in cfg:
        cfg["battery_force_tofull"] = False

    return cfg


def save_config(data):
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, CONFIG_FILE)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(force=False):
    global _last_state_save
    now = time.time()
    if not force and now - _last_state_save < 300:
        return
    _last_state_save = now
    state = {
        ip: {
            "last_active_time": st.get("last_active_time", 0),
            "legionella_active": st.get("legionella_active", False),
            "legionella_start": st.get("legionella_start"),
        }
        for ip, st in device_states.items()
    }
    state["__gas__"] = {
        "gas_day_start_m3": gas_day_start_m3,
        "gas_day_date": gas_day_date,
    }
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"State save fout: {e}")


_energy_baselines: dict = {}


def load_energy_baselines():
    global _energy_baselines
    try:
        with open(ENERGY_BASELINES_FILE, encoding="utf-8") as f:
            _energy_baselines = json.load(f)
    except Exception:
        _energy_baselines = {}
    return _energy_baselines


def save_energy_baselines():
    with _energy_baselines_lock:
        try:
            with open(ENERGY_BASELINES_FILE, "w", encoding="utf-8") as f:
                json.dump(_energy_baselines, f, indent=2)
        except Exception as e:
            print(f"Energy baselines save fout: {e}")


def get_runtime_settings(cfg):
    if cfg.get("expert_mode", False):
        settings = cfg.get("expert_settings", {}).copy()
        for key, value in DEFAULT_EXPERT_SETTINGS.items():
            if key not in settings:
                settings[key] = value
        return settings
    return DEFAULT_EXPERT_SETTINGS.copy()


def parse_expert_settings_from_request(req):
    expert_settings = {}
    for key, default_value in DEFAULT_EXPERT_SETTINGS.items():
        raw_value = req.form.get(key, str(default_value)).strip()
        try:
            if isinstance(default_value, int):
                expert_settings[key] = int(raw_value)
            else:
                expert_settings[key] = float(raw_value)
        except ValueError:
            expert_settings[key] = default_value
    return expert_settings


def parse_mqtt_settings_from_request(req):
    return {
        "mqtt_enabled": req.form.get("mqtt_enabled") == "on",
        "mqtt_broker": req.form.get("mqtt_broker", "").strip(),
        "mqtt_port": safe_int(req.form.get("mqtt_port", "1883"), 1883),
        "mqtt_username": req.form.get("mqtt_username", "").strip(),
        "mqtt_password": req.form.get("mqtt_password", ""),
        "mqtt_topic_prefix": (req.form.get("mqtt_topic_prefix", "solarbuffer").strip() or "solarbuffer"),
        "mqtt_ha_discovery": req.form.get("mqtt_ha_discovery") == "on",
        "mqtt_publish_interval": safe_int(req.form.get("mqtt_publish_interval", "30"), 30),
    }


def safe_int(value, default):
    try:
        return int(str(value).strip())
    except Exception:
        return default


# ================= AUDIT =================
audit_lock = threading.Lock()
MAX_AUDIT_LINES = 100


def safe_session_username():
    try:
        return session.get("username", "unknown")
    except Exception:
        return "system"


def get_client_ip():
    try:
        return request.remote_addr or "unknown"
    except Exception:
        return "unknown"


def safe_request_ip():
    try:
        return get_client_ip()
    except Exception:
        return "system"


def trim_audit_log(max_lines=MAX_AUDIT_LINES):
    try:
        if not os.path.exists(AUDIT_LOG_FILE):
            return
        with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
            with open(AUDIT_LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines)
    except Exception as e:
        print(f"Audit log trim fout: {e}")


def write_audit_log(action, details=None):
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "user": safe_session_username(),
        "ip": safe_request_ip(),
        "action": action,
        "details": details or {}
    }
    try:
        with audit_lock:
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            trim_audit_log()
    except Exception as e:
        print(f"Audit log fout: {e}")


def compare_configs(old_cfg, new_cfg):
    changes = {}

    if old_cfg.get("p1_ip") != new_cfg.get("p1_ip"):
        changes["p1_ip"] = {"old": old_cfg.get("p1_ip"), "new": new_cfg.get("p1_ip")}

    if old_cfg.get("expert_mode") != new_cfg.get("expert_mode"):
        changes["expert_mode"] = {"old": old_cfg.get("expert_mode"), "new": new_cfg.get("expert_mode")}

    old_settings = old_cfg.get("expert_settings", {})
    new_settings = new_cfg.get("expert_settings", {})
    settings_changes = {}
    for key in DEFAULT_EXPERT_SETTINGS.keys():
        if old_settings.get(key) != new_settings.get(key):
            settings_changes[key] = {"old": old_settings.get(key), "new": new_settings.get(key)}
    if settings_changes:
        changes["expert_settings"] = settings_changes

    old_devices = old_cfg.get("shelly_devices", [])
    new_devices = new_cfg.get("shelly_devices", [])
    old_by_ip = {d["ip"]: d for d in old_devices if d.get("ip")}
    new_by_ip = {d["ip"]: d for d in new_devices if d.get("ip")}

    added = [dev for ip, dev in new_by_ip.items() if ip not in old_by_ip]
    removed = [dev for ip, dev in old_by_ip.items() if ip not in new_by_ip]
    modified = []
    for ip in set(old_by_ip.keys()) & set(new_by_ip.keys()):
        if old_by_ip[ip] != new_by_ip[ip]:
            modified.append({"ip": ip, "old": old_by_ip[ip], "new": new_by_ip[ip]})

    if added:
        changes["devices_added"] = added
    if removed:
        changes["devices_removed"] = removed
    if modified:
        changes["devices_modified"] = modified

    mqtt_fields = ["mqtt_enabled", "mqtt_broker", "mqtt_port", "mqtt_username",
                   "mqtt_topic_prefix", "mqtt_ha_discovery", "mqtt_publish_interval"]
    mqtt_changes = {}
    for field in mqtt_fields:
        if old_cfg.get(field) != new_cfg.get(field):
            mqtt_changes[field] = {"old": old_cfg.get(field), "new": new_cfg.get(field)}
    if old_cfg.get("mqtt_password") != new_cfg.get("mqtt_password"):
        mqtt_changes["mqtt_password"] = {"changed": True}
    if mqtt_changes:
        changes["mqtt"] = mqtt_changes

    return changes


# ================= PID =================
PID_KP = 0.018
PID_KI = 0.00115
PID_KD = 0.00

device_pids = {}
enabled = True
schedules_enabled = True
vacation_mode = False
device_states = {}
accessory_states = {}
inverter_power = None
inverter_online = False
battery_state = {
    "soc": None, "power_w": None, "voltage_v": None, "current_a": None,
    "frequency_hz": None, "energy_import_kwh": None, "energy_export_kwh": None,
    "cycles": None, "mode": None, "permissions": None, "online": False,
    "max_consumption_w": 0, "max_production_w": 0,
    "charge_today_kwh": None, "discharge_today_kwh": None,
}
_battery_blocks_start = False
_last_battery_permissions = None
_last_battery_mode = None
_bat_day_date = None
_bat_charge_start_kwh = None
_bat_discharge_start_kwh = None
_last_marstek_send = 0.0
_last_marstek_power = None
_broadlink_online = {}  # bl_id -> bool
current_power = 0
_p1_online = False
current_brightness = 0
current_gas_m3 = None      # meest recente meterstand (m³)
gas_day_start_m3 = None    # meterstand bij start van vandaag
gas_day_date = None        # datum (YYYY-MM-DD) waarvoor de dagstand geldt
active_schedule_info = None
_mqtt_client = None
_mqtt_connected = False
_update_available = False
_api_tokens = {}           # token -> {"username": str, "expires": float}
_api_tokens_lock = threading.Lock()
_tailscale_auth_url = None

# ================= HW UPDATE STATE =================
_hw_update_running = False
_hw_update_done = False
_hw_update_success = False
_hw_update_log = []
_hw_update_cond = threading.Condition()

# ================= CONTROL CONSTANTS =================
MIN_BRIGHTNESS = 30
MAX_BRIGHTNESS = 100

# ================= ANTI-LEGIONELLA =================
LEGIONELLA_IDLE_SECONDS = 72 * 3600   # 72 uur zonder activiteit → cyclus starten
LEGIONELLA_RUN_SECONDS = 3 * 3600     # 3 uur op maximaal vermogen draaien
anti_legionella_enabled = False

# ================= FLASK =================
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "verander_dit_naar_iets_veiligs!")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=False,
    PERMANENT_SESSION_LIFETIME=timedelta(days=30)
)


def _get_bearer_username():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:].strip()
    with _api_tokens_lock:
        entry = _api_tokens.get(token)
        if not entry:
            return None
        if time.time() > entry["expires"]:
            del _api_tokens[token]
            return None
        return entry["username"]


def require_login():
    if session.get("logged_in"):
        return True
    return _get_bearer_username() is not None


def get_user_dark_mode():
    cfg = load_config()
    username = session.get("username", "")
    user = next((u for u in cfg.get("users", []) if u.get("username") == username), None)
    return bool(user.get("dark_mode", False)) if user else False


def is_current_user_admin():
    username = session.get("username") or _get_bearer_username()
    if not username:
        return False
    cfg = load_config()
    user = next((u for u in cfg.get("users", []) if u.get("username") == username), None)
    return user is not None and user.get("role", "admin") == "admin"


# ================= NETWORK SCAN HELPERS =================
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "192.168.1.100"
    finally:
        s.close()


def get_subnet_ips():
    local_ip = get_local_ip()
    network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
    return [str(ip) for ip in network.hosts()]


def detect_homewizard_p1(ip):
    try:
        r = requests.get(f"http://{ip}/api/v1/data", timeout=1.2)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict):
            return None
        possible_keys = ["meter_model", "smr_version", "active_tariff", "total_gas_m3"]
        if any(k in data for k in possible_keys):
            return {"type": "homewizard_p1", "name": f"HomeWizard P1 ({ip})", "ip": ip}
    except Exception:
        pass
    return None


def detect_shelly(ip):
    try:
        r = requests.get(f"http://{ip}/rpc/Shelly.GetDeviceInfo", timeout=1.2)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict):
            return None
        model = (data.get("model") or "").strip()
        allowed_models = {"S3DM-0010WW", "0010WW"}
        if model not in allowed_models:
            return None
        return {
            "type": "shelly",
            "name": data.get("name") or "SolarBuffer",
            "ip": ip,
            "model": model,
            "gen": data.get("gen", 3)
        }
    except Exception:
        return None


DIMMER_MODELS = {"S3DM-0010WW", "0010WW"}


def detect_shelly_pm(ip):
    try:
        r = requests.get(f"http://{ip}/rpc/Shelly.GetDeviceInfo", timeout=1.2)
        if r.status_code != 200:
            return None
        info = r.json()
        if not isinstance(info, dict):
            return None
        model = (info.get("model") or "").strip()
        if model in DIMMER_MODELS:
            return None
        name = (info.get("name") or info.get("id") or f"Shelly ({ip})").strip()
        # Controleer of het apparaat PM-data kan leveren
        pm_endpoints = [
            f"http://{ip}/rpc/PM1.GetStatus?id=0",
            f"http://{ip}/rpc/Switch.GetStatus?id=0",
            f"http://{ip}/rpc/EM.GetStatus?id=0",
        ]
        for url in pm_endpoints:
            try:
                pr = requests.get(url, timeout=1.2)
                if pr.status_code != 200:
                    continue
                pd = pr.json()
                if isinstance(pd, dict) and "apower" in pd:
                    return {"name": name, "ip": ip, "model": model, "type": "shelly"}
                for v in pd.values():
                    if isinstance(v, dict) and "apower" in v:
                        return {"name": name, "ip": ip, "model": model, "type": "shelly"}
            except Exception:
                pass
    except Exception:
        pass
    return None


def detect_shelly_temp(ip):
    try:
        r = requests.get(f"http://{ip}/rpc/Shelly.GetStatus", timeout=1.5)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, dict):
            return None
        channels = [int(k.split(":")[1]) for k in data if k.startswith("temperature:") and int(k.split(":")[1]) >= 100]
        if not channels:
            return None
        info_r = requests.get(f"http://{ip}/rpc/Shelly.GetDeviceInfo", timeout=1.2)
        info = info_r.json() if info_r.status_code == 200 else {}
        name = (info.get("name") or info.get("id") or f"Shelly Temp ({ip})").strip()
        return {"name": name, "ip": ip, "type": "shelly_temp", "channels": sorted(channels)}
    except Exception:
        pass
    return None


P1_PRODUCT_TYPES = {"HWE-P1", "SDM230-wifi", "SDM630-wifi"}


def detect_homewizard_pm(ip):
    try:
        r = requests.get(f"http://{ip}/api", timeout=1.2)
        if r.status_code != 200:
            return None
        info = r.json()
        if not isinstance(info, dict):
            return None
        product_type = (info.get("product_type") or "").strip()
        # Sla P1-meters over, die zijn voor netstroom niet voor accessoires
        if product_type in P1_PRODUCT_TYPES:
            return None
        # Controleer of het apparaat actief vermogen kan meten
        dr = requests.get(f"http://{ip}/api/v1/data", timeout=1.2)
        if dr.status_code != 200:
            return None
        data = dr.json()
        if not isinstance(data, dict) or "active_power_w" not in data:
            return None
        name = (info.get("product_name") or f"HomeWizard ({ip})").strip()
        return {"name": name, "ip": ip, "type": "homewizard"}
    except Exception:
        pass
    return None


def scan_network_for_devices():
    ips = get_subnet_ips()
    known_p1_ip = (load_config().get("p1_ip") or "").strip()
    found_p1 = []
    found_shelly = []

    with ThreadPoolExecutor(max_workers=25) as executor:
        future_map = {}
        for ip in ips:
            if ip == known_p1_ip:
                # Al geconfigureerd; voeg direct toe zonder extra HTTP-hit
                found_p1.append({"type": "homewizard_p1", "name": f"HomeWizard P1 ({ip})", "ip": ip})
                continue
            future_map[executor.submit(detect_homewizard_p1, ip)] = ("p1", ip)
            future_map[executor.submit(detect_shelly, ip)] = ("shelly", ip)

        for future in as_completed(future_map):
            kind, ip = future_map[future]
            try:
                result = future.result()
                if not result:
                    continue
                if kind == "p1":
                    found_p1.append(result)
                elif kind == "shelly":
                    found_shelly.append(result)
            except Exception as e:
                print(f"Scan error ({ip}): {e}")

    unique_p1 = []
    seen_p1 = set()
    for d in found_p1:
        if d["ip"] not in seen_p1:
            unique_p1.append(d)
            seen_p1.add(d["ip"])

    unique_shelly = []
    seen_shelly = set()
    for d in found_shelly:
        if d["ip"] not in seen_shelly:
            unique_shelly.append(d)
            seen_shelly.add(d["ip"])

    return {"p1_meters": unique_p1, "shelly_devices": unique_shelly}


# ================= ROUTES =================
def is_first_boot():
    cfg = load_config()
    return not any(u.get("password_hash") for u in cfg.get("users", []))


@app.route("/first_boot/welcome")
def first_boot_welcome():
    if not is_first_boot():
        return redirect("/")
    return render_template("first_boot_welcome.html")


@app.route("/first_boot/setup_choice")
def first_boot_setup_choice():
    if not is_first_boot():
        return redirect("/")
    error = request.args.get("error", "")
    return render_template("setup_choice.html", error=error, first_boot=True)


@app.route("/first_boot/import", methods=["POST"])
def first_boot_import():
    if not is_first_boot():
        return redirect("/")
    f = request.files.get("config_file")
    if not f:
        return redirect("/first_boot/setup_choice?error=Geen+bestand+geselecteerd")
    try:
        raw = f.read().decode("utf-8")
        imported = json.loads(raw)
        if not isinstance(imported, dict):
            raise ValueError("Geen geldig configuratieobject")
    except Exception as e:
        return redirect(f"/first_boot/setup_choice?error=Ongeldig+JSON-bestand:+{e}")
    with open(CONFIG_FILE, "w", encoding="utf-8") as fout:
        json.dump(imported, fout, indent=4)
    cfg = load_config()
    save_config(cfg)
    init_device_states(cfg["shelly_devices"])
    init_device_pids(cfg["shelly_devices"])
    write_audit_log("config_imported_firstboot", {"devices": len(cfg.get("shelly_devices", []))})
    return redirect("/login")


@app.route("/first_boot/user", methods=["GET", "POST"])
def first_boot_user():
    if not is_first_boot():
        return redirect("/")
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not username:
            error = "Gebruikersnaam mag niet leeg zijn"
        elif not password:
            error = "Wachtwoord mag niet leeg zijn"
        elif len(password) < 6:
            error = "Wachtwoord moet minimaal 6 tekens bevatten"
        elif password != confirm:
            error = "Wachtwoorden komen niet overeen"
        if not error:
            cfg = load_config()
            cfg["users"] = [{"username": username, "password_hash": generate_password_hash(password), "dark_mode": False, "role": "admin"}]
            save_config(cfg)
            session["logged_in"] = True
            session["username"] = username
            write_audit_log("first_boot_user_created", {"username": username})
            return redirect("/?fresh=1")
    return render_template("first_boot_user.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if is_first_boot():
        return redirect("/first_boot/welcome")
    cfg = load_config()
    client_ip = get_client_ip()

    if request.method == "POST":
        entered_username = request.form.get("username", "").strip()
        entered_password = request.form.get("password", "")

        users = cfg.get("users", [])
        matched = next((u for u in users if u["username"].lower() == entered_username.lower()), None)
        if matched and check_password_hash(matched.get("password_hash", ""), entered_password):
            if request.form.get("remember_me"):
                session.permanent = True
            session["logged_in"] = True
            session["username"] = matched["username"]
            write_audit_log("login_success", {"username": entered_username})
            return redirect("/")
        else:
            write_audit_log("login_failed", {"username": entered_username, "ip": client_ip})
            return render_template("login.html", error="Ongeldige gebruikersnaam of wachtwoord.")

    return render_template("login.html")


@app.route("/logout")
def logout():
    old_username = session.get("username", "unknown")
    session.clear()
    write_audit_log("logout", {"username": old_username})
    return redirect("/login")


def _cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response


@app.route("/api/ping")
def api_ping():
    return _cors_headers(jsonify({"solarbuffer": True}))


@app.route("/api/token", methods=["OPTIONS"])
def api_token_options():
    return _cors_headers(jsonify({}))


@app.route("/api/token", methods=["POST"])
def api_token_create():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    cfg = load_config()
    matched = next((u for u in cfg.get("users", []) if u.get("username", "").lower() == username.lower()), None)
    if not matched or not check_password_hash(matched.get("password_hash", ""), password):
        write_audit_log("api_login_failed", {"username": username, "ip": get_client_ip()})
        return _cors_headers(jsonify({"error": "Ongeldige gebruikersnaam of wachtwoord"})), 401
    token = secrets.token_hex(32)
    expires = time.time() + 30 * 86400  # 30 dagen
    with _api_tokens_lock:
        _api_tokens[token] = {"username": matched["username"], "expires": expires}
    write_audit_log("api_login_success", {"username": matched["username"], "ip": get_client_ip()})
    return _cors_headers(jsonify({"token": token, "username": matched["username"], "expires_in": 30 * 86400}))


@app.route("/api/token", methods=["DELETE"])
def api_token_revoke():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return jsonify({"error": "Geen token opgegeven"}), 400
    token = auth[7:].strip()
    with _api_tokens_lock:
        _api_tokens.pop(token, None)
    return jsonify({"ok": True})


@app.route("/api/session")
def api_session():
    token = request.args.get("token", "")
    with _api_tokens_lock:
        entry = _api_tokens.get(token)
        username = entry["username"] if entry and time.time() <= entry["expires"] else None
    if not username:
        return redirect("/login")
    session["logged_in"] = True
    session["username"] = username
    return redirect("/")


@app.route("/change_credentials", methods=["GET", "POST"])
def change_credentials():
    if not require_login():
        return redirect("/login")

    cfg = load_config()

    if request.method == "POST":
        action = request.form.get("action", "")
        current_password = request.form.get("current_password", "")
        current_username = session.get("username", "")
        users = cfg.get("users", [])
        user = next((u for u in users if u["username"] == current_username), None)

        dm = get_user_dark_mode()

        def err(msg):
            return render_template("change_credentials.html", error=msg, dark_mode=dm)

        if not user:
            return err("Gebruiker niet gevonden")
        if not check_password_hash(user.get("password_hash", ""), current_password):
            return err("Huidig wachtwoord is onjuist")

        if action == "change_username":
            new_username = request.form.get("new_username", "").strip()
            if not new_username:
                return err("Gebruikersnaam mag niet leeg zijn")
            old_username = user["username"]
            user["username"] = new_username
            cfg["users"] = users
            save_config(cfg)
            session["username"] = new_username
            write_audit_log("username_changed", {"old_username": old_username, "new_username": new_username})
            return render_template("change_credentials.html", success="Gebruikersnaam gewijzigd naar " + new_username, dark_mode=dm)

        elif action == "change_password":
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            if not new_password:
                return err("Nieuw wachtwoord mag niet leeg zijn")
            if len(new_password) < 6:
                return err("Nieuw wachtwoord moet minimaal 6 tekens bevatten")
            if new_password != confirm_password:
                return err("Wachtwoorden komen niet overeen")
            user["password_hash"] = generate_password_hash(new_password)
            cfg["users"] = users
            save_config(cfg)
            write_audit_log("password_changed", {"username": current_username})
            return render_template("change_credentials.html", success="Wachtwoord succesvol gewijzigd", dark_mode=dm)

    return render_template("change_credentials.html", dark_mode=get_user_dark_mode())


def parse_devices_from_request(req):
    devices = []
    names = req.form.getlist("shelly_name[]")
    ips = req.form.getlist("shelly_ip[]")
    priorities = req.form.getlist("priority[]")
    power_meters = req.form.getlist("power_meter[]")
    power_ips = req.form.getlist("power_ip[]")
    power_socket_types = req.form.getlist("power_socket_type[]")
    power_socket_ips = req.form.getlist("power_socket_ip[]")
    boiler_volumes = req.form.getlist("boiler_volume[]")

    row_count = max(len(names), len(ips), len(priorities), len(power_meters),
                    len(power_ips), len(power_socket_types), len(power_socket_ips))

    def get_val(lst, idx, default=""):
        return lst[idx] if idx < len(lst) else default

    for i in range(row_count):
        name = get_val(names, i).strip()
        ip = get_val(ips, i).strip()
        if not name or not ip:
            continue
        prio = safe_int(get_val(priorities, i, "1"), 1)
        pm = get_val(power_meters, i).strip()
        pip = get_val(power_ips, i).strip()
        ps_type = get_val(power_socket_types, i).strip().lower()
        ps_ip = get_val(power_socket_ips, i).strip()
        bv = max(10, safe_int(get_val(boiler_volumes, i, "100"), 100))
        devices.append({
            "name": name, "ip": ip, "priority": prio,
            "power_meter": pm if pm else "",
            "power_ip": pip if pip else "",
            "power_socket_type": ps_type if ps_type else "",
            "power_socket_ip": ps_ip if ps_ip else "",
            "boiler_volume": bv,
        })
    return devices


@app.route("/", methods=["GET", "POST"])
def wizard():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    if cfg.get("p1_ip") and cfg.get("shelly_devices"):
        return redirect("/dashboard")
    # On first boot (no config yet), show setup choice screen unless user chose fresh install
    if request.method == "GET" and not request.args.get("fresh"):
        return redirect("/setup")
    if request.method == "POST":
        old_cfg = load_config()
        cfg["p1_ip"] = request.form.get("p1ip", "").strip()
        cfg["expert_mode"] = request.form.get("expert_mode") == "on"
        cfg["expert_settings"] = parse_expert_settings_from_request(request)
        cfg["shelly_devices"] = parse_devices_from_request(request)
        cfg.update(parse_mqtt_settings_from_request(request))
        changes = compare_configs(old_cfg, cfg)
        save_config(cfg)
        if changes:
            write_audit_log("config_updated", changes)
        else:
            write_audit_log("config_saved_no_changes", {})
        init_device_states(cfg["shelly_devices"])
        init_device_pids(cfg["shelly_devices"])
        threading.Thread(target=sync_configured_devices_off, args=(cfg["shelly_devices"],), daemon=True).start()
        return redirect("/dashboard")
    return render_template("wizard.html", config=cfg, dark_mode=get_user_dark_mode())


@app.route("/setup", methods=["GET"])
def setup_choice():
    if is_first_boot():
        return redirect("/first_boot/welcome")
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    if cfg.get("p1_ip") and cfg.get("shelly_devices"):
        return redirect("/dashboard")
    error = request.args.get("error", "")
    return render_template("setup_choice.html", error=error)


@app.route("/setup/import", methods=["POST"])
def setup_import():
    if not require_login():
        return redirect("/login")
    f = request.files.get("config_file")
    if not f:
        return redirect("/setup?error=Geen+bestand+geselecteerd")
    try:
        raw = f.read().decode("utf-8")
        imported = json.loads(raw)
        if not isinstance(imported, dict):
            raise ValueError("Geen geldig configuratieobject")
    except Exception as e:
        return redirect(f"/setup?error=Ongeldig+JSON-bestand:+{e}")
    with open(CONFIG_FILE, "w", encoding="utf-8") as fout:
        json.dump(imported, fout, indent=4)
    cfg = load_config()
    save_config(cfg)
    new_ips = {d["ip"] for d in cfg.get("shelly_devices", [])}
    for old_ip in list(device_states.keys()):
        if old_ip not in new_ips:
            del device_states[old_ip]
    for old_ip in list(device_pids.keys()):
        if old_ip not in new_ips:
            del device_pids[old_ip]
    init_device_states(cfg["shelly_devices"])
    init_device_pids(cfg["shelly_devices"])
    write_audit_log("config_imported_firstboot", {"devices": len(cfg.get("shelly_devices", []))})
    if cfg.get("p1_ip") and cfg.get("shelly_devices"):
        return redirect("/dashboard")
    return redirect("/?fresh=1")


@app.route("/wizard_forced", methods=["GET", "POST"])
def wizard_forced():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    if request.method == "POST":
        old_cfg = load_config()
        cfg["p1_ip"] = request.form.get("p1ip", "").strip()
        cfg["expert_mode"] = request.form.get("expert_mode") == "on"
        cfg["expert_settings"] = parse_expert_settings_from_request(request)
        cfg["shelly_devices"] = parse_devices_from_request(request)
        cfg.update(parse_mqtt_settings_from_request(request))
        changes = compare_configs(old_cfg, cfg)
        save_config(cfg)
        if changes:
            write_audit_log("config_updated_forced", changes)
        else:
            write_audit_log("config_saved_forced_no_changes", {})
        init_device_states(cfg["shelly_devices"])
        init_device_pids(cfg["shelly_devices"])
        threading.Thread(target=sync_configured_devices_off, args=(cfg["shelly_devices"],), daemon=True).start()
        return redirect("/dashboard")
    return render_template("wizard.html", config=cfg, dark_mode=get_user_dark_mode())


@app.route("/settings/p1", methods=["GET", "POST"])
def settings_p1():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    cfg = load_config()
    if request.method == "POST":
        old_cfg = load_config()
        cfg["p1_ip"] = request.form.get("p1ip", "").strip()
        cfg["battery_enabled"] = "battery_enabled" in request.form
        cfg["battery_type"] = request.form.get("battery_type", "homewizard")
        raw_ips = request.form.getlist("battery_ip[]")
        cfg["battery_ips"] = [ip.strip() for ip in raw_ips if ip.strip()][:4]
        cfg.pop("battery_ip", None)
        raw_tokens = request.form.getlist("battery_token[]")
        cfg["battery_tokens"] = [t.strip() for t in raw_tokens]
        cfg.pop("battery_token", None)
        # Sync lengte met battery_ips
        while len(cfg["battery_tokens"]) < len(cfg["battery_ips"]):
            cfg["battery_tokens"].append("")
        cfg["battery_control_token"] = request.form.get("battery_control_token", "").strip()
        try:
            cfg["marstek_port"] = int(request.form.get("marstek_port", 30000))
        except (ValueError, TypeError):
            cfg["marstek_port"] = 30000
        try:
            cfg["marstek_max_power"] = int(request.form.get("marstek_max_power", 2000))
        except (ValueError, TypeError):
            cfg["marstek_max_power"] = 2000
        cfg["battery_priority"] = request.form.get("battery_priority", "boiler")
        try:
            cfg["battery_soc_threshold"] = int(request.form.get("battery_soc_threshold", 95))
        except (ValueError, TypeError):
            cfg["battery_soc_threshold"] = 95
        changes = compare_configs(old_cfg, cfg)
        save_config(cfg)
        if changes:
            write_audit_log("config_updated", changes)
        return redirect("/settings")
    return render_template("settings_p1.html", config=cfg, dark_mode=get_user_dark_mode())


@app.route("/api/battery/debug")
def battery_debug():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    cfg = load_config()
    if cfg.get("battery_type") != "marstek":
        return jsonify({"error": "Alleen beschikbaar voor Marstek"}), 400
    ips = cfg.get("battery_ips") or []
    port = int(cfg.get("marstek_port") or 30000)
    results = {}
    for ip in ips:
        entry = {}
        for method in ("ES.GetStatus", "Bat.GetStatus"):
            try:
                entry[method] = marstek_udp(ip, port, method).get("result", {})
            except Exception as e:
                entry[method] = {"error": str(e)}
        results[ip] = entry
    return jsonify(results)


def _hw_pair_with_ip(ip):
    r = requests.post(
        f"https://{ip}/api/user",
        headers={"X-Api-Version": "2", "Content-Type": "application/json"},
        json={"name": "local/solarbuffer"},
        timeout=4,
        verify=False,
    )
    return r


@app.route("/api/battery/pair", methods=["POST"])
def battery_pair():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True) or {}
    ip = (data.get("ip") or "").strip()
    if not ip:
        return jsonify(success=False, error="IP-adres is verplicht"), 400
    try:
        r = _hw_pair_with_ip(ip)
        if r.status_code == 200:
            token = r.json().get("token", "")
            return jsonify(success=True, token=token)
        elif r.status_code == 403:
            return jsonify(success=False, waiting=True)
        else:
            return jsonify(success=False, error=f"Onverwacht antwoord: {r.status_code}")
    except requests.exceptions.ConnectionError:
        return jsonify(success=False, error="Apparaat niet bereikbaar op dit IP-adres")
    except Exception as e:
        return jsonify(success=False, error=str(e))


@app.route("/api/battery/pair_p1", methods=["POST"])
def battery_pair_p1():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    cfg = load_config()
    p1_ip = cfg.get("p1_ip", "").strip()
    if not p1_ip:
        return jsonify(success=False, error="P1 IP-adres niet geconfigureerd"), 400
    try:
        r = _hw_pair_with_ip(p1_ip)
        if r.status_code == 200:
            token = r.json().get("token", "")
            return jsonify(success=True, token=token)
        elif r.status_code == 403:
            return jsonify(success=False, waiting=True)
        else:
            return jsonify(success=False, error=f"Onverwacht antwoord: {r.status_code}")
    except requests.exceptions.ConnectionError:
        return jsonify(success=False, error=f"P1 meter ({p1_ip}) niet bereikbaar")
    except Exception as e:
        return jsonify(success=False, error=str(e))


@app.route("/api/battery/set_mode", methods=["POST"])
def battery_set_mode():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True) or {}
    mode = data.get("mode", "zero")
    if mode not in ("to_full", "zero"):
        return jsonify(success=False, error="Ongeldige modus"), 400
    cfg = load_config()
    cfg["battery_force_tofull"] = (mode == "to_full")
    save_config(cfg)
    token = cfg.get("battery_control_token", "").strip()
    p1_ip = cfg.get("p1_ip", "").strip()
    sent = False
    if token and p1_ip:
        perms = [] if mode == "to_full" else ["charge_allowed", "discharge_allowed"]
        sent = set_battery_control(p1_ip, token, mode, perms)
    write_audit_log("battery_mode_set", {"mode": mode, "sent": sent})
    return jsonify(success=True, mode=mode, sent=sent)


@app.route("/settings/solarbuffers", methods=["GET", "POST"])
def settings_solarbuffers():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    cfg = load_config()
    if request.method == "POST":
        old_cfg = load_config()
        cfg["shelly_devices"] = parse_devices_from_request(request)
        changes = compare_configs(old_cfg, cfg)
        save_config(cfg)
        if changes:
            write_audit_log("config_updated", changes)
        init_device_states(cfg["shelly_devices"])
        init_device_pids(cfg["shelly_devices"])
        threading.Thread(target=sync_configured_devices_off, args=(cfg["shelly_devices"],), daemon=True).start()
        return redirect("/settings")
    return render_template("settings_solarbuffers.html", config=cfg, dark_mode=get_user_dark_mode())


@app.route("/settings/expert", methods=["GET", "POST"])
def settings_expert():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    cfg = load_config()
    if request.method == "POST":
        old_cfg = load_config()
        cfg["expert_mode"] = request.form.get("expert_mode") == "on"
        cfg["expert_settings"] = parse_expert_settings_from_request(request)
        cfg["dynamic_pricing_enabled"] = request.form.get("dynamic_pricing_enabled") == "on"
        try:
            cfg["price_threshold_ct"] = float(request.form.get("price_threshold_ct", "5").replace(",", "."))
        except ValueError:
            cfg["price_threshold_ct"] = 5.0
        try:
            cfg["price_brightness"] = max(1, min(100, int(request.form.get("price_brightness", "100"))))
        except ValueError:
            cfg["price_brightness"] = 100
        changes = compare_configs(old_cfg, cfg)
        save_config(cfg)
        if changes:
            write_audit_log("config_updated", changes)
        return redirect("/settings")
    return render_template("settings_expert.html", config=cfg, dark_mode=get_user_dark_mode())


@app.route("/settings/mqtt", methods=["GET", "POST"])
def settings_mqtt():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    cfg = load_config()
    if request.method == "POST":
        old_cfg = load_config()
        cfg.update(parse_mqtt_settings_from_request(request))
        changes = compare_configs(old_cfg, cfg)
        save_config(cfg)
        if changes:
            write_audit_log("config_updated", changes)
        return redirect("/settings")
    return render_template("settings_mqtt.html", config=cfg, dark_mode=get_user_dark_mode())


@app.route("/dashboard")
def dashboard():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    if not cfg.get("p1_ip") or not cfg.get("shelly_devices"):
        return redirect("/")
    return render_template("dashboard.html", config=cfg, dark_mode=get_user_dark_mode())


@app.route("/status_json")
def status_json():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    cfg = load_config()
    # bouw opzoektabel: device_ip -> lijst van gekoppelde temperatuursensoren
    linked_temp_map = {}
    for acc in cfg.get("accessories", []):
        if acc.get("acc_type") == "temperature" and acc.get("linked_device_ip"):
            dev_ip = acc["linked_device_ip"]
            acc_st = accessory_states.get(acc.get("id", ""), {})
            linked_temp_map.setdefault(dev_ip, []).append({
                "name": acc.get("name", ""),
                "icon": acc.get("icon", "mdi-thermometer"),
                "temperature": acc_st.get("temperature"),
                "online": acc_st.get("online", False),
            })

    devices = []
    for d in cfg.get("shelly_devices", []):
        s = device_states.get(d["ip"], {})
        devices.append({
            "name": d["name"], "ip": d["ip"],
            "priority": d.get("priority", 1),
            "on": s.get("on", False),
            "brightness": s.get("brightness", 0),
            "online": s.get("online", False),
            "power_meter_online": s.get("power_meter_online", False),
            "freeze": s.get("freeze", False),
            "started": s.get("started", False),
            "pending_start": s.get("pending_start", False),
            "power": s.get("power", 0),
            "power_meter": d.get("power_meter"),
            "power_ip": d.get("power_ip"),
            "power_socket_type": d.get("power_socket_type", ""),
            "power_socket_ip": d.get("power_socket_ip", ""),
            "power_socket_on": s.get("power_socket_on", False),
            "power_socket_online": s.get("power_socket_online", False),
            "waiting_for_power_socket": s.get("waiting_for_power_socket", False),
            "legionella_active": s.get("legionella_active", False),
            "legionella_start": s.get("legionella_start"),
            "boost_until": s.get("boost_until"),
            "price_triggered": s.get("price_triggered", False),
            "energy_today_kwh": round(s.get("energy_today_kwh", 0.0), 3),
            "linked_temperatures": linked_temp_map.get(d["ip"], []),
        })
    accessories = []
    for acc in cfg.get("accessories", []):
        acc_id = acc.get("id", "")
        st = accessory_states.get(acc_id, {})
        acc_type = acc.get("acc_type", "power")
        # gekoppelde temp-sensoren worden getoond op de apparaattegel, niet hier
        if acc_type == "temperature" and acc.get("linked_device_ip"):
            continue
        entry = {
            "id": acc_id,
            "name": acc.get("name", ""),
            "acc_type": acc_type,
            "icon": acc.get("icon", "mdi-power-plug"),
            "online": st.get("online", False),
        }
        if acc_type == "temperature":
            entry["temp_ip"] = acc.get("temp_ip", "")
            entry["temp_channel"] = acc.get("temp_channel", 100)
            entry["temperature"] = st.get("temperature")
        else:
            entry["power_meter_type"] = acc.get("power_meter_type", "")
            entry["power_ips"] = acc.get("power_ips", [acc.get("power_ip", "")])
            entry["power"] = st.get("power", 0.0)
            entry["energy_today_kwh"] = round(st.get("energy_today_kwh", 0.0), 2)
            entry["is_solar"] = acc.get("is_solar", False)
        accessories.append(entry)

    gas_today = None
    if cfg.get("gas_enabled") and current_gas_m3 is not None and gas_day_start_m3 is not None:
        gas_today = round(max(0.0, current_gas_m3 - gas_day_start_m3), 3)

    broadlink_ir_states = {}
    for bl in cfg.get("broadlink_devices", []):
        for ir in bl.get("ir_devices", []):
            acc_id = ir.get("linked_accessory_id", "")
            if acc_id:
                st = accessory_states.get(acc_id, {})
                power = st.get("power", 0.0)
                broadlink_ir_states[ir["id"]] = {
                    "power": round(power, 0),
                    "on": power > 5,
                }

    return jsonify(
        power=current_power, brightness=current_brightness, enabled=enabled,
        devices=devices, expert_mode=cfg.get("expert_mode", False),
        expert_settings=get_runtime_settings(cfg),
        schedules=cfg.get("schedules", []),
        active_schedule=active_schedule_info,
        anti_legionella_enabled=anti_legionella_enabled,
        schedules_enabled=schedules_enabled,
        accessories=accessories,
        gas_enabled=cfg.get("gas_enabled", False), gas_today_m3=gas_today,
        inverter_enabled=cfg.get("inverter_enabled", False),
        inverter_type=cfg.get("inverter_type", "solaredge"),
        inverter_power=inverter_power,
        inverter_online=inverter_online,
        broadlink_ir_states=broadlink_ir_states,
        broadlink_devices=cfg.get("broadlink_devices", []),
        vacation_mode=cfg.get("vacation_mode", False),
        vacation_until=cfg.get("vacation_until"),
        vacation_legionella=cfg.get("vacation_legionella", False),
        current_price_ct=_current_price_ct,
        dynamic_pricing_enabled=cfg.get("dynamic_pricing_enabled", False),
        price_threshold_ct=float(cfg.get("price_threshold_ct", 5.0)),
        battery_enabled=cfg.get("battery_enabled", False),
        battery_type=cfg.get("battery_type", "homewizard"),
        battery_priority=cfg.get("battery_priority", "boiler"),
        battery_soc_threshold=cfg.get("battery_soc_threshold", 95),
        battery_count=len(cfg.get("battery_ips") or []) if cfg.get("battery_enabled") else 0,
        battery=battery_state if cfg.get("battery_enabled") else None,
        battery_blocks_start=_battery_blocks_start if cfg.get("battery_enabled") else False,
        battery_force_tofull=cfg.get("battery_force_tofull", False),
    )


@app.route("/toggle_pid")
def toggle_pid():
    global enabled
    if not require_login():
        return jsonify(success=False), 401
    if not is_current_user_admin():
        return jsonify(success=False, error="Geen toegang"), 403
    enabled = not enabled
    cfg = load_config()
    cfg["pid_enabled"] = enabled
    save_config(cfg)
    write_audit_log("pid_toggled", {"enabled": enabled})
    return jsonify(success=True)


@app.route("/toggle_schedules")
def toggle_schedules():
    global schedules_enabled
    if not require_login():
        return jsonify(success=False), 401
    if not is_current_user_admin():
        return jsonify(success=False, error="Geen toegang"), 403
    schedules_enabled = not schedules_enabled
    cfg = load_config()
    cfg["schedules_enabled"] = schedules_enabled
    save_config(cfg)
    write_audit_log("schedules_toggled", {"enabled": schedules_enabled})
    return jsonify(success=True, enabled=schedules_enabled)


@app.route("/toggle_anti_legionella")
def toggle_anti_legionella():
    global anti_legionella_enabled
    if not require_login():
        return jsonify(success=False), 401
    if not is_current_user_admin():
        return jsonify(success=False, error="Geen toegang"), 403
    anti_legionella_enabled = not anti_legionella_enabled
    cfg = load_config()
    cfg["anti_legionella_enabled"] = anti_legionella_enabled
    save_config(cfg)
    write_audit_log("anti_legionella_toggled", {"enabled": anti_legionella_enabled})
    return jsonify(success=True, enabled=anti_legionella_enabled)


@app.route("/vacation", methods=["POST"])
def set_vacation_mode():
    global vacation_mode
    if not require_login():
        return jsonify(success=False), 401
    if not is_current_user_admin():
        return jsonify(success=False, error="Geen toegang"), 403
    data = request.json or {}
    active = bool(data.get("active", False))
    until_iso = data.get("until") or None
    leg = bool(data.get("legionella", False))
    cfg = load_config()
    cfg["vacation_mode"] = active
    cfg["vacation_legionella"] = leg
    if active and until_iso:
        try:
            cfg["vacation_until"] = datetime.fromisoformat(until_iso).timestamp()
        except (ValueError, TypeError):
            cfg["vacation_until"] = None
    else:
        cfg["vacation_until"] = None
    save_config(cfg)
    vacation_mode = active
    write_audit_log("vacation_mode_toggled", {"active": active, "until": until_iso, "legionella": leg})
    if active:
        send_notification("🌴 Vakantiestand ingeschakeld.", event_key="ntfy_notify_vacation")
    else:
        send_notification("🌴 Vakantiestand uitgeschakeld, normale regeling hervat.", event_key="ntfy_notify_vacation")
    return jsonify(success=True, active=active)


@app.route("/boost/<path:ip>", methods=["POST"])
def boost_device(ip):
    if not require_login():
        return jsonify(success=False), 401
    cfg = load_config()
    device = next((d for d in cfg.get("shelly_devices", []) if d["ip"] == ip), None)
    if not device or ip not in device_states:
        return jsonify(success=False), 404
    st = device_states[ip]
    now = time.time()
    settings = get_runtime_settings(cfg)
    boost_duration = int(settings.get("BOOST_DURATION", 900))
    if st.get("boost_until") and now < st["boost_until"]:
        st["boost_until"] = None
        write_audit_log("boost_cancelled", {"device_ip": ip})
        return jsonify(success=True, boost_active=False)
    st["boost_until"] = now + boost_duration
    write_audit_log("boost_started", {"device_ip": ip, "duration_seconds": boost_duration})
    return jsonify(success=True, boost_active=True, boost_until=st["boost_until"])


@app.route("/sw.js")
def service_worker():
    return send_file(os.path.join(BASE_DIR, "static", "sw.js"), mimetype="application/javascript")


@app.route("/updates")
def updates():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    return render_template("updates.html", dark_mode=get_user_dark_mode())


@app.route("/settings")
def settings():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    return render_template("settings.html", config=cfg, dark_mode=get_user_dark_mode(),
                           session_username=session.get("username", ""),
                           is_admin=is_current_user_admin())


@app.route("/system")
def system_page():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    return render_template("system.html", dark_mode=get_user_dark_mode())


@app.route("/system_info")
def system_info():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403

    info = {}

    try:
        usage = shutil.disk_usage("/")
        info["disk_total"] = usage.total
        info["disk_used"] = usage.used
        info["disk_free"] = usage.free
        info["disk_percent"] = round(usage.used / usage.total * 100, 1)
    except Exception:
        info["disk_total"] = info["disk_used"] = info["disk_free"] = 0
        info["disk_percent"] = 0

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            info["cpu_temp"] = round(int(f.read().strip()) / 1000, 1)
    except Exception:
        try:
            r = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=3)
            info["cpu_temp"] = float(r.stdout.replace("temp=", "").replace("'C", "").strip())
        except Exception:
            info["cpu_temp"] = None

    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        d = int(secs // 86400)
        h = int((secs % 86400) // 3600)
        m = int((secs % 3600) // 60)
        info["uptime_str"] = f"{d}d {h}u {m:02d}m" if d > 0 else f"{h}u {m:02d}m"
    except Exception:
        info["uptime_str"] = "—"

    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:"):
                    mem[parts[0]] = int(parts[1]) * 1024
        total = mem.get("MemTotal:", 0)
        used = total - mem.get("MemAvailable:", 0)
        info["mem_total"] = total
        info["mem_used"] = used
        info["mem_percent"] = round(used / total * 100, 1) if total > 0 else 0
    except Exception:
        info["mem_total"] = info["mem_used"] = 0
        info["mem_percent"] = 0

    return jsonify(info)


# ================= NETWERK ROUTES =================

def _iwlist_scan():
    try:
        result = subprocess.run(
            ["sudo", "iwlist", "wlan0", "scan"],
            capture_output=True, text=True, timeout=15
        )
        networks = []
        seen = set()
        current = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("Cell "):
                if current.get("ssid") and current["ssid"] not in seen and current["ssid"] not in {"PI-SETUP"}:
                    seen.add(current["ssid"])
                    networks.append(current)
                current = {"ssid": None, "signal": 0, "secured": False}
            elif 'ESSID:"' in line:
                m = re.search(r'ESSID:"(.*?)"', line)
                if m:
                    current["ssid"] = m.group(1)
            elif "Signal level=" in line:
                m = re.search(r'Signal level=(-?\d+)', line)
                if m:
                    dbm = int(m.group(1))
                    current["signal"] = max(0, min(100, 2 * (dbm + 100)))
            elif "Encryption key:on" in line:
                current["secured"] = True
        if current.get("ssid") and current["ssid"] not in seen and current["ssid"] not in {"PI-SETUP"}:
            networks.append(current)
        return networks
    except Exception:
        return []


def _wifi_scan_networks(rescan=False):
    try:
        if rescan:
            subprocess.run(
                ["nmcli", "dev", "wifi", "rescan"],
                capture_output=True, timeout=5
            )
            time.sleep(4)
        cmd = [
            "nmcli", "--terse", "--fields", "SSID,SIGNAL,SECURITY",
            "dev", "wifi", "list",
            "--rescan", "no",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        networks = []
        seen = set()
        for line in result.stdout.splitlines():
            parts = re.split(r'(?<!\\):', line)
            if not parts:
                continue
            ssid = parts[0].replace('\\:', ':').strip()
            if not ssid or ssid in seen or ssid in {"PI-SETUP"}:
                continue
            seen.add(ssid)
            signal = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else 0
            security = parts[2].strip() if len(parts) > 2 else ""
            networks.append({"ssid": ssid, "signal": signal, "secured": bool(security)})

        if rescan and len(networks) <= 1:
            iwlist = _iwlist_scan()
            if len(iwlist) > len(networks):
                networks = iwlist

        current = _wifi_get_current()
        if current and current not in seen and current not in {"PI-SETUP"}:
            networks.insert(0, {"ssid": current, "signal": 100, "secured": True})

        networks.sort(key=lambda x: x["signal"], reverse=True)
        return networks
    except Exception:
        return []


def _wifi_get_current():
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[0].strip().lower() == "yes":
                ssid = parts[1].replace("\\:", ":").strip()
                if ssid:
                    return ssid
    except Exception:
        pass
    return None


def _wifi_connect_and_reboot(ssid, password):
    try:
        subprocess.run(
            ["nmcli", "connection", "delete", "customer-wifi"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["nmcli", "connection", "add", "type", "wifi", "ifname", "wlan0",
             "con-name", "customer-wifi", "ssid", ssid],
            check=True
        )
        if password:
            subprocess.run(
                ["nmcli", "connection", "modify", "customer-wifi",
                 "wifi-sec.key-mgmt", "wpa-psk"],
                check=True
            )
            subprocess.run(
                ["nmcli", "connection", "modify", "customer-wifi",
                 "wifi-sec.psk", password],
                check=True
            )
        else:
            subprocess.run(
                ["nmcli", "connection", "modify", "customer-wifi",
                 "wifi-sec.key-mgmt", ""],
                check=False
            )
        for opt, val in [
            ("connection.autoconnect", "yes"),
            ("connection.autoconnect-priority", "100"),
            ("connection.autoconnect-retries", "0"),
        ]:
            subprocess.run(
                ["nmcli", "connection", "modify", "customer-wifi", opt, val],
                check=True
            )
        for opt, val in [
            ("connection.autoconnect", "no"),
            ("connection.autoconnect-priority", "-100"),
        ]:
            subprocess.run(
                ["nmcli", "connection", "modify", "PI-SETUP", opt, val],
                check=False
            )
        time.sleep(2)
        subprocess.run(
            ["nmcli", "connection", "up", "customer-wifi"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
        )
        time.sleep(5)
        subprocess.Popen(["systemctl", "reboot"])
    except Exception:
        time.sleep(8)
        subprocess.Popen(["systemctl", "reboot"])


@app.route("/network")
def network_page():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    return render_template("network.html", dark_mode=get_user_dark_mode())


@app.route("/network/current")
def network_current():
    if not require_login():
        return jsonify(error="unauthorized"), 401
    ssid = _wifi_get_current()
    ip = get_local_ip() if ssid else None
    return jsonify(ssid=ssid, ip=ip)


@app.route("/network/scan")
def network_scan():
    if not require_login():
        return jsonify(error="unauthorized"), 401
    rescan = request.args.get("rescan") == "1"
    return jsonify(_wifi_scan_networks(rescan=rescan))


@app.route("/network/debug")
def network_debug():
    if not require_login():
        return jsonify(error="unauthorized"), 401
    out = {}
    for label, cmd in [
        ("nmcli_list_no_rescan",  ["nmcli", "--terse", "--fields", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "no"]),
        ("nmcli_list_yes_rescan", ["nmcli", "--terse", "--fields", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list", "--rescan", "yes"]),
        ("nmcli_dev_status",      ["nmcli", "-t", "-f", "device,type,state,connection", "dev"]),
        ("iwconfig",              ["iwconfig"]),
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            out[label] = {"stdout": r.stdout, "stderr": r.stderr, "rc": r.returncode}
        except Exception as e:
            out[label] = {"error": str(e)}
    return jsonify(out)


@app.route("/network/connect", methods=["POST"])
def network_connect():
    if not require_login():
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    ssid = str(data.get("ssid", "")).strip()
    password = str(data.get("password", ""))
    if not ssid:
        return jsonify(error="Geen SSID opgegeven"), 400
    write_audit_log("wifi_changed", {"user": safe_session_username(), "ssid": ssid})
    threading.Thread(target=_wifi_connect_and_reboot, args=(ssid, password), daemon=True).start()
    return jsonify(success=True)


_forecast_cache = {"data": None, "ts": 0, "error": None, "error_ts": 0}

@app.route("/solar_forecast")
def solar_forecast():
    if not require_login():
        return jsonify(error="unauthorized"), 401
    cfg = load_config()
    lat = cfg.get("latitude", "")
    lon = cfg.get("longitude", "")
    if not lat or not lon:
        return jsonify(error="no_location")
    global _forecast_cache
    now = time.time()
    if now - _forecast_cache["ts"] < 3600 and _forecast_cache["data"]:
        return jsonify(_forecast_cache["data"])
    # Geef gecachte fout terug als de API kortgeleden al faalde (5 min)
    if now - _forecast_cache["error_ts"] < 300 and _forecast_cache["error"]:
        return jsonify(error=_forecast_cache["error"])
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=shortwave_radiation"
        f"&forecast_days=2&timezone=auto"
    )
    for poging in range(3):
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code >= 500 and poging < 2:
                print(f"Solar forecast HTTP {resp.status_code}, poging {poging + 1}/3…", flush=True)
                time.sleep(2)
                continue
            if not resp.ok:
                print(f"Solar forecast HTTP {resp.status_code}: {resp.text[:300]}", flush=True)
                fout = f"Open-Meteo tijdelijk niet beschikbaar (HTTP {resp.status_code})"
                _forecast_cache["error"] = fout
                _forecast_cache["error_ts"] = now
                return jsonify(error=fout)
            raw = resp.json()
            if "hourly" not in raw:
                print(f"Solar forecast onverwacht antwoord: {str(raw)[:300]}", flush=True)
                fout = f"Onverwacht API-antwoord: {str(raw)[:200]}"
                _forecast_cache["error"] = fout
                _forecast_cache["error_ts"] = now
                return jsonify(error=fout)
            times = raw["hourly"]["time"]
            radiation = raw["hourly"].get("shortwave_radiation")
            if radiation is None:
                beschikbare_keys = list(raw["hourly"].keys())
                print(f"Solar forecast: shortwave_radiation ontbreekt, beschikbaar: {beschikbare_keys}", flush=True)
                fout = f"shortwave_radiation niet beschikbaar. Beschikbaar: {beschikbare_keys}"
                _forecast_cache["error"] = fout
                _forecast_cache["error_ts"] = now
                return jsonify(error=fout)
            today = datetime.now().strftime("%Y-%m-%d")
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            result = {"today": [], "tomorrow": [], "today_date": today, "tomorrow_date": tomorrow}
            for t, r in zip(times, radiation):
                hour = int(t[11:13])
                if t.startswith(today):
                    result["today"].append({"hour": hour, "radiation": r or 0})
                elif t.startswith(tomorrow):
                    result["tomorrow"].append({"hour": hour, "radiation": r or 0})
            _forecast_cache = {"data": result, "ts": now, "error": None, "error_ts": 0}
            return jsonify(result)
        except Exception as e:
            print(f"Solar forecast fout (poging {poging + 1}/3): {e}", flush=True)
            if poging < 2:
                time.sleep(2)
                continue
            _forecast_cache["error"] = str(e)
            _forecast_cache["error_ts"] = now
            return jsonify(error=str(e))


@app.route("/location/detect")
def location_detect():
    if not require_login():
        return jsonify(error="unauthorized"), 401
    try:
        resp = requests.get("https://ipwho.is/", timeout=5)
        data = resp.json()
        if not data.get("success"):
            return jsonify(error="Kon locatie niet bepalen via IP")
        return jsonify(lat=str(data["latitude"]), lon=str(data["longitude"]),
                       city=data.get("city", ""), country=data.get("country", ""))
    except Exception as e:
        return jsonify(error=str(e))


@app.route("/location/geocode")
def location_geocode():
    if not require_login():
        return jsonify(error="unauthorized"), 401
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify(error="Geen zoekterm opgegeven")
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 1},
            headers={"User-Agent": "SolarBuffer/1.0"},
            timeout=5
        )
        results = resp.json()
        if not results:
            return jsonify(error="Geen resultaat gevonden voor deze plaatsnaam")
        r = results[0]
        return jsonify(lat=r["lat"], lon=r["lon"], name=r.get("display_name", q))
    except Exception as e:
        return jsonify(error=str(e))


@app.route("/location", methods=["GET", "POST"])
def location():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    cfg = load_config()
    error = ""
    if request.method == "POST":
        lat = request.form.get("latitude", "").strip().replace(",", ".")
        lon = request.form.get("longitude", "").strip().replace(",", ".")
        try:
            float(lat)
            float(lon)
        except ValueError:
            error = "Voer geldige coördinaten in (bijv. 52.3676 en 4.9041)"
        if not error:
            cfg["latitude"] = lat
            cfg["longitude"] = lon
            save_config(cfg)
            global _forecast_cache
            _forecast_cache = {"data": None, "ts": 0}
            write_audit_log("location_updated", {"lat": lat, "lon": lon})
            return redirect("/dashboard")
    return render_template("location.html", dark_mode=get_user_dark_mode(),
                           latitude=cfg.get("latitude", ""),
                           longitude=cfg.get("longitude", ""),
                           error=error)


@app.route("/restart", methods=["POST"])
def restart():
    if not require_login():
        return jsonify(success=False), 401
    if not is_current_user_admin():
        return jsonify(success=False, error="Geen toegang"), 403
    write_audit_log("manual_restart", {"user": safe_session_username()})
    def _do_restart():
        cfg = load_config()
        sync_configured_devices_off(cfg.get("shelly_devices", []))
        time.sleep(1)
        if os.name != "nt":
            subprocess.run(["sudo", "systemctl", "restart", "solarbuffer"])
    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify(success=True)


@app.route("/shutdown", methods=["POST"])
def shutdown():
    if not require_login():
        return jsonify(success=False), 401
    if not is_current_user_admin():
        return jsonify(success=False, error="Geen toegang"), 403
    write_audit_log("system_shutdown", {"user": safe_session_username()})
    def _do_shutdown():
        cfg = load_config()
        sync_configured_devices_off(cfg.get("shelly_devices", []))
        time.sleep(1)
        if os.name != "nt":
            subprocess.run(["sudo", "shutdown", "-h", "now"])
    threading.Thread(target=_do_shutdown, daemon=True).start()
    return jsonify(success=True)


@app.route("/factory_reset", methods=["POST"])
def factory_reset():
    if not require_login():
        return jsonify(success=False), 401
    if not is_current_user_admin():
        return jsonify(success=False, error="Geen toegang"), 403
    cfg = load_config()
    threading.Thread(target=sync_configured_devices_off, args=(cfg.get("shelly_devices", []),), daemon=True).start()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({}, f)
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    device_states.clear()
    device_pids.clear()
    write_audit_log("factory_reset", {"user": safe_session_username()})
    session.clear()
    return jsonify(success=True)


@app.route("/config/backup")
def config_backup():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    success = request.args.get("success", "")
    error = request.args.get("error", "")
    return render_template("config_backup.html", dark_mode=get_user_dark_mode(),
                           success=success, error=error)


@app.route("/config/export")
def config_export():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    json_bytes = json.dumps(cfg, indent=4, ensure_ascii=False).encode("utf-8")
    write_audit_log("config_exported", {})
    return send_file(
        io.BytesIO(json_bytes),
        mimetype="application/json",
        as_attachment=True,
        download_name="solarbuffer_config.json"
    )


@app.route("/config/import", methods=["POST"])
def config_import():
    if not require_login():
        return redirect("/login")
    f = request.files.get("config_file")
    if not f:
        return redirect("/config/backup?error=Geen+bestand+geselecteerd")
    try:
        raw = f.read().decode("utf-8")
        imported = json.loads(raw)
        if not isinstance(imported, dict):
            raise ValueError("Geen geldig configuratieobject")
    except Exception as e:
        return redirect(f"/config/backup?error=Ongeldig+JSON-bestand:+{e}")
    with open(CONFIG_FILE, "w", encoding="utf-8") as fout:
        json.dump(imported, fout, indent=4)
    cfg = load_config()
    save_config(cfg)
    new_ips = {d["ip"] for d in cfg.get("shelly_devices", [])}
    for old_ip in list(device_states.keys()):
        if old_ip not in new_ips:
            del device_states[old_ip]
    for old_ip in list(device_pids.keys()):
        if old_ip not in new_ips:
            del device_pids[old_ip]
    init_device_states(cfg["shelly_devices"])
    init_device_pids(cfg["shelly_devices"])
    write_audit_log("config_imported", {"devices": len(cfg.get("shelly_devices", []))})
    return redirect("/config/backup?success=1")


@app.route("/set_theme", methods=["POST"])
def set_theme():
    if not require_login():
        return jsonify(success=False), 401
    data = request.get_json(silent=True) or {}
    dark_mode = bool(data.get("dark_mode", False))
    cfg = load_config()
    username = session.get("username", "")
    user = next((u for u in cfg.get("users", []) if u.get("username") == username), None)
    if user is None:
        return jsonify(success=False), 404
    user["dark_mode"] = dark_mode
    save_config(cfg)
    return jsonify(success=True, dark_mode=dark_mode)


@app.route("/toggle_shelly/<path:ip>")
def toggle_shelly(ip):
    if not require_login():
        return jsonify(success=False), 401
    cfg = load_config()
    device = next((d for d in cfg.get("shelly_devices", []) if d["ip"] == ip), None)
    if not device or ip not in device_states:
        return jsonify(success=False), 404

    st = device_states[ip]
    new_on = not st["on"]

    if new_on:
        ready = ensure_power_socket_on(device)
        if not ready:
            write_audit_log("device_toggle_waiting_for_power_socket", {"device_ip": ip})
            return jsonify(success=False, waiting_for_power_socket=True)
        st["on"] = True
        st["manual_override"] = True
        st["started"] = True
        st["pending_start"] = False
        st["freeze"] = False
        st["saturated_since"] = None
        st["min_since"] = None
        st["brightness"] = MIN_BRIGHTNESS
        st["_reset_offline_timer"] = True
        set_shelly(st["brightness"], True, ip)
        mark_device_activity(device)
    else:
        st["on"] = False
        st["manual_override"] = True
        st["started"] = False
        st["pending_start"] = False
        st["brightness"] = 0
        st["freeze"] = False
        st["saturated_since"] = None
        st["min_since"] = None
        st["waiting_for_power_socket"] = False
        st["power_socket_ready_at"] = None
        threading.Thread(target=graceful_off_device, args=(ip,), daemon=True).start()

    write_audit_log("device_toggled", {
        "device_ip": ip,
        "new_state": "on" if new_on else "off",
        "brightness": st["brightness"]
    })
    return jsonify(success=True, on=new_on)


@app.route("/set_brightness/<path:ip>", methods=["POST"])
def set_brightness_manual(ip):
    if not require_login():
        return jsonify(success=False), 401
    cfg = load_config()
    device = next((d for d in cfg.get("shelly_devices", []) if d["ip"] == ip), None)
    if not device or ip not in device_states:
        return jsonify(success=False), 404

    try:
        brightness = int(request.json.get("brightness", 50))
        brightness = max(0, min(100, brightness))
    except (ValueError, TypeError):
        return jsonify(success=False, error="Ongeldige waarde"), 400

    st = device_states[ip]
    on = brightness > 0

    st["manual_override"] = True
    st["freeze"] = False
    st["saturated_since"] = None
    st["min_since"] = None

    if on:
        if has_power_socket(device):
            ready = ensure_power_socket_on(device)
            if not ready:
                st["started"] = True
                st["pending_start"] = True
                st["brightness"] = brightness
                write_audit_log("brightness_manual_set_waiting_for_socket", {
                    "device_ip": ip,
                    "brightness": brightness
                })
                return jsonify(success=True, waiting_for_socket=True)

        st["brightness"] = brightness
        st["on"] = True
        st["started"] = True
        st["pending_start"] = False
        st["_reset_offline_timer"] = True
        mark_device_activity(device)
    else:
        st["brightness"] = 0
        st["on"] = False
        st["started"] = False
        st["pending_start"] = False
        st["waiting_for_power_socket"] = False
        st["power_socket_ready_at"] = None

    set_shelly(brightness, on, ip)

    write_audit_log("brightness_manual_set", {
        "device_ip": ip,
        "brightness": brightness,
        "on": on
    })
    return jsonify(success=True, brightness=brightness, on=on)


UPDATE_DIR = "/home/solarbuffer/SolarBuffer"

@app.route("/check_updates_available")
def check_updates_available():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    try:
        subprocess.run(["git", "fetch"], cwd=UPDATE_DIR, capture_output=True, timeout=15)
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=UPDATE_DIR, capture_output=True, text=True
        ).stdout.strip()
        remote = subprocess.run(
            ["git", "rev-parse", "@{u}"], cwd=UPDATE_DIR, capture_output=True, text=True
        ).stdout.strip()
        available = bool(local and remote and local != remote)
        return jsonify(success=True, available=available)
    except Exception as e:
        return jsonify(success=False, available=False, error=str(e))


@app.route("/run_update_check")
def run_update_check():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    try:
        pull = subprocess.run(
            ["git", "pull"],
            cwd=UPDATE_DIR,
            capture_output=True, text=True, timeout=60
        )
        output = (pull.stdout + pull.stderr).strip() or "Geen uitvoer"
        already_up_to_date = "already up to date" in output.lower()
        has_changes = pull.returncode == 0 and not already_up_to_date

        write_audit_log("update_check_run", {"returncode": pull.returncode, "has_changes": has_changes})

        if has_changes:
            def delayed_restart():
                cfg = load_config()
                sync_configured_devices_off(cfg.get("shelly_devices", []))
                time.sleep(1.5)
                if os.name != "nt":
                    subprocess.run(["sudo", "systemctl", "restart", "solarbuffer"])
            threading.Thread(target=delayed_restart, daemon=True).start()

        return jsonify(success=True, returncode=pull.returncode, output=output,
                       has_changes=has_changes, restarting=has_changes)
    except subprocess.TimeoutExpired:
        return jsonify(success=False, error="git pull duurde te lang (timeout)"), 500
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500


# ================= FIRMWARE UPDATES =================

def _check_shelly_firmware(device):
    ip = device.get("ip", "")
    name = device.get("name", ip)
    try:
        info_r = requests.get(f"http://{ip}/rpc/Shelly.GetDeviceInfo", timeout=3)
        info = info_r.json() if info_r.status_code == 200 else {}
        current_ver = info.get("ver", "onbekend")

        upd_r = requests.get(f"http://{ip}/rpc/Shelly.CheckForUpdate", timeout=5)
        upd = upd_r.json() if upd_r.status_code == 200 else {}
        stable = upd.get("stable") or {}
        has_update = bool(stable.get("version"))
        latest_ver = stable.get("version", current_ver) if has_update else current_ver

        return {"ip": ip, "name": name, "current_ver": current_ver,
                "latest_ver": latest_ver, "has_update": has_update, "online": True}
    except Exception:
        return {"ip": ip, "name": name, "current_ver": "—",
                "latest_ver": "—", "has_update": False, "online": False}


@app.route("/firmware_check")
def firmware_check():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    cfg = load_config()
    devices = cfg.get("shelly_devices", [])
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_check_shelly_firmware, d): d for d in devices}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception:
                pass
    results.sort(key=lambda x: x["name"].lower())
    return jsonify(devices=results)


@app.route("/firmware_update/<path:ip>", methods=["POST"])
def firmware_update_device(ip):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    cfg = load_config()
    device = next((d for d in cfg.get("shelly_devices", []) if d["ip"] == ip), None)
    if not device:
        return jsonify(success=False, error="Apparaat niet gevonden"), 404
    try:
        r = requests.post(f"http://{ip}/rpc/Shelly.Update",
                          json={"stage": "stable"}, timeout=10)
        if r.status_code == 200:
            write_audit_log("firmware_update_triggered", {"device_ip": ip})
            return jsonify(success=True)
        return jsonify(success=False, error=f"HTTP {r.status_code}"), 500
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500


@app.route("/shelly_factory_reset")
def shelly_factory_reset_page():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    return render_template("shelly_factory_reset.html", dark_mode=get_user_dark_mode())


@app.route("/api/shelly/factory_reset/<path:ip>", methods=["POST"])
def shelly_factory_reset(ip):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    cfg = load_config()
    device = next((d for d in cfg.get("shelly_devices", []) if d["ip"] == ip), None)
    if not device:
        return jsonify(success=False, error="Apparaat niet gevonden"), 404
    try:
        requests.get(f"http://{ip}/rpc/Shelly.FactoryReset", timeout=5)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        pass  # Apparaat herstart direct — verbinding valt weg vóór antwoord, dat is normaal
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500

    # Verwijder apparaat uit config en herstel prioriteiten
    removed_prio = device.get("priority", 1)
    cfg["shelly_devices"] = [d for d in cfg["shelly_devices"] if d["ip"] != ip]
    for d in cfg["shelly_devices"]:
        if d.get("priority", 1) > removed_prio:
            d["priority"] -= 1
    save_config(cfg)
    device_states.pop(ip, None)
    device_pids.pop(ip, None)

    write_audit_log("shelly_factory_reset", {"device_ip": ip, "device_name": device.get("name")})
    return jsonify(success=True)


@app.route("/system_updates_check")
def system_updates_check():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    try:
        subprocess.run(["sudo", "apt-get", "update", "-qq"],
                       capture_output=True, timeout=60)
        result = subprocess.run(
            ["sudo", "apt-get", "full-upgrade", "--dry-run",
             "-o", "Dpkg::Options::=--force-confdef",
             "-o", "Dpkg::Options::=--force-confold"],
            capture_output=True, text=True, timeout=30
        )
        lines = []
        for ln in result.stdout.splitlines():
            ln = ln.strip()
            if ln.startswith("Inst "):
                lines.append(ln[5:])
        return jsonify(success=True, count=len(lines), packages=lines[:30])
    except Exception as e:
        return jsonify(success=False, error=str(e), count=0, packages=[])


def _stream_proc(proc):
    for raw in proc.stdout:
        line = raw.rstrip()
        if line:
            with _hw_update_cond:
                _hw_update_log.append(line)
                _hw_update_cond.notify_all()
    proc.wait()
    return proc.returncode


def _run_apt_upgrade_worker(username):
    global _hw_update_running, _hw_update_done, _hw_update_success
    env = {"DEBIAN_FRONTEND": "noninteractive", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    write_audit_log("system_upgrade_started", {"user": username})

    def run_step(label, cmd):
        with _hw_update_cond:
            _hw_update_log.append(f">>> {label}")
            _hw_update_cond.notify_all()
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env
        )
        return _stream_proc(proc)

    try:
        # Stap 1: herstel onderbroken dpkg-configuratie
        run_step("dpkg --configure -a", [
            "sudo", "dpkg", "--configure", "-a",
            "-o", "Dpkg::Options::=--force-confdef",
            "-o", "Dpkg::Options::=--force-confold",
        ])

        # Stap 2: herstel kapotte afhankelijkheden
        run_step("apt-get install -f", [
            "sudo", "apt-get", "install", "-f", "-y",
            "-o", "Dpkg::Options::=--force-confdef",
            "-o", "Dpkg::Options::=--force-confold",
        ])

        # Stap 3: pakketlijsten vernieuwen
        run_step("apt-get update", ["sudo", "apt-get", "update"])

        # Stap 4: volledige upgrade
        returncode = run_step("apt-get full-upgrade", [
            "sudo", "apt-get", "full-upgrade", "-y",
            "-o", "Dpkg::Progress-Fancy=0",
            "-o", "APT::Color=0",
            "-o", "Dpkg::Options::=--force-confdef",
            "-o", "Dpkg::Options::=--force-confold",
        ])
        success = returncode == 0
        write_audit_log("system_upgrade_run", {"returncode": returncode})
    except Exception as e:
        success = False
        with _hw_update_cond:
            _hw_update_log.append(f"Fout: {e}")
            _hw_update_cond.notify_all()
    with _hw_update_cond:
        _hw_update_running = False
        _hw_update_done = True
        _hw_update_success = success
        _hw_update_cond.notify_all()


@app.route("/system_update_status")
def system_update_status():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(
        running=_hw_update_running,
        done=_hw_update_done,
        success=_hw_update_success,
        log_count=len(_hw_update_log),
    )


@app.route("/run_system_update")
def run_system_update():
    global _hw_update_running, _hw_update_done, _hw_update_success, _hw_update_log
    if not require_login():
        return Response("data: {\"error\": \"unauthorized\"}\n\n", mimetype="text/event-stream"), 401

    reset = request.args.get("reset") == "1"
    if reset or (not _hw_update_running and not _hw_update_done):
        with _hw_update_cond:
            _hw_update_log = []
            _hw_update_done = False
            _hw_update_success = False
            _hw_update_running = True
        threading.Thread(
            target=_run_apt_upgrade_worker,
            args=(safe_session_username(),),
            daemon=True
        ).start()

    def generate():
        idx = 0
        while True:
            with _hw_update_cond:
                _hw_update_cond.wait_for(
                    lambda: idx < len(_hw_update_log) or _hw_update_done,
                    timeout=30
                )
                batch = _hw_update_log[idx:]
                done = _hw_update_done
                success = _hw_update_success

            for i, line in enumerate(batch):
                yield f"id: {idx + i}\ndata: {json.dumps({'line': line})}\n\n"
            idx += len(batch)

            if done and idx >= len(_hw_update_log):
                yield f"data: {json.dumps({'done': True, 'success': success})}\n\n"
                break

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ================= TAILSCALE =================
@app.route("/tailscale_status")
def tailscale_status():
    global _tailscale_auth_url
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401

    if os.name == "nt":
        return jsonify({"installed": False, "connected": False, "ip": None, "auth_url": None})

    check = subprocess.run(["which", "tailscale"], capture_output=True, text=True)
    if check.returncode != 0:
        return jsonify({"installed": False, "connected": False, "ip": None, "auth_url": None})

    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=5
        )
        data = json.loads(result.stdout)
        backend_state = data.get("BackendState", "")
        self_node = data.get("Self") or {}
        ips = self_node.get("TailscaleIPs", [])
        ip = ips[0] if ips else None
        connected = backend_state == "Running"

        auth_url = (data.get("AuthURL") or _tailscale_auth_url) if not connected else None
        if connected:
            _tailscale_auth_url = None

        return jsonify({
            "installed": True,
            "connected": connected,
            "ip": ip,
            "backend_state": backend_state,
            "auth_url": auth_url
        })
    except Exception:
        return jsonify({
            "installed": True,
            "connected": False,
            "ip": None,
            "auth_url": _tailscale_auth_url
        })


@app.route("/tailscale_connect", methods=["POST"])
def tailscale_connect():
    global _tailscale_auth_url
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401

    _tailscale_auth_url = None

    def _run():
        global _tailscale_auth_url
        try:
            proc = subprocess.Popen(
                ["sudo", "tailscale", "up"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            for line in proc.stdout:
                match = re.search(r'https://login\.tailscale\.com\S+', line)
                if match:
                    _tailscale_auth_url = match.group(0).rstrip('.')
                    break
            proc.wait()
        except Exception as e:
            print(f"Tailscale connect fout: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"success": True})


# ================= TIJDSCHEMA ROUTES =================
def _valid_time(t):
    return bool(re.match(r"^\d{2}:\d{2}$", str(t)))


@app.route("/schedules", methods=["GET"])
def get_schedules():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    cfg = load_config()
    return jsonify(schedules=cfg.get("schedules", []))


@app.route("/schedules", methods=["POST"])
def create_schedule():
    try:
        if not require_login():
            return jsonify({"error": "unauthorized"}), 401
        if not is_current_user_admin():
            return jsonify({"error": "Geen toegang"}), 403
        data = request.get_json(silent=True) or {}
        days = data.get("days", [])
        start_time = str(data.get("start_time", ""))
        end_time = str(data.get("end_time", ""))
        brightness = data.get("brightness", 50)
        name = str(data.get("name", ""))[:50]
        if not isinstance(days, list) or not days:
            return jsonify(success=False, error="Geen dagen geselecteerd"), 400
        if not _valid_time(start_time) or not _valid_time(end_time):
            return jsonify(success=False, error="Ongeldige tijd (gebruik HH:MM)"), 400
        try:
            brightness = int(brightness)
            if not (1 <= brightness <= 100):
                raise ValueError
        except (ValueError, TypeError):
            return jsonify(success=False, error="Helderheid moet tussen 1 en 100 zijn"), 400
        cfg = load_config()
        valid_ips = {d["ip"] for d in cfg.get("shelly_devices", [])}
        raw_ips = data.get("device_ips", [])
        device_ips = [ip for ip in (raw_ips or []) if isinstance(ip, str) and ip in valid_ips]
        new_sched = {
            "id": str(uuid.uuid4()),
            "name": name,
            "days": sorted({int(d) for d in days if isinstance(d, (int, float)) and 0 <= int(d) <= 6}),
            "start_time": start_time,
            "end_time": end_time,
            "brightness": brightness,
            "device_ips": device_ips,
            "enabled": True,
        }
        cfg["schedules"].append(new_sched)
        save_config(cfg)
        write_audit_log("schedule_created", {"id": new_sched["id"], "name": new_sched["name"]})
        return jsonify(success=True, schedule=new_sched)
    except Exception as e:
        traceback.print_exc()
        return jsonify(success=False, error=f"Serverfout: {e}"), 500


@app.route("/schedules/<sched_id>", methods=["PUT"])
def update_schedule(sched_id):
    try:
        if not require_login():
            return jsonify({"error": "unauthorized"}), 401
        if not is_current_user_admin():
            return jsonify({"error": "Geen toegang"}), 403
        data = request.get_json(silent=True) or {}
        cfg = load_config()
        schedules = cfg.get("schedules", [])
        idx = next((i for i, s in enumerate(schedules) if s.get("id") == sched_id), None)
        if idx is None:
            return jsonify(success=False, error="Niet gevonden"), 404
        sched = schedules[idx]
        if "days" in data:
            sched["days"] = sorted({int(d) for d in data["days"] if isinstance(d, (int, float)) and 0 <= int(d) <= 6})
        if "start_time" in data and _valid_time(data["start_time"]):
            sched["start_time"] = str(data["start_time"])
        if "end_time" in data and _valid_time(data["end_time"]):
            sched["end_time"] = str(data["end_time"])
        if "brightness" in data:
            try:
                b = int(data["brightness"])
                if 1 <= b <= 100:
                    sched["brightness"] = b
            except (ValueError, TypeError):
                pass
        if "name" in data:
            sched["name"] = str(data["name"])[:50]
        if "enabled" in data:
            sched["enabled"] = bool(data["enabled"])
        if "device_ips" in data:
            valid_ips = {d["ip"] for d in cfg.get("shelly_devices", [])}
            raw_ips = data["device_ips"]
            sched["device_ips"] = [ip for ip in (raw_ips or []) if isinstance(ip, str) and ip in valid_ips]
        cfg["schedules"] = schedules
        save_config(cfg)
        write_audit_log("schedule_updated", {"id": sched_id})
        return jsonify(success=True, schedule=sched)
    except Exception as e:
        traceback.print_exc()
        return jsonify(success=False, error=f"Serverfout: {e}"), 500


@app.route("/schedules/<sched_id>", methods=["DELETE"])
def delete_schedule(sched_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    cfg = load_config()
    schedules = cfg.get("schedules", [])
    new_schedules = [s for s in schedules if s.get("id") != sched_id]
    if len(new_schedules) == len(schedules):
        return jsonify(success=False, error="Niet gevonden"), 404
    cfg["schedules"] = new_schedules
    save_config(cfg)
    write_audit_log("schedule_deleted", {"id": sched_id})
    return jsonify(success=True)


# ================= ACCESSORIES =================
@app.route("/scan_accessories")
def scan_accessories():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401

    cfg = load_config()
    used_ips = set()
    used_ips.add((cfg.get("p1_ip") or "").strip())
    for d in cfg.get("shelly_devices", []):
        used_ips.add((d.get("ip") or "").strip())
        used_ips.add((d.get("power_ip") or "").strip())
        used_ips.add((d.get("power_socket_ip") or "").strip())
    for a in cfg.get("accessories", []):
        for ip in a.get("power_ips", [(a.get("power_ip") or "")]):
            used_ips.add(ip.strip())
    used_ips.discard("")

    ips = get_subnet_ips()
    found_power = []
    found_temp = []
    with ThreadPoolExecutor(max_workers=25) as executor:
        futures = {}
        for ip in ips:
            futures[executor.submit(detect_shelly_pm, ip)] = ("power", ip)
            futures[executor.submit(detect_homewizard_pm, ip)] = ("power", ip)
            futures[executor.submit(detect_shelly_temp, ip)] = ("temp", ip)
        for future in as_completed(futures):
            try:
                result = future.result()
                if not result:
                    continue
                kind = futures[future][0]
                if kind == "power" and result["ip"] not in used_ips:
                    if not any(f["ip"] == result["ip"] for f in found_power):
                        found_power.append(result)
                elif kind == "temp":
                    # nooit filteren op IP — per kanaal aparte accessoire mogelijk
                    if not any(f["ip"] == result["ip"] for f in found_temp):
                        found_temp.append(result)
            except Exception:
                pass

    found_power.sort(key=lambda d: d["ip"])
    found_temp.sort(key=lambda d: d["ip"])
    return jsonify(devices=found_power, temp_devices=found_temp)


@app.route("/charts")
def charts_page():
    if not require_login():
        return redirect("/login")
    return render_template("charts.html", dark_mode=get_user_dark_mode())


@app.route("/accessories", methods=["GET"])
def accessories_page():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    cfg = load_config()
    return render_template("accessories.html", accessories=cfg.get("accessories", []),
                           shelly_devices=cfg.get("shelly_devices", []), gas_enabled=cfg.get("gas_enabled", False), dark_mode=get_user_dark_mode())


@app.route("/accessories", methods=["POST"])
def add_accessory():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    acc_type = (data.get("acc_type") or "power").strip().lower()
    icon = (data.get("icon") or "mdi-power-plug").strip()
    cfg = load_config()
    if acc_type == "temperature":
        temp_ip = (data.get("temp_ip") or "").strip()
        raw_ch = data.get("temp_channel", 100)
        try:
            temp_channel = int(raw_ch)
        except (TypeError, ValueError):
            temp_channel = 100
        linked_device_ip = (data.get("linked_device_ip") or "").strip()
        if not name or not temp_ip:
            return jsonify(success=False, error="Naam en IP-adres zijn verplicht"), 400
        record_history = bool(data.get("record_history", False))
        new_acc = {"id": str(uuid.uuid4()), "name": name, "acc_type": "temperature",
                   "temp_ip": temp_ip, "temp_channel": temp_channel,
                   "linked_device_ip": linked_device_ip, "icon": icon,
                   "record_history": record_history}
    else:
        pm_type = (data.get("power_meter_type") or "").strip().lower()
        pm_ips = [ip.strip() for ip in data.get("power_ips", []) if str(ip).strip()]
        if not name or not pm_type or not pm_ips:
            return jsonify(success=False, error="Naam, type en minimaal één IP zijn verplicht"), 400
        if pm_type not in ("shelly", "homewizard"):
            return jsonify(success=False, error="Ongeldig type"), 400
        record_history = bool(data.get("record_history", False))
        is_solar = bool(data.get("is_solar", False))
        new_acc = {"id": str(uuid.uuid4()), "name": name, "acc_type": "power",
                   "power_meter_type": pm_type, "power_ip": pm_ips[0], "power_ips": pm_ips, "icon": icon,
                   "record_history": record_history, "is_solar": is_solar}
    cfg["accessories"].append(new_acc)
    save_config(cfg)
    write_audit_log("accessory_added", {"name": name, "acc_type": acc_type})
    return jsonify(success=True, accessory=new_acc)


@app.route("/accessories/<acc_id>", methods=["PUT"])
def update_accessory(acc_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    data = request.get_json(force=True) or {}
    cfg = load_config()
    acc = next((a for a in cfg["accessories"] if a["id"] == acc_id), None)
    if not acc:
        return jsonify(success=False, error="Niet gevonden"), 404
    name = (data.get("name") or "").strip()
    acc_type = (data.get("acc_type") or acc.get("acc_type") or "power").strip().lower()
    icon = (data.get("icon") or "mdi-power-plug").strip()
    if acc_type == "temperature":
        temp_ip = (data.get("temp_ip") or "").strip()
        raw_ch = data.get("temp_channel", acc.get("temp_channel", 100))
        try:
            temp_channel = int(raw_ch)
        except (TypeError, ValueError):
            temp_channel = 100
        linked_device_ip = (data.get("linked_device_ip") or "").strip()
        if not name or not temp_ip:
            return jsonify(success=False, error="Naam en IP-adres zijn verplicht"), 400
        acc["name"] = name
        acc["acc_type"] = "temperature"
        acc["temp_ip"] = temp_ip
        acc["temp_channel"] = temp_channel
        acc.pop("temp_channels", None)
        acc["linked_device_ip"] = linked_device_ip
        acc["icon"] = icon
        acc["record_history"] = bool(data.get("record_history", acc.get("record_history", False)))
    else:
        pm_type = (data.get("power_meter_type") or "").strip().lower()
        pm_ips = [ip.strip() for ip in data.get("power_ips", []) if str(ip).strip()]
        if not name or not pm_type or not pm_ips:
            return jsonify(success=False, error="Naam, type en minimaal één IP zijn verplicht"), 400
        if pm_type not in ("shelly", "homewizard"):
            return jsonify(success=False, error="Ongeldig type"), 400
        acc["name"] = name
        acc["acc_type"] = "power"
        acc["power_meter_type"] = pm_type
        acc["power_ip"] = pm_ips[0]
        acc["power_ips"] = pm_ips
        acc["icon"] = icon
        acc["record_history"] = bool(data.get("record_history", acc.get("record_history", False)))
        acc["is_solar"] = bool(data.get("is_solar", acc.get("is_solar", False)))
    save_config(cfg)
    write_audit_log("accessory_updated", {"id": acc_id, "name": name})
    return jsonify(success=True)


@app.route("/accessories/<acc_id>", methods=["DELETE"])
def delete_accessory(acc_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    cfg = load_config()
    before = len(cfg["accessories"])
    cfg["accessories"] = [a for a in cfg["accessories"] if a["id"] != acc_id]
    if len(cfg["accessories"]) == before:
        return jsonify(success=False, error="Niet gevonden"), 404
    accessory_states.pop(acc_id, None)
    save_config(cfg)
    write_audit_log("accessory_deleted", {"id": acc_id})
    return jsonify(success=True)


# ================= BROADLINK =================

def _broadlink_connect(ip, mac_str, devtype):
    if not BROADLINK_AVAILABLE:
        return None
    try:
        mac_bytes = bytes.fromhex(mac_str.replace(":", "").replace("-", ""))
        dev = _broadlink_lib.gendevice(devtype, (ip, 80), mac_bytes)
        dev.auth()
        return dev
    except Exception:
        return None


def _broadlink_discover_once(timeout=5):
    if not BROADLINK_AVAILABLE:
        return []
    try:
        devices = _broadlink_lib.discover(timeout=timeout)
        result = []
        for d in devices:
            try:
                ip = d.host[0]
                mac = ":".join(f"{b:02x}" for b in d.mac)
            except Exception:
                continue
            try:
                d.auth()
            except Exception:
                pass
            result.append({
                "ip": ip,
                "mac": mac,
                "devtype": d.devtype,
                "model": getattr(d, "model", str(d.devtype)),
                "type": getattr(d, "type", "Unknown"),
            })
        return result
    except Exception:
        return []


@app.route("/settings/broadlink")
def settings_broadlink():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    cfg = load_config()
    power_accessories = [
        {"id": a["id"], "name": a["name"]}
        for a in cfg.get("accessories", [])
        if a.get("acc_type", "power") == "power" and not a.get("is_solar", False)
    ]
    return render_template(
        "settings_broadlink.html",
        config=cfg,
        power_accessories=power_accessories,
        broadlink_available=BROADLINK_AVAILABLE,
        dark_mode=get_user_dark_mode(),
    )


@app.route("/api/broadlink/scan", methods=["POST"])
def broadlink_scan():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    if not BROADLINK_AVAILABLE:
        return jsonify(success=False, error="python-broadlink niet geïnstalleerd"), 503
    found = _broadlink_discover_once(timeout=5)
    cfg = load_config()
    existing_ips = {bl["ip"] for bl in cfg.get("broadlink_devices", [])}
    new_devices = [d for d in found if d["ip"] not in existing_ips]
    return jsonify(success=True, devices=found, new_devices=new_devices)


@app.route("/api/broadlink/online", methods=["GET"])
def broadlink_online_status():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(success=True, online=_broadlink_online)


@app.route("/api/broadlink/devices", methods=["POST"])
def broadlink_add_device():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    data = request.get_json(force=True) or {}
    ip = (data.get("ip") or "").strip()
    mac = (data.get("mac") or "").strip()
    devtype = int(data.get("devtype") or 0)
    model = (data.get("model") or "Broadlink").strip()
    name = (data.get("name") or model).strip()
    if not ip:
        return jsonify(success=False, error="IP-adres is verplicht"), 400
    cfg = load_config()
    if any(bl["ip"] == ip for bl in cfg.get("broadlink_devices", [])):
        return jsonify(success=False, error="Apparaat al toegevoegd"), 409
    new_bl = {
        "id": str(uuid.uuid4()),
        "name": name,
        "ip": ip,
        "mac": mac,
        "devtype": devtype,
        "ir_devices": [],
    }
    cfg["broadlink_devices"].append(new_bl)
    save_config(cfg)
    write_audit_log("broadlink_device_added", {"ip": ip, "name": name})
    return jsonify(success=True, device=new_bl)


@app.route("/api/broadlink/devices/<bl_id>", methods=["PUT"])
def broadlink_update_device(bl_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    data = request.get_json(force=True) or {}
    cfg = load_config()
    bl = next((b for b in cfg.get("broadlink_devices", []) if b["id"] == bl_id), None)
    if not bl:
        return jsonify(success=False, error="Niet gevonden"), 404
    bl["name"] = (data.get("name") or bl["name"]).strip()
    save_config(cfg)
    return jsonify(success=True)


@app.route("/api/broadlink/devices/<bl_id>", methods=["DELETE"])
def broadlink_delete_device(bl_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    cfg = load_config()
    before = len(cfg.get("broadlink_devices", []))
    cfg["broadlink_devices"] = [b for b in cfg.get("broadlink_devices", []) if b["id"] != bl_id]
    if len(cfg["broadlink_devices"]) == before:
        return jsonify(success=False, error="Niet gevonden"), 404
    save_config(cfg)
    write_audit_log("broadlink_device_deleted", {"id": bl_id})
    return jsonify(success=True)


@app.route("/api/broadlink/<bl_id>/ir_devices", methods=["POST"])
def broadlink_add_ir_device(bl_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    data = request.get_json(force=True) or {}
    cfg = load_config()
    bl = next((b for b in cfg.get("broadlink_devices", []) if b["id"] == bl_id), None)
    if not bl:
        return jsonify(success=False, error="Broadlink-apparaat niet gevonden"), 404
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(success=False, error="Naam is verplicht"), 400
    icon = (data.get("icon") or "mdi-remote").strip()
    show_on_dashboard = bool(data.get("show_on_dashboard", True))
    linked_accessory_id = (data.get("linked_accessory_id") or "").strip()
    new_ir = {
        "id": str(uuid.uuid4()),
        "name": name,
        "icon": icon,
        "show_on_dashboard": show_on_dashboard,
        "linked_accessory_id": linked_accessory_id,
        "commands": [],
    }
    bl["ir_devices"].append(new_ir)
    save_config(cfg)
    write_audit_log("broadlink_ir_device_added", {"bl_id": bl_id, "name": name})
    return jsonify(success=True, ir_device=new_ir)


@app.route("/api/broadlink/<bl_id>/ir_devices/<ir_id>", methods=["PUT"])
def broadlink_update_ir_device(bl_id, ir_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    data = request.get_json(force=True) or {}
    cfg = load_config()
    bl = next((b for b in cfg.get("broadlink_devices", []) if b["id"] == bl_id), None)
    if not bl:
        return jsonify(success=False, error="Niet gevonden"), 404
    ir = next((d for d in bl["ir_devices"] if d["id"] == ir_id), None)
    if not ir:
        return jsonify(success=False, error="Niet gevonden"), 404
    ir["name"] = (data.get("name") or ir["name"]).strip()
    ir["icon"] = (data.get("icon") or ir["icon"]).strip()
    ir["show_on_dashboard"] = bool(data.get("show_on_dashboard", ir.get("show_on_dashboard", True)))
    ir["linked_accessory_id"] = (data.get("linked_accessory_id") or "").strip()
    save_config(cfg)
    return jsonify(success=True)


@app.route("/api/broadlink/<bl_id>/ir_devices/<ir_id>", methods=["DELETE"])
def broadlink_delete_ir_device(bl_id, ir_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    cfg = load_config()
    bl = next((b for b in cfg.get("broadlink_devices", []) if b["id"] == bl_id), None)
    if not bl:
        return jsonify(success=False, error="Niet gevonden"), 404
    before = len(bl["ir_devices"])
    bl["ir_devices"] = [d for d in bl["ir_devices"] if d["id"] != ir_id]
    if len(bl["ir_devices"]) == before:
        return jsonify(success=False, error="Niet gevonden"), 404
    save_config(cfg)
    write_audit_log("broadlink_ir_device_deleted", {"bl_id": bl_id, "ir_id": ir_id})
    return jsonify(success=True)


@app.route("/api/broadlink/<bl_id>/ir_devices/<ir_id>/learn", methods=["POST"])
def broadlink_learn(bl_id, ir_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    if not BROADLINK_AVAILABLE:
        return jsonify(success=False, error="python-broadlink niet geïnstalleerd"), 503
    cfg = load_config()
    bl = next((b for b in cfg.get("broadlink_devices", []) if b["id"] == bl_id), None)
    if not bl:
        return jsonify(success=False, error="Broadlink-apparaat niet gevonden"), 404
    dev = _broadlink_connect(bl["ip"], bl["mac"], bl["devtype"])
    if not dev:
        return jsonify(success=False, error="Kan geen verbinding maken met apparaat"), 502
    try:
        dev.enter_learning()
        deadline = time.time() + 15
        code_bytes = None
        while time.time() < deadline:
            time.sleep(1)
            try:
                code_bytes = dev.check_data()
                if code_bytes:
                    break
            except Exception:
                pass
        if not code_bytes:
            return jsonify(success=False, error="Geen IR-signaal ontvangen binnen 15 seconden"), 408
        import base64
        code_b64 = base64.b64encode(code_bytes).decode()
        return jsonify(success=True, code=code_b64)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500


@app.route("/api/broadlink/<bl_id>/ir_devices/<ir_id>/commands", methods=["POST"])
def broadlink_add_command(bl_id, ir_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    data = request.get_json(force=True) or {}
    cfg = load_config()
    bl = next((b for b in cfg.get("broadlink_devices", []) if b["id"] == bl_id), None)
    if not bl:
        return jsonify(success=False, error="Niet gevonden"), 404
    ir = next((d for d in bl["ir_devices"] if d["id"] == ir_id), None)
    if not ir:
        return jsonify(success=False, error="Niet gevonden"), 404
    name = (data.get("name") or "").strip()
    code = (data.get("code") or "").strip()
    if not name or not code:
        return jsonify(success=False, error="Naam en IR-code zijn verplicht"), 400
    new_cmd = {"id": str(uuid.uuid4()), "name": name, "code": code}
    ir["commands"].append(new_cmd)
    save_config(cfg)
    write_audit_log("broadlink_command_added", {"bl_id": bl_id, "ir_id": ir_id, "name": name})
    return jsonify(success=True, command=new_cmd)


@app.route("/api/broadlink/<bl_id>/ir_devices/<ir_id>/commands/reorder", methods=["PUT"])
def broadlink_reorder_commands(bl_id, ir_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    data = request.get_json(force=True) or {}
    order = data.get("order", [])
    cfg = load_config()
    bl = next((b for b in cfg.get("broadlink_devices", []) if b["id"] == bl_id), None)
    if not bl:
        return jsonify(success=False, error="Niet gevonden"), 404
    ir = next((d for d in bl["ir_devices"] if d["id"] == ir_id), None)
    if not ir:
        return jsonify(success=False, error="Niet gevonden"), 404
    id_map = {cmd["id"]: cmd for cmd in ir["commands"]}
    ir["commands"] = [id_map[cid] for cid in order if cid in id_map]
    save_config(cfg)
    return jsonify(success=True)


@app.route("/api/broadlink/<bl_id>/ir_devices/<ir_id>/commands/<cmd_id>", methods=["DELETE"])
def broadlink_delete_command(bl_id, ir_id, cmd_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    cfg = load_config()
    bl = next((b for b in cfg.get("broadlink_devices", []) if b["id"] == bl_id), None)
    if not bl:
        return jsonify(success=False, error="Niet gevonden"), 404
    ir = next((d for d in bl["ir_devices"] if d["id"] == ir_id), None)
    if not ir:
        return jsonify(success=False, error="Niet gevonden"), 404
    before = len(ir["commands"])
    ir["commands"] = [c for c in ir["commands"] if c["id"] != cmd_id]
    if len(ir["commands"]) == before:
        return jsonify(success=False, error="Niet gevonden"), 404
    save_config(cfg)
    return jsonify(success=True)


@app.route("/api/broadlink/<bl_id>/ir_devices/<ir_id>/commands/<cmd_id>/send", methods=["POST"])
def broadlink_send_command(bl_id, ir_id, cmd_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not BROADLINK_AVAILABLE:
        return jsonify(success=False, error="python-broadlink niet geïnstalleerd"), 503
    cfg = load_config()
    bl = next((b for b in cfg.get("broadlink_devices", []) if b["id"] == bl_id), None)
    if not bl:
        return jsonify(success=False, error="Niet gevonden"), 404
    ir = next((d for d in bl["ir_devices"] if d["id"] == ir_id), None)
    if not ir:
        return jsonify(success=False, error="Niet gevonden"), 404
    cmd = next((c for c in ir["commands"] if c["id"] == cmd_id), None)
    if not cmd:
        return jsonify(success=False, error="Niet gevonden"), 404
    dev = _broadlink_connect(bl["ip"], bl["mac"], bl["devtype"])
    if not dev:
        return jsonify(success=False, error="Kan geen verbinding maken met apparaat"), 502
    try:
        import base64
        dev.send_data(base64.b64decode(cmd["code"]))
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e)), 500


@app.route("/set_gas_enabled", methods=["POST"])
def set_gas_enabled():
    global gas_day_start_m3, gas_day_date, current_gas_m3
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True) or {}
    cfg = load_config()
    cfg["gas_enabled"] = bool(data.get("enabled", False))
    save_config(cfg)
    # reset dagbaseline zodat hij opnieuw start vanaf het inschakelen
    if cfg["gas_enabled"] and current_gas_m3 is not None:
        gas_day_date = datetime.now().date().isoformat()
        gas_day_start_m3 = current_gas_m3
        save_state(force=True)
    return jsonify(success=True)


@app.route("/users")
def users_page():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    cfg = load_config()
    error = request.args.get("error", "")
    current_user = session.get("username", "")
    return render_template("users.html", users=cfg.get("users", []), error=error,
                           dark_mode=get_user_dark_mode(), current_user=current_user)


@app.route("/users/add", methods=["POST"])
def users_add():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "viewer")
    if role not in ("admin", "viewer"):
        role = "viewer"
    if not username or not password:
        return redirect("/users?error=Vul alle velden in")
    if len(password) < 6:
        return redirect("/users?error=Wachtwoord moet minimaal 6 tekens bevatten")
    cfg = load_config()
    users = cfg.get("users", [])
    if any(u["username"].lower() == username.lower() for u in users):
        return redirect("/users?error=Gebruikersnaam bestaat al")
    users.append({"username": username, "password_hash": generate_password_hash(password), "role": role})
    cfg["users"] = users
    save_config(cfg)
    write_audit_log("user_added", {"new_username": username, "role": role})
    return redirect("/users")


@app.route("/users/delete", methods=["POST"])
def users_delete():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    username = request.form.get("username", "").strip()
    current_user = session.get("username", "")
    cfg = load_config()
    users = cfg.get("users", [])
    if username == current_user:
        return redirect("/users?error=Je kunt je eigen account niet verwijderen")
    if len(users) <= 1:
        return redirect("/users?error=Laatste gebruiker kan niet worden verwijderd")
    cfg["users"] = [u for u in users if u["username"] != username]
    save_config(cfg)
    write_audit_log("user_deleted", {"deleted_username": username})
    return redirect("/users")


@app.route("/users/set_role", methods=["POST"])
def users_set_role():
    if not require_login():
        return redirect("/login")
    if not is_current_user_admin():
        return redirect("/dashboard")
    username = request.form.get("username", "").strip()
    role = request.form.get("role", "viewer")
    if role not in ("admin", "viewer"):
        return redirect("/users?error=Ongeldig rol")
    current_user = session.get("username", "")
    cfg = load_config()
    users = cfg.get("users", [])
    admins = [u for u in users if u.get("role", "admin") == "admin"]
    target = next((u for u in users if u["username"] == username), None)
    if not target:
        return redirect("/users?error=Gebruiker niet gevonden")
    if username == current_user and role != "admin":
        return redirect("/users?error=Je kunt je eigen admin-rol niet verwijderen")
    if target.get("role", "admin") == "admin" and role == "viewer" and len(admins) <= 1:
        return redirect("/users?error=Er moet minimaal één admin zijn")
    target["role"] = role
    save_config(cfg)
    write_audit_log("user_role_changed", {"username": username, "role": role})
    return redirect("/users")


@app.route("/scan_devices", methods=["GET"])
def scan_devices():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    try:
        result = scan_network_for_devices()
        write_audit_log("network_scan", {
            "p1_found": len(result.get("p1_meters", [])),
            "shelly_found": len(result.get("shelly_devices", []))
        })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ================= SHELLY / POWER =================
def set_shelly(brightness, on, ip):
    try:
        requests.post(
            f"http://{ip}/rpc/Light.Set",
            json={"id": 0, "on": on, "brightness": round(brightness)},
            timeout=2
        )
    except Exception as e:
        print(f"Shelly error ({ip}): {e}")


def check_shelly_online(ip):
    try:
        r = requests.get(f"http://{ip}/rpc/Shelly.GetStatus", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def check_http_device_online(ip, path):
    try:
        r = requests.get(f"http://{ip}{path}", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def get_homewizard_power(ip):
    pw, _ = get_homewizard_power_and_energy(ip)
    return pw


def get_homewizard_power_and_energy(ip):
    """Returns (active_power_w, total_import_kwh). Energy is None when unavailable."""
    try:
        r = requests.get(f"http://{ip}/api/v1/data", timeout=2)
        if r.status_code != 200:
            return 0.0, None
        data = r.json()
        pw = float(data.get("active_power_w", 0) or 0)
        raw = data.get("total_power_import_kwh")
        total_kwh = float(raw) if raw is not None else None
        return pw, total_kwh
    except Exception as e:
        print(f"HomeWizard power error ({ip}): {e}")
        return 0.0, None


def get_shelly_device_power(ip):
    pw, _ = get_shelly_power_and_energy(ip)
    return pw


def get_shelly_power_and_energy(ip):
    """Returns (apower_w, aenergy_total_wh). Energy is None when unavailable."""
    endpoints = [
        f"http://{ip}/rpc/Switch.GetStatus?id=0",
        f"http://{ip}/rpc/PM1.GetStatus?id=0",
        f"http://{ip}/rpc/EM.GetStatus?id=0",
        f"http://{ip}/rpc/Shelly.GetStatus",
    ]
    for url in endpoints:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code != 200:
                continue
            data = r.json()
            if not isinstance(data, dict):
                continue
            if "apower" in data:
                pw = float(data.get("apower", 0) or 0)
                ae = data.get("aenergy") or {}
                total_wh = float(ae["total"]) if "total" in ae else None
                return pw, total_wh
            for value in data.values():
                if isinstance(value, dict) and "apower" in value:
                    pw = float(value.get("apower", 0) or 0)
                    ae = value.get("aenergy") or {}
                    total_wh = float(ae["total"]) if "total" in ae else None
                    return pw, total_wh
        except Exception:
            pass
    return 0.0, None


def get_shelly_temperature(ip, channel=100):
    try:
        r = requests.get(f"http://{ip}/rpc/Temperature.GetStatus?id={channel}", timeout=2)
        data = r.json()
        if isinstance(data, dict) and data.get("tC") is not None:
            return round(float(data["tC"]), 1)
    except Exception:
        pass
    return None


# ================= POWER SOCKET HELPERS =================
def has_power_socket(device):
    return bool((device.get("power_socket_type") or "").strip() and (device.get("power_socket_ip") or "").strip())


def set_power_socket(power_socket_type, ip, on):
    try:
        pst = (power_socket_type or "").lower().strip()
        if not pst or not ip:
            return False
        if pst == "shelly":
            r = requests.post(f"http://{ip}/rpc/Switch.Set", json={"id": 0, "on": on}, timeout=2)
            return r.status_code == 200
        elif pst == "homewizard":
            r = requests.put(f"http://{ip}/api/v1/state", json={"power_on": on}, timeout=2)
            return r.status_code == 200
    except Exception as e:
        print(f"Power socket error ({power_socket_type} {ip}): {e}")
    return False


def check_power_socket_online(power_socket_type, ip):
    try:
        pst = (power_socket_type or "").lower().strip()
        if not pst or not ip:
            return False
        if pst == "shelly":
            r = requests.get(f"http://{ip}/rpc/Shelly.GetStatus", timeout=2)
            return r.status_code == 200
        elif pst == "homewizard":
            r = requests.get(f"http://{ip}/api", timeout=2)
            return r.status_code == 200
    except Exception:
        pass
    return False


def mark_device_activity(device):
    st = get_device_state(device)
    now = time.time()
    st["last_active_time"] = now
    if has_power_socket(device):
        st["power_socket_last_on_command"] = now
    save_state()


def ensure_power_socket_on(device):
    st = get_device_state(device)
    if not has_power_socket(device):
        return True
    pstype = device.get("power_socket_type")
    psip = device.get("power_socket_ip")
    cfg = load_config()
    settings = get_runtime_settings(cfg)
    delay = int(settings.get("POWER_SOCKET_DELAY", 5) or 5)

    if st["power_socket_on"] and not st["waiting_for_power_socket"]:
        st["power_socket_last_on_command"] = time.time()
        return True
    if not st["power_socket_on"] and not st["waiting_for_power_socket"]:
        ok = set_power_socket(pstype, psip, True)
        if ok:
            now = time.time()
            st["power_socket_on"] = True
            st["power_socket_last_on_command"] = now
            st["waiting_for_power_socket"] = True
            st["power_socket_ready_at"] = now + delay
            st["pending_start"] = True
        return False
    if st["waiting_for_power_socket"]:
        if time.time() >= (st["power_socket_ready_at"] or 0):
            st["waiting_for_power_socket"] = False
            st["power_socket_ready_at"] = None
            st["power_socket_last_on_command"] = time.time()
            return True
        return False
    return False


def maybe_turn_off_power_socket(device):
    st = get_device_state(device)
    if not has_power_socket(device):
        return
    cfg = load_config()
    settings = get_runtime_settings(cfg)
    hold_seconds = int(settings.get("POWER_SOCKET_HOLD_SECONDS", 3600) or 3600)
    if st["started"] or st["on"] or st["brightness"] > 0 or st["waiting_for_power_socket"] or st.get("pending_start"):
        return
    last_cmd = st.get("power_socket_last_on_command", 0)
    if not st["power_socket_on"]:
        return
    if last_cmd and (time.time() - last_cmd) >= hold_seconds:
        pstype = device.get("power_socket_type")
        psip = device.get("power_socket_ip")
        ok = set_power_socket(pstype, psip, False)
        if ok:
            st["power_socket_on"] = False
            st["power_socket_ready_at"] = None
            st["waiting_for_power_socket"] = False


# ================= STATE INIT =================
def init_device_states(devices):
    global device_states
    saved = load_state()
    baselines = load_energy_baselines()
    today_str = datetime.now().strftime("%Y-%m-%d")
    for d in devices:
        ip = d["ip"]
        if ip not in device_states:
            s = saved.get(ip, {})
            bl = baselines.get(ip, {})
            device_states[ip] = {
                "on": False, "brightness": 0, "online": False,
                "power_meter_online": False, "manual_override": False,
                "freeze": False, "started": False, "pending_start": False,
                "saturated_since": None, "min_since": None,
                "last_active_time": s.get("last_active_time", time.time()), "power": 0,
                "power_socket_on": False, "power_socket_online": False,
                "power_socket_last_on_command": 0,
                "waiting_for_power_socket": False, "power_socket_ready_at": None,
                "legionella_active": s.get("legionella_active", False),
                "legionella_start": s.get("legionella_start"),
                "boost_until": None,
                "price_triggered": False,
                "pre_schedule_started": None,
                "pre_schedule_brightness": None,
                "pre_schedule_freeze": None,
                "pre_legionella_started": None,
                "pre_legionella_brightness": None,
                "pre_legionella_freeze": None,
                "energy_today_kwh": 0.0,
                "energy_day_date": today_str if bl.get("date") == today_str else "",
                "energy_day_start_wh": bl.get("start_wh") if bl.get("date") == today_str else None,
            }


def init_device_pids(devices):
    global device_pids
    for d in devices:
        ip = d["ip"]
        if ip not in device_pids:
            p = PID(PID_KP, PID_KI, PID_KD, setpoint=20, sample_time=2)
            p.output_limits = (MIN_BRIGHTNESS, MAX_BRIGHTNESS)
            device_pids[ip] = p


def _is_host_reachable(ip, port=80, timeout=0.3):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _sync_single_device_off(d):
    ip = d["ip"]
    if ip not in device_states:
        return
    state = device_states[ip]
    state["on"] = False
    state["brightness"] = 0
    state["freeze"] = False
    state["started"] = False
    state["pending_start"] = False
    state["manual_override"] = False
    state["saturated_since"] = None
    state["min_since"] = None
    state["waiting_for_power_socket"] = False
    state["power_socket_ready_at"] = None
    state["power_socket_on"] = False
    state["power_socket_last_on_command"] = 0

    # Quick TCP pre-check: skip HTTP calls entirely if device is unreachable.
    if not _is_host_reachable(ip):
        return

    # Check current state first; only send turn-off if device is actually on.
    try:
        r = requests.get(f"http://{ip}/rpc/Shelly.GetStatus", timeout=2)
        if r.status_code == 200:
            data = r.json()
            light = (data.get("light:0") or data.get("switch:0") or {})
            already_off = not light.get("output", True) and light.get("brightness", 0) == 0
            if not already_off:
                set_shelly(0, False, ip)
        # Device unreachable: internal state already reset above, skip HTTP call.
    except Exception as e:
        print(f"Sync error ({ip}): {e}")

    if has_power_socket(d):
        ps_ip = d.get("power_socket_ip")
        if ps_ip and _is_host_reachable(ps_ip):
            try:
                set_power_socket(d.get("power_socket_type"), ps_ip, False)
            except Exception as e:
                print(f"Power socket sync error ({ps_ip}): {e}")


def sync_configured_devices_off(devices):
    if not devices:
        return
    print("Sync: geconfigureerde Shelly apparaten naar UIT zetten...")
    with ThreadPoolExecutor(max_workers=len(devices)) as ex:
        list(ex.map(_sync_single_device_off, devices))
    print("Sync voltooid.")


def startup_sync_devices():
    global enabled, schedules_enabled
    cfg = load_config()
    enabled = cfg.get("pid_enabled", True)
    schedules_enabled = cfg.get("schedules_enabled", True)
    devices = cfg.get("shelly_devices", [])
    if not devices:
        return
    init_device_states(devices)
    init_device_pids(devices)
    # Run HTTP sync in background so offline devices don't delay Flask startup.
    threading.Thread(target=sync_configured_devices_off, args=(devices,), daemon=True).start()


# ================= MQTT =================
def _check_update_available():
    global _update_available
    try:
        subprocess.run(["git", "fetch"], cwd=UPDATE_DIR, capture_output=True, timeout=15)
        local = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=UPDATE_DIR, capture_output=True, text=True
        ).stdout.strip()
        remote = subprocess.run(
            ["git", "rev-parse", "@{u}"], cwd=UPDATE_DIR, capture_output=True, text=True
        ).stdout.strip()
        _update_available = bool(local and remote and local != remote)
    except Exception as e:
        print(f"Update check fout: {e}")


def _sanitize_ip(ip):
    return (ip or "").replace(".", "_").replace(":", "_")


def _device_status_label(st):
    if not st.get("online", False):
        return "Offline"
    if st.get("pending_start") or st.get("waiting_for_power_socket"):
        return "Opstarten"
    boost_until = st.get("boost_until")
    if boost_until and time.time() < boost_until:
        return "Boost"
    if st.get("freeze"):
        return "Bevroren"
    if st.get("started") and st.get("on"):
        return "Actief"
    return "Uit"


def _system_status_label(devices_data, cfg):
    if not enabled:
        return "Uitgeschakeld"
    pending = [d for d in devices_data if d.get("pending_start") or d.get("waiting_for_power_socket")]
    frozen = [d for d in devices_data if d.get("freeze")]
    running = [d for d in devices_data if d.get("started") and not d.get("freeze")]
    if pending:
        return "Opstarten"
    if running:
        if frozen:
            last_prio = max((d.get("priority", 1) for d in devices_data), default=1)
            if any(d.get("priority", 1) == last_prio and d.get("power", 0) >= 100 for d in running):
                return "Overcapaciteit"
        if all(d.get("price_triggered") for d in running):
            return "Goedkoop tarief"
        return "Actief"
    expert = cfg.get("expert_settings") or {}
    export_threshold = int(expert.get("EXPORT_THRESHOLD", -50))
    started = [d for d in devices_data if d.get("started") or d.get("pending_start")]
    if current_power <= export_threshold and not started:
        return "Wachten op teruglevering"
    return "Standby"


def _publish_ha_discovery(client, prefix, devices):
    base_device = {"identifiers": ["solarbuffer"], "name": "SolarBuffer", "model": "SolarBuffer Controller"}

    def pub(topic, payload):
        client.publish(topic, json.dumps(payload), retain=True)

    pub(f"homeassistant/switch/solarbuffer_enabled/config", {
        "name": "SolarBuffer Regeling",
        "unique_id": "solarbuffer_enabled_switch",
        "state_topic": f"{prefix}/enabled",
        "command_topic": f"{prefix}/set_enabled",
        "payload_on": "ON",
        "payload_off": "OFF",
        "icon": "mdi:solar-power",
        "availability_topic": f"{prefix}/availability",
        "device": base_device,
    })
    pub(f"homeassistant/sensor/solarbuffer_status/config", {
        "name": "SolarBuffer Status",
        "unique_id": "solarbuffer_status",
        "state_topic": f"{prefix}/status",
        "icon": "mdi:information-outline",
        "availability_topic": f"{prefix}/availability",
        "device": base_device,
    })
    pub(f"homeassistant/sensor/solarbuffer_grid_power/config", {
        "name": "SolarBuffer Netvermogen",
        "unique_id": "solarbuffer_grid_power",
        "state_topic": f"{prefix}/grid_power",
        "unit_of_measurement": "W",
        "device_class": "power",
        "state_class": "measurement",
        "availability_topic": f"{prefix}/availability",
        "device": base_device,
    })
    pub(f"homeassistant/switch/solarbuffer_anti_legionella/config", {
        "name": "SolarBuffer Anti-Legionella",
        "unique_id": "solarbuffer_anti_legionella_switch",
        "state_topic": f"{prefix}/anti_legionella",
        "command_topic": f"{prefix}/set_anti_legionella",
        "payload_on": "ON",
        "payload_off": "OFF",
        "icon": "mdi:bacteria-outline",
        "availability_topic": f"{prefix}/availability",
        "device": base_device,
    })
    pub(f"homeassistant/switch/solarbuffer_schedules_enabled/config", {
        "name": "SolarBuffer Tijdschema's",
        "unique_id": "solarbuffer_schedules_enabled_switch",
        "state_topic": f"{prefix}/schedules_enabled",
        "command_topic": f"{prefix}/set_schedules_enabled",
        "payload_on": "ON",
        "payload_off": "OFF",
        "icon": "mdi:clock-outline",
        "availability_topic": f"{prefix}/availability",
        "device": base_device,
    })
    pub(f"homeassistant/binary_sensor/solarbuffer_update_available/config", {
        "name": "SolarBuffer Update Beschikbaar",
        "unique_id": "solarbuffer_update_available",
        "state_topic": f"{prefix}/update_available",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device_class": "update",
        "availability_topic": f"{prefix}/availability",
        "device": base_device,
    })
    pub(f"homeassistant/button/solarbuffer_run_update/config", {
        "name": "SolarBuffer Update Uitvoeren",
        "unique_id": "solarbuffer_run_update_button",
        "command_topic": f"{prefix}/run_update",
        "payload_press": "PRESS",
        "icon": "mdi:update",
        "availability_topic": f"{prefix}/availability",
        "device": base_device,
    })
    for d in devices:
        uid = _sanitize_ip(d.get("ip", ""))
        dname = d.get("name", "Apparaat")
        pub(f"homeassistant/switch/solarbuffer_{uid}_switch/config", {
            "name": f"{dname}",
            "unique_id": f"solarbuffer_{uid}_switch",
            "state_topic": f"{prefix}/device/{uid}/on",
            "command_topic": f"{prefix}/device/{uid}/set_on",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:water-boiler",
            "availability_topic": f"{prefix}/availability",
            "device": base_device,
        })
        pub(f"homeassistant/sensor/solarbuffer_{uid}_status/config", {
            "name": f"{dname} Status",
            "unique_id": f"solarbuffer_{uid}_status",
            "state_topic": f"{prefix}/device/{uid}/status",
            "icon": "mdi:information-outline",
            "availability_topic": f"{prefix}/availability",
            "device": base_device,
        })

def _publish_mqtt_state(client, prefix, cfg):
    devices_data = []
    for d in cfg.get("shelly_devices", []):
        ip = d["ip"]
        st = device_states.get(ip, {})
        uid = _sanitize_ip(ip)
        status_label = _device_status_label(st)
        devices_data.append({
            "name": d.get("name"),
            "ip": ip,
            "priority": d.get("priority", 1),
            "on": st.get("on", False),
            "power": round(st.get("brightness", 0)),
            "freeze": st.get("freeze", False),
            "online": st.get("online", False),
            "started": st.get("started", False),
            "pending_start": st.get("pending_start", False),
            "waiting_for_power_socket": st.get("waiting_for_power_socket", False),
            "manual_override": st.get("manual_override", False),
            "price_triggered": st.get("price_triggered", False),
            "status": status_label,
        })
        client.publish(f"{prefix}/device/{uid}/on", "ON" if st.get("on", False) else "OFF", retain=True)
        client.publish(f"{prefix}/device/{uid}/power", str(round(st.get("brightness", 0))), retain=True)
        client.publish(f"{prefix}/device/{uid}/status", status_label, retain=True)

    system_status = _system_status_label(devices_data, cfg)
    client.publish(f"{prefix}/grid_power", str(round(current_power)), retain=True)
    client.publish(f"{prefix}/enabled", "ON" if enabled else "OFF", retain=True)
    client.publish(f"{prefix}/anti_legionella", "ON" if anti_legionella_enabled else "OFF", retain=True)
    client.publish(f"{prefix}/schedules_enabled", "ON" if schedules_enabled else "OFF", retain=True)
    client.publish(f"{prefix}/update_available", "ON" if _update_available else "OFF", retain=True)
    client.publish(f"{prefix}/status", system_status, retain=True)
    client.publish(f"{prefix}/state", json.dumps({
        "active_power_w": round(current_power),
        "enabled": enabled,
        "anti_legionella": anti_legionella_enabled,
        "status": system_status,
        "devices": devices_data,
    }), retain=True)


def _handle_mqtt_command(prefix, topic, payload):
    global enabled, anti_legionella_enabled
    if topic == f"{prefix}/set_enabled":
        new_state = payload.strip().upper() == "ON"
        enabled = new_state
        cfg = load_config()
        cfg["pid_enabled"] = enabled
        save_config(cfg)
        write_audit_log("pid_toggled_via_mqtt", {"enabled": enabled})
        return

    if topic == f"{prefix}/set_anti_legionella":
        new_state = payload.strip().upper() == "ON"
        anti_legionella_enabled = new_state
        cfg = load_config()
        cfg["anti_legionella_enabled"] = anti_legionella_enabled
        save_config(cfg)
        write_audit_log("anti_legionella_toggled_via_mqtt", {"enabled": anti_legionella_enabled})
        return

    if topic == f"{prefix}/set_schedules_enabled":
        new_state = payload.strip().upper() == "ON"
        schedules_enabled = new_state
        cfg = load_config()
        cfg["schedules_enabled"] = schedules_enabled
        save_config(cfg)
        write_audit_log("schedules_toggled_via_mqtt", {"enabled": schedules_enabled})
        return

    if topic == f"{prefix}/run_update":
        def _do_update():
            global _update_available
            try:
                pull = subprocess.run(
                    ["git", "pull"], cwd=UPDATE_DIR,
                    capture_output=True, text=True, timeout=60
                )
                output = (pull.stdout + pull.stderr).strip()
                has_changes = pull.returncode == 0 and "already up to date" not in output.lower()
                write_audit_log("update_run_via_mqtt", {"returncode": pull.returncode, "has_changes": has_changes})
                if has_changes:
                    _update_available = False
                    cfg = load_config()
                    sync_configured_devices_off(cfg.get("shelly_devices", []))
                    time.sleep(1.5)
                    if os.name != "nt":
                        subprocess.run(["sudo", "systemctl", "restart", "solarbuffer"])
            except Exception as e:
                print(f"MQTT update fout: {e}")
        threading.Thread(target=_do_update, daemon=True).start()
        return

    device_set_on_prefix = f"{prefix}/device/"
    if topic.startswith(device_set_on_prefix) and topic.endswith("/set_on"):
        uid = topic[len(device_set_on_prefix):-len("/set_on")]
        cfg = load_config()
        device = next(
            (d for d in cfg.get("shelly_devices", []) if _sanitize_ip(d["ip"]) == uid),
            None
        )
        if device is None or device["ip"] not in device_states:
            return
        ip = device["ip"]
        st = device_states[ip]
        new_on = payload.strip().upper() == "ON"
        if new_on:
            ensure_power_socket_on(device)
            st["on"] = True
            st["manual_override"] = True
            st["started"] = True
            st["pending_start"] = False
            st["freeze"] = False
            st["saturated_since"] = None
            st["min_since"] = None
            if st["brightness"] == 0:
                st["brightness"] = MIN_BRIGHTNESS
            set_shelly(st["brightness"], True, ip)
            mark_device_activity(device)
        else:
            st["on"] = False
            st["manual_override"] = True
            st["started"] = False
            st["pending_start"] = False
            st["brightness"] = 0
            st["freeze"] = False
            st["saturated_since"] = None
            st["min_since"] = None
            st["waiting_for_power_socket"] = False
            st["power_socket_ready_at"] = None
            threading.Thread(target=graceful_off_device, args=(ip,), daemon=True).start()
        write_audit_log("device_toggled_via_mqtt", {"device_ip": ip, "new_state": "on" if new_on else "off"})


def _get_mqtt_conn_key(cfg):
    return (
        cfg.get("mqtt_broker", ""),
        int(cfg.get("mqtt_port", 1883)),
        cfg.get("mqtt_username", ""),
        cfg.get("mqtt_password", ""),
        cfg.get("mqtt_topic_prefix", "solarbuffer"),
    )


def mqtt_loop():
    global _mqtt_client, _mqtt_connected
    if not MQTT_AVAILABLE:
        return

    current_key = None
    client = None
    last_publish = 0
    last_update_check = 0

    while True:
        try:
            cfg = load_config()
            mqtt_on = cfg.get("mqtt_enabled", False)
            broker = cfg.get("mqtt_broker", "").strip()

            if not mqtt_on or not broker:
                if client is not None:
                    prefix = cfg.get("mqtt_topic_prefix", "solarbuffer")
                    try:
                        client.publish(f"{prefix}/availability", "offline", retain=True)
                    except Exception:
                        pass
                    client.loop_stop()
                    client.disconnect()
                    client = None
                    _mqtt_client = None
                    _mqtt_connected = False
                    current_key = None
                time.sleep(10)
                continue

            conn_key = _get_mqtt_conn_key(cfg)

            if client is not None and conn_key != current_key:
                prefix = cfg.get("mqtt_topic_prefix", "solarbuffer")
                try:
                    client.publish(f"{prefix}/availability", "offline", retain=True)
                except Exception:
                    pass
                client.loop_stop()
                client.disconnect()
                client = None
                _mqtt_client = None
                _mqtt_connected = False
                current_key = None
                last_publish = 0

            if client is None:
                port = int(cfg.get("mqtt_port", 1883))
                username = cfg.get("mqtt_username", "")
                password = cfg.get("mqtt_password", "")
                prefix = cfg.get("mqtt_topic_prefix", "solarbuffer")
                ha_discovery = cfg.get("mqtt_ha_discovery", True)

                try:
                    try:
                        client = _mqtt_lib.Client(
                            _mqtt_lib.CallbackAPIVersion.VERSION1,
                            client_id="solarbuffer", clean_session=True
                        )
                    except AttributeError:
                        client = _mqtt_lib.Client(client_id="solarbuffer", clean_session=True)

                    if username:
                        client.username_pw_set(username, password or None)

                    client.will_set(f"{prefix}/availability", "offline", retain=True)

                    def on_connect(c, userdata, flags, rc, _prefix=prefix, _ha=ha_discovery):
                        global _mqtt_connected
                        if rc == 0:
                            _mqtt_connected = True
                            c.publish(f"{_prefix}/availability", "online", retain=True)
                            c.subscribe(f"{_prefix}/set_enabled")
                            c.subscribe(f"{_prefix}/set_anti_legionella")
                            c.subscribe(f"{_prefix}/set_schedules_enabled")
                            c.subscribe(f"{_prefix}/run_update")
                            c.subscribe(f"{_prefix}/device/+/set_on")
                            if _ha:
                                _publish_ha_discovery(c, _prefix, load_config().get("shelly_devices", []))
                            print(f"MQTT verbonden met {broker}:{port}")
                        else:
                            _mqtt_connected = False
                            print(f"MQTT verbindingsfout code {rc}")

                    def on_disconnect(c, userdata, rc):
                        global _mqtt_connected
                        _mqtt_connected = False
                        if rc != 0:
                            print(f"MQTT verbroken (code {rc}), herverbinden...")

                    def on_message(_c, _userdata, msg, _prefix=prefix):
                        try:
                            _handle_mqtt_command(_prefix, msg.topic, msg.payload.decode("utf-8", errors="replace"))
                        except Exception as exc:
                            print(f"MQTT command fout ({msg.topic}): {exc}")

                    client.on_connect = on_connect
                    client.on_disconnect = on_disconnect
                    client.on_message = on_message
                    client.connect(broker, port, keepalive=60)
                    client.loop_start()
                    _mqtt_client = client
                    current_key = conn_key
                    last_publish = 0
                except Exception as e:
                    print(f"MQTT verbinding mislukt ({broker}:{port}): {e}")
                    client = None
                    _mqtt_client = None
                    _mqtt_connected = False
                    time.sleep(30)
                    continue

            now = time.time()
            interval = int(cfg.get("mqtt_publish_interval", 30))
            if _mqtt_connected and now - last_publish >= interval:
                prefix = cfg.get("mqtt_topic_prefix", "solarbuffer")
                _publish_mqtt_state(client, prefix, cfg)
                last_publish = now

            if now - last_update_check >= 600:
                threading.Thread(target=_check_update_available, daemon=True).start()
                last_update_check = now

            time.sleep(5)

        except Exception as e:
            print(f"MQTT loop fout: {e}")
            if client is not None:
                try:
                    client.loop_stop()
                    client.disconnect()
                except Exception:
                    pass
                client = None
                _mqtt_client = None
                _mqtt_connected = False
                current_key = None
            time.sleep(30)


# ================= CONTROL HELPERS =================
def get_sorted_devices(devices):
    return sorted(devices, key=lambda x: x["priority"])


def get_device_state(device):
    return device_states[device["ip"]]


def is_started(device):
    return get_device_state(device)["started"]


def is_frozen(device):
    return get_device_state(device)["freeze"]


def is_running(device):
    st = get_device_state(device)
    return st["started"] and not st["freeze"] and st["on"]


def _socket_offline_unstarted(device):
    """True when a device's power socket is unreachable and the device hasn't started yet."""
    st = get_device_state(device)
    if st["started"] or st.get("pending_start"):
        return False
    return (has_power_socket(device)
            and not st.get("power_socket_online")
            and not st["power_socket_on"])


def higher_priorities_started_and_frozen(devices_sorted, priority):
    for d in devices_sorted:
        if d["priority"] < priority:
            if _socket_offline_unstarted(d):
                continue  # socket unreachable → transparent to priority chain
            st = get_device_state(d)
            if not st["started"] or not st["freeze"]:
                return False
    return True


def lower_priorities_off(devices_sorted, priority):
    for d in devices_sorted:
        if d["priority"] > priority:
            st = get_device_state(d)
            if st["started"] or st["on"] or st["brightness"] > 0 or st.get("pending_start"):
                return False
    return True


def get_next_startable_device(devices_sorted):
    for d in devices_sorted:
        st = get_device_state(d)
        prio = d["priority"]
        if st["started"] or st.get("pending_start"):
            continue
        if _socket_offline_unstarted(d):
            continue  # socket unreachable → skip, try next priority
        if prio == 1:
            return d
        if higher_priorities_started_and_frozen(devices_sorted, prio):
            return d
    return None


def get_lowest_priority_running(devices_sorted):
    for d in reversed(devices_sorted):
        if is_running(d):
            return d
    return None


def get_highest_frozen_allowed_to_unfreeze(devices_sorted):
    for d in reversed(devices_sorted):
        st = get_device_state(d)
        if st["started"] and st["freeze"] and lower_priorities_off(devices_sorted, d["priority"]):
            return d
    return None


def is_last_possible_priority(devices_sorted, device):
    if not devices_sorted:
        return False
    last_priority = max(d["priority"] for d in devices_sorted)
    return device["priority"] == last_priority


def graceful_off_device(ip):
    set_shelly(MIN_BRIGHTNESS, True, ip)
    time.sleep(1)
    set_shelly(0, False, ip)


def reset_device_to_off(ip):
    state = device_states[ip]
    state["on"] = False
    state["brightness"] = 0
    state["freeze"] = False
    state["started"] = False
    state["pending_start"] = False
    state["saturated_since"] = None
    state["min_since"] = None
    state["waiting_for_power_socket"] = False
    state["power_socket_ready_at"] = None
    threading.Thread(target=graceful_off_device, args=(ip,), daemon=True).start()


def hold_frozen_output(ip):
    state = device_states[ip]
    if state["brightness"] < MIN_BRIGHTNESS:
        state["brightness"] = MIN_BRIGHTNESS
    state["on"] = True
    set_shelly(state["brightness"], True, ip)


# ================= TIJDSCHEMA =================
def get_active_schedule(schedules):
    now = datetime.now()
    weekday = now.weekday()  # 0=maandag … 6=zondag
    current_minutes = now.hour * 60 + now.minute
    for sched in schedules:
        if not sched.get("enabled", True):
            continue
        if weekday not in sched.get("days", []):
            continue
        try:
            sh, sm = map(int, sched["start_time"].split(":"))
            eh, em = map(int, sched["end_time"].split(":"))
        except (KeyError, ValueError):
            continue
        if (sh * 60 + sm) <= current_minutes < (eh * 60 + em):
            return sched
    return None


# ================= CONTROL LOOP =================
def control_loop():
    global current_power, current_brightness, active_schedule_info, anti_legionella_enabled, schedules_enabled, vacation_mode

    online_check_interval = 10
    last_online_check = {}
    offline_since_map = {}
    offline_notified = set()
    prev_active_sched_id = None
    export_start = None
    price_start = None
    import_unfreeze_start = None
    import_off_start = None
    prev_schedule_active_ips = set()
    _bat_tofull_active = False  # to_full mode actief voor accu-eerst bij max lading + boiler aan

    while True:
        try:
            cfg = load_config()
            anti_legionella_enabled = cfg.get("anti_legionella_enabled", False)
            schedules_enabled = cfg.get("schedules_enabled", True)
            vacation_mode = cfg.get("vacation_mode", False)
            vacation_until = cfg.get("vacation_until")
            vacation_legionella = cfg.get("vacation_legionella", False)

            if vacation_mode and vacation_until and time.time() > vacation_until:
                vacation_mode = False
                cfg["vacation_mode"] = False
                cfg["vacation_until"] = None
                save_config(cfg)
                write_audit_log("vacation_mode_ended", {})
                send_notification("🌴 Vakantiestand beëindigd, normale regeling hervat.", event_key="ntfy_notify_vacation")

            p1_ip = cfg.get("p1_ip")
            devices = cfg.get("shelly_devices", [])
            settings = get_runtime_settings(cfg)

            EXPORT_THRESHOLD = settings["EXPORT_THRESHOLD"]
            EXPORT_DELAY = settings["EXPORT_DELAY"]
            FREEZE_AT = settings["FREEZE_AT"]
            FREEZE_CONFIRM = settings["FREEZE_CONFIRM"]
            IMPORT_UNFREEZE_THRESHOLD = settings["IMPORT_UNFREEZE_THRESHOLD"]
            UNFREEZE_DELAY = settings["UNFREEZE_DELAY"]
            IMPORT_OFF_THRESHOLD = settings["IMPORT_OFF_THRESHOLD"]
            OFF_DELAY = settings["OFF_DELAY"]
            PID_NEUTRAL_LOW = settings["PID_NEUTRAL_LOW"]
            PID_NEUTRAL_HIGH = settings["PID_NEUTRAL_HIGH"]
            DYNAMIC_PRICING = cfg.get("dynamic_pricing_enabled", False)
            PRICE_THRESHOLD_CT = float(cfg.get("price_threshold_ct", 5.0))
            PRICE_BRIGHTNESS = int(cfg.get("price_brightness", 100))

            if not p1_ip or not devices:
                time.sleep(2)
                continue

            init_device_states(devices)
            init_device_pids(devices)
            now = time.time()

            for d in devices:
                ip = d["ip"]
                pm_type = (d.get("power_meter") or "").lower()
                pm_ip = (d.get("power_ip") or "").strip() or ip
                ps_type = (d.get("power_socket_type") or "").lower().strip()
                ps_ip = (d.get("power_socket_ip") or "").strip()

                if now - last_online_check.get(ip, 0) > online_check_interval:
                    device_states[ip]["online"] = check_shelly_online(ip)
                    if pm_type == "shelly":
                        device_states[ip]["power_meter_online"] = check_http_device_online(pm_ip, "/rpc/Shelly.GetStatus")
                    elif pm_type == "homewizard":
                        device_states[ip]["power_meter_online"] = check_http_device_online(pm_ip, "/api/v1/data")
                    else:
                        device_states[ip]["power_meter_online"] = False
                    if ps_type and ps_ip:
                        device_states[ip]["power_socket_online"] = check_power_socket_online(ps_type, ps_ip)
                    else:
                        device_states[ip]["power_socket_online"] = False
                    last_online_check[ip] = now

            # --- Watchdog: track offline timestamps ---
            for d in devices:
                ip = d["ip"]
                if not device_states[ip]["online"]:
                    if ip not in offline_since_map:
                        offline_since_map[ip] = now
                else:
                    offline_since_map.pop(ip, None)

            # --- Watchdog: started + offline >30s → reset so next priority can take over ---
            # Bepaal welke IPs vallen onder het actieve tijdschema: de watchdog
            # mag deze niet resetten — het schema herstart het device zodra het
            # weer online komt.
            _wd_sched = get_active_schedule(cfg.get("schedules", [])) if schedules_enabled else None
            _wd_sched_ips = set()
            if _wd_sched:
                _wd_sched_dev_ips = set(_wd_sched.get("device_ips") or [])
                for _wd_d in devices:
                    if not _wd_sched_dev_ips or _wd_d["ip"] in _wd_sched_dev_ips:
                        _wd_sched_ips.add(_wd_d["ip"])

            for d in devices:
                ip = d["ip"]
                st = device_states[ip]
                if st.pop("_reset_offline_timer", False):
                    offline_since_map.pop(ip, None)
                offline_for = now - offline_since_map.get(ip, now)
                if (offline_for >= 30
                        and st.get("started")
                        and not st.get("pending_start")
                        and not st.get("freeze")
                        and not st.get("legionella_active")
                        and ip not in _wd_sched_ips):
                    print(f"Watchdog: {ip} al {int(offline_for)}s offline terwijl gestart → gereset")
                    if ip not in offline_notified:
                        send_notification(f"⚠️ <b>{d.get('name', ip)}</b> is niet bereikbaar en wordt uitgeschakeld.", event_key="ntfy_notify_offline")
                        offline_notified.add(ip)
                    reset_device_to_off(ip)
                    offline_since_map.pop(ip, None)
                elif device_states[ip].get("online"):
                    offline_notified.discard(ip)

            energy_today_str = datetime.now().strftime("%Y-%m-%d")
            for d in devices:
                ip = d["ip"]
                state = device_states[ip]
                pm_type = (d.get("power_meter") or "").lower()
                pm_ip = (d.get("power_ip") or "").strip() or ip
                total_wh = None
                if pm_type == "shelly":
                    pw, total_wh = get_shelly_power_and_energy(pm_ip)
                    state["power"] = pw
                elif pm_type == "homewizard":
                    pw, total_kwh = get_homewizard_power_and_energy(pm_ip)
                    state["power"] = pw
                    total_wh = total_kwh * 1000 if total_kwh is not None else None
                else:
                    state["power"] = 0

                # Dagelijkse kWh via cumulatieve teller van het apparaat
                if state.get("energy_day_date") != energy_today_str:
                    # Dag gewisseld (of eerste run): sla huidige stand op als baseline
                    if total_wh is not None:
                        state["energy_day_date"] = energy_today_str
                        state["energy_day_start_wh"] = total_wh
                        state["energy_today_kwh"] = 0.0
                        _energy_baselines[ip] = {"date": energy_today_str, "start_wh": total_wh}
                        save_energy_baselines()
                elif total_wh is not None and state.get("energy_day_start_wh") is not None:
                    delta = total_wh - state["energy_day_start_wh"]
                    if delta < -10:
                        # Teller gereset (factory reset apparaat) → herstart baseline
                        state["energy_day_start_wh"] = total_wh
                        state["energy_today_kwh"] = 0.0
                        _energy_baselines[ip] = {"date": energy_today_str, "start_wh": total_wh}
                        save_energy_baselines()
                    else:
                        state["energy_today_kwh"] = max(0.0, delta / 1000)

            measured_power = current_power
            pid_power = 20 if PID_NEUTRAL_LOW <= measured_power <= PID_NEUTRAL_HIGH else measured_power
            devices_sorted = get_sorted_devices(devices)
            active_brightness = 0

            # --- Anti-Legionella ---
            legionella_handled = set()
            if anti_legionella_enabled and (not vacation_mode or vacation_legionella):
                for d in devices_sorted:
                    ip = d["ip"]
                    st = device_states[ip]
                    last_active = st.get("last_active_time", 0)
                    idle_too_long = (now - last_active) >= LEGIONELLA_IDLE_SECONDS
                    legionella_run_seconds = int((d.get("boiler_volume", 100) / 100) * 3 * 3600)

                    if not st.get("legionella_active") and idle_too_long:
                        if st.get("pre_legionella_started") is None:
                            st["pre_legionella_started"] = st.get("started", False)
                            st["pre_legionella_brightness"] = st.get("brightness", 0)
                            st["pre_legionella_freeze"] = st.get("freeze", False)
                        st["legionella_active"] = True
                        st["legionella_start"] = now
                        save_state(force=True)
                        print(f"Anti-Legionella: cyclus gestart voor {ip}")

                    if st.get("legionella_active"):
                        elapsed = now - (st.get("legionella_start") or now)
                        if elapsed >= legionella_run_seconds:
                            st["legionella_active"] = False
                            st["legionella_start"] = None
                            st["last_active_time"] = now
                            save_state(force=True)
                            print(f"Anti-Legionella: cyclus voltooid voor {ip}")
                            send_notification(f"🦠 <b>Legionellabeveiliging voltooid</b> voor {d.get('name', ip)}.", event_key="ntfy_notify_legionella")
                            pre_started = st.get("pre_legionella_started")
                            pre_brightness = st.get("pre_legionella_brightness", 0)
                            pre_freeze = st.get("pre_legionella_freeze", False)
                            st["saturated_since"] = None
                            st["min_since"] = None
                            st["pending_start"] = False
                            st["waiting_for_power_socket"] = False
                            st["power_socket_ready_at"] = None
                            if not enabled:
                                if pre_started:
                                    restore_b = max(MIN_BRIGHTNESS, pre_brightness)
                                    st["on"] = True
                                    st["started"] = True
                                    st["brightness"] = restore_b
                                    st["freeze"] = pre_freeze
                                    set_shelly(restore_b, True, ip)
                                else:
                                    st["on"] = False
                                    st["started"] = False
                                    st["brightness"] = 0
                                    st["freeze"] = False
                                    threading.Thread(target=graceful_off_device, args=(ip,), daemon=True).start()
                            else:
                                if pre_started:
                                    st["freeze"] = pre_freeze
                                else:
                                    st["on"] = False
                                    st["started"] = False
                                    st["brightness"] = 0
                                    st["freeze"] = False
                                    threading.Thread(target=graceful_off_device, args=(ip,), daemon=True).start()
                            st["pre_legionella_started"] = None
                            st["pre_legionella_brightness"] = None
                            st["pre_legionella_freeze"] = None
                            continue

                        if st.get("pending_start"):
                            ready = ensure_power_socket_on(d)
                            if ready:
                                st["started"] = True
                                st["pending_start"] = False
                                st["on"] = True
                                st["freeze"] = False
                                st["saturated_since"] = None
                                st["min_since"] = None
                                st["brightness"] = MAX_BRIGHTNESS
                                set_shelly(MAX_BRIGHTNESS, True, ip)
                                mark_device_activity(d)
                            legionella_handled.add(ip)
                            continue

                        if has_power_socket(d) and not st["power_socket_on"]:
                            ensure_power_socket_on(d)
                            legionella_handled.add(ip)
                            continue

                        if st["online"]:
                            st["on"] = True
                            st["started"] = True
                            st["freeze"] = False
                            st["saturated_since"] = None
                            st["min_since"] = None
                            st["brightness"] = MAX_BRIGHTNESS
                            set_shelly(MAX_BRIGHTNESS, True, ip)
                            mark_device_activity(d)

                        legionella_handled.add(ip)
            else:
                for d in devices_sorted:
                    device_states[d["ip"]]["legionella_active"] = False
                    device_states[d["ip"]]["legionella_start"] = None
                    device_states[d["ip"]]["pre_legionella_started"] = None
            # --- Einde Anti-Legionella ---

            # --- Vakantiestand: alles uit behalve actieve legionella ---
            if vacation_mode:
                for d in devices_sorted:
                    ip = d["ip"]
                    st = device_states[ip]
                    if ip in legionella_handled:
                        continue
                    if st.get("on") or st.get("started") or st.get("pending_start"):
                        st["on"] = False
                        st["started"] = False
                        st["freeze"] = False
                        st["brightness"] = 0
                        st["pending_start"] = False
                        st["waiting_for_power_socket"] = False
                        st["power_socket_ready_at"] = None
                        threading.Thread(target=graceful_off_device, args=(ip,), daemon=True).start()
                    maybe_turn_off_power_socket(d)
                current_brightness = 0
                time.sleep(2)
                continue
            # --- Einde vakantiestand ---

            # --- Boost override ---
            boost_handled = set()
            for d in devices_sorted:
                ip = d["ip"]
                st = device_states[ip]
                if ip in legionella_handled:
                    continue
                boost_until = st.get("boost_until")
                if not boost_until:
                    continue
                if now >= boost_until:
                    st["boost_until"] = None
                    continue
                if st.get("pending_start"):
                    ready = ensure_power_socket_on(d)
                    if ready:
                        st["started"] = True
                        st["pending_start"] = False
                        st["on"] = True
                        st["freeze"] = False
                        st["saturated_since"] = None
                        st["min_since"] = None
                        st["brightness"] = MAX_BRIGHTNESS
                        set_shelly(MAX_BRIGHTNESS, True, ip)
                        mark_device_activity(d)
                    boost_handled.add(ip)
                    continue
                if has_power_socket(d) and not st["power_socket_on"]:
                    ensure_power_socket_on(d)
                    boost_handled.add(ip)
                    continue
                if st["online"]:
                    st["on"] = True
                    st["started"] = True
                    st["freeze"] = False
                    st["saturated_since"] = None
                    st["min_since"] = None
                    st["brightness"] = MAX_BRIGHTNESS
                    set_shelly(MAX_BRIGHTNESS, True, ip)
                    mark_device_activity(d)
                boost_handled.add(ip)
            # --- Einde Boost override ---

            # --- Tijdschema override ---
            active_sched = get_active_schedule(cfg.get("schedules", [])) if schedules_enabled else None
            schedule_handled = set()
            if active_sched:
                active_schedule_info = active_sched
                sched_id = active_sched.get("id")
                if sched_id != prev_active_sched_id:
                    sched_name = active_sched.get("name") or f"{active_sched.get('start_time')}–{active_sched.get('end_time')}"
                    send_notification(f"🕐 Tijdschema <b>{sched_name}</b> is actief ({active_sched.get('brightness', MIN_BRIGHTNESS)}% vermogen).", event_key="ntfy_notify_schedule")
                    prev_active_sched_id = sched_id
                sched_brightness = max(MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, int(active_sched.get("brightness", MIN_BRIGHTNESS))))
                sched_device_ips = set(active_sched.get("device_ips") or [])
                for d in devices_sorted:
                    ip = d["ip"]
                    st = device_states[ip]
                    if ip in legionella_handled:
                        continue
                    if ip in boost_handled:
                        continue
                    if sched_device_ips and ip not in sched_device_ips:
                        continue
                    if st.get("pre_schedule_started") is None:
                        st["pre_schedule_started"] = st.get("started", False)
                        st["pre_schedule_brightness"] = st.get("brightness", 0)
                        st["pre_schedule_freeze"] = st.get("freeze", False)
                    if st.get("pending_start"):
                        ready = ensure_power_socket_on(d)
                        if ready:
                            st["started"] = True
                            st["pending_start"] = False
                            st["on"] = True
                            st["freeze"] = False
                            st["saturated_since"] = None
                            st["min_since"] = None
                            st["brightness"] = sched_brightness
                            set_shelly(sched_brightness, True, ip)
                            mark_device_activity(d)
                        schedule_handled.add(ip)
                        continue
                    # Socket eerst aanzetten vóór online-check — apparaat is
                    # offline zolang de socket uit staat
                    if has_power_socket(d) and not st["power_socket_on"]:
                        ensure_power_socket_on(d)
                        schedule_handled.add(ip)
                        continue
                    if not st["online"]:
                        schedule_handled.add(ip)
                        continue
                    st["started"] = True
                    st["on"] = True
                    st["freeze"] = False
                    st["saturated_since"] = None
                    st["min_since"] = None
                    st["brightness"] = sched_brightness
                    set_shelly(sched_brightness, True, ip)
                    mark_device_activity(d)
                    schedule_handled.add(ip)
                prev_schedule_active_ips = schedule_handled.copy()
                current_brightness = sched_brightness
                if not sched_device_ips:
                    time.sleep(2)
                    continue
            else:
                active_schedule_info = None
                prev_active_sched_id = None
                for ip in prev_schedule_active_ips:
                    st = device_states.get(ip)
                    if st is None:
                        continue
                    pre_started = st.get("pre_schedule_started")
                    pre_brightness = st.get("pre_schedule_brightness", 0)
                    pre_freeze = st.get("pre_schedule_freeze", False)
                    if not enabled:
                        st["saturated_since"] = None
                        st["min_since"] = None
                        st["pending_start"] = False
                        st["waiting_for_power_socket"] = False
                        st["power_socket_ready_at"] = None
                        if pre_started:
                            restore_b = max(MIN_BRIGHTNESS, pre_brightness)
                            st["on"] = True
                            st["started"] = True
                            st["brightness"] = restore_b
                            st["freeze"] = pre_freeze
                            set_shelly(restore_b, True, ip)
                        else:
                            st["on"] = False
                            st["started"] = False
                            st["brightness"] = 0
                            st["freeze"] = False
                            threading.Thread(target=graceful_off_device, args=(ip,), daemon=True).start()
                    else:
                        if pre_started:
                            st["freeze"] = pre_freeze
                            st["saturated_since"] = None
                            st["min_since"] = None
                        else:
                            st["on"] = False
                            st["started"] = False
                            st["brightness"] = 0
                            st["freeze"] = False
                            st["saturated_since"] = None
                            st["min_since"] = None
                            st["pending_start"] = False
                            st["waiting_for_power_socket"] = False
                            st["power_socket_ready_at"] = None
                            threading.Thread(target=graceful_off_device, args=(ip,), daemon=True).start()
                    st["pre_schedule_started"] = None
                    st["pre_schedule_brightness"] = None
                    st["pre_schedule_freeze"] = None
                prev_schedule_active_ips = set()
            # --- Einde tijdschema override ---

            if not enabled:
                for d in devices_sorted:
                    ip = d["ip"]
                    st = device_states[ip]
                    if ip in legionella_handled or ip in schedule_handled:
                        continue
                    if st.get("pending_start"):
                        ready = ensure_power_socket_on(d)
                        if ready:
                            st["started"] = True
                            st["pending_start"] = False
                            st["on"] = True
                            st["freeze"] = False
                            st["saturated_since"] = None
                            st["min_since"] = None
                            if st["brightness"] < MIN_BRIGHTNESS:
                                st["brightness"] = MIN_BRIGHTNESS
                            set_shelly(st["brightness"], True, ip)
                            mark_device_activity(d)
                        continue
                    if not st["started"]:
                        continue
                    if st["freeze"]:
                        hold_frozen_output(ip)
                        mark_device_activity(d)
                    elif st["on"] and st["brightness"] >= MIN_BRIGHTNESS:
                        set_shelly(st["brightness"], True, ip)
                        mark_device_activity(d)
                current_brightness = 0
                for d in devices_sorted:
                    if d["ip"] not in legionella_handled:
                        maybe_turn_off_power_socket(d)
                time.sleep(2)
                continue

            for d in devices_sorted:
                ip = d["ip"]
                st = device_states[ip]
                if ip in legionella_handled:
                    continue
                if st.get("pending_start"):
                    ready = ensure_power_socket_on(d)
                    if ready:
                        st["started"] = True
                        st["pending_start"] = False
                        st["on"] = True
                        st["freeze"] = False
                        st["saturated_since"] = None
                        st["min_since"] = None
                        if st["brightness"] < MIN_BRIGHTNESS:
                            st["brightness"] = MIN_BRIGHTNESS
                        set_shelly(st["brightness"], True, ip)
                        mark_device_activity(d)
                        offline_since_map.pop(ip, None)
                        if st.get("price_triggered"):
                            price_ct_now = get_current_price_ct()
                            price_label = f" ({price_ct_now:.1f} ct/kWh)" if price_ct_now is not None else ""
                            send_notification(f"💶 <b>{d.get('name', ip)}</b> gestart op goedkoop stroomtarief{price_label}.", event_key="ntfy_notify_start")
                        else:
                            send_notification(f"☀️ <b>{d.get('name', ip)}</b> gestart op zonnestroom.", event_key="ntfy_notify_start")

            if measured_power <= EXPORT_THRESHOLD:
                if export_start is None:
                    export_start = now
            else:
                export_start = None

            non_legionella = [d for d in devices_sorted if d["ip"] not in legionella_handled and d["ip"] not in boost_handled and d["ip"] not in schedule_handled]

            # ================= BATTERIJ PRIORITEIT =================
            battery_blocks_start = False
            _bat_cfg_enabled = cfg.get("battery_enabled", False)
            if _bat_cfg_enabled:
                _bat_token = cfg.get("battery_control_token", "").strip()
                _bat_control_ip = p1_ip
                _bat_priority = cfg.get("battery_priority", "boiler")
                _bat_soc_thr = cfg.get("battery_soc_threshold", 95)
                _bat_soc = battery_state.get("soc")

                if not battery_state.get("online"):
                    # Batterij niet bereikbaar → normale besturing, reset cache
                    global _last_battery_permissions
                    _last_battery_permissions = None
                    # battery_blocks_start blijft False, geen permissies sturen

                else:
                    _any_sb_active = any(device_states[d["ip"]].get("started") for d in non_legionella)
                    _legionella_active = bool(legionella_handled)
                    _schedule_active = active_sched is not None
                    _price_active = any(device_states[d["ip"]].get("price_triggered") for d in devices_sorted)
                    _force_no_discharge = _legionella_active or _schedule_active or _price_active
                    _has_export = measured_power < 0
                    _pid_at_max = current_brightness >= MAX_BRIGHTNESS
                    _sb_can_run = (
                        enabled
                        and bool(non_legionella)
                        and any(
                            device_states[d["ip"]].get("online") or
                            device_states[d["ip"]].get("power_socket_online")
                            for d in non_legionella
                        )
                    )

                    _force_tofull = cfg.get("battery_force_tofull", False)
                    # Auto-uitschakelen als SoC 100% bereikt
                    if _force_tofull and _bat_soc is not None and _bat_soc >= 100:
                        cfg["battery_force_tofull"] = False
                        save_config(cfg)
                        _force_tofull = False
                        write_audit_log("battery_force_tofull_auto_off", {"soc": _bat_soc})

                    if _force_tofull:
                        _desired_mode = "to_full"
                        _desired_perms = []
                        battery_blocks_start = False
                    elif _bat_priority == "battery":
                        _desired_mode = "zero"
                        if not _sb_can_run:
                            _desired_perms = ["charge_allowed", "discharge_allowed"]
                        elif _bat_soc is not None and _bat_soc < _bat_soc_thr:
                            _bat_pw = battery_state.get("power_w")
                            _num_bats = max(len(cfg.get("battery_ips") or []), 1)
                            # Gebruik max_consumption_w uit API als beschikbaar, anders 800W per accu
                            _api_max = battery_state.get("max_consumption_w") or 0
                            _total_max_charge = _api_max if _api_max > 0 else _num_bats * 800
                            _bat_at_max = (
                                _bat_pw is not None and
                                _bat_pw <= -(_total_max_charge - 50 * _num_bats)
                            )
                            # to_full state machine
                            if _bat_tofull_active:
                                # Uitschakelconditie: boiler uit EN P1 > +50W (stop grid-import)
                                if not _any_sb_active and measured_power > 50:
                                    _bat_tofull_active = False
                            else:
                                # Inschakelconditie: accu OP MAX en boiler draait.
                                if _bat_at_max and _any_sb_active:
                                    _bat_tofull_active = True

                            if _bat_tofull_active:
                                # to_full: accu laadt vast op max, boiler is de enige regelaar
                                battery_blocks_start = False
                                _desired_mode = "to_full"
                                _desired_perms = []  # read-only in to_full, wordt genegeerd
                            elif _bat_at_max:
                                # Max vermogen bereikt maar to_full nog niet actief → boiler mag starten
                                battery_blocks_start = False
                                _desired_perms = ["charge_allowed"]
                            else:
                                # Nog niet op max → accu vrij in zero mode, boiler wacht
                                battery_blocks_start = True
                                for _bd in non_legionella:
                                    _bst = device_states[_bd["ip"]]
                                    if _bst.get("started") and not _bst.get("freeze") and not _bst.get("legionella_active"):
                                        reset_device_to_off(_bd["ip"])
                                _desired_perms = ["charge_allowed", "discharge_allowed"]
                        else:
                            # SoC-drempel bereikt: accu standby, boiler is primaire regelaar.
                            _bat_tofull_active = False
                            if not _sb_can_run:
                                _desired_perms = ["discharge_allowed"]
                            elif _force_no_discharge:
                                _desired_perms = ["charge_allowed"]
                            elif _any_sb_active and _pid_at_max:
                                # Boiler op 100%: accu absorbeert restoverschot
                                _desired_perms = ["charge_allowed"]
                            else:
                                # Boiler regelt (of nog uit): accu standby
                                _desired_perms = []
                    else:  # solarbuffer eerst
                        _desired_mode = "zero"
                        if not _sb_can_run:
                            _desired_perms = ["discharge_allowed"]
                        elif _force_no_discharge:
                            _desired_perms = ["charge_allowed"]
                        elif _any_sb_active:
                            _desired_perms = ["charge_allowed"] if _pid_at_max else []
                        else:
                            _desired_perms = ["discharge_allowed"]

                _bat_type = cfg.get("battery_type", "homewizard")
                if battery_state.get("online"):
                    if _bat_type == "marstek":
                        _marstek_ips = cfg.get("battery_ips") or []
                        _marstek_port = int(cfg.get("marstek_port") or 30000)
                        _marstek_max = int(cfg.get("marstek_max_power") or 2000)
                        if _marstek_ips:
                            if not enabled or not _p1_online:
                                threading.Thread(
                                    target=release_marstek_to_auto,
                                    args=(_marstek_ips[0], _marstek_port),
                                    daemon=True,
                                ).start()
                            else:
                                threading.Thread(
                                    target=set_marstek_control,
                                    args=(_marstek_ips[0], _marstek_port, _desired_mode,
                                          _desired_perms, measured_power, _marstek_max),
                                    daemon=True,
                                ).start()
                    elif _bat_token and _bat_control_ip and (
                        sorted(_desired_perms) != (_last_battery_permissions or []) or
                        _desired_mode != _last_battery_mode
                    ):
                        threading.Thread(
                            target=set_battery_control,
                            args=(_bat_control_ip, _bat_token, _desired_mode, _desired_perms),
                            daemon=True,
                        ).start()
            # ================= EINDE BATTERIJ PRIORITEIT =================
            global _battery_blocks_start
            _battery_blocks_start = battery_blocks_start

            if export_start is not None and (now - export_start) >= EXPORT_DELAY and not battery_blocks_start:
                next_dev = get_next_startable_device(non_legionella)
                if next_dev:
                    ip = next_dev["ip"]
                    st = device_states[ip]
                    if has_power_socket(next_dev):
                        if not st.get("pending_start"):
                            ready = ensure_power_socket_on(next_dev)
                            if ready:
                                st["started"] = True
                                st["pending_start"] = False
                                st["on"] = True
                                st["freeze"] = False
                                st["saturated_since"] = None
                                st["min_since"] = None
                                st["brightness"] = MIN_BRIGHTNESS
                                if ip in device_pids:
                                    device_pids[ip].set_auto_mode(False)
                                    device_pids[ip].set_auto_mode(True, last_output=MIN_BRIGHTNESS)
                                set_shelly(MIN_BRIGHTNESS, True, ip)
                                mark_device_activity(next_dev)
                                offline_since_map.pop(ip, None)
                                send_notification(f"☀️ <b>{next_dev.get('name', ip)}</b> gestart op zonnestroom.", event_key="ntfy_notify_start")
                        export_start = None
                    else:
                        st["started"] = True
                        st["pending_start"] = False
                        st["on"] = True
                        st["freeze"] = False
                        st["saturated_since"] = None
                        st["min_since"] = None
                        st["brightness"] = MIN_BRIGHTNESS
                        if ip in device_pids:
                            device_pids[ip].set_auto_mode(False)
                            device_pids[ip].set_auto_mode(True, last_output=MIN_BRIGHTNESS)
                        set_shelly(MIN_BRIGHTNESS, True, ip)
                        mark_device_activity(next_dev)
                        offline_since_map.pop(ip, None)
                        send_notification(f"☀️ <b>{next_dev.get('name', ip)}</b> gestart op zonnestroom.", event_key="ntfy_notify_start")
                        export_start = None

            # === DYNAMISCH TARIEF ===
            price_ct = get_current_price_ct()
            price_cheap = (
                DYNAMIC_PRICING
                and price_ct is not None
                and price_ct <= PRICE_THRESHOLD_CT
            )

            if price_cheap:
                if price_start is None:
                    price_start = now
            else:
                if price_start is not None:
                    price_start = None
                # Niet meer goedkoop: geef price-triggered apparaten terug aan PID
                if not price_cheap:
                    for d in non_legionella:
                        st_d = device_states[d["ip"]]
                        if st_d.get("price_triggered"):
                            st_d["price_triggered"] = False

            PRICE_START_DELAY = 30
            if price_start is not None and (now - price_start) >= PRICE_START_DELAY and not battery_blocks_start:
                next_dev = get_next_startable_device(non_legionella)
                if next_dev:
                    ip = next_dev["ip"]
                    st = device_states[ip]
                    if not st.get("pending_start"):
                        b = max(MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, PRICE_BRIGHTNESS))
                        if has_power_socket(next_dev):
                            st["brightness"] = b
                            st["price_triggered"] = True
                            ready = ensure_power_socket_on(next_dev)
                            if ready:
                                st["started"] = True
                                st["pending_start"] = False
                                st["on"] = True
                                st["freeze"] = False
                                st["saturated_since"] = None
                                st["min_since"] = None
                                set_shelly(b, True, ip)
                                mark_device_activity(next_dev)
                                offline_since_map.pop(ip, None)
                                send_notification(f"💶 <b>{next_dev.get('name', ip)}</b> gestart op goedkoop stroomtarief ({price_ct:.1f} ct/kWh).", event_key="ntfy_notify_start")
                        else:
                            st["started"] = True
                            st["pending_start"] = False
                            st["on"] = True
                            st["freeze"] = False
                            st["saturated_since"] = None
                            st["min_since"] = None
                            st["brightness"] = b
                            st["price_triggered"] = True
                            set_shelly(b, True, ip)
                            mark_device_activity(next_dev)
                            offline_since_map.pop(ip, None)
                            send_notification(f"💶 <b>{next_dev.get('name', ip)}</b> gestart op goedkoop stroomtarief ({price_ct:.1f} ct/kWh).", event_key="ntfy_notify_start")

            # Price-triggered apparaten niet via PID regelen — houd op vast vermogen
            non_price_regulated = [d for d in non_legionella if not device_states[d["ip"]].get("price_triggered")]
            regulating_device = get_lowest_priority_running(non_price_regulated)

            for d in non_legionella:
                ip = d["ip"]
                st = device_states[ip]

                if not st["started"]:
                    if st.get("pending_start"):
                        continue
                    if st["on"] or st["brightness"] != 0 or st["freeze"]:
                        reset_device_to_off(ip)
                    continue

                if has_power_socket(d) and st["waiting_for_power_socket"]:
                    ready = ensure_power_socket_on(d)
                    if not ready:
                        continue

                if not st["online"]:
                    continue

                if st["freeze"]:
                    hold_frozen_output(ip)
                    mark_device_activity(d)
                    continue

                if regulating_device and ip == regulating_device["ip"]:
                    b_pid = device_pids[ip](pid_power)
                    b_pid = max(MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, b_pid))
                    # Zolang de accu actief laadt (>10W) bevriest de boiler op huidig vermogen
                    # en laat de accu als eerste afregelen. Pas als de accu <10W laadt
                    # hervat de PID de normale regeling.
                    # Geldt voor boiler-eerst én battery-eerst wanneer SoC-drempel bereikt is.
                    _bat_holds_boiler = (
                        cfg.get("battery_enabled") and
                        battery_state.get("online") and
                        (battery_state.get("power_w") or 0) < -10 and
                        (
                            cfg.get("battery_priority") == "boiler" or
                            (
                                cfg.get("battery_priority") == "battery" and
                                battery_state.get("soc") is not None and
                                battery_state.get("soc") >= cfg.get("battery_soc_threshold", 95)
                            )
                        )
                    )
                    # teruglevering (negatief) → mag alleen omhoog; importerend (positief) → mag alleen omlaag
                    if measured_power > 0 and _bat_holds_boiler:
                        b = st["brightness"]  # accu regelt, boiler bevriest
                    elif measured_power > 0:
                        b = min(b_pid, st["brightness"])
                    elif measured_power < 0:
                        b = max(b_pid, st["brightness"])
                    else:
                        b = b_pid
                    # back-calculation anti-windup: als de richtingsbeperking de output heeft aangepast,
                    # herbereken de integraal zodat hij overeenkomt met de werkelijke output
                    if b != b_pid:
                        device_pids[ip].set_auto_mode(False)
                        device_pids[ip].set_auto_mode(True, last_output=b)
                    st["brightness"] = b
                    st["on"] = True
                    set_shelly(b, True, ip)
                    active_brightness = b
                    mark_device_activity(d)

                    if b >= FREEZE_AT and not is_last_possible_priority(non_legionella, d):
                        if st["saturated_since"] is None:
                            st["saturated_since"] = now
                        elif (now - st["saturated_since"]) >= FREEZE_CONFIRM:
                            st["freeze"] = True
                            st["saturated_since"] = None
                    else:
                        st["saturated_since"] = None

                    if b <= MIN_BRIGHTNESS:
                        if st["min_since"] is None:
                            st["min_since"] = now
                    else:
                        st["min_since"] = None
                else:
                    if st["brightness"] < MIN_BRIGHTNESS:
                        st["brightness"] = MIN_BRIGHTNESS
                    st["on"] = True
                    set_shelly(st["brightness"], True, ip)
                    mark_device_activity(d)

            current_brightness = active_brightness
            lowest_running = get_lowest_priority_running(non_legionella)

            if lowest_running:
                st = get_device_state(lowest_running)
                at_minimum = st["brightness"] <= MIN_BRIGHTNESS and st["min_since"] is not None
                if at_minimum and measured_power >= IMPORT_OFF_THRESHOLD:
                    if import_off_start is None:
                        import_off_start = now
                    elif (now - import_off_start) >= OFF_DELAY:
                        reset_device_to_off(lowest_running["ip"])
                        import_off_start = None
                        import_unfreeze_start = None
                else:
                    import_off_start = None
            else:
                import_off_start = None

            candidate_unfreeze = get_highest_frozen_allowed_to_unfreeze(non_legionella)
            if candidate_unfreeze and get_lowest_priority_running(non_legionella) is None:
                if measured_power >= IMPORT_UNFREEZE_THRESHOLD:
                    if import_unfreeze_start is None:
                        import_unfreeze_start = now
                    elif (now - import_unfreeze_start) >= UNFREEZE_DELAY:
                        st = get_device_state(candidate_unfreeze)
                        st["freeze"] = False
                        st["on"] = True
                        st["saturated_since"] = None
                        st["min_since"] = None
                        if st["brightness"] < MIN_BRIGHTNESS:
                            st["brightness"] = MIN_BRIGHTNESS
                        if candidate_unfreeze["ip"] in device_pids:
                            device_pids[candidate_unfreeze["ip"]].set_auto_mode(True, last_output=st["brightness"])
                        set_shelly(st["brightness"], True, candidate_unfreeze["ip"])
                        mark_device_activity(candidate_unfreeze)
                        import_unfreeze_start = None
                else:
                    import_unfreeze_start = None
            else:
                import_unfreeze_start = None

            for d in devices_sorted:
                if d["ip"] not in legionella_handled:
                    maybe_turn_off_power_socket(d)

            time.sleep(2)

        except Exception as e:
            print("Fout control_loop:", e)
            time.sleep(1)


# ================= TELEGRAM =================
def _strip_html(text):
    import re
    return re.sub(r'<[^>]+>', '', text)


def send_notification(text, event_key=None):
    def _send():
        cfg = load_config()
        for user in cfg.get("users", []):
            if not user.get("ntfy_enabled"):
                continue
            if event_key and not user.get(event_key, True):
                continue
            url = (user.get("ntfy_url") or "").strip().rstrip("/")
            if not url:
                continue
            try:
                requests.post(
                    url,
                    data=_strip_html(text).encode("utf-8"),
                    headers={"Title": "SolarBuffer"},
                    timeout=5
                )
            except Exception:
                pass
    threading.Thread(target=_send, daemon=True).start()


def _get_current_user(cfg):
    username = session.get("username", "")
    return next((u for u in cfg.get("users", []) if u.get("username") == username), None)


@app.route("/settings/ntfy", methods=["GET", "POST"])
def settings_ntfy():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    user = _get_current_user(cfg)
    if user is None:
        return redirect("/settings")
    if request.method == "POST":
        old_cfg = load_config()
        user["ntfy_enabled"] = "ntfy_enabled" in request.form
        user["ntfy_url"] = request.form.get("ntfy_url", "").strip().rstrip("/")
        user["ntfy_notify_start"] = "ntfy_notify_start" in request.form
        user["ntfy_notify_legionella"] = "ntfy_notify_legionella" in request.form
        user["ntfy_notify_schedule"] = "ntfy_notify_schedule" in request.form
        user["ntfy_notify_offline"] = "ntfy_notify_offline" in request.form
        changes = compare_configs(old_cfg, cfg)
        save_config(cfg)
        if changes:
            write_audit_log("config_updated", changes)
        return redirect("/settings")
    return render_template("settings_ntfy.html", config=cfg, user=user, dark_mode=get_user_dark_mode())


@app.route("/ntfy/test")
def ntfy_test():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    cfg = load_config()
    user = _get_current_user(cfg)
    url = (user.get("ntfy_url") if user else None or "").strip().rstrip("/")
    if not url:
        return jsonify({"error": "Geen URL ingevuld"}), 400
    try:
        r = requests.post(url, data="✅ SolarBuffer is verbonden met ntfy!".encode("utf-8"),
                          headers={"Title": "SolarBuffer"}, timeout=5)
        if r.status_code < 300:
            return jsonify({"ok": True})
        return jsonify({"error": f"HTTP {r.status_code}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ================= INVERTER MODBUS =================
_INVERTER_TYPES = {
    "solaredge": {"label": "SolarEdge",     "port": 1502, "unit": 1,   "proto": "sunspec"},
    "fronius":   {"label": "Fronius",        "port": 502,  "unit": 1,   "proto": "sunspec"},
    "sma":       {"label": "SMA",            "port": 502,  "unit": 3,   "proto": "sunspec"},
    "abb":       {"label": "ABB / FIMER",    "port": 502,  "unit": 1,   "proto": "sunspec"},
    "kostal":    {"label": "Kostal",         "port": 1502, "unit": 71,  "proto": "sunspec"},
    "huawei":    {"label": "Huawei SUN2000", "port": 6607, "unit": 1,   "proto": "huawei"},
    "growatt":   {"label": "Growatt",        "port": 502,  "unit": 1,   "proto": "growatt"},
    "sungrow":   {"label": "Sungrow",        "port": 502,  "unit": 1,   "proto": "sungrow"},
    "goodwe":    {"label": "GoodWe",         "port": 502,  "unit": 247, "proto": "goodwe"},
}


def _modbus_read(ip, port, unit, func, address, count, timeout=3):
    import struct as _s
    req = _s.pack('>HHHBBHH', 1, 0, 6, unit, func, address, count)
    with socket.create_connection((ip, port), timeout=timeout) as sock:
        sock.sendall(req)
        return sock.recv(256)


def _read_inverter_ac_power(ip, inverter_type, timeout=3):
    import struct as _s
    _SUNSPEC_NI = -32768  # SunSpec "not implemented" sentinel (0x8000 as INT16)
    meta = _INVERTER_TYPES.get(inverter_type)
    if not meta:
        return None
    port, unit, proto = meta["port"], meta["unit"], meta["proto"]
    try:
        if proto == "sunspec":
            # Register 40083 (addr 82): INT16 power + register 40084 (addr 83): INT16 scale factor
            r = _modbus_read(ip, port, unit, 3, 82, 2, timeout)
            if len(r) < 13 or r[7] != 3:
                return None
            val = _s.unpack('>h', r[9:11])[0]
            sf  = _s.unpack('>h', r[11:13])[0]
            # 0x8000 means "not implemented" in SunSpec; sf buiten -10..10 is corrupte data
            if val == _SUNSPEC_NI or sf == _SUNSPEC_NI or not (-10 <= sf <= 10):
                return None
            return round(val * (10 ** sf), 1)
        elif proto == "huawei":
            # Register 32080, INT32 (2 regs), unit W
            r = _modbus_read(ip, port, unit, 3, 32080, 2, timeout)
            if len(r) < 13 or r[7] != 3:
                return None
            return float(_s.unpack('>i', r[9:13])[0])
        elif proto == "growatt":
            # Register 3 (Pac), UINT16, unit 0.1 W
            r = _modbus_read(ip, port, unit, 3, 3, 1, timeout)
            if len(r) < 11 or r[7] != 3:
                return None
            return round(_s.unpack('>H', r[9:11])[0] * 0.1, 1)
        elif proto == "sungrow":
            # Register 13003, INT16, unit W
            r = _modbus_read(ip, port, unit, 3, 13003, 1, timeout)
            if len(r) < 11 or r[7] != 3:
                return None
            return float(_s.unpack('>h', r[9:11])[0])
        elif proto == "goodwe":
            # Register 35121, INT16, unit W (input registers, func 4)
            r = _modbus_read(ip, port, unit, 4, 35121, 1, timeout)
            if len(r) < 11 or r[7] != 4:
                return None
            return float(_s.unpack('>h', r[9:11])[0])
    except Exception:
        return None
    return None


def inverter_poll_loop():
    global inverter_power, inverter_online
    while True:
        try:
            cfg = load_config()
            if cfg.get("inverter_enabled") and cfg.get("inverter_ip"):
                val = _read_inverter_ac_power(cfg["inverter_ip"], cfg.get("inverter_type", "solaredge"))
                import math as _math
                if val is not None and _math.isfinite(val):
                    inverter_power = val
                    inverter_online = True
                else:
                    inverter_online = False
            else:
                inverter_power = None
                inverter_online = False
        except Exception:
            inverter_online = False
        time.sleep(5)


# ================= BATTERIJ (HomeWizard HWE-BAT v2) =================
try:
    import urllib3 as _urllib3
    _urllib3.disable_warnings(_urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass


# ================= MARSTEK UDP API =================

def marstek_udp(ip, port, method, params=None, timeout=3):
    payload = json.dumps({"id": 1, "method": method, "params": params or {"id": 0}}).encode()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(payload, (ip, int(port)))
        data, _ = sock.recvfrom(4096)
        return json.loads(data.decode())


def release_marstek_to_auto(ip, port):
    """Switch the Marstek battery back to its own automatic mode."""
    global _last_battery_permissions, _last_battery_mode, _last_marstek_send, _last_marstek_power
    now = time.time()
    if _last_battery_mode == "auto" and (now - _last_marstek_send) < 240:
        return True
    try:
        result = marstek_udp(ip, port, "ES.SetMode", {"id": 0, "config": {"mode": "Auto", "auto_cfg": {"enable": 1}}})
        if result.get("result", {}).get("set_result"):
            _last_battery_permissions = None
            _last_battery_mode = "auto"
            _last_marstek_power = None
            _last_marstek_send = now
            return True
    except Exception as e:
        print(f"Marstek auto-release error ({ip}:{port}): {e}")
    return False


def set_marstek_control(ip, port, mode, perms, measured_power=0, max_power=2000):
    """Send a Passive-mode power setpoint to the Marstek battery.

    Setpoint is derived from mode+perms and the current P1 reading (measured_power):
    - to_full          → charge at max_power (battery locks at max, boiler regulates)
    - charge_only      → charge only from solar export: SP = max(0, -P1); standby on import
    - no perms         → standby (0 W)
    - discharge_only   → discharge proportional to grid import: SP = -min(P1, max); no charge
    - both perms       → balance grid to 0 via SP = -P1 (charge on export, discharge on import)
    """
    global _last_battery_permissions, _last_battery_mode, _last_marstek_send, _last_marstek_power

    now = time.time()
    desired_perms = sorted(perms)
    max_power = int(max_power or 2000)

    charge_only = (desired_perms == ["charge_allowed"])
    discharge_only = (desired_perms == ["discharge_allowed"])

    if mode == "to_full":
        # Battery locked at max charge power; boiler is the regulating device.
        target_power = max_power
    elif charge_only:
        # Charge from solar export only — no grid charging, no discharge.
        # SP = clamp(-P1, 0, max): export (P1<0) → charge at |P1|, import (P1>0) → standby.
        target_power = int(max(0, min(max_power, -measured_power)))
    elif not perms:
        target_power = 0
    elif discharge_only:
        # Discharge proportional to grid import; no charging.
        # SP = -clamp(P1, 0, max): import (P1>0) → discharge at P1, export → standby.
        target_power = -int(max(0, min(max_power, measured_power)))
    else:
        # Both permissions: balance grid to 0 via SP = -P1.
        target_power = int(max(-max_power, min(max_power, -measured_power)))

    mode_changed = (desired_perms != _last_battery_permissions or mode != _last_battery_mode)
    power_changed = abs(target_power - (_last_marstek_power or 0)) > 25
    needs_refresh = (now - _last_marstek_send) > 240

    if not mode_changed and not power_changed and not needs_refresh:
        return True

    try:
        cfg_obj = {"mode": "Passive", "passive_cfg": {"power": target_power, "cd_time": 300}}
        result = marstek_udp(ip, port, "ES.SetMode", {"id": 0, "config": cfg_obj})
        if result.get("result", {}).get("set_result"):
            _last_battery_permissions = desired_perms
            _last_battery_mode = mode
            _last_marstek_power = target_power
            _last_marstek_send = now
            return True
    except Exception as e:
        print(f"Marstek control error ({ip}:{port}): {e}")
    return False


# ================= HOMEWIZARD BATTERY API =================

def _hw_v2_headers(token):
    return {"Authorization": f"Bearer {token}", "X-Api-Version": "2"}


def get_battery_measurement(ip, token):
    r = requests.get(
        f"https://{ip}/api/measurement",
        headers=_hw_v2_headers(token), timeout=3, verify=False,
    )
    r.raise_for_status()
    return r.json()


def get_battery_control(control_ip, token):
    r = requests.get(
        f"https://{control_ip}/api/batteries",
        headers=_hw_v2_headers(token), timeout=3, verify=False,
    )
    r.raise_for_status()
    return r.json()


def set_battery_permissions(control_ip, token, permissions):
    return set_battery_control(control_ip, token, "zero", permissions)


def set_battery_control(control_ip, token, mode, permissions):
    global _last_battery_permissions, _last_battery_mode
    desired_perms = sorted(permissions)
    if _last_battery_permissions == desired_perms and _last_battery_mode == mode:
        return True
    payload = {"mode": mode}
    if mode != "to_full":
        payload["permissions"] = permissions
    try:
        r = requests.put(
            f"https://{control_ip}/api/batteries",
            headers={**_hw_v2_headers(token), "Content-Type": "application/json"},
            json=payload,
            timeout=3, verify=False,
        )
        if r.status_code == 200:
            _last_battery_permissions = desired_perms
            _last_battery_mode = mode
            return True
        print(f"[BAT] set_battery_control mislukt: HTTP {r.status_code} → {r.text[:200]}")
    except Exception as e:
        print(f"[BAT] set_battery_control fout: {e}")
    return False


def battery_poll_loop():
    global battery_state, _bat_day_date, _bat_charge_start_kwh, _bat_discharge_start_kwh
    bl = load_energy_baselines().get("__battery__", {})
    _bat_day_date = bl.get("date")
    _bat_charge_start_kwh = bl.get("charge_start_kwh")
    _bat_discharge_start_kwh = bl.get("discharge_start_kwh")
    while True:
        try:
            cfg = load_config()
            ips = cfg.get("battery_ips") or []
            bat_type = cfg.get("battery_type", "homewizard")

            if not cfg.get("battery_enabled") or not ips:
                battery_state["online"] = False
                time.sleep(5)
                continue

            if bat_type == "marstek":
                port = int(cfg.get("marstek_port") or 30000)
                max_power = int(cfg.get("marstek_max_power") or 2000)
                soc_list, power_list = [], []
                any_online = False
                for ip in ips:
                    try:
                        r = marstek_udp(ip, port, "ES.GetStatus")
                        data = r.get("result", {})
                        any_online = True
                        soc = data.get("bat_soc")
                        bp = data.get("bat_power")
                        if soc is not None:
                            soc_list.append(float(soc))
                        if bp is not None:
                            # Marstek: positive = charging → negate to match SolarBuffer convention
                            # (power_w < 0 = charging, power_w > 0 = discharging)
                            power_list.append(-float(bp))
                        else:
                            # bat_power missing for this model — use Bat.GetStatus flags as proxy
                            try:
                                br = marstek_udp(ip, port, "Bat.GetStatus")
                                bd = br.get("result", {})
                                if bd.get("charg_flag") is True:
                                    power_list.append(-float(max_power))
                                elif bd.get("dischrg_flag") is True:
                                    power_list.append(float(max_power))
                                else:
                                    power_list.append(0.0)
                            except Exception:
                                power_list.append(0.0)
                    except Exception:
                        pass
                if any_online:
                    battery_state.update({
                        "soc": round(sum(soc_list) / len(soc_list), 1) if soc_list else None,
                        "power_w": round(sum(power_list), 1) if power_list else None,
                        "voltage_v": None,
                        "current_a": None,
                        "cycles": None,
                        "mode": "Passive",
                        "permissions": None,
                        "max_consumption_w": max_power,
                        "max_production_w": max_power,
                        "online": True,
                    })
                else:
                    battery_state["online"] = False

            else:  # homewizard
                tokens = cfg.get("battery_tokens", [])
                if not any(t for t in tokens):
                    battery_state["online"] = False
                    time.sleep(5)
                    continue
                soc_list, power_total, voltage_list = [], 0.0, []
                import_total, export_total = 0.0, 0.0
                cycles_total = 0
                any_online = False
                for i, ip in enumerate(ips):
                    token = tokens[i] if i < len(tokens) else ""
                    if not token:
                        continue
                    try:
                        data = get_battery_measurement(ip, token)
                        any_online = True
                        soc = data.get("state_of_charge_pct")
                        pw = data.get("power_w")
                        vv = data.get("voltage_v")
                        cy = data.get("cycles")
                        ei = data.get("energy_import_kwh")
                        ee = data.get("energy_export_kwh")
                        if soc is not None:
                            soc_list.append(float(soc))
                        if pw is not None:
                            # HWE-BAT: positive = charging, negative = discharging
                            # Negate to match SolarBuffer convention (power_w < 0 = charging)
                            power_total -= float(pw)
                        if vv is not None:
                            voltage_list.append(float(vv))
                        if cy is not None:
                            cycles_total += int(cy)
                        if ei is not None:
                            import_total += float(ei)
                        if ee is not None:
                            export_total += float(ee)
                    except Exception:
                        pass
                if any_online:
                    today = datetime.now().strftime('%Y-%m-%d')
                    if _bat_day_date != today or _bat_charge_start_kwh is None:
                        _bat_day_date = today
                        _bat_charge_start_kwh = import_total
                        _bat_discharge_start_kwh = export_total
                        _energy_baselines["__battery__"] = {
                            "date": today,
                            "charge_start_kwh": import_total,
                            "discharge_start_kwh": export_total,
                        }
                        save_energy_baselines()
                    charge_today = round(max(0.0, import_total - _bat_charge_start_kwh), 2)
                    discharge_today = round(max(0.0, export_total - _bat_discharge_start_kwh), 2)
                    battery_state.update({
                        "soc": round(sum(soc_list) / len(soc_list), 1) if soc_list else None,
                        "power_w": round(power_total, 1),
                        "voltage_v": round(sum(voltage_list) / len(voltage_list), 1) if voltage_list else None,
                        "current_a": None,
                        "frequency_hz": None,
                        "energy_import_kwh": None,
                        "energy_export_kwh": None,
                        "cycles": cycles_total if soc_list else None,
                        "online": True,
                        "charge_today_kwh": charge_today,
                        "discharge_today_kwh": discharge_today,
                    })
                else:
                    battery_state["online"] = False
                control_ip = cfg.get("p1_ip", "")
                control_token = cfg.get("battery_control_token", "").strip()
                if control_ip and control_token:
                    try:
                        ctrl = get_battery_control(control_ip, control_token)
                        battery_state["mode"] = ctrl.get("mode")
                        battery_state["permissions"] = ctrl.get("permissions")
                        battery_state["max_consumption_w"] = ctrl.get("max_consumption_w", 0) or 0
                        battery_state["max_production_w"] = ctrl.get("max_production_w", 0) or 0
                    except Exception:
                        pass

        except Exception:
            battery_state["online"] = False
        time.sleep(5)


def broadlink_poll_loop():
    global _broadlink_online
    while True:
        try:
            cfg = load_config()
            for bl in cfg.get("broadlink_devices", []):
                dev = _broadlink_connect(bl["ip"], bl.get("mac", ""), bl.get("devtype", 0))
                _broadlink_online[bl["id"]] = dev is not None
        except Exception:
            pass
        time.sleep(15)


# ================= HISTORY DB =================
HISTORY_DB = os.path.join(BASE_DIR, "history.db")

HISTORY_RETENTION = {
    "history_5s": 86400,       # 24 uur
    "history_1m": 2_592_000,   # 30 dagen
    "history_1h": 31_536_000,  # 1 jaar
    "history_1d": 315_360_000, # 10 jaar
}


def init_history_db():
    with sqlite3.connect(HISTORY_DB) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=100")
        for table in HISTORY_RETENTION:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table} (
                    ts     INTEGER NOT NULL,
                    metric TEXT    NOT NULL,
                    value  REAL    NOT NULL,
                    PRIMARY KEY (ts, metric)
                )
            """)
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_ts ON {table}(ts)")
        conn.commit()


def aggregate_and_purge(conn):
    now = int(time.time())
    try:
        # 5s → 1m  (data ouder dan 2 minuten)
        conn.execute("""
            INSERT OR REPLACE INTO history_1m (ts, metric, value)
            SELECT (ts / 60) * 60, metric, AVG(value)
            FROM history_5s WHERE ts <= ?
            GROUP BY (ts / 60) * 60, metric
        """, (now - 120,))
        # 1m → 1h  (data ouder dan 2 uur)
        conn.execute("""
            INSERT OR REPLACE INTO history_1h (ts, metric, value)
            SELECT (ts / 3600) * 3600, metric, AVG(value)
            FROM history_1m WHERE ts <= ?
            GROUP BY (ts / 3600) * 3600, metric
        """, (now - 7200,))
        # 1h → 1d  (data ouder dan 2 dagen)
        conn.execute("""
            INSERT OR REPLACE INTO history_1d (ts, metric, value)
            SELECT (ts / 86400) * 86400, metric, AVG(value)
            FROM history_1h WHERE ts <= ?
            GROUP BY (ts / 86400) * 86400, metric
        """, (now - 172800,))
        for table, max_age in HISTORY_RETENTION.items():
            conn.execute(f"DELETE FROM {table} WHERE ts < ?", (now - max_age,))
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass


def history_worker():
    last_aggregate = 0
    conn = sqlite3.connect(HISTORY_DB, check_same_thread=False)
    try:
        while True:
            try:
                now = int(time.time())
                ts = (now // 5) * 5

                points = [("net_power", current_power, ts)]
                cfg = load_config()
                for d in cfg.get("shelly_devices", []):
                    ip = d["ip"]
                    st = device_states.get(ip, {})
                    name = (d.get("name") or ip).strip()
                    if d.get("power_meter"):
                        points.append((f"device:{name}:power", st.get("power", 0), ts))
                for acc in cfg.get("accessories", []):
                    if not acc.get("record_history"):
                        continue
                    acc_id = acc.get("id", "")
                    acc_name = (acc.get("name") or acc_id).strip()
                    st = accessory_states.get(acc_id, {})
                    if acc.get("acc_type") == "temperature":
                        temp = st.get("temperature")
                        if temp is not None:
                            points.append((f"acc:{acc_name}:temperature", temp, ts))
                    else:
                        points.append((f"acc:{acc_name}:power", st.get("power", 0), ts))

                conn.executemany(
                    "INSERT OR REPLACE INTO history_5s (ts, metric, value) VALUES (?, ?, ?)",
                    [(ts, m, float(v)) for m, v, ts in points]
                )
                conn.commit()

                if now - last_aggregate >= 60:
                    aggregate_and_purge(conn)
                    last_aggregate = now
            except Exception:
                pass
            time.sleep(5)
    finally:
        conn.close()


@app.route("/api/history")
def history_api():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    metric     = request.args.get("metric", "net_power")
    resolution = request.args.get("resolution", "auto")
    to_ts      = int(request.args.get("to",   int(time.time())))
    from_ts    = int(request.args.get("from", to_ts - 86400))
    span       = to_ts - from_ts
    if resolution == "auto":
        if span <= 86400:
            resolution = "5s"
        elif span <= 2_592_000:
            resolution = "1m"
        elif span <= 31_536_000:
            resolution = "1h"
        else:
            resolution = "1d"
    table = {"5s": "history_5s", "1m": "history_1m",
             "1h": "history_1h", "1d": "history_1d"}.get(resolution, "history_1h")
    conn = sqlite3.connect(HISTORY_DB)
    try:
        rows = conn.execute(
            f"SELECT ts, value FROM {table} WHERE metric=? AND ts>=? AND ts<=? ORDER BY ts",
            (metric, from_ts, to_ts)
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return jsonify({"metric": metric, "resolution": resolution,
                    "data": [{"ts": r[0], "v": r[1]} for r in rows]})


@app.route("/api/history/reset", methods=["POST"])
def history_reset_api():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    if not is_current_user_admin():
        return jsonify({"error": "Geen toegang"}), 403
    conn = sqlite3.connect(HISTORY_DB)
    try:
        for table in HISTORY_RETENTION:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.execute("VACUUM")
    except Exception as e:
        conn.close()
        return jsonify(success=False, error=str(e))
    conn.close()
    write_audit_log("history_reset", {"user": safe_session_username()})
    return jsonify(success=True)


@app.route("/api/history/metrics")
def history_metrics_api():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    conn = sqlite3.connect(HISTORY_DB)
    try:
        all_metrics = [r[0] for r in conn.execute(
            "SELECT DISTINCT metric FROM history_5s "
            "UNION SELECT DISTINCT metric FROM history_1m"
        ).fetchall()]
    except Exception:
        all_metrics = []
    finally:
        conn.close()

    cfg = load_config()
    active_acc_names = {
        (acc.get("name") or acc.get("id", "")).strip()
        for acc in cfg.get("accessories", [])
        if acc.get("record_history")
    }

    filtered = []
    for m in all_metrics:
        if m.startswith("acc:"):
            parts = m.split(":", 2)
            if len(parts) == 3 and parts[1] in active_acc_names:
                filtered.append(m)
        else:
            filtered.append(m)

    return jsonify({"metrics": filtered})


# ================= P1 POLL =================
def p1_poll_loop():
    global current_power, _p1_online, current_gas_m3, gas_day_start_m3, gas_day_date
    saved = load_state()
    gas_info = saved.get("__gas__", {})
    gas_day_start_m3 = gas_info.get("gas_day_start_m3")
    gas_day_date = gas_info.get("gas_day_date")
    while True:
        try:
            cfg = load_config()
            p1_ip = cfg.get("p1_ip")
            if p1_ip:
                data = requests.get(f"http://{p1_ip}/api/v1/data", timeout=2).json()
                current_power = float(data.get("active_power_w", 0) or 0)
                _p1_online = True
                if cfg.get("gas_enabled"):
                    gas_raw = data.get("total_gas_m3")
                    if gas_raw is not None:
                        current_gas_m3 = float(gas_raw)
                        today = datetime.now().date().isoformat()
                        if gas_day_date != today:
                            gas_day_date = today
                            gas_day_start_m3 = current_gas_m3
                            save_state(force=True)
        except Exception:
            _p1_online = False
        time.sleep(1)


def accessory_poll_loop():
    while True:
        try:
            cfg = load_config()
            for acc in cfg.get("accessories", []):
                acc_id = acc.get("id")
                if not acc_id:
                    continue
                acc_type = acc.get("acc_type", "power")
                if acc_type == "temperature":
                    temp_ip = (acc.get("temp_ip") or "").strip()
                    channel = acc.get("temp_channel", 100)
                    if not temp_ip:
                        continue
                    if acc_id not in accessory_states:
                        accessory_states[acc_id] = {"temperature": None, "online": False}
                    try:
                        t = get_shelly_temperature(temp_ip, channel)
                        accessory_states[acc_id]["temperature"] = t
                        accessory_states[acc_id]["online"] = t is not None
                    except Exception:
                        accessory_states[acc_id]["online"] = False
                else:
                    pm_type = (acc.get("power_meter_type") or "").lower()
                    pm_ips = [ip.strip() for ip in acc.get("power_ips", []) if ip.strip()]
                    if not pm_ips:
                        continue
                    acc_today_str = datetime.now().strftime("%Y-%m-%d")
                    if acc_id not in accessory_states:
                        acc_bl = _energy_baselines.get(acc_id, {})
                        accessory_states[acc_id] = {
                            "power": 0.0, "online": False,
                            "energy_today_kwh": 0.0,
                            "energy_day_date": acc_today_str if acc_bl.get("date") == acc_today_str else "",
                            "energy_day_start_wh": acc_bl.get("start_wh") if acc_bl.get("date") == acc_today_str else None,
                        }
                    try:
                        total_power = 0.0
                        total_wh_sum = 0.0
                        total_wh_valid = True
                        any_online = False
                        for pm_ip in pm_ips:
                            if pm_type == "shelly":
                                p, wh = get_shelly_power_and_energy(pm_ip)
                                total_power += p
                                if wh is not None:
                                    total_wh_sum += wh
                                else:
                                    total_wh_valid = False
                                if p > 0 or check_http_device_online(pm_ip, "/rpc/Shelly.GetStatus"):
                                    any_online = True
                            elif pm_type == "homewizard":
                                p, kwh = get_homewizard_power_and_energy(pm_ip)
                                total_power += p
                                if kwh is not None:
                                    total_wh_sum += kwh * 1000
                                else:
                                    total_wh_valid = False
                                any_online = True
                        total_wh = total_wh_sum if total_wh_valid and pm_ips else None
                        accessory_states[acc_id]["power"] = total_power
                        accessory_states[acc_id]["online"] = any_online
                        st = accessory_states[acc_id]
                        if st.get("energy_day_date") != acc_today_str:
                            if total_wh is not None:
                                st["energy_day_date"] = acc_today_str
                                st["energy_day_start_wh"] = total_wh
                                st["energy_today_kwh"] = 0.0
                                _energy_baselines[acc_id] = {"date": acc_today_str, "start_wh": total_wh}
                                save_energy_baselines()
                        elif total_wh is not None and st.get("energy_day_start_wh") is not None:
                            st["energy_today_kwh"] = max(0.0, (total_wh - st["energy_day_start_wh"]) / 1000)
                    except Exception:
                        accessory_states[acc_id]["online"] = False
        except Exception:
            pass
        time.sleep(5)


# ================= START =================
if __name__ == "__main__":
    init_history_db()
    startup_sync_devices()
    threading.Thread(target=p1_poll_loop, daemon=True).start()
    threading.Thread(target=control_loop, daemon=True).start()
    threading.Thread(target=_price_fetch_loop, daemon=True).start()
    threading.Thread(target=mqtt_loop, daemon=True).start()
    threading.Thread(target=accessory_poll_loop, daemon=True).start()
    threading.Thread(target=inverter_poll_loop, daemon=True).start()
    threading.Thread(target=battery_poll_loop, daemon=True).start()
    threading.Thread(target=broadlink_poll_loop, daemon=True).start()
    threading.Thread(target=history_worker, daemon=True).start()
    import logging
    class _NoRequestLogs(logging.Filter):
        def filter(self, record):
            return "HTTP/1." not in record.getMessage()
    logging.getLogger("werkzeug").addFilter(_NoRequestLogs())
    app.run(host="0.0.0.0", port=5001)
