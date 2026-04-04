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

    return cfg

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ================= PID =================
PID_KP = 0.016
PID_KI = 0.0012
PID_KD = 0.0

device_pids = {}

enabled = True
device_states = {}

current_power = 0
current_brightness = 0

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

# ================= FLASK =================
app = Flask(__name__)
app.secret_key = "verander_dit_naar_iets_veiligs!"

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

        # Soepele check: als de HomeWizard API JSON geeft en power-achtige keys bevat
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

        # Alleen Gen3 0/1-10V dimmer toelaten
        if model not in ("S3DM-0010WW", "0010WW"):
            return None

        return {
            "type": "shelly",
            "name": data.get("name") or f"Shelly Dimmer Gen3",
            "ip": ip,
            "model": model,
            "gen": data.get("gen", 3)
        }

    except Exception:
        pass

    return None

    # Fallback voor Gen2 / Plus / Pro
    try:
        r = requests.get(f"http://{ip}/rpc/Shelly.GetDeviceInfo", timeout=1.2)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return {
                    "type": "shelly",
                    "name": data.get("name") or data.get("app") or f"Shelly {ip}",
                    "ip": ip,
                    "model": data.get("model", ""),
                    "gen": data.get("gen", "")
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

    # Dubbele entries eruit halen
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
@app.route("/login", methods=["GET","POST"])
def login():
    cfg = load_config()
    username_cfg = cfg.get("username","admin")
    password_cfg = cfg.get("password","admin123")
    if request.method=="POST":
        if request.form.get("username")==username_cfg and request.form.get("password")==password_cfg:
            session["logged_in"]=True
            return redirect("/")
        else:
            return render_template("login.html", error="Ongeldige login")
    return render_template("login.html")

@app.route("/", methods=["GET","POST"])
def wizard():
    if not require_login():
        return redirect("/login")
    cfg = load_config()
    if cfg.get("p1_ip") and cfg.get("shelly_devices"):
        return redirect("/dashboard")

    if request.method=="POST":
        cfg["p1_ip"] = request.form["p1ip"]

        devices=[]
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
                    "power_meter": pm,
                    "power_ip": pip.strip()
                })

        cfg["shelly_devices"] = devices
        save_config(cfg)
        init_device_states(devices)
        init_device_pids(devices)
        return redirect("/dashboard")

    return render_template("wizard.html", config=cfg)

@app.route("/wizard_forced", methods=["GET","POST"])
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
        return jsonify({"error":"unauthorized"}),401

    cfg = load_config()
    devices=[]

    for d in cfg.get("shelly_devices",[]):
        s=device_states.get(d["ip"],{})
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

    return jsonify(power=current_power, brightness=current_brightness, enabled=enabled, devices=devices)

@app.route("/toggle_pid")
def toggle_pid():
    global enabled
    if not require_login():
        return jsonify(success=False),401
    enabled=not enabled
    return jsonify(success=True)

@app.route("/toggle_shelly/<path:ip>")
def toggle_shelly(ip):
    if not require_login():
        return jsonify(success=False),401

    if ip in device_states:
        device_states[ip]["on"]=not device_states[ip]["on"]
        device_states[ip]["manual_override"]=True
        device_states[ip]["brightness"]=100 if device_states[ip]["on"] else 0
        set_shelly(device_states[ip]["brightness"],device_states[ip]["on"],ip)
        return jsonify(success=True, on=device_states[ip]["on"])

    return jsonify(success=False),404

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
            json={"id":0,"on":on,"brightness":round(brightness)},
            timeout=2
        )
    except Exception as e:
        print(f"Shelly error ({ip}):",e)

def check_shelly_online(ip):
    try:
        r=requests.get(f"http://{ip}/rpc/Shelly.GetStatus", timeout=2)
        return r.status_code==200
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

def init_device_states(devices):
    global device_states
    for d in devices:
        ip=d["ip"]
        if ip not in device_states:
            device_states[ip]={
                "on":False,
                "brightness":0,
                "online":False,
                "manual_override":False,
                "freeze":False,
                "started":False,
                "saturated_since":None,
                "min_since":None,
                "last_active_time":time.time(),
                "power":0
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

            if not p1_ip or not devices:
                time.sleep(2)
                continue

            init_device_states(devices)
            init_device_pids(devices)

            now = time.time()

            # ONLINE CHECK
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

                    # freeze logica
                    if b >= FREEZE_AT and not is_last_possible_priority(devices_sorted, d):
                        if st["saturated_since"] is None:
                            st["saturated_since"] = now
                        elif (now - st["saturated_since"]) >= FREEZE_CONFIRM:
                            st["freeze"] = True
                            st["saturated_since"] = None
                    else:
                        st["saturated_since"] = None

                    # minimum logica
                    if b <= MIN_BRIGHTNESS:
                        if st["min_since"] is None:
                            st["min_since"] = now
                    else:
                        st["min_since"] = None

                else:
                    # veiligheid: actieve niet-frozen devices buiten de regelende prio
                    # hun huidige stand gewoon vasthouden
                    if st["brightness"] < MIN_BRIGHTNESS:
                        st["brightness"] = MIN_BRIGHTNESS
                    st["on"] = True
                    set_shelly(st["brightness"], True, ip)

            current_brightness = active_brightness

            # 4. Laagste actieve prio uitschakelen als:
            #    - hij op minimum staat
            #    - EN 2 minuten >= 300W import
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
threading.Thread(target=control_loop, daemon=True).start()
app.run(host="0.0.0.0", port=5001)
