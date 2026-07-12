#!/bin/bash
# SolarBuffer rescue — zelfherstel wanneer de app-service blijft crashen.
#
# Draait via solarbuffer-rescue.timer (elke 10 minuten) en doet niets zolang
# de app gewoon draait. Werkwijze bij een gecrashte service:
#   1. git pull  — de fix kan al op GitHub staan; binnen 10 minuten na een
#                  push herstelt elk kastje zichzelf.
#   2. rollback  — helpt de pull niet, dan terug naar de laatst bekende
#                  goede versie (onthouden toen de app nog stabiel draaide).
#
# Vereist in solarbuffer.service: StartLimitIntervalSec/StartLimitBurst,
# zodat een crash-loop daadwerkelijk in de 'failed'-status belandt
# (zie solarbuffer.service.example).

REPO="/home/solarbuffer/SolarBuffer"
SERVICE="solarbuffer"
GOOD_FILE="/var/lib/solarbuffer-rescue/last-good-commit"
LOG="/var/log/solarbuffer-rescue.log"
MIN_STABLE_SECONDS=300   # zo lang moet de app draaien voor "laatst goede versie"
STARTUP_WAIT=45          # wachttijd na een herstart voordat we het resultaat beoordelen

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"$LOG"; }

mkdir -p "$(dirname "$GOOD_FILE")"

# Git altijd als de eigenaar van de repo draaien, nooit als root:
# anders raken bestanden root-owned en breekt de update-knop in de app.
REPO_USER=$(stat -c %U "$REPO" 2>/dev/null || echo root)
run_git() { runuser -u "$REPO_USER" -- git -C "$REPO" "$@"; }

# ---- App draait? Onthoud dan deze versie als laatst bekende goede ----
if systemctl is-active --quiet "$SERVICE"; then
    started=$(systemctl show -p ActiveEnterTimestamp --value "$SERVICE")
    if [ -n "$started" ]; then
        uptime=$(( $(date +%s) - $(date -d "$started" +%s) ))
        if [ "$uptime" -ge "$MIN_STABLE_SECONDS" ]; then
            run_git rev-parse HEAD >"$GOOD_FILE" 2>/dev/null
        fi
    fi
    exit 0
fi

# Alleen ingrijpen wanneer systemd de service heeft opgegeven (failed).
systemctl is-failed --quiet "$SERVICE" || exit 0

log "== Service '$SERVICE' staat op failed — rescue gestart =="
before=$(run_git rev-parse HEAD 2>/dev/null)

# ---- Stap 1: nieuwste versie ophalen (de fix kan al gepusht zijn) ----
if run_git pull --ff-only >>"$LOG" 2>&1; then
    after=$(run_git rev-parse HEAD 2>/dev/null)
    if [ -n "$after" ] && [ "$before" != "$after" ]; then
        log "Update opgehaald: ${before:0:9} -> ${after:0:9}"
    fi
else
    log "git pull mislukt (geen netwerk?)"
fi

systemctl reset-failed "$SERVICE" 2>/dev/null
systemctl restart "$SERVICE"
sleep "$STARTUP_WAIT"
if systemctl is-active --quiet "$SERVICE"; then
    log "Service draait weer na update/herstart"
    exit 0
fi

# ---- Stap 2: nog steeds kapot -> terug naar laatst bekende goede versie ----
good=$(cat "$GOOD_FILE" 2>/dev/null)
current=$(run_git rev-parse HEAD 2>/dev/null)
if [ -n "$good" ] && [ "$good" != "$current" ]; then
    log "Rollback naar laatst bekende goede versie ${good:0:9}"
    run_git reset --hard "$good" >>"$LOG" 2>&1
    systemctl reset-failed "$SERVICE" 2>/dev/null
    systemctl restart "$SERVICE"
    sleep "$STARTUP_WAIT"
    if systemctl is-active --quiet "$SERVICE"; then
        log "Service draait weer na rollback"
        exit 0
    fi
    log "Service faalt ook na rollback"
else
    log "Geen (andere) laatst bekende goede versie beschikbaar voor rollback"
fi

log "Rescue kon de service niet herstellen — handmatige actie nodig"
exit 1
