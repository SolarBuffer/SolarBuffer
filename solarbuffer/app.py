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
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, jsonify, request, redirect, session, send_file
import io
import threading
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import paho.mqtt.client as _mqtt_lib
    MQTT_AVAILABLE = True
except ImportError:
    _mqtt_lib = None
    MQTT_AVAILABLE = False

# ================= CONFIG =================
BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
AUDIT_LOG_FILE = os.path.join(BASE_DIR, "audit.log")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
_last_state_save = 0

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
    "POWER_SOCKET_HOLD_SECONDS": 600,
    "BOOST_DURATION": 900
}


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
    if "username" not in cfg:
        cfg["username"] = "solarbuffer"
    if "password_hash" not in cfg:
        cfg["password_hash"] = ""
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
        if "power_meter_type" not in acc:
            acc["power_meter_type"] = "shelly"
        # Migreer enkel power_ip → power_ips array
        if "power_ips" not in acc:
            old_ip = (acc.get("power_ip") or "").strip()
            acc["power_ips"] = [old_ip] if old_ip else []
        acc["power_ips"] = [ip for ip in acc["power_ips"] if ip.strip()]
        if "power_ip" not in acc:
            acc["power_ip"] = acc["power_ips"][0] if acc["power_ips"] else ""
        if "icon" not in acc:
            acc["icon"] = "mdi-power-plug"

    # Migreer oud formaat (enkele gebruiker) naar gebruikerslijst
    if "users" not in cfg:
        old_username = cfg.pop("username", "solarbuffer")
        old_hash = cfg.pop("password_hash", "")
        cfg["users"] = [{"username": old_username, "password_hash": old_hash}]

    for user in cfg["users"]:
        if "dark_mode" not in user:
            user["dark_mode"] = False

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
    return cfg


def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


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
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"State save fout: {e}")


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
PID_KP = 0.02
PID_KI = 0.0013
PID_KD = 0.01

device_pids = {}
enabled = True
schedules_enabled = True
device_states = {}
accessory_states = {}
current_power = 0
current_brightness = 0
active_schedule_info = None
_mqtt_client = None
_mqtt_connected = False
_update_available = False
_tailscale_auth_url = None

# ================= CONTROL CONSTANTS =================
MIN_BRIGHTNESS = 34
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


def require_login():
    return session.get("logged_in", False)


def get_user_dark_mode():
    cfg = load_config()
    username = session.get("username", "")
    user = next((u for u in cfg.get("users", []) if u.get("username") == username), None)
    return bool(user.get("dark_mode", False)) if user else False


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
    found_p1 = []
    found_shelly = []

    with ThreadPoolExecutor(max_workers=50) as executor:
        future_map = {}
        for ip in ips:
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
            cfg["users"] = [{"username": username, "password_hash": generate_password_hash(password), "dark_mode": False}]
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
    devices = []
    for d in cfg.get("shelly_devices", []):
        s = device_states.get(d["ip"], {})
        devices.append({
            "name": d["name"], "ip": d["ip"],
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
        })
    accessories = []
    for acc in cfg.get("accessories", []):
        acc_id = acc.get("id", "")
        st = accessory_states.get(acc_id, {})
        accessories.append({
            "id": acc_id,
            "name": acc.get("name", ""),
            "power_meter_type": acc.get("power_meter_type", ""),
            "power_ips": acc.get("power_ips", [acc.get("power_ip", "")]),
            "icon": acc.get("icon", "mdi-power-plug"),
            "power": st.get("power", 0.0),
            "online": st.get("online", False),
        })

    return jsonify(
        power=current_power, brightness=current_brightness, enabled=enabled,
        devices=devices, expert_mode=cfg.get("expert_mode", False),
        expert_settings=get_runtime_settings(cfg),
        schedules=cfg.get("schedules", []),
        active_schedule=active_schedule_info,
        anti_legionella_enabled=anti_legionella_enabled,
        schedules_enabled=schedules_enabled,
        accessories=accessories
    )


@app.route("/toggle_pid")
def toggle_pid():
    global enabled
    if not require_login():
        return jsonify(success=False), 401
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
    anti_legionella_enabled = not anti_legionella_enabled
    cfg = load_config()
    cfg["anti_legionella_enabled"] = anti_legionella_enabled
    save_config(cfg)
    write_audit_log("anti_legionella_toggled", {"enabled": anti_legionella_enabled})
    return jsonify(success=True, enabled=anti_legionella_enabled)


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
    return render_template("updates.html", dark_mode=get_user_dark_mode())


