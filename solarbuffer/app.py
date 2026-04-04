from simple_pid import PID
import requests
import time
import json
import os
import socket
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, jsonify, request, redirect, session
import threading

# ================= CONFIG =================
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")


def default_battery_config():
    return {
        "enabled": False,
        "ip": "",
        "mode": "hybrid",              # battery_first | buffers_first | hybrid
        "min_soc": 20,
        "target_soc": 50,
        "max_soc": 95,
        "max_charge_w": 1500,
        "max_discharge_w": 1500,
        "allow_discharge": True
    }


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
    else:
        cfg = {}

    if "shelly_devices" not in cfg:
        cfg["shelly_devices"] = []
    if "p1_ip" not in cfg:
        cfg["p1_ip"] = ""
    if "battery" not in cfg or not isinstance(cfg["battery"], dict):
        cfg["battery"] = default_battery_config()
    else:
        merged_battery = default_battery_config()
        merged_battery.update(cfg["battery"])
        cfg["battery"] = merged_battery

    return cfg


def save_config(data):
    if "battery" not in data or not isinstance(data["battery"], dict):
        data["battery"] = default_battery_config()
    else:
        merged_battery = default_battery_config()
        merged_battery.update(data["battery"])
        data["battery"] = merged_battery

    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)


# ================= PID =================
PID_KP = 0.02
PID_KI = 0.0015
PID_KD = 0.0

device_pids = {}

enabled = True
device_states = {}

current_power = 0
current_virtual_power = 0
current_brightness = 0

battery_state = {
    "online": False,
    "soc": 0,
    "power": 0,
    "mode": "idle",                   # idle | charge | discharge
    "last_command_w": 0,
    "last_action": "idle",
    "ip": ""
}

# ================= CONTROL CONSTANTS =================
MIN_BRIGHTNESS = 34
MAX_BRIGHTNESS = 100

EXPORT_THRESHOLD = -50               # bij <= -50W export
EXPORT_DELAY = 15                    # 15s voordat volgende prio start

FREEZE_AT = 95                       # freeze vanaf 95%
FREEZE_CONFIRM = 2                   # 2s bevestigd op >=95%

IMPORT_UNFREEZE_THRESHOLD = 200      # bij >=200W import mag hogere prio terug regelen
UNFREEZE_DELAY = 5                   # 5s

IMPORT_OFF_THRESHOLD = 300           # bij >=300W import mag laagste actieve prio uit
OFF_DELAY = 120                      # 2 minuten

PID_NEUTRAL_LOW = -5
PID_NEUTRAL_HIGH = 45

BATTERY_EXPORT_MARGIN = 100
BATTERY_IMPORT_MARGIN = 150
BATTERY_BIG_SURPLUS_W = 1200
BATTERY_BUFFERS_FIRST_EXTRA_SURPLUS = 1500

# ================= FLASK =================
app = Flask(__name__)
app.secret_key = "verander_dit_naar_iets_veiligs!"


def require_login():
    return session.get("logged_in", False)


# ================= HELPERS =================
def to_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def safe_int(value, default):
    try:
        return int(value)
    except Exception:
        return default


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


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
        model = (data.get("model") or "").strip()

        if model not in ("S3DM-0010WW", "0010WW"):
            return None

        return {
            "type": "shelly",
            "name": data.get("name") or "Shelly Dimmer Gen3",
            "ip": ip,
            "model": model,
            "gen": data.get("gen", 3)
        }

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
        if request.form.get("username") == username_cfg and request.form.get("password") == password_cfg:
            session["logged_in"] = True
            return redirect("/")
        else:
            return render_template("login.html", error="Ongeldige login")
    return render_template("login.html")


def parse_battery_from_form(form):
    battery = default_battery_config()

    battery["enabled"] = to_bool(form.get("battery_enabled"))
    battery["ip"] = form.get("battery_ip", "").strip()
    battery["mode"] = form.get("battery_mode", "hybrid").strip() or "hybrid"
    battery["min_soc"] = clamp(safe_int(form.get("battery_min_soc"), 20), 0, 100)
    battery["target_soc"] = clamp(safe_int(form.get("battery_target_soc"), 50), 0, 100)
    battery["max_soc"] = clamp(safe_int(form.get("battery_max_soc"), 95), 0, 100)
    battery["max_charge_w"] = max(0, safe_int(form.get("battery_max_charge_w"), 1500))
    battery["max_discharge_w"] = max(0, safe_int(form.get("battery_max_discharge_w"), 1500))
    battery["allow_discharge"] = to_bool(form.get("battery_allow_discharge"))

    if battery["target_soc"] < battery["min_soc"]:
        battery["target_soc"] = battery["min_soc"]

    if battery["max_soc"] < battery["target_soc"]:
        battery["max_soc"] = battery["target_soc"]

    if battery["mode"] not in ("battery_first", "buffers_first", "hybrid"):
        battery["mode"] = "hybrid"

    return battery


