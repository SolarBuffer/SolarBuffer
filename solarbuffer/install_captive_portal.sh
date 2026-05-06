#!/bin/bash
# Installeert de captive portal voor de SolarBuffer hotspot.
# Uitvoeren als root op de Raspberry Pi: sudo bash install_captive_portal.sh
# Raakt dnsmasq/NetworkManager NIET aan.

set -e

PORTAL_IP="10.4.0.1"
IFACE="wlan0"

echo "→ Captive portal installeren via iptables..."

# Redirect al het HTTP-verkeer op de hotspot-interface naar wifi.py
# Werkt op netwerk-niveau, onafhankelijk van DNS
iptables -t nat -C PREROUTING -i "$IFACE" -p tcp --dport 80 -j DNAT --to-destination "$PORTAL_IP:80" 2>/dev/null || \
  iptables -t nat -A PREROUTING -i "$IFACE" -p tcp --dport 80 -j DNAT --to-destination "$PORTAL_IP:80"
echo "  iptables HTTP-redirect actief."

# Opslaan zodat regels na herstart bewaard blijven
if command -v netfilter-persistent &>/dev/null; then
    netfilter-persistent save
    echo "  Regels opgeslagen via netfilter-persistent."
elif command -v iptables-save &>/dev/null; then
    mkdir -p /etc/iptables
    iptables-save > /etc/iptables/rules.v4
    echo "  Regels opgeslagen in /etc/iptables/rules.v4."
else
    echo "  Let op: installeer iptables-persistent om regels na herstart te bewaren:"
    echo "  sudo apt install iptables-persistent"
fi

echo ""
echo "✓ Klaar. Verbinden met PI-SETUP → popup verschijnt automatisch op iOS en Android."
