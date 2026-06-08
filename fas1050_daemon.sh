#!/bin/bash
# ──────────────────────────────────────────────────────────────
#  FAS 1050 PRO Listener Daemon (Wrapper)
# ──────────────────────────────────────────────────────────────
#  Startet den Python-Listener in einer Endlosschleife.
#  Wird von systemd verwendet, kann aber auch manuell 
#  gestartet werden:  ./fas1050_daemon.sh
#
# 用途: Lauscht auf dem FAS 1050 PRO Automaten (TCP 8888)
#        und zeichnet alle Statusdaten auf.
#        Sendet KEINE Kommandos.
# ──────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

python3 -u camiro-automat-vas1050/fas1050_listener.py
