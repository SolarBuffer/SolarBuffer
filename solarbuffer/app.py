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
    "PID_NEUTRAL_HIGH": 45,
    "POWER_SOCKET_DELAY": 5,
    "POWER_SOCKET_HOLD_SECONDS": 600
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

    normalized_devices = []
    for d in cfg.get("shelly_devices", []):
        dev = dict(d)
        if "power_socket_type" not in dev:
            dev["power_socket_type"] = ""
        if "power_socket_ip" not in dev:
            dev["power_socket_ip"] = ""
        normalized_devices.append(dev)

    cfg["shelly_devices"] = normalized_devices

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


def safe_int(value, default):
    try:
        return int(str(value).strip())
    except Exception:
        return default


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


def parse_devices_from_request(req):
    devices = []

    names = req.form.getlist("shelly_name[]")
    ips = req.form.getlist("shelly_ip[]")
    priorities = req.form.getlist("priority[]")
    power_meters = req.form.getlist("power_meter[]")
    power_ips = req.form.getlist("power_ip[]")

    power_socket_types = req.form.getlist("power_socket_type[]")
    power_socket_ips = req.form.getlist("power_socket_ip[]")

    row_count = max(
        len(names),
        len(ips),
        len(priorities),
        len(power_meters),
        len(power_ips),
        len(power_socket_types),
        len(power_socket_ips),
    )

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

        devices.append({
            "name": name,
            "ip": ip,
            "priority": prio,
            "power_meter": pm if pm else "",
            "power_ip": pip if pip else "",
            "power_socket_type": ps_type if ps_type else "",
            "power_socket_ip": ps_ip if ps_ip else ""
        })

    return devices


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
        cfg["shelly_devices"] = parse_devices_from_request(request)

        changes = compare_configs(old_cfg, cfg)

        save_config(cfg)

        if changes:
            write_audit_log("config_updated", changes)
        else:
            write_audit_log("config_saved_no_changes", {})

        init_device_states(cfg["shelly_devices"])
        init_device_pids(cfg["shelly_devices"])
        sync_configured_devices_off(cfg["shelly_devices"])

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
        cfg["shelly_devices"] = parse_devices_from_request(request)

        changes = compare_configs(old_cfg, cfg)

        save_config(cfg)

        if changes:
            write_audit_log("config_updated_forced", changes)
        else:
            write_audit_log("config_saved_forced_no_changes", {})

        init_device_states(cfg["shelly_devices"])
        init_device_pids(cfg["shelly_devices"])
        sync_configured_devices_off(cfg["shelly_devices"])

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
            "pending_start": s.get("pending_start", False),
            "power": s.get("power", 0),
            "power_meter": d.get("power_meter"),
            "power_ip": d.get("power_ip"),
            "power_socket_type": d.get("power_socket_type", ""),
            "power_socket_ip": d.get("power_socket_ip", ""),
            "power_socket_on": s.get("power_socket_on", False),
            "waiting_for_power_socket": s.get("waiting_for_power_socket", False),
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

    cfg = load_config()
    device = next((d for d in cfg.get("shelly_devices", []) if d["ip"] == ip), None)

    if not device or ip not in device_states:
        return jsonify(success=False), 404

    st = device_states[ip]
    new_on = not st["on"]

    if new_on:
        ready = ensure_power_socket_on(device)
        if not ready:
            write_audit_log("device_toggle_waiting_for_power_socket", {
                "device_ip": ip
            })
            return jsonify(success=False, waiting_for_power_socket=True)

        st["on"] = True
        st["manual_override"] = True
        st["started"] = True
        st["pending_start"] = False
        st["freeze"] = False
        st["saturated_since"] = None
        st["min_since"] = None
        st["brightness"] = 100

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
        set_shelly(0, False, ip)

    write_audit_log("device_toggled", {
        "device_ip": ip,
        "new_state": "on" if new_on else "off",
        "brightness": st["brightness"]
    })

    return jsonify(success=True, on=new_on)


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
            r = requests.post(
                f"http://{ip}/rpc/Switch.Set",
                json={"id": 0, "on": on},
                timeout=2
            )
            return r.status_code == 200

        elif pst == "homewizard":
            r = requests.put(
                f"http://{ip}/api/v1/state",
                json={"power_on": on},
                timeout=2
            )
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


def ensure_power_socket_on(device):
    st = get_device_state(device)

    if not has_power_socket(device):
        return True

    pstype = device.get("power_socket_type")
    psip = device.get("power_socket_ip")
    cfg = load_config()
    settings = get_runtime_settings(cfg)
    delay = int(settings.get("POWER_SOCKET_DELAY", 5) or 5)

    # socket al aan en wachttijd klaar
    if st["power_socket_on"] and not st["waiting_for_power_socket"]:
        st["power_socket_last_on_command"] = time.time()
        return True

    # eerste inschakeling
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

    # wachten tot de opstart-delay verstreken is
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
                "pending_start": False,
                "saturated_since": None,
                "min_since": None,
                "last_active_time": 0,
                "power": 0,
                "power_socket_on": False,
                "power_socket_last_on_command": 0,
                "waiting_for_power_socket": False,
                "power_socket_ready_at": None
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
            if st["started"] or st["on"] or st["brightness"] > 0 or st.get("pending_start"):
                return False
    return True


def get_next_startable_device(devices_sorted):
    for d in devices_sorted:
        st = get_device_state(d)
        prio = d["priority"]

        if st["started"] or st.get("pending_start"):
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
    state["pending_start"] = False
    state["saturated_since"] = None
    state["min_since"] = None
    state["waiting_for_power_socket"] = False
    state["power_socket_ready_at"] = None
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
                    maybe_turn_off_power_socket(d)

                time.sleep(1)
                continue

            # 0. Rond pending starts altijd eerst af
            for d in devices_sorted:
                ip = d["ip"]
                st = device_states[ip]

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

                                if st["brightness"] < MIN_BRIGHTNESS:
                                    st["brightness"] = MIN_BRIGHTNESS

                                set_shelly(st["brightness"], True, ip)
                                mark_device_activity(next_dev)

                        export_start = None

                    else:
                        st["started"] = True
                        st["pending_start"] = False
                        st["on"] = True
                        st["freeze"] = False
                        st["saturated_since"] = None
                        st["min_since"] = None

                        if st["brightness"] < MIN_BRIGHTNESS:
                            st["brightness"] = MIN_BRIGHTNESS

                        set_shelly(st["brightness"], True, ip)
                        mark_device_activity(next_dev)
                        export_start = None

            # 3. Bepaal welke prio actief mag regelen
            regulating_device = get_lowest_priority_running(devices_sorted)

            for d in devices_sorted:
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
                    mark_device_activity(d)

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
                        mark_device_activity(candidate_unfreeze)
                        import_unfreeze_start = None
                else:
                    import_unfreeze_start = None
            else:
                import_unfreeze_start = None

            # 6. Powersockets eventueel uitschakelen na hold-time
            for d in devices_sorted:
                maybe_turn_off_power_socket(d)

            time.sleep(1)

        except Exception as e:
            print("Fout control_loop:", e)
            time.sleep(2)


# ================= START =================
if __name__ == "__main__":
    startup_sync_devices()
    threading.Thread(target=control_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5001)
