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
    max-width: 400px;
}

/* Header */
.header {
    text-align: center;
    margin-bottom: 1.75rem;
}
.header-icon {
    width: 56px; height: 56px;
    background: linear-gradient(135deg, hsl(32, 95%, 52%), hsl(40, 100%, 60%));
    border-radius: 1rem;
    display: flex; align-items: center; justify-content: center;
    margin: 0 auto 0.75rem;
    box-shadow: 0 6px 20px -4px hsla(32, 95%, 52%, 0.4);
}
.header-icon i { font-size: 1.6rem; color: white; }
.header h1 {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 700;
    font-size: 1.55rem;
}
.header h1 .solar {
    background: linear-gradient(135deg, hsl(32, 95%, 52%), hsl(40, 100%, 60%));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.header p { color: hsl(220, 10%, 50%); font-size: 0.83rem; margin-top: 0.2rem; }

/* Card */
.card {
    background: white;
    border: 1px solid hsl(30, 15%, 88%);
    border-radius: 1rem;
    box-shadow: 0 8px 32px -8px hsla(32, 95%, 52%, 0.12);
    overflow: hidden;
    margin-bottom: 0.75rem;
}

/* Scan header */
.scan-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.9rem 1rem 0.6rem;
}
.scan-label {
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 600;
    font-size: 0.88rem;
    color: hsl(220, 15%, 35%);
    letter-spacing: 0.02em;
    text-transform: uppercase;
}
.btn-refresh {
    display: flex; align-items: center; gap: 0.3rem;
    background: hsl(30, 20%, 96%);
    color: hsl(220, 15%, 40%);
    font-weight: 600;
    font-size: 0.75rem;
    padding: 0.28rem 0.65rem;
    border: 1px solid hsl(30, 15%, 86%);
    border-radius: 999px;
    cursor: pointer;
    transition: all 0.15s;
    font-family: 'Inter', sans-serif;
}
.btn-refresh:hover { background: hsl(32, 100%, 95%); border-color: hsl(32, 80%, 70%); color: hsl(32, 90%, 40%); }
.btn-refresh:disabled { opacity: 0.5; cursor: default; }

/* Network list */
.network-list { display: flex; flex-direction: column; }

.network-entry { border-top: 1px solid hsl(30, 15%, 92%); overflow: hidden; }

.network-header {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    padding: 0.7rem 1rem;
    cursor: pointer;
    transition: background 0.15s;
    user-select: none;
}
.network-header:hover { background: hsl(30, 30%, 98%); }
.network-entry.open .network-header { background: hsl(32, 100%, 97%); }

.net-signal { font-size: 1.1rem; color: hsl(220, 10%, 58%); flex-shrink: 0; }
.network-entry.open .net-signal { color: hsl(32, 90%, 50%); }