@app.route("/settings")
def settings():
    if not require_login():
        return redirect("/login")
    return render_template("settings.html", dark_mode=get_user_dark_mode())


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


_forecast_cache = {"data": None, "ts": 0}

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
    if time.time() - _forecast_cache["ts"] < 3600 and _forecast_cache["data"]:
        return jsonify(_forecast_cache["data"])
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=shortwave_radiation"
            f"&forecast_days=2&timezone=auto"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        times = raw["hourly"]["time"]
        radiation = raw["hourly"]["shortwave_radiation"]
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        result = {"today": [], "tomorrow": [], "today_date": today, "tomorrow_date": tomorrow}
        for t, r in zip(times, radiation):
            hour = int(t[11:13])
            if t.startswith(today):
                result["today"].append({"hour": hour, "radiation": r or 0})
            elif t.startswith(tomorrow):
                result["tomorrow"].append({"hour": hour, "radiation": r or 0})
        _forecast_cache = {"data": result, "ts": time.time()}
        return jsonify(result)
    except Exception as e:
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
    write_audit_log("manual_restart", {"user": safe_session_username()})
    def _do_restart():
        cfg = load_config()
        sync_configured_devices_off(cfg.get("shelly_devices", []))
        time.sleep(1)
        if os.name != "nt":
            subprocess.run(["sudo", "systemctl", "restart", "solarbuffer"])
    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify(success=True)


@app.route("/factory_reset", methods=["POST"])
def factory_reset():
    if not require_login():
        return jsonify(success=False), 401
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
        st["brightness"] = 34
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
    # Verzamel alle IPs die al in gebruik zijn
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
    found = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = {}
        for ip in ips:
            futures[executor.submit(detect_shelly_pm, ip)] = ip
            futures[executor.submit(detect_homewizard_pm, ip)] = ip
        for future in as_completed(futures):
            try:
                result = future.result()
                if result and result["ip"] not in used_ips:
                    if not any(f["ip"] == result["ip"] for f in found):
                        found.append(result)
            except Exception:
                pass

    found.sort(key=lambda d: d["ip"])
    return jsonify(devices=found)


@app.route("/accessories", methods=["GET"])
def accessories_page():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    return render_template("accessories.html", accessories=cfg.get("accessories", []), dark_mode=get_user_dark_mode())


@app.route("/accessories", methods=["POST"])
def add_accessory():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    pm_type = (data.get("power_meter_type") or "").strip().lower()
    pm_ips = [ip.strip() for ip in data.get("power_ips", []) if str(ip).strip()]
    if not name or not pm_type or not pm_ips:
        return jsonify(success=False, error="Naam, type en minimaal één IP zijn verplicht"), 400
    if pm_type not in ("shelly", "homewizard"):
        return jsonify(success=False, error="Ongeldig type"), 400
    cfg = load_config()
    icon = (data.get("icon") or "mdi-power-plug").strip()
    new_acc = {"id": str(uuid.uuid4()), "name": name, "power_meter_type": pm_type,
               "power_ip": pm_ips[0], "power_ips": pm_ips, "icon": icon}
    cfg["accessories"].append(new_acc)
    save_config(cfg)
    write_audit_log("accessory_added", {"name": name, "power_ips": pm_ips})
    return jsonify(success=True, accessory=new_acc)


@app.route("/accessories/<acc_id>", methods=["PUT"])
def update_accessory(acc_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True) or {}
    cfg = load_config()
    acc = next((a for a in cfg["accessories"] if a["id"] == acc_id), None)
    if not acc:
        return jsonify(success=False, error="Niet gevonden"), 404
    name = (data.get("name") or "").strip()
    pm_type = (data.get("power_meter_type") or "").strip().lower()
    pm_ips = [ip.strip() for ip in data.get("power_ips", []) if str(ip).strip()]
    if not name or not pm_type or not pm_ips:
        return jsonify(success=False, error="Naam, type en minimaal één IP zijn verplicht"), 400
    if pm_type not in ("shelly", "homewizard"):
        return jsonify(success=False, error="Ongeldig type"), 400
    acc["name"] = name
    acc["power_meter_type"] = pm_type
    acc["power_ip"] = pm_ips[0]
    acc["power_ips"] = pm_ips
    acc["icon"] = (data.get("icon") or "mdi-power-plug").strip()
    save_config(cfg)
    write_audit_log("accessory_updated", {"id": acc_id, "name": name})
    return jsonify(success=True)


