#!/bin/bash
# Installeert subdomain routing voor SolarBuffer
# Gebruik: sudo bash install_subdomain.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== SolarBuffer subdomain setup ==="

# 1. Nginx installeren en configureren
echo "[1/4] Nginx instellen..."
apt-get install -y nginx

cp "$SCRIPT_DIR/nginx/solarbuffer.conf" /etc/nginx/sites-available/solarbuffer
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/solarbuffer /etc/nginx/sites-enabled/solarbuffer

nginx -t
systemctl enable nginx
systemctl restart nginx

# 2. Avahi CNAME service installeren
echo "[2/4] Avahi mDNS aliassen instellen..."
cp "$SCRIPT_DIR/avahi/solarbuffer-cname.service" /etc/systemd/system/solarbuffer-cname.service
systemctl daemon-reload
systemctl enable solarbuffer-cname.service
systemctl start solarbuffer-cname.service

# 3. WiFi hotspot DNS wildcard toevoegen (zodat wifi.solarbuffer.local werkt in AP-modus)
echo "[3/4] Hotspot DNS wildcard toevoegen..."
DNSMASQ_CONF="/etc/dnsmasq.conf"
if [ -f "$DNSMASQ_CONF" ]; then
    if ! grep -q "address=/.solarbuffer.local/" "$DNSMASQ_CONF"; then
        echo "" >> "$DNSMASQ_CONF"
        echo "# SolarBuffer subdomain wildcard" >> "$DNSMASQ_CONF"
        echo "address=/.solarbuffer.local/10.4.0.1" >> "$DNSMASQ_CONF"
        echo "Toegevoegd aan $DNSMASQ_CONF"
    else
        echo "Al aanwezig in $DNSMASQ_CONF"
    fi
    systemctl restart dnsmasq 2>/dev/null || true
fi

# 4. Poort 5001 firewall: alleen localhost (nginx doet de externe toegang)
echo "[4/4] Firewall aanpassen (optioneel)..."
if command -v ufw &>/dev/null; then
    ufw allow 80/tcp comment "SolarBuffer nginx"
    ufw deny 5001/tcp comment "SolarBuffer app intern" 2>/dev/null || true
    ufw deny 8080/tcp comment "SolarBuffer wifi intern" 2>/dev/null || true
fi

echo ""
echo "=== Klaar! ==="
echo "  Hoofdapp:    http://app.solarbuffer.local"
echo "  WiFi setup:  http://wifi.solarbuffer.local"
echo ""
echo "Let op: solarbuffer.local (zonder subdomain) stuurt automatisch door naar app.solarbuffer.local"