@app.route("/", methods=["GET", "POST"])
def wizard():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    if cfg.get("p1_ip") and cfg.get("shelly_devices"):
        return redirect("/dashboard")

    if request.method == "POST":
        cfg["p1_ip"] = request.form["p1ip"].strip()

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
        cfg["battery"] = parse_battery_from_form(request.form)

        save_config(cfg)
        init_device_states(devices)
        init_device_pids(devices)
        return redirect("/dashboard")

    return render_template("wizard.html", config=cfg)


@app.route("/wizard_forced", methods=["GET", "POST"])
def wizard_forced():
    if not require_login():
        return redirect("/login")

    cfg = load_config()

    if request.method == "POST":
        cfg["p1_ip"] = request.form.get("p1ip", "").strip()
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
        cfg["battery"] = parse_battery_from_form(request.form)

        save_config(cfg)
        init_device_states(devices)
        init_device_pids(devices)

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
            "freeze": s.get("freeze", False),
            "started": s.get("started", False),
            "power": s.get("power", 0),
            "power_meter": d.get("power_meter"),
            "power_ip": d.get("power_ip")
        })

    return jsonify(
        power=current_power,
        virtual_power=current_virtual_power,
        brightness=current_brightness,
        enabled=enabled,
        devices=devices,
        battery={
            "enabled": cfg.get("battery", {}).get("enabled", False),
            "ip": battery_state.get("ip", ""),
            "online": battery_state.get("online", False),
            "soc": battery_state.get("soc", 0),
            "power": battery_state.get("power", 0),
            "mode": battery_state.get("mode", "idle"),
            "strategy": cfg.get("battery", {}).get("mode", "hybrid"),
            "last_command_w": battery_state.get("last_command_w", 0),
            "last_action": battery_state.get("last_action", "idle"),
            "config": cfg.get("battery", {})
        }
    )


@app.route("/toggle_pid")
def toggle_pid():
    global enabled
    if not require_login():
        return jsonify(success=False), 401
    enabled = not enabled
    return jsonify(success=True)


@app.route("/toggle_shelly/<path:ip>")
def toggle_shelly(ip):
    if not require_login():
        return jsonify(success=False), 401

    if ip in device_states:
        device_states[ip]["on"] = not device_states[ip]["on"]
        device_states[ip]["manual_override"] = True
        device_states[ip]["brightness"] = 100 if device_states[ip]["on"] else 0
        set_shelly(device_states[ip]["brightness"], device_states[ip]["on"], ip)
        return jsonify(success=True, on=device_states[ip]["on"])

    return jsonify(success=False), 404


@app.route("/scan_devices", methods=["GET"])
def scan_devices():
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401

    try:
        result = scan_network_for_devices()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ================= SHELLY =================
def set_shelly(brightness, on, ip):
    try:
        requests.post(
            f"http://{ip}/rpc/Light.Set",
            json={"id": 0, "on": on, "brightness": round(brightness)},
            timeout=2
        )
    except Exception as e:
        print(f"Shelly error ({ip}):", e)