@app.route("/accessories/<acc_id>", methods=["DELETE"])
def delete_accessory(acc_id):
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
    cfg = load_config()
    before = len(cfg["accessories"])
    cfg["accessories"] = [a for a in cfg["accessories"] if a["id"] != acc_id]
    if len(cfg["accessories"]) == before:
        return jsonify(success=False, error="Niet gevonden"), 404
    accessory_states.pop(acc_id, None)
    save_config(cfg)
    write_audit_log("accessory_deleted", {"id": acc_id})
    return jsonify(success=True)


@app.route("/users")
def users_page():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    error = request.args.get("error", "")
    return render_template("users.html", users=cfg.get("users", []), error=error, dark_mode=get_user_dark_mode())


@app.route("/users/add", methods=["POST"])
def users_add():
    if not require_login():
        return redirect("/login")
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or not password:
        return redirect("/users?error=Vul alle velden in")
    if len(password) < 6:
        return redirect("/users?error=Wachtwoord moet minimaal 6 tekens bevatten")
    cfg = load_config()
    users = cfg.get("users", [])
    if any(u["username"].lower() == username.lower() for u in users):
        return redirect("/users?error=Gebruikersnaam bestaat al")
    users.append({"username": username, "password_hash": generate_password_hash(password)})
    cfg["users"] = users
    save_config(cfg)
    write_audit_log("user_added", {"new_username": username})
    return redirect("/users")


@app.route("/users/delete", methods=["POST"])
def users_delete():
    if not require_login():
        return redirect("/login")
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


@app.route("/scan_devices", methods=["GET"])
def scan_devices():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401
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
    try:
        r = requests.get(f"http://{ip}/api/v1/data", timeout=2)
        if r.status_code != 200:
            return 0
        data = r.json()
        return float(data.get("active_power_w", 0) or 0)
    except Exception as e:
        print(f"HomeWizard power error ({ip}): {e}")
        return 0


def get_shelly_device_power(ip):
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
            if isinstance(data, dict):
                if "apower" in data:
                    return float(data.get("apower", 0) or 0)
                for _, value in data.items():
                    if isinstance(value, dict) and "apower" in value:
                        return float(value.get("apower", 0) or 0)
        except Exception:
            pass
    return 0


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
    for d in devices:
        ip = d["ip"]
        if ip not in device_states:
            s = saved.get(ip, {})
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
                "pre_schedule_started": None,
                "pre_schedule_brightness": None,
                "pre_schedule_freeze": None,
                "pre_legionella_started": None,
                "pre_legionella_brightness": None,
                "pre_legionella_freeze": None,
            }


def init_device_pids(devices):
    global device_pids
    for d in devices:
        ip = d["ip"]
        if ip not in device_pids:
            p = PID(PID_KP, PID_KI, PID_KD, setpoint=20, sample_time=2)
            p.output_limits = (MIN_BRIGHTNESS, MAX_BRIGHTNESS)
            device_pids[ip] = p


