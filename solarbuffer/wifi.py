from flask import Flask, request, render_template_string, jsonify
import subprocess
import threading
import time
import re

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WiFi Setup - SolarBuffer</title>

<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/css/materialdesignicons.min.css" />

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

.header .icon { font-size: 2rem; color: hsl(32, 95%, 52%); }

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

.header p { color: hsl(220, 10%, 46%); font-size: 0.85rem; margin-top: 0.25rem; }

form { display: flex; flex-direction: column; gap: 1.25rem; }
form > div { display: flex; flex-direction: column; gap: 0.35rem; }

label { font-weight: 600; font-size: 0.95rem; }

input[type="text"], input[type="password"] {
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

button[type="submit"] {
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

button[type="submit"]:hover { background: hsl(32, 85%, 45%); transform: translateY(-1px); }
button[type="submit"]:active { transform: translateY(0); }
button[type="submit"]:disabled { opacity: 0.45; cursor: default; transform: none; }

.password-wrapper { position: relative; }
.password-wrapper input { padding-right: 3rem; }

.toggle-password {
    position: absolute;
    top: 50%; right: 0.9rem;
    transform: translateY(-50%);
    cursor: pointer;
    color: #777;
    font-size: 1.2rem;
    line-height: 1;
    z-index: 2;
}
.toggle-password:hover { color: #333; }

/* Scan header row */
.scan-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.5rem;
}

.btn-refresh {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    background: white;
    color: hsl(220, 20%, 30%);
    font-weight: 600;
    font-size: 0.8rem;
    padding: 0.3rem 0.65rem;
    border: 1.5px solid hsl(30, 15%, 82%);
    border-radius: 999px;
    cursor: pointer;
    transition: all 0.15s ease;
    font-family: 'Inter', sans-serif;
}

.btn-refresh:hover {
    border-color: hsl(32, 85%, 60%);
    color: hsl(32, 90%, 40%);
    background: hsl(32, 100%, 97%);
}

.btn-refresh:disabled { opacity: 0.5; cursor: default; }

.spin { display: inline-block; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Network list */
.network-list {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    max-height: 220px;
    overflow-y: auto;
}

.network-placeholder {
    font-size: 0.82rem;
    color: hsl(220, 10%, 56%);
    text-align: center;
    padding: 0.75rem 0;
}

.scan-error {
    font-size: 0.8rem;
    color: hsl(0, 65%, 45%);
    text-align: center;
    padding: 0.5rem 0;
}

.network-item {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.5rem 0.7rem;
    border: 1.5px solid hsl(30, 15%, 88%);
    border-radius: 0.5rem;
    cursor: pointer;
    transition: all 0.15s ease;
    background: white;
    flex-shrink: 0;
}

.network-item:hover { border-color: hsl(32, 85%, 60%); background: hsl(32, 100%, 97%); }

.network-item.selected {
    border-color: hsl(32, 95%, 52%);
    background: hsl(32, 100%, 96%);
    box-shadow: 0 0 0 2px hsla(32, 95%, 52%, 0.18);
}

.network-ssid {
    flex: 1;
    font-size: 0.9rem;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.network-meta {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    flex-shrink: 0;
    color: hsl(220, 10%, 55%);
    font-size: 1.05rem;
}

.network-item.selected .network-meta { color: hsl(32, 90%, 45%); }

/* Divider */
.or-divider {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    color: hsl(220, 10%, 60%);
    font-size: 0.78rem;
    font-weight: 500;
    margin: 0.25rem 0;
}
.or-divider::before, .or-divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: hsl(30, 15%, 88%);
}

.manual-hint {
    font-size: 0.78rem;
    color: hsl(220, 10%, 52%);
    margin-bottom: 0.1rem;
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

    <form method="POST" id="wifiForm">

        <div>
            <div class="scan-header">
                <label>Gevonden netwerken</label>
                <button type="button" class="btn-refresh" id="refreshBtn" onclick="startScan()">
                    <i class="mdi mdi-refresh" id="refreshIcon"></i>
                    <span id="refreshText">Ververs</span>
                </button>
            </div>

            <div class="network-list" id="networkList">
                <div class="network-placeholder">
                    <span class="spin"><i class="mdi mdi-loading"></i></span> Netwerken zoeken…
                </div>
            </div>
        </div>

        <div class="or-divider">of voer handmatig in</div>

        <div>
            <div class="manual-hint">Netwerk staat er niet bij? Typ de naam hieronder.</div>
            <input type="text" id="ssidManual" placeholder="WiFi naam (SSID)" autocomplete="off" oninput="onManualInput()">
        </div>

        <input type="hidden" id="ssidHidden" name="ssid">

        <div>
            <label>WiFi wachtwoord</label>
            <div class="password-wrapper">
                <input id="wifi_password" name="password" type="password" autocomplete="current-password">
                <i class="mdi mdi-eye-off toggle-password" onclick="togglePassword('wifi_password', this)"></i>
            </div>
        </div>

        <button type="submit" id="submitBtn" disabled>Verbinden met netwerk</button>
    </form>
</div>

<script>
let selectedSsid = null;

function signalIcon(s) {
    if (s >= 75) return 'mdi-wifi-strength-4';
    if (s >= 50) return 'mdi-wifi-strength-3';
    if (s >= 25) return 'mdi-wifi-strength-2';
    return 'mdi-wifi-strength-1';
}

function escHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function updateSubmit() {
    const manual = document.getElementById('ssidManual').value.trim();
    document.getElementById('submitBtn').disabled = !selectedSsid && !manual;
}

function onManualInput() {
    // Deselect list item when user starts typing manually
    if (document.getElementById('ssidManual').value.trim()) {
        document.querySelectorAll('.network-item').forEach(i => i.classList.remove('selected'));
        selectedSsid = null;
    }
    updateSubmit();
}

function selectNetwork(ssid, el) {
    document.querySelectorAll('.network-item').forEach(i => i.classList.remove('selected'));
    el.classList.add('selected');
    selectedSsid = ssid;
    document.getElementById('ssidManual').value = '';
    updateSubmit();
    document.getElementById('wifi_password').focus();
}

function startScan() {
    const btn = document.getElementById('refreshBtn');
    const icon = document.getElementById('refreshIcon');
    const text = document.getElementById('refreshText');
    const list = document.getElementById('networkList');

    btn.disabled = true;
    icon.className = 'mdi mdi-loading spin';
    text.textContent = 'Bezig…';
    list.innerHTML = '<div class="network-placeholder"><span class="spin"><i class="mdi mdi-loading"></i></span> Netwerken zoeken…</div>';

    fetch('/scan')
        .then(r => r.json())
        .then(data => {
            btn.disabled = false;
            icon.className = 'mdi mdi-refresh';
            text.textContent = 'Ververs';

            if (!data.networks || data.networks.length === 0) {
                list.innerHTML = '<div class="scan-error">Geen netwerken gevonden.</div>';
                return;
            }

            list.innerHTML = '';
            data.networks.forEach(n => {
                const item = document.createElement('div');
                item.className = 'network-item' + (n.ssid === selectedSsid ? ' selected' : '');
                item.onclick = () => selectNetwork(n.ssid, item);
                item.innerHTML =
                    '<i class="mdi ' + signalIcon(n.signal) + '" style="font-size:1.15rem;color:hsl(220,10%,52%);flex-shrink:0"></i>' +
                    '<span class="network-ssid">' + escHtml(n.ssid) + '</span>' +
                    '<span class="network-meta">' +
                        (n.secured
                            ? '<i class="mdi mdi-lock" title="Beveiligd"></i>'
                            : '<i class="mdi mdi-lock-open-outline" title="Open netwerk"></i>') +
                    '</span>';
                list.appendChild(item);
            });
        })
        .catch(() => {
            btn.disabled = false;
            icon.className = 'mdi mdi-refresh';
            text.textContent = 'Ververs';
            list.innerHTML = '<div class="scan-error">Scan mislukt. Probeer opnieuw.</div>';
        });
}

function togglePassword(fieldId, icon) {
    const field = document.getElementById(fieldId);
    if (field.type === 'password') {
        field.type = 'text';
        icon.classList.replace('mdi-eye-off', 'mdi-eye');
    } else {
        field.type = 'password';
        icon.classList.replace('mdi-eye', 'mdi-eye-off');
    }
}

document.getElementById('wifiForm').addEventListener('submit', function(e) {
    const manual = document.getElementById('ssidManual').value.trim();
    const ssid = manual || selectedSsid;
    if (!ssid) { e.preventDefault(); return; }
    document.getElementById('ssidHidden').value = ssid;
});

startScan();
</script>

</body>
</html>
"""

PROCESSING_HTML = """
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
    color: hsl(32, 95%, 52%);
}

p {
    color: hsl(220, 10%, 46%);
    font-size: 0.95rem;
}
</style>
</head>

<body>
<div class="container">
    <div class="icon">⏳</div>
    <h1><span class="solar">Solar</span>Buffer</h1>
    <h3>WiFi wordt opgeslagen</h3>
    <p>De instellingen zijn ontvangen. SolarBuffer probeert nu verbinding te maken en start daarna opnieuw op.</p>
</div>
</body>
</html>
"""


def configure_wifi_and_reboot(ssid, password):
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

        if password:
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
        else:
            subprocess.run(
                [
                    "nmcli", "connection", "modify",
                    "customer-wifi",
                    "wifi-sec.key-mgmt",
                    ""
                ],
                check=False
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
            check=False
        )

        subprocess.run(
            [
                "nmcli", "connection", "modify",
                "PI-SETUP",
                "connection.autoconnect-priority",
                "-100"
            ],
            check=False
        )

        time.sleep(2)

        subprocess.run(
            ["nmcli", "connection", "up", "customer-wifi"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False
        )

        time.sleep(5)

        subprocess.Popen(["systemctl", "reboot"])

    except Exception:
        time.sleep(8)
        subprocess.Popen(["systemctl", "reboot"])


@app.route("/scan")
def scan_wifi():
    try:
        result = subprocess.run(
            ["nmcli", "--rescan", "yes", "-t", "-f", "SSID,SIGNAL,SECURITY",
             "device", "wifi", "list"],
            capture_output=True, text=True, timeout=20
        )
        networks = []
        seen = set()
        for line in result.stdout.splitlines():
            # nmcli terse mode escapes ':' in values as '\:' — split on unescaped colons
            parts = re.split(r'(?<!\\):', line, maxsplit=2)
            if not parts:
                continue
            ssid = parts[0].replace('\\:', ':').strip()
            signal = int(parts[1].strip()) if len(parts) > 1 and parts[1].strip().isdigit() else 0
            security = parts[2].strip() if len(parts) > 2 else ""
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            networks.append({
                "ssid": ssid,
                "signal": signal,
                "secured": bool(security and security not in ("--", "")),
            })
        networks.sort(key=lambda x: -x["signal"])
        return jsonify(networks=networks)
    except Exception:
        return jsonify(networks=[], error="Scan mislukt")


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        ssid = request.form.get("ssid", "").strip()
        password = request.form.get("password", "")

        if not ssid:
            return render_template_string(HTML)

        threading.Thread(
            target=configure_wifi_and_reboot,
            args=(ssid, password),
            daemon=True
        ).start()

        return render_template_string(PROCESSING_HTML)

    return render_template_string(HTML)


app.run(host="0.0.0.0", port=80)
