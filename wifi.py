from flask import Flask, request, jsonify, Response
import subprocess
import threading
import time
import re

app = Flask(__name__)

# ── Pagina's ──────────────────────────────────────────────────────────────────

SETUP_HTML = """<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WiFi Setup - SolarBuffer</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/css/materialdesignicons.min.css">
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap');
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family:'Inter',sans-serif;
  background:hsl(30,25%,97%);
  color:hsl(220,20%,14%);
  min-height:100vh;
  display:flex; align-items:center; justify-content:center;
  padding:1rem;
}
.wrap { width:100%; max-width:400px; }

/* Header */
.hdr { text-align:center; margin-bottom:1.75rem; }
.logo {
  width:56px; height:56px;
  background:linear-gradient(135deg,hsl(32,95%,52%),hsl(40,100%,60%));
  border-radius:1rem;
  display:flex; align-items:center; justify-content:center;
  margin:0 auto 0.75rem;
  box-shadow:0 6px 20px -4px hsla(32,95%,52%,0.4);
}
.logo i { font-size:1.6rem; color:white; }
.hdr h1 { font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:1.55rem; }
.sol { background:linear-gradient(135deg,hsl(32,95%,52%),hsl(40,100%,60%)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
.hdr p { color:hsl(220,10%,50%); font-size:0.83rem; margin-top:0.2rem; }

/* Kaart */
.card {
  background:white; border:1px solid hsl(30,15%,88%);
  border-radius:1rem;
  box-shadow:0 8px 32px -8px hsla(32,95%,52%,0.12);
  overflow:hidden; margin-bottom:0.75rem;
}

/* Scan rij */
.scan-row { display:flex; align-items:center; justify-content:space-between; padding:0.9rem 1rem 0.6rem; }
.scan-lbl { font-family:'Space Grotesk',sans-serif; font-weight:600; font-size:0.88rem; color:hsl(220,15%,35%); text-transform:uppercase; letter-spacing:0.02em; }
.btn-refresh {
  display:flex; align-items:center; gap:0.3rem;
  background:hsl(30,20%,96%); color:hsl(220,15%,40%);
  font-weight:600; font-size:0.75rem;
  padding:0.28rem 0.65rem;
  border:1px solid hsl(30,15%,86%); border-radius:999px;
  cursor:pointer; font-family:'Inter',sans-serif;
  transition:all 0.15s;
}
.btn-refresh:disabled { opacity:0.5; cursor:default; }

/* Netwerk lijst */
.net-list { display:flex; flex-direction:column; }
.net-entry { border-top:1px solid hsl(30,15%,92%); overflow:hidden; }
.net-hdr {
  display:flex; align-items:center; gap:0.7rem;
  padding:0.7rem 1rem; cursor:pointer; user-select:none;
}
.net-entry.open .net-hdr { background:hsl(32,100%,97%); }
.net-sig { font-size:1.1rem; color:hsl(220,10%,58%); flex-shrink:0; }
.net-entry.open .net-sig { color:hsl(32,90%,50%); }
.net-name { flex:1; font-size:0.92rem; font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.net-entry.open .net-name { font-weight:600; color:hsl(32,80%,35%); }
.net-lock { font-size:0.95rem; color:hsl(220,10%,62%); }
.net-chev { font-size:1rem; color:hsl(220,10%,68%); transition:transform 0.2s; }
.net-entry.open .net-chev { transform:rotate(180deg); color:hsl(32,80%,55%); }

.net-body { max-height:0; overflow:hidden; transition:max-height 0.28s ease; }
.net-entry.open .net-body { max-height:160px; }
.net-body-inner { padding:0 1rem 0.9rem; display:flex; flex-direction:column; gap:0.6rem; }

/* Wachtwoord veld */
.pw-row { position:relative; }
.pw-row input {
  width:100%; padding:0.6rem 2.6rem 0.6rem 0.75rem;
  border:1.5px solid hsl(30,15%,86%); border-radius:0.55rem;
  font-size:0.92rem; font-family:'Inter',sans-serif;
  background:hsl(30,20%,98%);
}
.pw-row input:focus { outline:none; border-color:hsl(32,90%,55%); box-shadow:0 0 0 3px hsla(32,95%,52%,0.13); background:white; }
.pw-eye { position:absolute; top:50%; right:0.75rem; transform:translateY(-50%); cursor:pointer; color:hsl(220,10%,60%); font-size:1.1rem; }

/* Verbinden knop */
.btn-conn {
  width:100%;
  background:linear-gradient(135deg,hsl(32,95%,52%),hsl(38,98%,56%));
  color:white; font-family:'Space Grotesk',sans-serif;
  font-weight:700; font-size:0.88rem;
  padding:0.6rem; border:none; border-radius:0.55rem;
  cursor:pointer; box-shadow:0 3px 12px -3px hsla(32,95%,52%,0.45);
}

/* Meldingen */
.msg { padding:1.1rem 1rem; text-align:center; font-size:0.82rem; color:hsl(220,10%,58%); border-top:1px solid hsl(30,15%,92%); }
.msg-err { padding:0.9rem 1rem; text-align:center; font-size:0.82rem; color:hsl(0,60%,50%); border-top:1px solid hsl(30,15%,92%); }

/* Handmatige invoer */
.btn-manual {
  width:100%; background:white; color:hsl(220,15%,38%);
  font-family:'Inter',sans-serif; font-weight:600; font-size:0.88rem;
  padding:0.75rem; border:1.5px dashed hsl(30,15%,82%);
  border-radius:1rem; cursor:pointer;
  display:flex; align-items:center; justify-content:center; gap:0.5rem;
  transition:all 0.15s;
}
.btn-manual.on { border-style:solid; border-color:hsl(32,90%,55%); color:hsl(32,85%,40%); background:hsl(32,100%,97%); }

.man-card {
  background:white; border:1px solid hsl(30,15%,88%);
  border-radius:1rem; box-shadow:0 8px 32px -8px hsla(32,95%,52%,0.12);
  overflow:hidden; max-height:0; transition:max-height 0.3s ease;
  margin-bottom:0.75rem;
}
.man-card.open { max-height:400px; }
.man-inner { padding:1rem; display:flex; flex-direction:column; gap:0.65rem; }
.man-inner input {
  width:100%; padding:0.6rem 0.75rem;
  border:1.5px solid hsl(30,15%,86%); border-radius:0.55rem;
  font-size:0.92rem; font-family:'Inter',sans-serif;
  background:hsl(30,20%,98%);
}
.man-inner input:focus { outline:none; border-color:hsl(32,90%,55%); box-shadow:0 0 0 3px hsla(32,95%,52%,0.13); background:white; }

.spin { display:inline-block; animation:spin 0.8s linear infinite; }
@keyframes spin { to { transform:rotate(360deg); } }
</style>
</head>
<body>
<div class="wrap">

  <div class="hdr">
    <div class="logo"><i class="mdi mdi-solar-power"></i></div>
    <h1><span class="sol">Solar</span>Buffer</h1>
    <p>Verbind met uw WiFi netwerk</p>
  </div>

  <div class="card">
    <div class="scan-row">
      <span class="scan-lbl">Netwerken</span>
      <button type="button" class="btn-refresh" id="refreshBtn" onclick="startScan()">
        <i class="mdi mdi-refresh" id="refreshIcon"></i>
        <span id="refreshText">Ververs</span>
      </button>
    </div>
    <div class="net-list" id="netList">
      <div class="msg"><span class="spin"><i class="mdi mdi-loading"></i></span>&nbsp; Netwerken zoeken...</div>
    </div>
  </div>

  <button type="button" class="btn-manual" id="manBtn" onclick="toggleManual()">
    <i class="mdi mdi-pencil-outline"></i>
    <span id="manBtnTxt">Netwerk handmatig invoeren</span>
  </button>

  <div class="man-card" id="manCard">
    <div class="man-inner">
      <input type="text" id="manSsid" placeholder="WiFi naam (SSID)"
             autocomplete="off" autocorrect="off" autocapitalize="none" spellcheck="false">
      <div class="pw-row">
        <input class="pw-input" type="password" id="manPw" placeholder="Wachtwoord (leeg = open netwerk)">
        <i class="mdi mdi-eye-off pw-eye" id="manEye" onclick="toggleEye('manPw','manEye')"></i>
      </div>
      <button type="button" class="btn-conn" onclick="connectManual()">
        <i class="mdi mdi-wifi-arrow-right"></i>&nbsp; Verbinden
      </button>
    </div>
  </div>

  <form id="wifiForm" method="POST" action="/" style="display:none">
    <input type="hidden" id="fSsid" name="ssid">
    <input type="hidden" id="fPw" name="password">
  </form>

</div>
<script>
var activeEntry = null;
var manOpen = false;

function sigIcon(s) {
  if (s >= 75) return 'mdi-wifi-strength-4';
  if (s >= 50) return 'mdi-wifi-strength-3';
  if (s >= 25) return 'mdi-wifi-strength-2';
  return 'mdi-wifi-strength-1';
}

function safeHtml(s) {
  var t = document.createTextNode(s);
  var d = document.createElement('span');
  d.appendChild(t);
  return d.innerHTML;
}

function toggleEye(inputId, eyeId) {
  var inp = document.getElementById(inputId);
  var eye = document.getElementById(eyeId);
  if (inp.type === 'password') {
    inp.type = 'text';
    eye.classList.remove('mdi-eye-off');
    eye.classList.add('mdi-eye');
  } else {
    inp.type = 'password';
    eye.classList.remove('mdi-eye');
    eye.classList.add('mdi-eye-off');
  }
}

function doConnect(ssid, password) {
  document.getElementById('fSsid').value = ssid;
  document.getElementById('fPw').value = password;
  document.getElementById('wifiForm').submit();
}

function connectEntry(btn) {
  var entry = btn.parentElement.parentElement.parentElement;
  var ssid = entry.getAttribute('data-ssid');
  var pw = entry.querySelector('.pw-input').value;
  doConnect(ssid, pw);
}

function connectManual() {
  var ssid = document.getElementById('manSsid').value.trim();
  if (!ssid) { document.getElementById('manSsid').focus(); return; }
  doConnect(ssid, document.getElementById('manPw').value);
}

function toggleManual() {
  manOpen = !manOpen;
  document.getElementById('manCard').classList.toggle('open', manOpen);
  document.getElementById('manBtn').classList.toggle('on', manOpen);
  document.getElementById('manBtnTxt').textContent = manOpen
    ? 'Handmatig invoeren verbergen'
    : 'Netwerk handmatig invoeren';
  if (manOpen) {
    setTimeout(function() { document.getElementById('manSsid').focus(); }, 300);
  }
}

function toggleEntry(entry) {
  if (activeEntry && activeEntry !== entry) {
    activeEntry.classList.remove('open');
    var old = activeEntry.querySelector('.pw-input');
    if (old) old.value = '';
    activeEntry = null;
  }
  var nowOpen = entry.classList.toggle('open');
  activeEntry = nowOpen ? entry : null;
  if (nowOpen) {
    setTimeout(function() {
      var pw = entry.querySelector('.pw-input');
      if (pw) pw.focus();
    }, 250);
  }
}

function showScanResult(networks) {
  var list = document.getElementById('netList');
  if (!networks || networks.length === 0) {
    list.innerHTML = '<div class="msg-err"><i class="mdi mdi-wifi-off"></i>&nbsp; Geen netwerken gevonden. Probeer opnieuw.</div>';
    return;
  }
  list.innerHTML = '';
  for (var i = 0; i < networks.length; i++) {
    var n = networks[i];
    var entry = document.createElement('div');
    entry.className = 'net-entry';
    entry.setAttribute('data-ssid', n.ssid);
    var pwId = 'pw' + i;
    var eyeId = 'ey' + i;
    entry.innerHTML =
      '<div class="net-hdr" onclick="toggleEntry(this.parentElement)">' +
        '<i class="mdi ' + sigIcon(n.signal) + ' net-sig"></i>' +
        '<span class="net-name">' + safeHtml(n.ssid) + '</span>' +
        '<i class="mdi ' + (n.secured ? 'mdi-lock' : 'mdi-lock-open-outline') + ' net-lock"></i>' +
        '<i class="mdi mdi-chevron-down net-chev"></i>' +
      '</div>' +
      '<div class="net-body">' +
        '<div class="net-body-inner">' +
          '<div class="pw-row">' +
            '<input class="pw-input" id="' + pwId + '" type="password" placeholder="' + (n.secured ? 'Wachtwoord' : 'Wachtwoord (optioneel)') + '">' +
            '<i class="mdi mdi-eye-off pw-eye" id="' + eyeId + '" onclick="toggleEye(\'' + pwId + '\',\'' + eyeId + '\')"></i>' +
          '</div>' +
          '<button type="button" class="btn-conn" onclick="connectEntry(this)">' +
            '<i class="mdi mdi-wifi-arrow-right"></i>&nbsp; Verbinden met ' + safeHtml(n.ssid) +
          '</button>' +
        '</div>' +
      '</div>';
    list.appendChild(entry);
  }
}

function startScan() {
  var btn = document.getElementById('refreshBtn');
  var icon = document.getElementById('refreshIcon');
  var txt = document.getElementById('refreshText');
  var list = document.getElementById('netList');

  btn.disabled = true;
  icon.className = 'mdi mdi-loading spin';
  txt.textContent = 'Bezig...';
  list.innerHTML = '<div class="msg"><span class="spin"><i class="mdi mdi-loading"></i></span>&nbsp; Netwerken zoeken...</div>';
  activeEntry = null;

  var xhr = new XMLHttpRequest();
  xhr.timeout = 25000;

  function scanDone() {
    btn.disabled = false;
    icon.className = 'mdi mdi-refresh';
    txt.textContent = 'Ververs';
  }

  xhr.onload = function() {
    scanDone();
    if (xhr.status === 200) {
      try {
        var d = JSON.parse(xhr.responseText);
        showScanResult(d.networks);
      } catch (e) {
        list.innerHTML = '<div class="msg-err"><i class="mdi mdi-alert-circle-outline"></i>&nbsp; Scan mislukt. Probeer opnieuw.</div>';
      }
    } else {
      list.innerHTML = '<div class="msg-err"><i class="mdi mdi-alert-circle-outline"></i>&nbsp; Scan mislukt (' + xhr.status + ').</div>';
    }
  };

  xhr.onerror = function() {
    scanDone();
    list.innerHTML = '<div class="msg-err"><i class="mdi mdi-alert-circle-outline"></i>&nbsp; Verbindingsfout. Probeer opnieuw.</div>';
  };

  xhr.ontimeout = function() {
    scanDone();
    list.innerHTML = '<div class="msg-err"><i class="mdi mdi-timer-off-outline"></i>&nbsp; Time-out. Probeer opnieuw.</div>';
  };

  xhr.open('GET', '/scan', true);
  xhr.send();
}

startScan();
</script>
</body>
</html>"""