def sync_configured_devices_off(devices):
    if not devices:
        return
    print("Sync: geconfigureerde Shelly apparaten naar UIT zetten...")
    for d in devices:
        ip = d["ip"]
        if ip not in device_states:
            continue
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
        for _ in range(3):
            try:
                set_shelly(0, False, ip)
                time.sleep(0.35)
            except Exception as e:
                print(f"Sync error ({ip}): {e}")
        if has_power_socket(d):
            try:
                set_power_socket(d.get("power_socket_type"), d.get("power_socket_ip"), False)
                time.sleep(0.2)
            except Exception as e:
                print(f"Power socket sync error ({d.get('power_socket_ip')}): {e}")
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
    sync_configured_devices_off(devices)


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
    if frozen and running:
        return "Overcapaciteit"
    if frozen:
        return "Bevroren"
    if running:
        return "Actief"
    expert = cfg.get("expert_settings") or {}
    import_threshold = int(expert.get("IMPORT_OFF_THRESHOLD", 300))
    export_threshold = int(expert.get("EXPORT_THRESHOLD", -50))
    started = [d for d in devices_data if d.get("started") or d.get("pending_start")]
    if current_power >= import_threshold:
        return "Geen teruglevering"
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
            "on": st.get("on", False),
            "power": round(st.get("brightness", 0)),
            "freeze": st.get("freeze", False),
            "online": st.get("online", False),
            "started": st.get("started", False),
            "pending_start": st.get("pending_start", False),
            "waiting_for_power_socket": st.get("waiting_for_power_socket", False),
            "manual_override": st.get("manual_override", False),
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
                st["brightness"] = 34
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
    global current_power, current_brightness, active_schedule_info, anti_legionella_enabled, schedules_enabled

    online_check_interval = 10
    last_online_check = {}
    offline_since_map = {}
    export_start = None
    import_unfreeze_start = None
    import_off_start = None
    prev_schedule_active_ips = set()

    while True:
        try:
            cfg = load_config()
            anti_legionella_enabled = cfg.get("anti_legionella_enabled", False)
            schedules_enabled = cfg.get("schedules_enabled", True)
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
            for d in devices:
                ip = d["ip"]
                st = device_states[ip]
                offline_for = now - offline_since_map.get(ip, now)
                if (offline_for >= 30
                        and st.get("started")
                        and not st.get("freeze")
                        and not st.get("legionella_active")):
                    print(f"Watchdog: {ip} al {int(offline_for)}s offline terwijl gestart → gereset")
                    reset_device_to_off(ip)
                    offline_since_map.pop(ip, None)

            for d in devices:
                ip = d["ip"]
                state = device_states[ip]
                pm_type = (d.get("power_meter") or "").lower()
                pm_ip = (d.get("power_ip") or "").strip() or ip
                if pm_type == "shelly":
                    state["power"] = get_shelly_device_power(pm_ip)
                elif pm_type == "homewizard":
                    state["power"] = get_homewizard_power(pm_ip)
                else:
                    state["power"] = 0

            measured_power = current_power
            pid_power = 20 if PID_NEUTRAL_LOW <= measured_power <= PID_NEUTRAL_HIGH else measured_power
            devices_sorted = get_sorted_devices(devices)
            active_brightness = 0

            # --- Anti-Legionella ---
            legionella_handled = set()
            if anti_legionella_enabled:
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

            if measured_power <= EXPORT_THRESHOLD:
                if export_start is None:
                    export_start = now
            else:
                export_start = None

            non_legionella = [d for d in devices_sorted if d["ip"] not in legionella_handled and d["ip"] not in boost_handled and d["ip"] not in schedule_handled]

            if export_start is not None and (now - export_start) >= EXPORT_DELAY:
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
                        export_start = None

            regulating_device = get_lowest_priority_running(non_legionella)

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
                    b = device_pids[ip](pid_power)
                    b = max(MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, b))
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


# ================= P1 POLL =================
def p1_poll_loop():
    global current_power
    while True:
        try:
            cfg = load_config()
            p1_ip = cfg.get("p1_ip")
            if p1_ip:
                data = requests.get(f"http://{p1_ip}/api/v1/data", timeout=2).json()
                current_power = float(data.get("active_power_w", 0) or 0)
        except Exception:
            pass
        time.sleep(1)


def accessory_poll_loop():
    while True:
        try:
            cfg = load_config()
            for acc in cfg.get("accessories", []):
                acc_id = acc.get("id")
                pm_type = (acc.get("power_meter_type") or "").lower()
                pm_ips = [ip.strip() for ip in acc.get("power_ips", []) if ip.strip()]
                if not acc_id or not pm_ips:
                    continue
                if acc_id not in accessory_states:
                    accessory_states[acc_id] = {"power": 0.0, "online": False}
                try:
                    total_power = 0.0
                    any_online = False
                    for pm_ip in pm_ips:
                        if pm_type == "shelly":
                            p = get_shelly_device_power(pm_ip)
                            total_power += p
                            if p > 0 or check_http_device_online(pm_ip, "/rpc/Shelly.GetStatus"):
                                any_online = True
                        elif pm_type == "homewizard":
                            total_power += get_homewizard_power(pm_ip)
                            any_online = True
                    accessory_states[acc_id]["power"] = total_power
                    accessory_states[acc_id]["online"] = any_online
                except Exception:
                    accessory_states[acc_id]["online"] = False
        except Exception:
            pass
        time.sleep(5)


# ================= START =================
if __name__ == "__main__":
    startup_sync_devices()
    threading.Thread(target=p1_poll_loop, daemon=True).start()
    threading.Thread(target=control_loop, daemon=True).start()
    threading.Thread(target=mqtt_loop, daemon=True).start()
    threading.Thread(target=accessory_poll_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5001)
