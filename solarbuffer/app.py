from simple_pid import PID
import requests
import time
import json
import os
from flask import Flask, render_template, jsonify, request, redirect, session
import threading

# ================= CONFIG =================
CONFIG_FILE = "config.json"

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
        json.dump(data, f)

# ================= PID =================
pid = PID(0.016, 0.0008, 0.0, setpoint=0)
pid.output_limits = (34, 100)

enabled = True
device_states = {}  # {shelly_ip: {"on": True, "brightness": 0, "online": False}}

current_power = 0
current_brightness = 0
last_brightness = 0

# ================= OVERRIDE LOGICA =================
high_power_start = None
low_power_start = None
force_off = False
force_on = False

# ================= FLASK =================
app = Flask(__name__)
app.secret_key = "verander_dit_naar_iets_veiligs!"

# ================= AUTH HELPER =================
def require_login():
    return session.get("logged_in", False)

# ================= ROUTES =================
@app.route("/login", methods=["GET", "POST"])
def login():
    config_data = load_config()
    username_cfg = config_data.get("username", "admin")
    password_cfg = config_data.get("password", "admin123")

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username == username_cfg and password == password_cfg:
            session["logged_in"] = True
            return redirect("/")
        else:
            return render_template("login.html", error="Ongeldige login")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/", methods=["GET", "POST"])
def wizard():
    if not require_login():
        return redirect("/login")

    config_data = load_config()

    if config_data.get("p1_ip") and config_data.get("shelly_devices"):
        return redirect("/dashboard")

    if request.method == "POST":
        config_data["p1_ip"] = request.form["p1ip"]

        shelly_devices = []
        names = request.form.getlist("shelly_name[]")
        ips = request.form.getlist("shelly_ip[]")

        for name, ip in zip(names, ips):
            if name.strip() and ip.strip():
                shelly_devices.append({"name": name.strip(), "ip": ip.strip()})

        config_data["shelly_devices"] = shelly_devices
        save_config(config_data)
        init_device_states(shelly_devices)

        return redirect("/dashboard")

    return render_template("wizard.html", config=config_data)

@app.route("/wizard_forced")
def wizard_forced():
    if not require_login():
        return redirect("/login")
    config_data = load_config()
    return render_template("wizard.html", config=config_data)

@app.route("/dashboard")
def dashboard():
    if not require_login():
        return redirect("/login")

    config_data = load_config()
    if not config_data.get("p1_ip") or not config_data.get("shelly_devices"):
        return redirect("/")

    return render_template("dashboard.html", config=config_data)

@app.route("/status_json")
def status_json():
    global force_off, force_on  # ← vergeet dit niet!
    
    if not require_login():
        return jsonify({"error": "unauthorized"}), 401

    config_data = load_config()
    devices_status = []

    for device in config_data.get("shelly_devices", []):
        ip = device["ip"]
        state = device_states.get(ip, {"on": True, "brightness": 0, "online": False})

        # Force logica zichtbaar maken op web
        if force_off:
            display_on = False
        elif force_on:
            display_on = True
        else:
            display_on = state["on"]

        devices_status.append({
            "name": device["name"],
            "ip": ip,
            "on": display_on,
            "brightness": state["brightness"],
            "online": state.get("online", False)
        })

    return jsonify(
        power=current_power,
        brightness=current_brightness,
        enabled=enabled,
        devices=devices_status,
        force_off=force_off,
        force_on=force_on
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
        return jsonify(success=True, on=device_states[ip]["on"])

    return jsonify(success=False), 404

# ================= SHELLY =================
def set_shelly(brightness, on, shelly_ip):
    try:
        requests.post(
            f"http://{shelly_ip}/rpc/Light.Set",
            json={"id": 0, "on": on, "brightness": round(brightness)},
            timeout=2
        )
    except Exception as e:
        print(f"Shelly error ({shelly_ip}):", e)

def check_shelly_online(shelly_ip):
    try:
        r = requests.get(f"http://{shelly_ip}/rpc/Shelly.GetStatus", timeout=2)
        return r.status_code == 200
    except Exception:
        return False

def init_device_states(devices):
    global device_states
    for device in devices:
        ip = device["ip"]
        if ip not in device_states:
            device_states[ip] = {"on": True, "brightness": 0, "online": False}

# ================= LOOP =================
def control_loop():
    global current_power, current_brightness, last_brightness
    global high_power_start, low_power_start, force_off, force_on

    online_check_interval = 10  # seconden tussen online-checks
    last_online_check = {}

    while True:
        try:
            config_data = load_config()
            p1_ip = config_data.get("p1_ip")
            shelly_devices = config_data.get("shelly_devices", [])

            if not p1_ip or not shelly_devices:
                time.sleep(2)
                continue

            init_device_states(shelly_devices)
            now = time.time()

            # Online-check elke 10 seconden per apparaat
            for device in shelly_devices:
                ip = device["ip"]
                if now - last_online_check.get(ip, 0) >= online_check_interval:
                    device_states[ip]["online"] = check_shelly_online(ip)
                    last_online_check[ip] = now

            # P1 uitlezen
            data = requests.get(f"http://{p1_ip}/api/v1/data", timeout=2).json()
            measured_power = data.get("active_power_w", 0)
            current_power = measured_power

            if -5 <= measured_power <= 45:
                pid_power = 0
            else:
                pid_power = measured_power

            # ================= OVERRIDE LOGICA =================
            # Reset timers als buiten thresholds
            if measured_power < 300:
                high_power_start = None
            if measured_power > -50:
                low_power_start = None

            # UIT na 300W voor 2 min
            if measured_power >= 300:
                if high_power_start is None:
                    high_power_start = now
                elif now - high_power_start >= 120:
                    force_off = True
                    force_on = False

            # AAN bij -50W voor 30 sec
            if measured_power <= -50:
                if low_power_start is None:
                    low_power_start = now
                elif now - low_power_start >= 30:
                    force_on = True
                    force_off = False

            # PID berekening
            if enabled:
                brightness = pid(pid_power)
                brightness = max(0, min(100, brightness))
                last_brightness = brightness
            else:
                brightness = last_brightness

            current_brightness = brightness

            # Stuur naar Shelly apparaten
            for device in shelly_devices:
                ip = device["ip"]
                state = device_states.get(ip, {"on": True, "online": False})

                if state.get("online"):
                    if force_off:
                        set_shelly(0, False, ip)
                        device_states[ip]["brightness"] = 0
                    elif force_on:
                        set_shelly(100, True, ip)
                        device_states[ip]["brightness"] = 100
                    elif state["on"]:
                        set_shelly(brightness, brightness > 0, ip)
                        device_states[ip]["brightness"] = brightness
                    else:
                        set_shelly(0, False, ip)
                        device_states[ip]["brightness"] = 0
                else:
                    device_states[ip]["brightness"] = 0

            print(f"{current_power}W → {brightness:.1f}% ({len(shelly_devices)} devices)")
            time.sleep(1)

        except Exception as e:
            print("Loop error:", e)
            time.sleep(2)

# ================= START =================
threading.Thread(target=control_loop, daemon=True).start()
app.run(host="0.0.0.0", port=5001)