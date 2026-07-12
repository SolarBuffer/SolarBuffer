#!/bin/bash
# Installeert het SolarBuffer rescue-mechanisme. Uitvoeren met sudo:
#   sudo bash install-rescue.sh
set -e
cd "$(dirname "$0")"

if [ "$(id -u)" -ne 0 ]; then
    echo "Dit script moet met sudo/root draaien." >&2
    exit 1
fi

install -m 755 solarbuffer-rescue.sh /usr/local/bin/solarbuffer-rescue.sh
install -m 644 solarbuffer-rescue.service /etc/systemd/system/solarbuffer-rescue.service
install -m 644 solarbuffer-rescue.timer /etc/systemd/system/solarbuffer-rescue.timer

systemctl daemon-reload
systemctl enable --now solarbuffer-rescue.timer

echo "Rescue geinstalleerd en actief."
echo "Controleer met: systemctl list-timers solarbuffer-rescue.timer"
echo "Logboek:        /var/log/solarbuffer-rescue.log"
echo ""
echo "LET OP: zorg dat solarbuffer.service StartLimitIntervalSec/StartLimitBurst"
echo "bevat (zie solarbuffer.service.example), anders blijft een crash-loop"
echo "eindeloos herstarten en wordt de unit nooit 'failed'."
