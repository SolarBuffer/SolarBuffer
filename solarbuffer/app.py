from simple_pid import PID
import requests
import time
import json
import os
import socket
import ipaddress
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, jsonify, request, redirect, session
import threading

# ================= CONFIG =================
BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
AUDIT_LOG_FILE = os.path.join(BASE_DIR, "audit.log")

DEFAULT_EXPERT_SETTINGS = {
    "EXPORT_THRESHOLD": -50,
    "EXPORT_DELAY": 15,
    "FREEZE_AT": 95,
    "FREEZE_CONFIRM": 2,
    "IMPORT_UNFREEZE_THRESHOLD": 200,
    "UNFREEZE_DELAY": 5,
    "IMPORT_OFF_THRESHOLD": 300,
    "OFF_DELAY": 120,
    "PID_NEUTRAL_LOW": -5,
    "PID_NEUTRAL_HIGH": 45
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

    if "expert_mode" not in cfg:
        cfg["expert_mode"] = False

    if "expert_settings" not in cfg or not isinstance(cfg["expert_settings"], dict):
        cfg["expert_settings"] = {}

    for key, value in DEFAULT_EXPERT_SETTINGS.items():
        if key not in cfg["expert_settings"]:
            cfg["expert_settings"][key] = value

    return cfg

def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

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

# ================= AUDIT =================
audit_lock = threading.Lock()

def safe_session_username():
    try:
        return session.get("username", "unknown")
    except Exception:
        return "system"

def safe_request_ip():
    try:
        if request.headers.get("X-Forwarded-For"):
            return request.headers.get("X-Forwarded-For").split(",")[0].strip()
        return request.remote_addr or "unknown"
    except Exception:
        return "system"

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
    except Exception as e:
        print(f"Audit log fout: {e}")

def compare_configs(old_cfg, new_cfg):
    changes = {}

    if old_cfg.get("p1_ip") != new_cfg.get("p1_ip"):
        changes["p1_ip"] = {
            "old": old_cfg.get("p1_ip"),
            "new": new_cfg.get("p1_ip")
        }

    if old_cfg.get("expert_mode") != new_cfg.get("expert_mode"):
        changes["expert_mode"] = {
            "old": old_cfg.get("expert_mode"),
            "new": new_cfg.get("expert_mode")
        }

    old_settings = old_cfg.get("expert_settings", {})
    new_settings = new_cfg.get("expert_settings", {})
    settings_changes = {}

    for key in DEFAULT_EXPERT_SETTINGS.keys():
        if old_settings.get(key) != new_settings.get(key):
            settings_changes[key] = {
                "old": old_settings.get(key),
                "new": new_settings.get(key)
            }

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
            modified.append({
                "ip": ip,
                "old": old_by_ip[ip],
                "new": new_by_ip[ip]
            })

    if added:
        changes["devices_added"] = added
    if removed:
        changes["devices_removed"] = removed
    if modified:
        changes["devices_modified"] = modified

    return changes

# ================= PID =================
PID_KP = 0.02
PID_KI = 0.0015
PID_KD = 0.0

device_pids = {}

enabled = True
device_states = {}

current_power = 0
current_brightness = 0

# ================= CONTROL CONSTANTS =================
MIN_BRIGHTNESS = 34
MAX_BRIGHTNESS = 100

# ================= FLASK =================
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "verander_dit_naar_iets_veiligs!")

def require_login():
    return session.get("logged_in", False)

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

        possible_keys = [
            "active_power_w",
            "total_power_import_t1_kwh",
            "total_power_export_t1_kwh",
            "wifi_ssid"
        ]

        if any(k in data for k in possible_keys):
            return {
                "type": "homewizard_p1",
                "name": f"HomeWizard P1 ({ip})",
                "ip": ip
            }
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

        # Alleen Shelly Dimmer Gen3 0/1-10V toelaten
        allowed_models = {"S3DM-0010WW", "0010WW"}
        if model not in allowed_models:
            return None

        return {
            "type": "shelly",
            "name": data.get("name") or "Shelly Dimmer Gen3",
            "ip": ip,
            "model": model,
            "gen": data.get("gen", 3)
        }

    except Exception:
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

    return {
        "p1_meters": unique_p1,
        "shelly_devices": unique_shelly
    }

