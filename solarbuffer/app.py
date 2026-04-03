from simple_pid import PID
import requests
import time
import json
import os
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
pid = PID(0.016, 0.0008, 0.0, setpoint=0)
pid.output_limits = (0, 100)

enabled = True
device_states = {}

current_power = 0
current_brightness = 0

# ================= FLASK =================
app = Flask(__name__)
app.secret_key = "verander_dit_naar_iets_veiligs!"

def require_login():
    return session.get("logged_in", False)

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

        # ✅ Gebruik alle lijsten bij het opslaan
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
        power_meters = request.form.getlist("power_meter[]")  # NIEUW
        power_ips = request.form.getlist("power_ip[]")        # NIEUW

        # Zorg dat alle velden worden meegenomen
        for name, ip, prio, pm, pip in zip(names, ips, priorities, power_meters, power_ips):
            if name.strip() and ip.strip():
                devices.append({
                    "name": name.strip(),
                    "ip": ip.strip(),
                    "priority": int(prio),
                    "power_meter": pm.strip() if pm else "",     # optioneel
                    "power_ip": pip.strip() if pip else ""       # optioneel
                })

        cfg["shelly_devices"] = devices
        save_config(cfg)
        init_device_states(devices)

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

# ================= SHELLY =================
def set_shelly(brightness, on, ip):
    try:
        requests.post(f"http://{ip}/rpc/Light.Set",
                      json={"id":0,"on":on,"brightness":round(brightness)},
                      timeout=2)
    except Exception as e:
        print(f"Shelly error ({ip}):",e)

def check_shelly_online(ip):
    try:
        r=requests.get(f"http://{ip}/rpc/Shelly.GetStatus", timeout=2)
        return r.status_code==200
    except:
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
                "last_active_time":time.time(),
                "power": 0
            }

# ================= CONTROL LOOP =================
def control_loop():
    global current_power, current_brightness

    online_check_interval=10
    last_online_check={}
    low_power_start={}
    high_power_start={}

    while True:
        try:
            cfg=load_config()
            p1_ip=cfg.get("p1_ip")
            devices=cfg.get("shelly_devices",[])

            if not p1_ip or not devices:
                time.sleep(2)
                continue

            init_device_states(devices)
            now=time.time()

            # ONLINE CHECK
            for d in devices:
                ip=d["ip"]
                if now - last_online_check.get(ip,0) > online_check_interval:
                    device_states[ip]["online"]=check_shelly_online(ip)
                    last_online_check[ip]=now

            # DEVICE POWER
            for d in devices:
                ip=d["ip"]
                state=device_states[ip]
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
            data=requests.get(f"http://{p1_ip}/api/v1/data", timeout=2).json()
            measured_power=data.get("active_power_w",0)
            current_power=measured_power
            pid_power=0 if -5<=measured_power<=45 else measured_power

            # PRIORITEIT
            devices_sorted=sorted(devices,key=lambda x:x["priority"])
            active_brightness=0

            for idx,d in enumerate(devices_sorted):
                ip=d["ip"]
                prio=d["priority"]
                state=device_states[ip]

                # hogere prio check
                if prio>1:
                    higher_frozen=True
                    for h in devices_sorted[:idx]:
                        if not device_states[h["ip"]]["freeze"]:
                            higher_frozen=False
                    if not higher_frozen:
                        continue

                # start delay
                if prio>1:
                    if measured_power>-50:
                        continue
                    if ip not in low_power_start:
                        low_power_start[ip]=now
                    elif now - low_power_start[ip]<30:
                        continue

                # PID
                if not state["freeze"] and state["on"]:
                    b=pid(pid_power)
                    b=max(0,min(100,b))
                    state["brightness"]=b
                    set_shelly(b,b>0,ip)
                    active_brightness=b
                    if prio==1 and b>=95:
                        state["freeze"]=True

                current_brightness=active_brightness

            # force off
            for d in reversed(devices_sorted):
                ip=d["ip"]
                state=device_states[ip]
                if state["on"] and state["brightness"]<=5:
                    if ip not in high_power_start:
                        high_power_start[ip]=now
                    elif now - high_power_start[ip]>=120 and measured_power>200:
                        state["on"]=False
                        state["freeze"]=True
                        set_shelly(0,False,ip)

            time.sleep(1)

        except Exception as e:
            print("Fout control_loop:",e)
            time.sleep(2)

# ================= START =================
threading.Thread(target=control_loop, daemon=True).start()
app.run(host="0.0.0.0", port=5001)