def check_shelly_online(ip):
    try:
        r = requests.get(f"http://{ip}/rpc/Shelly.GetStatus", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def get_homewizard_power(ip):
    try:
        r = requests.get(f"http://{ip}/api/v1/data", timeout=2).json()
        return r.get("active_power_w", 0)
    except Exception as e:
        print(f"HomeWizard power error ({ip}):", e)
        return 0


def get_shelly_device_power(ip):
    try:
        r = requests.get(f"http://{ip}/rpc/Switch.GetStatus?id=0", timeout=2).json()
        return r.get("apower", 0)
    except Exception as e:
        print(f"Power read error ({ip}):", e)
        return 0


# ================= BATTERY HELPERS =================
def get_battery_status(ip):
    """
    Probeer zo generiek mogelijk battery data te lezen.
    Pas deze functie aan zodra je de exacte HomeWizard batterij JSON kent.
    """
    try:
        r = requests.get(f"http://{ip}/api/v1/data", timeout=2)
        if r.status_code != 200:
            return {
                "online": False,
                "soc": 0,
                "power": 0
            }

        data = r.json()
        if not isinstance(data, dict):
            return {
                "online": False,
                "soc": 0,
                "power": 0
            }

        soc = (
            data.get("soc")
            or data.get("state_of_charge_pct")
            or data.get("state_of_charge")
            or data.get("battery_soc")
            or 0
        )

        power = (
            data.get("active_power_w")
            or data.get("battery_power_w")
            or data.get("power_w")
            or 0
        )

        return {
            "online": True,
            "soc": clamp(safe_int(soc, 0), 0, 100),
            "power": safe_int(power, 0)
        }
    except Exception as e:
        print(f"Battery status error ({ip}):", e)
        return {
            "online": False,
            "soc": 0,
            "power": 0
        }


def set_battery_idle(ip):
    """
    VUL HIER de echte HomeWizard battery API in.
    Nu veilig als no-op.
    """
    try:
        # Voorbeeld placeholder:
        # requests.post(f"http://{ip}/api/v1/battery/idle", timeout=2)
        return True
    except Exception as e:
        print(f"Battery idle error ({ip}):", e)
        return False


def set_battery_charge_power(ip, watts):
    """
    VUL HIER de echte HomeWizard battery API in.
    Nu veilig als no-op.
    """
    try:
        # Voorbeeld placeholder:
        # requests.post(
        #     f"http://{ip}/api/v1/battery/charge",
        #     json={"power_w": int(watts)},
        #     timeout=2
        # )
        return True
    except Exception as e:
        print(f"Battery charge error ({ip}):", e)
        return False


def set_battery_discharge_power(ip, watts):
    """
    VUL HIER de echte HomeWizard battery API in.
    Nu veilig als no-op.
    """
    try:
        # Voorbeeld placeholder:
        # requests.post(
        #     f"http://{ip}/api/v1/battery/discharge",
        #     json={"power_w": int(watts)},
        #     timeout=2
        # )
        return True
    except Exception as e:
        print(f"Battery discharge error ({ip}):", e)
        return False


def apply_battery_command(ip, action, power_w):
    power_w = max(0, int(power_w))

    success = False
    if action == "charge":
        success = set_battery_charge_power(ip, power_w)
    elif action == "discharge":
        success = set_battery_discharge_power(ip, power_w)
    else:
        success = set_battery_idle(ip)

    battery_state["last_action"] = action
    battery_state["last_command_w"] = power_w if action in ("charge", "discharge") else 0
    battery_state["mode"] = action if success else "idle"

    return success


def decide_battery_action(measured_power, battery_cfg, batt_state):
    """
    measured_power:
      negatief = export
      positief = import
    """
    if not battery_cfg.get("enabled"):
        return {"action": "idle", "power_w": 0}

    if not batt_state.get("online"):
        return {"action": "idle", "power_w": 0}

    soc = batt_state.get("soc", 0)
    mode = battery_cfg.get("mode", "hybrid")
    min_soc = clamp(safe_int(battery_cfg.get("min_soc"), 20), 0, 100)
    target_soc = clamp(safe_int(battery_cfg.get("target_soc"), 50), 0, 100)
    max_soc = clamp(safe_int(battery_cfg.get("max_soc"), 95), 0, 100)
    max_charge_w = max(0, safe_int(battery_cfg.get("max_charge_w"), 1500))
    max_discharge_w = max(0, safe_int(battery_cfg.get("max_discharge_w"), 1500))
    allow_discharge = bool(battery_cfg.get("allow_discharge", True))

    surplus_w = max(0, -measured_power)
    import_w = max(0, measured_power)

    if mode == "battery_first":
        if surplus_w >= BATTERY_EXPORT_MARGIN and soc < max_soc:
            return {
                "action": "charge",
                "power_w": min(surplus_w, max_charge_w)
            }

        if allow_discharge and import_w >= BATTERY_IMPORT_MARGIN and soc > min_soc:
            return {
                "action": "discharge",
                "power_w": min(import_w, max_discharge_w)
            }

        return {"action": "idle", "power_w": 0}

    if mode == "buffers_first":
        if allow_discharge and import_w >= BATTERY_IMPORT_MARGIN and soc > min_soc:
            return {
                "action": "discharge",
                "power_w": min(import_w, max_discharge_w)
            }

        if surplus_w >= BATTERY_BUFFERS_FIRST_EXTRA_SURPLUS and soc < max_soc:
            reserve_for_buffers = 1000
            available_for_battery = max(0, surplus_w - reserve_for_buffers)
            if available_for_battery > 0:
                return {
                    "action": "charge",
                    "power_w": min(available_for_battery, max_charge_w)
                }

        return {"action": "idle", "power_w": 0}

    # hybrid
    if surplus_w >= BATTERY_EXPORT_MARGIN:
        if soc < target_soc:
            return {
                "action": "charge",
                "power_w": min(surplus_w, max_charge_w)
            }

        if soc < max_soc and surplus_w >= BATTERY_BIG_SURPLUS_W:
            reserve_for_buffers = 800
            available_for_battery = max(0, surplus_w - reserve_for_buffers)
            if available_for_battery > 0:
                return {
                    "action": "charge",
                    "power_w": min(available_for_battery, max_charge_w)
                }

    if allow_discharge and import_w >= BATTERY_IMPORT_MARGIN and soc > min_soc:
        return {
            "action": "discharge",
            "power_w": min(import_w, max_discharge_w)
        }

    return {"action": "idle", "power_w": 0}


# ================= DEVICE STATE INIT =================
def init_device_states(devices):
    global device_states
    for d in devices:
        ip = d["ip"]
        if ip not in device_states:
            device_states[ip] = {
                "on": False,
                "brightness": 0,
                "online": False,
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
    global current_power, current_virtual_power, current_brightness, battery_state

    online_check_interval = 10
    last_online_check = {}
    last_battery_online_check = 0

    export_start = None
    import_unfreeze_start = None
    import_off_start = None

    while True:
        try:
            cfg = load_config()
            p1_ip = cfg.get("p1_ip")
            devices = cfg.get("shelly_devices", [])
            battery_cfg = cfg.get("battery", default_battery_config())

            if not p1_ip:
                time.sleep(2)
                continue

            init_device_states(devices)
            init_device_pids(devices)

            now = time.time()

            # ONLINE CHECK SHELLY
            for d in devices:
                ip = d["ip"]
                if now - last_online_check.get(ip, 0) > online_check_interval:
                    device_states[ip]["online"] = check_shelly_online(ip)
                    last_online_check[ip] = now

            # DEVICE POWER
            for d in devices:
                ip = d["ip"]
                state = device_states[ip]
                if not state.get("online"):
                    state["power"] = 0
                    continue

                pm_type = (d.get("power_meter") or "").lower()
                pm_ip = d.get("power_ip") or ip

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

            # BATTERY STATUS
            battery_ip = (battery_cfg.get("ip") or "").strip()
            battery_state["ip"] = battery_ip

            if battery_cfg.get("enabled") and battery_ip:
                if now - last_battery_online_check > 2:
                    batt = get_battery_status(battery_ip)
                    battery_state["online"] = batt.get("online", False)
                    battery_state["soc"] = batt.get("soc", 0)
                    battery_state["power"] = batt.get("power", 0)
                    last_battery_online_check = now

                batt_cmd = decide_battery_action(measured_power, battery_cfg, battery_state)

                if enabled:
                    apply_battery_command(
                        battery_ip,
                        batt_cmd["action"],
                        batt_cmd["power_w"]
                    )
                else:
                    apply_battery_command(battery_ip, "idle", 0)

                # virtual power: wat er overblijft voor buffers na battery-keuze
                if batt_cmd["action"] == "charge":
                    virtual_power = measured_power + batt_cmd["power_w"]
                elif batt_cmd["action"] == "discharge":
                    virtual_power = measured_power - batt_cmd["power_w"]
                else:
                    virtual_power = measured_power
            else:
                battery_state["online"] = False
                battery_state["soc"] = 0
                battery_state["power"] = 0
                battery_state["mode"] = "idle"
                battery_state["last_action"] = "idle"
                battery_state["last_command_w"] = 0
                virtual_power = measured_power

            current_virtual_power = virtual_power

            pid_power = 0 if PID_NEUTRAL_LOW <= virtual_power <= PID_NEUTRAL_HIGH else virtual_power

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

            # Als er geen devices zijn, dan alleen batterijlogica draaien
            if not devices_sorted:
                current_brightness = 0
                time.sleep(1)
                continue

            # 1. Start timer voor export op basis van virtual power
            if virtual_power <= EXPORT_THRESHOLD:
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

            # 4. Laagste actieve prio uitschakelen bij import
            lowest_running = get_lowest_priority_running(devices_sorted)

            if lowest_running:
                st = get_device_state(lowest_running)
                at_minimum = st["brightness"] <= MIN_BRIGHTNESS and st["min_since"] is not None

                if at_minimum and virtual_power >= IMPORT_OFF_THRESHOLD:
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
                if virtual_power >= IMPORT_UNFREEZE_THRESHOLD:
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
threading.Thread(target=control_loop, daemon=True).start()
app.run(host="0.0.0.0", port=5001)