DONE_HTML = """<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SolarBuffer</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@300;400;500;600&display=swap');
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family:'Inter',sans-serif; background:hsl(30,25%,97%);
  color:hsl(220,20%,14%); min-height:100vh;
  display:flex; align-items:center; justify-content:center; padding:1rem;
}
.box {
  width:100%; max-width:420px; background:white;
  border:1px solid hsl(30,15%,88%); border-radius:0.75rem;
  box-shadow:0 10px 40px -10px hsla(32,95%,52%,0.15);
  padding:2rem; text-align:center;
}
.icon { font-size:2rem; margin-bottom:0.75rem; }
h1 { font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:1.6rem; margin-bottom:0.5rem; }
.sol { background:linear-gradient(135deg,hsl(32,95%,52%),hsl(40,100%,60%)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
h3 { margin-top:1rem; margin-bottom:0.5rem; color:hsl(32,95%,52%); }
p { color:hsl(220,10%,46%); font-size:0.95rem; }
</style>
</head>
<body>
<div class="box">
  <div class="icon">&#8987;</div>
  <h1><span class="sol">Solar</span>Buffer</h1>
  <h3>WiFi wordt opgeslagen</h3>
  <p>De instellingen zijn ontvangen. SolarBuffer probeert nu verbinding te maken en start daarna opnieuw op.</p>
</div>
</body>
</html>"""


