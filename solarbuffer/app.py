from simple_pid import PID
import requests
import time
import json
import os
from flask import Flask, render_template, jsonify, request, redirect
import threading

# ================= CONFIG =================
CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"p1_ip": "", "shelly_ip": ""}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

# ================= PID =================
pid = PID(0.02, 0.002, 0.0, setpoint=0)
pid.output_limits = (0, 100)

enabled = True
shelly_on = True

current_power = 0
current_brightness = 0
last_brightness = 0  # ⚡ Houdt de laatste waarde vast

# ================= FLASK =================
app = Flask(__name__)

# ================= ROUTES =================
@app.route("/", methods=["GET", "POST"])
def wizard():
    config_data = load_config()
    # Als configuratie compleet is → dashboard
    if config_data.get("p1_ip") and config_data.get("shelly_ip"):
        return redirect("/dashboard")

    if request.method == "POST":
        config_data["p1_ip"] = request.form["p1ip"]
        config_data["shelly_ip"] = request.form["shellyip"]
        save_config(config_data)
        return redirect("/dashboard")

    return render_template("wizard.html", config=config_data)

@app.route("/wizard_forced")
def wizard_forced():
    return render_template("wizard.html")

@app.route("/dashboard")
def dashboard():
    config_data = load_config()
    if not config_data.get("p1_ip") or not config_data.get("shelly_ip"):
        return redirect("/")
    return render_template("dashboard.html", config=config_data)

@app.route("/status_json")
def status_json():
    return jsonify(
        power=current_power,
        brightness=current_brightness,
        enabled=enabled,
        shelly_on=shelly_on
    )

@app.route("/toggle_pid")
def toggle_pid():
    global enabled
    enabled = not enabled
    return jsonify(success=True)

@app.route("/toggle_shelly")
def toggle_shelly():
    global shelly_on
    shelly_on = not shelly_on
    return jsonify(success=True)

# ================= SHELLY =================
def set_shelly(brightness, on, shelly_ip):
    try:
        requests.post(
            f"http://{shelly_ip}/rpc/Light.Set",
            json={"id": 0, "on": on, "brightness": round(brightness)},
            timeout=2
        )
    except Exception as e:
        print("Shelly error:", e)

# ================= LOOP =================
def control_loop():
    global current_power, current_brightness, last_brightness

    while True:
        try:
            config_data = load_config()
            p1_ip = config_data.get("p1_ip")
            shelly_ip = config_data.get("shelly_ip")

            if not p1_ip or not shelly_ip:
                time.sleep(2)
                continue

            data = requests.get(f"http://{p1_ip}/api/v1/data", timeout=2).json()
            measured_power = data.get("active_power_w", 0)

            if -5 <= measured_power <= 45:
                pid_power = 0
            else:
                pid_power = measured_power
            current_power = measured_power

            # PID berekening of laatste waarde vasthouden
            if enabled and shelly_on:
                brightness = pid(pid_power)
                brightness = max(0, min(100, brightness))
                last_brightness = brightness
            else:
                brightness = last_brightness

            # Shelly aansturen
            if shelly_on:
                set_shelly(brightness, brightness > 0, shelly_ip)
            else:
                set_shelly(last_brightness, False, shelly_ip)  # waarde blijft, maar uit

            current_brightness = brightness

            print(f"{current_power}W → {brightness:.1f}%")
            time.sleep(1)

        except Exception as e:
            print("Loop error:", e)
            time.sleep(2)

# ================= START =================
threading.Thread(target=control_loop, daemon=True).start()
app.run(host="0.0.0.0", port=5001)