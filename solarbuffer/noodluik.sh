#!/bin/bash
# Het noodluik: zet ssh open op de tunnel als de software het heeft opgegeven.
#
# Waarom dit bestaat
# ------------------
# De tunnel staat los van de software en blijft dus gewoon draaien als die
# crasht. Maar op die tunnel is alleen poort 5001 open, en dat is precies de
# poort die een gecrashte app niet meer bedient. Ssh zou helpen, maar dat staat
# dicht, en de schakelaar die het opent zit in de app die net is omgevallen.
#
# Dit draait daarom buiten de app om, aangeroepen door systemd zodra die het
# herstarten opgeeft. Het blijft open zolang de software niet draait: een vaste
# tijdslimiet zou precies aflopen terwijl je onderweg bent. De app sluit het zelf
# zodra hij weer een tijdje overeind staat.
set -u
KETEN=SOLARBUFFER-WG
OPEN_BESTAND=/run/solarbuffer-noodluik-open

open_luik() {
    # De keten bestaat alleen als de tunnel ooit is opgezet. Zo niet, dan valt er
    # ook niets open te zetten en is er niets aan de hand.
    iptables -n -L "$KETEN" >/dev/null 2>&1 || exit 0
    # Niet dubbel toevoegen bij een tweede keer.
    iptables -C "$KETEN" -p tcp --dport 22 -j ACCEPT 2>/dev/null \
        || iptables -I "$KETEN" 2 -p tcp --dport 22 -j ACCEPT
    date +%s > "$OPEN_BESTAND"
    logger -t solarbuffer-noodluik "ssh op de tunnel geopend, de software gaf het op"
}

sluit_luik() {
    while iptables -D "$KETEN" -p tcp --dport 22 -j ACCEPT 2>/dev/null; do :; done
    rm -f "$OPEN_BESTAND"
    logger -t solarbuffer-noodluik "ssh op de tunnel weer gesloten"
}

case "${1:-open}" in
    open)  open_luik ;;
    dicht) sluit_luik ;;
    *)     echo "gebruik: $0 [open|dicht]" >&2; exit 2 ;;
esac