# ================= ROUTES =================
@app.route("/login", methods=["GET", "POST"])
def login():
    cfg = load_config()
    username_cfg = cfg.get("username", "admin")
    password_cfg = cfg.get("password", "admin123")

    if request.method == "POST":
        entered_username = request.form.get("username", "").strip()
        entered_password = request.form.get("password", "")

        if entered_username == username_cfg and entered_password == password_cfg:
            session["logged_in"] = True
            session["username"] = entered_username

            write_audit_log("login_success", {
                "username": entered_username
            })

            return redirect("/")
        else:
            write_audit_log("login_failed", {
                "username": entered_username
            })
            return render_template("login.html", error="Ongeldige login")

    return render_template("login.html")

@app.route("/", methods=["GET", "POST"])
def wizard():
    if not require_login():
        return redirect("/login")

    cfg = load_config()
    if cfg.get("p1_ip") and cfg.get("shelly_devices"):
        return redirect("/dashboard")

    if request.method == "POST":
        old_cfg = load_config()

        cfg["p1_ip"] = request.form.get("p1ip", "").strip()
        cfg["expert_mode"] = request.form.get("expert_mode") == "on"
        cfg["expert_settings"] = parse_expert_settings_from_request(request)

        devices = []
        names = request.form.getlist("shelly_name[]")
        ips = request.form.getlist("shelly_ip[]")
        priorities = request.form.getlist("priority[]")
        power_meters = request.form.getlist("power_meter[]")
        power_ips = request.form.getlist("power_ip[]")

        for name, ip, prio, pm, pip in zip(names, ips, priorities, power_meters, power_ips):
            if name.strip() and ip.strip():
                devices.append({
                    "name": name.strip(),
                    "ip": ip.strip(),
                    "priority": int(prio),
                    "power_meter": pm.strip() if pm else "",
                    "power_ip": pip.strip() if pip else ""
                })

        cfg["shelly_devices"] = devices
        changes = compare_configs(old_cfg, cfg)

        save_config(cfg)

        if changes:
            write_audit_log("config_updated", changes)
        else:
            write_audit_log("config_saved_no_changes", {})

        init_device_states(devices)
        init_device_pids(devices)
        sync_configured_devices_off(devices)

        return redirect("/dashboard")

    return render_template("wizard.html", config=cfg)

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

        devices = []
        names = request.form.getlist("shelly_name[]")
        ips = request.form.getlist("shelly_ip[]")
        priorities = request.form.getlist("priority[]")
        power_meters = request.form.getlist("power_meter[]")
        power_ips = request.form.getlist("power_ip[]")

        for name, ip, prio, pm, pip in zip(names, ips, priorities, power_meters, power_ips):
            if name.strip() and ip.strip():
                devices.append({
                    "name": name.strip(),
                    "ip": ip.strip(),
                    "priority": int(prio),
                    "power_meter": pm.strip() if pm else "",
                    "power_ip": pip.strip() if pip else ""
                })

        cfg["shelly_devices"] = devices
        changes = compare_configs(old_cfg, cfg)

        save_config(cfg)

        if changes:
            write_audit_log("config_updated_forced", changes)
        else:
            write_audit_log("config_saved_forced_no_changes", {})

        init_device_states(devices)
        init_device_pids(devices)
        sync_configured_devices_off(devices)

        return redirect("/dashboard")

    return render_template("wizard.html", config=cfg)

@app.route("/dashboard")
def dashboard():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    if not cfg.get("p1_ip") or not cfg.get("shelly_devices"):
        return redirect("/")
    return render_template("dashboard.html", config=cfg)

@app.route("/status_json")
def status_json():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401

    cfg = load_config()
    devices = []

    for d in cfg.get("shelly_devices", []):
        s = device_states.get(d["ip"], {})
        devices.append({
            "name": d["name"],
            "ip": d["ip"],
            "on": s.get("on", False),
            "brightness": s.get("brightness", 0),
            "online": s.get("online", False),
            "power_meter_online": s.get("power_meter_online", False),
            "freeze": s.get("freeze", False),
            "started": s.get("started", False),
            "power": s.get("power", 0),
            "power_meter": d.get("power_meter"),
            "power_ip": d.get("power_ip")
        })

    return jsonify(
        power=current_power,
        brightness=current_brightness,
        enabled=enabled,
        devices=devices,
        expert_mode=cfg.get("expert_mode", False),
        expert_settings=get_runtime_settings(cfg)
    )

