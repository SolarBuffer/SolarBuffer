from flask import Flask, request, render_template_string, jsonify, redirect
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

form > div {
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

button[type="submit"]:hover {
    background: hsl(32, 85%, 45%);
    transform: translateY(-1px);
}

button[type="submit"]:active {
    transform: translateY(0);
}

.message {
    text-align: center;
    font-size: 0.9rem;
    min-height: 1.2rem;
    white-space: pre-wrap;
    word-break: break-word;
    margin-bottom: 1rem;
}

.password-wrapper {
    position: relative;
}

.password-wrapper input {
    padding-right: 3rem;
}

.toggle-password {
    position: absolute;
    top: 50%;
    right: 0.9rem;
    transform: translateY(-50%);
    cursor: pointer;
    color: #777;
    font-size: 1.2rem;
    line-height: 1;
    z-index: 2;
}

.toggle-password:hover {
    color: #333;
}

.scan-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.4rem;
}

.refresh-btn {
    background: none;
    border: none;
    color: hsl(32, 95%, 52%);
    cursor: pointer;
    padding: 0;
    font-size: 0.82rem;
    font-weight: 500;
    font-family: 'Inter', sans-serif;
    display: flex;
    align-items: center;
    gap: 0.2rem;
}

.refresh-btn:hover {
    text-decoration: underline;
}

.network-list {
    border: 1px solid hsl(30, 15%, 88%);
    border-radius: 0.5rem;
    overflow: hidden;
    max-height: 190px;
    overflow-y: auto;
}

.network-item {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    padding: 0.55rem 0.75rem;
    cursor: pointer;
    transition: background 0.15s;
    border-bottom: 1px solid hsl(30, 15%, 93%);
    user-select: none;
}

.network-item:last-child {
    border-bottom: none;
}

.network-item:hover {
    background: hsla(32, 95%, 52%, 0.07);
}

.network-item.selected {
    background: hsla(32, 95%, 52%, 0.13);
}

.network-signal {
    color: hsl(32, 95%, 52%);
    font-size: 1.05rem;
    flex-shrink: 0;
}

