from flask import Flask, request, render_template_string
import subprocess
import threading
import time

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WiFi Setup - SolarBuffer</title>

<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap');

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'Inter', sans-serif;
    background: hsl(30, 25%, 97%);
    color: hsl(220, 20%, 14%);
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
}

.container {
    width: 100%;
    max-width: 420px;
    background: white;
    border: 1px solid hsl(30, 15%, 88%);
    border-radius: 0.75rem;
    box-shadow: 0 10px 40px -10px hsla(32, 95%, 52%, 0.15);
    padding: 2rem;
}

.header {
    text-align: center;
    margin-bottom: 2rem;
}

.header .icon {
    font-size: 2rem;
    color: hsl(32, 95%, 52%);
}

.header h1 {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 700;
    font-size: 1.6rem;
    margin-top: 0.25rem;
}

.header h1 .solar {
    background: linear-gradient(135deg, hsl(32, 95%, 52%), hsl(40, 100%, 60%));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.header p {
    color: hsl(220, 10%, 46%);
    font-size: 0.85rem;
    margin-top: 0.25rem;
}

form {
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
}

form div {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
}

label {
    font-weight: 600;
    font-size: 0.95rem;
}

input {
    width: 100%;
    padding: 0.65rem 0.75rem;
    border: 1px solid hsl(30, 15%, 88%);
    border-radius: 0.5rem;
    font-size: 1rem;
    font-family: 'Inter', sans-serif;
    transition: all 0.2s ease;
}

input:focus {
    border-color: hsl(32, 95%, 52%);
    box-shadow: 0 0 0 3px hsla(32, 95%, 52%, 0.15);
    outline: none;
}

button {
    width: 100%;
    background: hsl(32, 95%, 52%);
    color: white;
    font-weight: 600;
    font-size: 1rem;
    padding: 0.75rem;
    border: none;
    border-radius: 0.5rem;
    cursor: pointer;
    transition: all 0.2s ease;
}

button:hover {
    background: hsl(32, 85%, 45%);
    transform: translateY(-1px);
}

button:active {
    transform: translateY(0);
}

.message {
    text-align: center;
    font-size: 0.9rem;
    min-height: 1.2rem;
    white-space: pre-wrap;
    word-break: break-word;
}

.success {
    color: hsl(140, 60%, 40%);
}

.error {
    color: hsl(0, 75%, 60%);
}
</style>
</head>

<body>

<div class="container">
    <div class="header">
        <div class="icon">📡</div>
        <h1><span class="solar">Solar</span>Buffer</h1>
        <p>Configureer uw WiFi netwerk</p>
    </div>

    <div class="message {{ status_class }}">
        {{ message or "" }}
    </div>

    <form method="POST">
        <div>
            <label>WiFi naam (SSID)</label>
            <input name="ssid" required>
        </div>

        <div>
            <label>WiFi wachtwoord</label>
            <input name="password" type="password">
        </div>

        <button type="submit">Verbinden met netwerk</button>
    </form>
</div>

</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SolarBuffer</title>

<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap');

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'Inter', sans-serif;
    background: hsl(30, 25%, 97%);
    color: hsl(220, 20%, 14%);
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
}

.container {
    width: 100%;
    max-width: 420px;
    background: white;
    border: 1px solid hsl(30, 15%, 88%);
    border-radius: 0.75rem;
    box-shadow: 0 10px 40px -10px hsla(32, 95%, 52%, 0.15);
    padding: 2rem;
    text-align: center;
}

.icon {
    font-size: 2rem;
    color: hsl(32, 95%, 52%);
    margin-bottom: 0.75rem;
}

h1 {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 700;
    font-size: 1.6rem;
    margin-bottom: 0.5rem;
}

h1 .solar {
    background: linear-gradient(135deg, hsl(32, 95%, 52%), hsl(40, 100%, 60%));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

h3 {
    margin-top: 1rem;
    margin-bottom: 0.5rem;
    color: hsl(140, 60%, 40%);
}

p {
    color: hsl(220, 10%, 46%);
    font-size: 0.95rem;
}
</style>
</head>

<body>
<div class="container">
    <div class="icon">✅</div>
    <h1><span class="solar">Solar</span>Buffer</h1>
    <h3>WiFi opgeslagen</h3>
    <p>SolarBuffer herstart over enkele seconden...</p>
</div>
</body>
</html>
"""

def delayed_reboot(delay=3):
    def _reboot():
        time.sleep(delay)
        subprocess.Popen(["systemctl", "reboot"])
    threading.Thread(target=_reboot, daemon=True).start()

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        ssid = request.form["ssid"].strip()
        password = request.form["password"]

        try:
            subprocess.run(
                ["nmcli", "connection", "delete", "customer-wifi"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            subprocess.run(
                [
                    "nmcli", "connection", "add",
                    "type", "wifi",
                    "ifname", "wlan0",
                    "con-name", "customer-wifi",
                    "ssid", ssid
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "wifi-sec.key-mgmt",
                    "wpa-psk"
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "wifi-sec.psk",
                    password
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "connection.autoconnect",
                    "yes"
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "connection.autoconnect-priority",
                    "100"
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "connection.autoconnect-retries",
                    "0"
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "PI-SETUP",
                    "connection.autoconnect",
                    "no"
                ],
                check=True
            )

            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "PI-SETUP",
                    "connection.autoconnect-priority",
                    "-100"
                ],
                check=True
            )

            subprocess.run(
                ["nmcli", "connection", "down", "PI-SETUP"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            result = subprocess.run(
                ["nmcli", "connection", "up", "customer-wifi"],
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                delayed_reboot(3)
                return SUCCESS_HTML

            return render_template_string(
                HTML,
                message=f"WiFi opgeslagen, maar verbinden mislukte:\n{result.stderr}",
                status_class="error"
            )

        except Exception as e:
            return render_template_string(
                HTML,
                message=f"Fout:\n{e}",
                status_class="error"
            )

    return render_template_string(HTML, message="", status_class="")

app.run(host="0.0.0.0", port=80)