@app.route("/toggle_pid")
def toggle_pid():
    global enabled
    if not require_login():
        return jsonify(success=False), 401

    enabled = not enabled

    write_audit_log("pid_toggled", {
        "enabled": enabled
    })

    return jsonify(success=True)

@app.route("/toggle_shelly/<path:ip>")
def toggle_shelly(ip):
    if not require_login():
        return jsonify(success=False), 401

    if ip in device_states:
        new_on = not device_states[ip]["on"]
        device_states[ip]["on"] = new_on
        device_states[ip]["manual_override"] = True
        device_states[ip]["started"] = new_on
        device_states[ip]["brightness"] = 100 if new_on else 0

        if not new_on:
            device_states[ip]["freeze"] = False
            device_states[ip]["saturated_since"] = None
            device_states[ip]["min_since"] = None

        set_shelly(device_states[ip]["brightness"], new_on, ip)

        write_audit_log("device_toggled", {
            "device_ip": ip,
            "new_state": "on" if new_on else "off",
            "brightness": device_states[ip]["brightness"]
        })

        return jsonify(success=True, on=new_on)

    return jsonify(success=False), 404

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

def init_device_states(devices):
    global device_states
    for d in devices:
        ip = d["ip"]
        if ip not in device_states:
            device_states[ip] = {
                "on": False,
                "brightness": 0,
                "online": False,
                "power_meter_online": False,
                "manual_override": False,
                "freeze": False,
                "started": False,
                "saturated_since": None,
                "min_since": None,
                "last_active_time": time.time(),
                "power": 0
            }

def init_device_pids(devices):
    global device_pids
    for d in devices:
        ip = d["ip"]
        if ip not in device_pids:
            p = PID(PID_KP, PID_KI, PID_KD, setpoint=0)
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
        state["manual_override"] = False
        state["saturated_since"] = None
        state["min_since"] = None

        for _ in range(3):
            try:
                set_shelly(0, False, ip)
                time.sleep(0.35)
            except Exception as e:
                print(f"Sync error ({ip}): {e}")

    print("Sync voltooid.")

def startup_sync_devices():
    cfg = load_config()
    devices = cfg.get("shelly_devices", [])

    if not devices:
        return

    init_device_states(devices)
    init_device_pids(devices)
    sync_configured_devices_off(devices)

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

def higher_priorities_started_and_frozen(devices_sorted, priority):
    for d in devices_sorted:
        if d["priority"] < priority:
            st = get_device_state(d)
            if not st["started"] or not st["freeze"]:
                return False
    return True

def lower_priorities_off(devices_sorted, priority):
    for d in devices_sorted:
        if d["priority"] > priority:
            st = get_device_state(d)
            if st["started"] or st["on"] or st["brightness"] > 0:
                return False
    return True

def get_next_startable_device(devices_sorted):
    for d in devices_sorted:
        st = get_device_state(d)
        prio = d["priority"]

        if st["started"]:
            continue

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

def reset_device_to_off(ip):
    state = device_states[ip]
    state["on"] = False
    state["brightness"] = 0
    state["freeze"] = False
    state["started"] = False
    state["saturated_since"] = None
    state["min_since"] = None
    set_shelly(0, False, ip)

def hold_frozen_output(ip):
    state = device_states[ip]
    if state["brightness"] < MIN_BRIGHTNESS:
        state["brightness"] = MIN_BRIGHTNESS
    state["on"] = True
    set_shelly(state["brightness"], True, ip)

