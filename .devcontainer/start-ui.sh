#!/usr/bin/env bash
# Starts the web interface in the background when the Codespace starts; GitHub then opens it
# in a browser tab (port 8765). Logs: /tmp/leadgen-ui.log
cd "$(dirname "$0")/.."
pkill -f "tool ui" 2>/dev/null || true
setsid nohup tool ui --no-browser --port 8765 > /tmp/leadgen-ui.log 2>&1 < /dev/null &
for i in $(seq 1 30); do
  curl -s -o /dev/null http://127.0.0.1:8765/ && { echo "Interface ready on port 8765"; exit 0; }
  sleep 1
done
echo "Interface did not start; see /tmp/leadgen-ui.log"
