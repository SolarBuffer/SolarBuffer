#!/bin/bash
# Installeert de captive portal voor de SolarBuffer hotspot.
# Uitvoeren als root op de Raspberry Pi: sudo bash install_captive_portal.sh

set -e

CONF_SRC="$(dirname "$(realpath "$0")")/captive_portal.conf"
NM_DNSMASQ_DIR="/etc/NetworkManager/dnsmasq-shared.d"
DEST="$NM_DNSMASQ_DIR/captive-portal.conf"

echo "→ Captive portal installeren..."

# Zorg dat de map bestaat (NetworkManager-hotspot dnsmasq config)
mkdir -p "$NM_DNSMASQ_DIR"

# Kopieer de dnsmasq config
cp "$CONF_SRC" "$DEST"
echo "  Config geplaatst: $DEST"

# Herlaad NetworkManager zodat de config actief wordt
systemctl reload NetworkManager 2>/dev/null || systemctl restart NetworkManager
echo "  NetworkManager herladen."

echo ""
echo "✓ Captive portal actief. Zodra een apparaat verbinding maakt met PI-SETUP"
echo "  wordt het automatisch doorgestuurd naar http://10.4.0.1/"