# ================= CONTROL LOOP =================
def control_loop():
    global current_power, current_brightness

    online_check_interval = 10
    last_online_check = {}

    export_start = None
    import_unfreeze_start = None
    import_off_start = None

    while True:
        try:
            cfg = load_config()
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

            # ONLINE CHECKS
            for d in devices:
                ip = d["ip"]
                pm_type = (d.get("power_meter") or "").lower()
                pm_ip = (d.get("power_ip") or "").strip() or ip

                if now - last_online_check.get(ip, 0) > online_check_interval:
                    device_states[ip]["online"] = check_shelly_online(ip)

                    if pm_type == "shelly":
                        device_states[ip]["power_meter_online"] = check_http_device_online(pm_ip, "/rpc/Shelly.GetStatus")
                    elif pm_type == "homewizard":
                        device_states[ip]["power_meter_online"] = check_http_device_online(pm_ip, "/api/v1/data")
                    else:
                        device_states[ip]["power_meter_online"] = False

                    last_online_check[ip] = now

            # DEVICE POWER
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

            # P1 POWER
            data = requests.get(f"http://{p1_ip}/api/v1/data", timeout=2).json()
            measured_power = data.get("active_power_w", 0)
            current_power = measured_power

            pid_power = 0 if PID_NEUTRAL_LOW <= measured_power <= PID_NEUTRAL_HIGH else measured_power

            devices_sorted = get_sorted_devices(devices)
            active_brightness = 0

            # PID uitgeschakeld -> huidige standen vasthouden
            if not enabled:
                for d in devices_sorted:
                    ip = d["ip"]
                    st = device_states[ip]

                    if not st["started"]:
                        continue

                    if st["freeze"]:
                        hold_frozen_output(ip)
                    elif st["on"] and st["brightness"] >= MIN_BRIGHTNESS:
                        set_shelly(st["brightness"], True, ip)

                current_brightness = 0
                time.sleep(1)
                continue

            # 1. Start timer voor export
            if measured_power <= EXPORT_THRESHOLD:
                if export_start is None:
                    export_start = now
            else:
                export_start = None

            # 2. Start volgende prio bij export
            if export_start is not None and (now - export_start) >= EXPORT_DELAY:
                next_dev = get_next_startable_device(devices_sorted)
                if next_dev:
                    ip = next_dev["ip"]
                    st = device_states[ip]

                    st["started"] = True
                    st["on"] = True
                    st["freeze"] = False
                    st["saturated_since"] = None
                    st["min_since"] = None

                    if st["brightness"] < MIN_BRIGHTNESS:
                        st["brightness"] = MIN_BRIGHTNESS

                    set_shelly(st["brightness"], True, ip)
                    export_start = None

            # 3. Bepaal welke prio actief mag regelen
            regulating_device = get_lowest_priority_running(devices_sorted)

            for d in devices_sorted:
                ip = d["ip"]
                st = device_states[ip]

                if not st["started"]:
                    if st["on"] or st["brightness"] != 0 or st["freeze"]:
                        reset_device_to_off(ip)
                    continue

                if not st["online"]:
                    continue

                if st["freeze"]:
                    hold_frozen_output(ip)
                    continue

                if regulating_device and ip == regulating_device["ip"]:
                    b = device_pids[ip](pid_power)
                    b = max(MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, b))

                    st["brightness"] = b
                    st["on"] = True
                    set_shelly(b, True, ip)
                    active_brightness = b

                    if b >= FREEZE_AT and not is_last_possible_priority(devices_sorted, d):
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

            current_brightness = active_brightness

            # 4. Laagste actieve prio uitschakelen
            lowest_running = get_lowest_priority_running(devices_sorted)

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

            # 5. Hogere frozen prio pas unfreeze als alle lagere prio's uit staan
            candidate_unfreeze = get_highest_frozen_allowed_to_unfreeze(devices_sorted)

            if candidate_unfreeze and get_lowest_priority_running(devices_sorted) is None:
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
                        set_shelly(st["brightness"], True, candidate_unfreeze["ip"])
                        import_unfreeze_start = None
                else:
                    import_unfreeze_start = None
            else:
                import_unfreeze_start = None

            time.sleep(1)

        except Exception as e:
            print("Fout control_loop:", e)
            time.sleep(2)

# ================= START =================
if __name__ == "__main__":
    startup_sync_devices()
    threading.Thread(target=control_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5001)