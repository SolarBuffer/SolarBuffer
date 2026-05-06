#!/bin/bash
# Installeert de captive portal voor de SolarBuffer hotspot.
# Uitvoeren als root op de Raspberry Pi: sudo bash install_captive_portal.sh

set -e

CONF_SRC="$(dirname "$(realpath "$0")")/captive_portal.conf"
NM_DNSMASQ_DIR="/etc/NetworkManager/dnsmasq-shared.d"
DEST="$NM_DNSMASQ_DIR/captive-portal.conf"
PORTAL_IP="10.4.0.1"
IFACE="wlan0"

echo "→ Captive portal installeren..."

# 1. dnsmasq config zodat alle domeinen naar de Pi wijzen
mkdir -p "$NM_DNSMASQ_DIR"
cp "$CONF_SRC" "$DEST"
echo "  dnsmasq config geplaatst: $DEST"

# 2. iptables: forceer alle DNS-queries op de hotspot interface naar de Pi
#    (ook als een apparaat een andere DNS-server probeert)
iptables -t nat -C PREROUTING -i "$IFACE" -p udp --dport 53 -j DNAT --to-destination "$PORTAL_IP" 2>/dev/null || \
  iptables -t nat -A PREROUTING -i "$IFACE" -p udp --dport 53 -j DNAT --to-destination "$PORTAL_IP"
iptables -t nat -C PREROUTING -i "$IFACE" -p tcp --dport 53 -j DNAT --to-destination "$PORTAL_IP" 2>/dev/null || \
  iptables -t nat -A PREROUTING -i "$IFACE" -p tcp --dport 53 -j DNAT --to-destination "$PORTAL_IP"
echo "  iptables DNS-redirect actief."

# 3. iptables: stuur al het HTTP-verkeer op de hotspot interface naar wifi.py (port 80)
iptables -t nat -C PREROUTING -i "$IFACE" -p tcp --dport 80 -j DNAT --to-destination "$PORTAL_IP:80" 2>/dev/null || \
  iptables -t nat -A PREROUTING -i "$IFACE" -p tcp --dport 80 -j DNAT --to-destination "$PORTAL_IP:80"
echo "  iptables HTTP-redirect actief."

# 4. Sla iptables-regels op zodat ze na herstart behouden blijven
if command -v netfilter-persistent &>/dev/null; then
  netfilter-persistent save
elif command -v iptables-save &>/dev/null; then
  iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
fi

# 5. Herlaad NetworkManager
systemctl reload NetworkManager 2>/dev/null || systemctl restart NetworkManager
echo "  NetworkManager herladen."

echo ""
echo "✓ Captive portal actief."
echo "  Verbinden met PI-SETUP → popup verschijnt automatisch op iOS en Android."