# ── WiFi configuratie ─────────────────────────────────────────────────────────

def configure_and_reboot(ssid, password):
    try:
        subprocess.run(
            ["sudo", "nmcli", "connection", "delete", "customer-wifi"],
            capture_output=True
        )
        subprocess.run(
            ["sudo", "nmcli", "connection", "add",
             "type", "wifi", "ifname", "wlan0",
             "con-name", "customer-wifi", "ssid", ssid],
            check=True
        )
        if password:
            subprocess.run(
                ["sudo", "nmcli", "connection", "modify", "customer-wifi",
                 "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password],
                check=True
            )
        subprocess.run(
            ["sudo", "nmcli", "connection", "modify", "customer-wifi",
             "connection.autoconnect", "yes",
             "connection.autoconnect-priority", "100",
             "connection.autoconnect-retries", "0"],
            check=True
        )
        subprocess.run(
            ["sudo", "nmcli", "connection", "modify", "PI-SETUP",
             "connection.autoconnect", "no",
             "connection.autoconnect-priority", "-100"],
            check=False
        )
        time.sleep(2)
        subprocess.run(
            ["sudo", "nmcli", "connection", "up", "customer-wifi"],
            capture_output=True
        )
        time.sleep(5)
    except Exception:
        pass
    subprocess.Popen(["sudo", "systemctl", "reboot"])


# ── Routes ────────────────────────────────────────────────────────────────────

def parse_iwlist(output):
    networks = []
    seen = set()
    for cell in re.split(r'Cell \d+', output)[1:]:
        ssid_m = re.search(r'ESSID:"(.*?)"', cell)
        if not ssid_m:
            continue
        ssid = ssid_m.group(1).strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        q = re.search(r'Quality=(\d+)/(\d+)', cell)
        if q:
            signal = int(int(q.group(1)) * 100 / int(q.group(2)))
        else:
            r = re.search(r'Signal level=(-?\d+)', cell)
            signal = max(0, min(100, 2 * (int(r.group(1)) + 100))) if r else 0
        secured = 'Encryption key:on' in cell
        networks.append({"ssid": ssid, "signal": signal, "secured": secured})
    return sorted(networks, key=lambda x: -x["signal"])


@app.route("/scan")
def scan():
    try:
        result = subprocess.run(
            ["sudo", "iwlist", "scan"],
            capture_output=True, text=True, timeout=15
        )
        networks = parse_iwlist(result.stdout)
        return jsonify(ok=True, networks=networks)
    except Exception as e:
        return jsonify(ok=False, networks=[], error=str(e))


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        ssid = request.form.get("ssid", "").strip()
        password = request.form.get("password", "")
        if ssid:
            threading.Thread(
                target=configure_and_reboot,
                args=(ssid, password),
                daemon=True
            ).start()
            return Response(DONE_HTML, mimetype='text/html')
    return Response(SETUP_HTML, mimetype='text/html')


app.run(host="0.0.0.0", port=80, threaded=True)