.network-name {
    flex: 1;
    font-size: 0.9rem;
    font-weight: 500;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.network-lock {
    color: hsl(220, 10%, 62%);
    font-size: 0.95rem;
    flex-shrink: 0;
}

.scan-status {
    text-align: center;
    padding: 0.8rem;
    color: hsl(220, 10%, 55%);
    font-size: 0.85rem;
}

@keyframes spin {
    to { transform: rotate(360deg); }
}

.spinning {
    display: inline-block;
    animation: spin 0.9s linear infinite;
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

    <div class="message"></div>

    <form method="POST">
        <div>
            <div class="scan-header">
                <label>Beschikbare netwerken</label>
                <button type="button" class="refresh-btn" id="refreshBtn" onclick="scanNetworks(true)">
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle"><path d="M17.65,6.35C16.2,4.9 14.21,4 12,4A8,8 0 0,0 4,12A8,8 0 0,0 12,20C15.73,20 18.84,17.45 19.73,14H17.65C16.83,16.33 14.61,18 12,18A6,6 0 0,1 6,12A6,6 0 0,1 12,6C13.66,6 15.14,6.69 16.22,7.78L13,11H20V4L17.65,6.35Z"/></svg> Vernieuwen
                </button>
            </div>
            <div class="network-list" id="networkList">
                <div class="scan-status"><svg class="spinning" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle"><path d="M12,4V2A10,10 0 0,0 2,12H4A8,8 0 0,1 12,4Z"/></svg> Netwerken zoeken...</div>
            </div>
        </div>

        <div>
            <label>WiFi naam (SSID)</label>
            <input name="ssid" id="ssidInput" required placeholder="Selecteer hierboven of typ hier">
        </div>

        <div>
            <label>WiFi wachtwoord</label>
            <div class="password-wrapper">
                <input id="wifi_password" name="password" type="password">
                <span class="toggle-password" onclick="togglePassword('wifi_password', this)">
                    <svg id="eye_icon" width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M11.83,9L15,12.16C15,12.11 15,12.05 15,12A3,3 0 0,0 12,9C11.94,9 11.89,9 11.83,9M7.53,9.8L9.08,11.35C9.03,11.56 9,11.77 9,12A3,3 0 0,0 12,15C12.22,15 12.44,14.97 12.65,14.92L14.2,16.47C13.53,16.8 12.79,17 12,17A5,5 0 0,1 7,12C7,11.21 7.2,10.47 7.53,9.8M2,4.27L4.28,6.55L4.73,7C3.08,8.3 1.78,10 1,12C2.73,16.39 7,19.5 12,19.5C13.55,19.5 15.03,19.2 16.38,18.66L16.81,19.08L19.73,22L21,20.73L3.27,3M12,7A5,5 0 0,1 17,12C17,12.64 16.87,13.26 16.64,13.82L19.57,16.75C21.07,15.5 22.27,13.86 23,12C21.27,7.61 17,4.5 12,4.5C10.6,4.5 9.26,4.75 8,5.2L10.17,7.35C10.74,7.13 11.35,7 12,7Z"/></svg>
                </span>
            </div>
        </div>

        <button type="submit">Verbinden met netwerk</button>
    </form>
</div>

<script>
const SVG_EYE     = '<path d="M12,9A3,3 0 0,0 9,12A3,3 0 0,0 12,15A3,3 0 0,0 15,12A3,3 0 0,0 12,9M12,17A5,5 0 0,1 7,12A5,5 0 0,1 12,7A5,5 0 0,1 17,12A5,5 0 0,1 12,17M12,4.5C7,4.5 2.73,7.61 1,12C2.73,16.39 7,19.5 12,19.5C17,19.5 21.27,16.39 23,12C21.27,7.61 17,4.5 12,4.5Z"/>';
const SVG_EYE_OFF = '<path d="M11.83,9L15,12.16C15,12.11 15,12.05 15,12A3,3 0 0,0 12,9C11.94,9 11.89,9 11.83,9M7.53,9.8L9.08,11.35C9.03,11.56 9,11.77 9,12A3,3 0 0,0 12,15C12.22,15 12.44,14.97 12.65,14.92L14.2,16.47C13.53,16.8 12.79,17 12,17A5,5 0 0,1 7,12C7,11.21 7.2,10.47 7.53,9.8M2,4.27L4.28,6.55L4.73,7C3.08,8.3 1.78,10 1,12C2.73,16.39 7,19.5 12,19.5C13.55,19.5 15.03,19.2 16.38,18.66L16.81,19.08L19.73,22L21,20.73L3.27,3M12,7A5,5 0 0,1 17,12C17,12.64 16.87,13.26 16.64,13.82L19.57,16.75C21.07,15.5 22.27,13.86 23,12C21.27,7.61 17,4.5 12,4.5C10.6,4.5 9.26,4.75 8,5.2L10.17,7.35C10.74,7.13 11.35,7 12,7Z"/>';

function togglePassword(fieldId, btn) {
    const field = document.getElementById(fieldId);
    const svg = btn.querySelector('svg');
    if (field.type === "password") {
        field.type = "text";
        svg.innerHTML = SVG_EYE;
    } else {
        field.type = "password";
        svg.innerHTML = SVG_EYE_OFF;
    }
}

function signalIcon(signal) {
    const bars = signal >= 75 ? 4 : signal >= 50 ? 3 : signal >= 25 ? 2 : 1;
    const paths = [
        `<rect x="1"  y="13" width="3" height="5" rx="0.5" fill="${bars>=1?'hsl(32,95%,52%)':'#ccc'}"/>`,
        `<rect x="6"  y="9"  width="3" height="9" rx="0.5" fill="${bars>=2?'hsl(32,95%,52%)':'#ccc'}"/>`,
        `<rect x="11" y="5"  width="3" height="13" rx="0.5" fill="${bars>=3?'hsl(32,95%,52%)':'#ccc'}"/>`,
        `<rect x="16" y="1"  width="3" height="17" rx="0.5" fill="${bars>=4?'hsl(32,95%,52%)':'#ccc'}"/>`,
    ];
    return `<svg width="20" height="18" viewBox="0 0 20 18">${paths.join('')}</svg>`;
}

function escapeHtml(str) {
    return str
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function selectNetwork(el) {
    document.querySelectorAll('.network-item').forEach(i => i.classList.remove('selected'));
    el.classList.add('selected');
    document.getElementById('ssidInput').value = el.dataset.ssid;
}

function renderNetworks(networks) {
    const list = document.getElementById('networkList');
    if (networks.length === 0) {
        list.innerHTML = '<div class="scan-status">Geen netwerken gevonden. Typ de netwerknaam handmatig.</div>';
        return;
    }
    const lockSvg = `<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M18,8H17V6A5,5 0 0,0 7,6V8H6A2,2 0 0,0 4,10V20A2,2 0 0,0 6,22H18A2,2 0 0,0 20,20V10A2,2 0 0,0 18,8M12,17A2,2 0 0,1 10,15A2,2 0 0,1 12,13A2,2 0 0,1 14,15A2,2 0 0,1 12,17M15.1,8H8.9V6A3.1,3.1 0 0,1 12,2.9A3.1,3.1 0 0,1 15.1,6V8Z"/></svg>`;
    list.innerHTML = networks.map(n => `
        <div class="network-item" data-ssid="${escapeHtml(n.ssid)}">
            <span class="network-signal">${signalIcon(n.signal)}</span>
            <span class="network-name">${escapeHtml(n.ssid)}</span>
            <span class="network-lock" style="${n.secured ? '' : 'opacity:0.25'}">${n.secured ? lockSvg : lockSvg}</span>
        </div>
    `).join('');
    list.querySelectorAll('.network-item').forEach(el => {
        el.addEventListener('click', () => selectNetwork(el));
    });
}

async function scanNetworks(force = false) {
    const list = document.getElementById('networkList');
    const btn = document.getElementById('refreshBtn');
    list.innerHTML = '<div class="scan-status"><svg class="spinning" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" style="vertical-align:middle"><path d="M12,4V2A10,10 0 0,0 2,12H4A8,8 0 0,1 12,4Z"/></svg> Netwerken zoeken...</div>';
    btn.disabled = true;
    try {
        const res = await fetch(force ? '/scan?rescan=1' : '/scan');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const networks = await res.json();
        renderNetworks(networks);
    } catch {
        list.innerHTML = '<div class="scan-status">Scannen mislukt. Typ de netwerknaam handmatig.</div>';
    } finally {
        btn.disabled = false;
    }
}

window.addEventListener('DOMContentLoaded', () => scanNetworks(false));
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


OWN_SSIDS = {"PI-SETUP"}
PORTAL_IP = "10.4.0.1"


# iOS detecteert een captive portal als /hotspot-detect.html NIET de exacte
# "Success"-tekst teruggeeft. We sturen een redirect zodat WebSheet opent.
APPLE_PROBES = {
    '/hotspot-detect.html',
    '/library/test/success.html',
    '/library/test/success',
}

_CAPTIVE_REDIRECT = (
    '<HTML><HEAD>'
    f'<meta http-equiv="refresh" content="0;url=http://{PORTAL_IP}/">'
    '</HEAD><BODY></BODY></HTML>'
)


@app.route('/hotspot-detect.html')
@app.route('/library/test/success.html')
def apple_captive_probe():
    # Geeft een non-Success pagina terug → iOS toont "Verbinden met netwerk" popup
    return _CAPTIVE_REDIRECT, 200, {'Content-Type': 'text/html'}


@app.route('/generate_204')
@app.route('/gen_204')
def android_captive_probe():
    return redirect(f'http://{PORTAL_IP}/', 302)


@app.before_request
def captive_portal_redirect():
    if request.path in ('/', '/scan') or request.method == 'POST':
        return None
    # Specifieke Apple/Android routes worden hierboven al afgehandeld
    if request.path in APPLE_PROBES or request.path in ('/generate_204', '/gen_204'):
        return None
    # Alle overige paden (Windows probes, onbekende hosts) → redirect
    return redirect(f'http://{PORTAL_IP}/', 302)

def scan_networks(rescan=False):
    try:
        cmd = [
            "nmcli", "--terse", "--fields", "SSID,SIGNAL,SECURITY",
            "dev", "wifi", "list",
            "--rescan", "yes" if rescan else "no",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        networks = []
        seen = set()
        for line in result.stdout.splitlines():
            parts = re.split(r'(?<!\\):', line)
            if not parts:
                continue
            ssid = parts[0].replace('\\:', ':').strip()
            if not ssid or ssid in seen or ssid in OWN_SSIDS:
                continue
            seen.add(ssid)
            signal = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else 0
            security = parts[2].strip() if len(parts) > 2 else ""
            networks.append({"ssid": ssid, "signal": signal, "secured": bool(security)})
        networks.sort(key=lambda x: x["signal"], reverse=True)
        return networks
    except Exception:
        return []


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
def scan():
    rescan = request.args.get("rescan") == "1"
    return jsonify(scan_networks(rescan=rescan))


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        ssid = request.form["ssid"].strip()
        password = request.form["password"]

        threading.Thread(
            target=configure_wifi_and_reboot,
            args=(ssid, password),
            daemon=True
        ).start()

        return render_template_string(PROCESSING_HTML)

    return render_template_string(HTML)

app.run(host="0.0.0.0", port=80, threaded=True)
