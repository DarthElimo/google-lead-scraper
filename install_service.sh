#!/usr/bin/env bash
# install_service.sh
# ------------------
# Einmaliges Setup: Installiert den Lead Scraper als macOS-Hintergrunddienst.
# Der Server startet ab sofort bei jedem Login automatisch.
#
# Ausführen:  bash install_service.sh
# Stoppen:    launchctl stop ai.autorise.lead-scraper
# Starten:    launchctl start ai.autorise.lead-scraper
# Entfernen:  launchctl unload ~/Library/LaunchAgents/ai.autorise.lead-scraper.plist

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$(which python3)"
PLIST_NAME="ai.autorise.lead-scraper.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME"
LOG_DIR="$PROJECT_DIR/.tmp"

echo ""
echo "  AutoRise AI — Lead Scraper Service Setup"
echo "  ─────────────────────────────────────────"
echo "  Projektpfad: $PROJECT_DIR"
echo "  Python:      $PYTHON"
echo ""

# Create log directory
mkdir -p "$LOG_DIR"

# Unload existing service if present (ignore errors)
launchctl unload "$PLIST_DST" 2>/dev/null || true

# Write the plist with resolved absolute paths
cat > "$PLIST_DST" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>ai.autorise.lead-scraper</string>

  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$PROJECT_DIR/app.py</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/server.log</string>

  <key>StandardErrorPath</key>
  <string>$LOG_DIR/server_err.log</string>
</dict>
</plist>
PLIST

# Load and start the service
launchctl load "$PLIST_DST"

# Wait a moment for the server to start
echo "  Starte Server…"
sleep 2

# Verify the server is up
if curl -s http://127.0.0.1:5001 > /dev/null 2>&1; then
  echo "  ✓ Server läuft auf http://localhost:5001"
else
  echo "  ⚠  Server braucht evtl. noch einen Moment. Log: $LOG_DIR/server.log"
fi

# Open the browser
open http://localhost:5001

echo ""
echo "  ✓ Setup abgeschlossen."
echo "  → Jetzt http://localhost:5001 als Browser-Lesezeichen speichern."
echo ""