.net-name {
    flex: 1;
    font-size: 0.92rem;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.network-entry.open .net-name { font-weight: 600; color: hsl(32, 80%, 35%); }

.net-icons { display: flex; align-items: center; gap: 0.4rem; flex-shrink: 0; color: hsl(220, 10%, 62%); font-size: 0.95rem; }
.network-entry.open .net-icons { color: hsl(32, 80%, 55%); }

.net-chevron { font-size: 1rem; color: hsl(220, 10%, 68%); transition: transform 0.2s; }
.network-entry.open .net-chevron { transform: rotate(180deg); color: hsl(32, 80%, 55%); }

/* Expand area */
.network-body {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.28s ease;
}
.network-entry.open .network-body { max-height: 160px; }

.network-body-inner {
    padding: 0 1rem 0.9rem;
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
}

.pw-row { position: relative; }
.pw-row input {
    width: 100%;
    padding: 0.6rem 2.6rem 0.6rem 0.75rem;
    border: 1.5px solid hsl(30, 15%, 86%);
    border-radius: 0.55rem;
    font-size: 0.92rem;
    font-family: 'Inter', sans-serif;
    transition: border-color 0.15s, box-shadow 0.15s;
    background: hsl(30, 20%, 98%);
}
.pw-row input:focus {
    outline: none;
    border-color: hsl(32, 90%, 55%);
    box-shadow: 0 0 0 3px hsla(32, 95%, 52%, 0.13);
    background: white;
}
.pw-eye {
    position: absolute;
    top: 50%; right: 0.75rem;
    transform: translateY(-50%);
    cursor: pointer;
    color: hsl(220, 10%, 60%);
    font-size: 1.1rem;
}
.pw-eye:hover { color: hsl(220, 15%, 35%); }

.btn-connect {
    width: 100%;
    background: linear-gradient(135deg, hsl(32, 95%, 52%), hsl(38, 98%, 56%));
    color: white;
    font-family: 'Space Grotesk', sans-serif;
    font-weight: 700;
    font-size: 0.88rem;
    padding: 0.6rem;
    border: none;
    border-radius: 0.55rem;
    cursor: pointer;
    letter-spacing: 0.01em;
    box-shadow: 0 3px 12px -3px hsla(32, 95%, 52%, 0.45);
    transition: all 0.15s;
}
.btn-connect:hover { filter: brightness(1.06); transform: translateY(-1px); }
.btn-connect:active { transform: translateY(0); filter: brightness(0.97); }

/* Placeholders */
.list-placeholder {
    padding: 1.1rem 1rem;
    text-align: center;
    font-size: 0.82rem;
    color: hsl(220, 10%, 58%);
    border-top: 1px solid hsl(30, 15%, 92%);
}
.list-error {
    padding: 0.9rem 1rem;
    text-align: center;
    font-size: 0.82rem;
    color: hsl(0, 60%, 50%);
    border-top: 1px solid hsl(30, 15%, 92%);
}

/* Manual button */
.btn-manual {
    width: 100%;
    background: white;
    color: hsl(220, 15%, 38%);
    font-family: 'Inter', sans-serif;
    font-weight: 600;
    font-size: 0.88rem;
    padding: 0.75rem;
    border: 1.5px dashed hsl(30, 15%, 82%);
    border-radius: 1rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0.5rem;
    transition: all 0.15s;
}
.btn-manual:hover { border-color: hsl(32, 80%, 65%); color: hsl(32, 85%, 40%); background: hsl(32, 100%, 98%); }
.btn-manual.active { border-style: solid; border-color: hsl(32, 90%, 55%); color: hsl(32, 85%, 40%); background: hsl(32, 100%, 97%); }

/* Manual form card */
.manual-card {
    background: white;
    border: 1px solid hsl(30, 15%, 88%);
    border-radius: 1rem;
    box-shadow: 0 8px 32px -8px hsla(32, 95%, 52%, 0.12);
    overflow: hidden;
    max-height: 0;
    transition: max-height 0.3s ease;
    margin-bottom: 0.75rem;
}
.manual-card.open { max-height: 400px; }

.manual-inner {
    padding: 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.65rem;
}
.manual-inner input {
    width: 100%;
    padding: 0.6rem 0.75rem;
    border: 1.5px solid hsl(30, 15%, 86%);
    border-radius: 0.55rem;
    font-size: 0.92rem;
    font-family: 'Inter', sans-serif;
    transition: border-color 0.15s, box-shadow 0.15s;
    background: hsl(30, 20%, 98%);
}
.manual-inner input:focus {
    outline: none;
    border-color: hsl(32, 90%, 55%);
    box-shadow: 0 0 0 3px hsla(32, 95%, 52%, 0.13);
    background: white;
}

/* Spinner */
.spin { display: inline-block; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="container">

    <div class="header">
        <h1><span class="solar">Solar</span>Buffer</h1>
        <p>Verbind met uw WiFi netwerk</p>
    </div>

    <!-- Netwerken kaart -->
    <div class="card">
        <div class="scan-row">
            <span class="scan-label">Netwerken</span>
            <button type="button" class="btn-refresh" id="refreshBtn" onclick="startScan()">
                <i class="mdi mdi-refresh" id="refreshIcon"></i>
                <span id="refreshText">Ververs</span>
            </button>
        </div>
        <div class="network-list" id="networkList">
            <div class="list-placeholder">
                <span class="spin"><i class="mdi mdi-loading"></i></span>&nbsp; Netwerken zoeken…
            </div>
        </div>
    </div>

    <!-- Handmatige invoer knop -->
    <button type="button" class="btn-manual" id="manualBtn" onclick="toggleManual()">
        <i class="mdi mdi-pencil-outline"></i>
        <span id="manualBtnText">Netwerk handmatig invoeren</span>
    </button>

    <!-- Handmatige invoer kaart -->
    <div class="manual-card" id="manualCard">
        <div class="manual-inner">
            <input type="text" id="manualSsid" placeholder="WiFi naam (SSID)" autocomplete="off">
            <div class="pw-row">
                <input type="password" id="manualPw" placeholder="Wachtwoord (leeg = open netwerk)">
                <i class="mdi mdi-eye-off pw-eye" onclick="toggleEye('manualPw', this)"></i>
            </div>
            <button type="button" class="btn-connect" onclick="connectManual()">
                <i class="mdi mdi-wifi-arrow-right"></i>&nbsp; Verbinden
            </button>
        </div>
    </div>

    <!-- Verborgen formulier voor submit -->
    <form id="wifiForm" method="POST" style="display:none;">
        <input type="hidden" id="fSsid" name="ssid">
        <input type="hidden" id="fPassword" name="password">
    </form>

</div>
<script>
let openEntry = null;
let manualOpen = false;

function signalIcon(s) {
    if (s >= 75) return 'mdi-wifi-strength-4';
    if (s >= 50) return 'mdi-wifi-strength-3';
    if (s >= 25) return 'mdi-wifi-strength-2';
    return 'mdi-wifi-strength-1';
}
function esc(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function toggleEye(inputId, icon) {
    const f = document.getElementById(inputId);
    if (f.type === 'password') { f.type = 'text'; icon.classList.replace('mdi-eye-off','mdi-eye'); }
    else { f.type = 'password'; icon.classList.replace('mdi-eye','mdi-eye-off'); }
}

function doConnect(ssid, password) {
    document.getElementById('fSsid').value = ssid;
    document.getElementById('fPassword').value = password;
    document.getElementById('wifiForm').submit();
}

function connectManual() {
    const ssid = document.getElementById('manualSsid').value.trim();
    if (!ssid) { document.getElementById('manualSsid').focus(); return; }
    doConnect(ssid, document.getElementById('manualPw').value);
}

function toggleManual() {
    manualOpen = !manualOpen;
    document.getElementById('manualCard').classList.toggle('open', manualOpen);
    document.getElementById('manualBtn').classList.toggle('active', manualOpen);
    document.getElementById('manualBtnText').textContent = manualOpen
        ? 'Handmatig invoeren verbergen'
        : 'Netwerk handmatig invoeren';
    if (manualOpen) {
        setTimeout(() => document.getElementById('manualSsid').focus(), 300);
    }
}

function toggleEntry(entry) {
    if (openEntry && openEntry !== entry) {
        openEntry.classList.remove('open');
        openEntry.querySelector('.pw-input').value = '';
    }
    const isOpen = entry.classList.toggle('open');
    openEntry = isOpen ? entry : null;
    if (isOpen) {
        setTimeout(() => entry.querySelector('.pw-input').focus(), 250);
    }
}

function startScan() {
    const btn = document.getElementById('refreshBtn');
    const icon = document.getElementById('refreshIcon');
    const txt = document.getElementById('refreshText');
    const list = document.getElementById('networkList');

    btn.disabled = true;
    icon.className = 'mdi mdi-loading spin';
    txt.textContent = 'Bezig…';
    list.innerHTML = '<div class="list-placeholder"><span class="spin"><i class="mdi mdi-loading"></i></span>&nbsp; Netwerken zoeken…</div>';
    openEntry = null;

    fetch('/scan')
        .then(r => r.json())
        .then(data => {
            btn.disabled = false;
            icon.className = 'mdi mdi-refresh';
            txt.textContent = 'Ververs';

            if (!data.networks || data.networks.length === 0) {
                list.innerHTML = '<div class="list-error"><i class="mdi mdi-wifi-off"></i>&nbsp; Geen netwerken gevonden.</div>';
                return;
            }

            list.innerHTML = '';
            data.networks.forEach(n => {
                const entry = document.createElement('div');
                entry.className = 'network-entry';
                const pwId = 'pw_' + Math.random().toString(36).slice(2);
                const eyeId = 'eye_' + Math.random().toString(36).slice(2);
                entry.innerHTML =
                    '<div class="network-header" onclick="toggleEntry(this.parentElement)">' +
                        '<i class="mdi ' + signalIcon(n.signal) + ' net-signal"></i>' +
                        '<span class="net-name">' + esc(n.ssid) + '</span>' +
                        '<span class="net-icons">' +
                            (n.secured ? '<i class="mdi mdi-lock"></i>' : '<i class="mdi mdi-lock-open-outline"></i>') +
                        '</span>' +
                        '<i class="mdi mdi-chevron-down net-chevron"></i>' +
                    '</div>' +
                    '<div class="network-body">' +
                        '<div class="network-body-inner">' +
                            '<div class="pw-row">' +
                                '<input class="pw-input" id="' + pwId + '" type="password" placeholder="' + (n.secured ? 'Wachtwoord' : 'Wachtwoord (optioneel)') + '">' +
                                '<i class="mdi mdi-eye-off pw-eye" id="' + eyeId + '" onclick="toggleEye(\'' + pwId + '\', document.getElementById(\'' + eyeId + '\'))"></i>' +
                            '</div>' +
                            '<button type="button" class="btn-connect" onclick="doConnect(\'' + esc(n.ssid).replace(/'/g,"\\'") + '\', document.getElementById(\'' + pwId + '\').value)">' +
                                '<i class="mdi mdi-wifi-arrow-right"></i>&nbsp; Verbinden met ' + esc(n.ssid) +
                            '</button>' +
                        '</div>' +
                    '</div>';
                list.appendChild(entry);
            });
        })
        .catch(() => {
            btn.disabled = false;
            icon.className = 'mdi mdi-refresh';
            txt.textContent = 'Ververs';
            list.innerHTML = '<div class="list-error"><i class="mdi mdi-alert-circle-outline"></i>&nbsp; Scan mislukt. Probeer opnieuw.</div>';
        });
}

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


def do_wifi_scan():
    # Rescan in achtergrond starten, niet wachten op resultaat
    subprocess.run(
        ["nmcli", "device", "wifi", "rescan"],
        capture_output=True, timeout=8
    )


@app.route("/scan")
def scan_wifi():
    try:
        # Eerst de gecachde lijst ophalen voor snelle response
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=10
        )
        networks = []
        seen = set()
        for line in result.stdout.splitlines():
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

        # Rescan op de achtergrond starten voor de volgende keer
        threading.Thread(target=do_wifi_scan, daemon=True).start()

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


app.run(host="0.0.0.0", port=80, threaded=True)
