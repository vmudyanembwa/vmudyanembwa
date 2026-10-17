#!/bin/bash
# Double-click in Finder to start Margin and open it in your browser.
# Keep this window open while you use Margin; close it (or press Ctrl+C) to stop.
cd "$(dirname "$0")" || exit 1
URL="http://127.0.0.1:8080"

# Already running (e.g. in the background)? Just open it.
if curl -s -o /dev/null --max-time 2 "$URL/api/stats"; then
  echo "Margin is already running — opening $URL"
  open "$URL"
  exit 0
fi

if ! command -v python3 >/dev/null; then
  echo "Python 3 isn't installed. Get it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close."
  exit 1
fi

# Open the browser once the server answers (up to ~10 seconds).
( for _ in $(seq 20); do
    sleep 0.5
    curl -s -o /dev/null --max-time 1 "$URL/api/stats" && { open "$URL"; break; }
  done ) &

echo "Starting Margin… (close this window to stop it)"
python3 -m margin serve
status=$?
if [ $status -ne 0 ]; then
  echo
  echo "Margin stopped with an error (see above)."
  read -r -p "Press Enter to close."
fi
exit $status
