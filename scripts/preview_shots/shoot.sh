#!/bin/sh
# PAY-9's screenshots in the preview (scripts/preview_docs.py's evidence): each page here shot in headless Chrome, as
# `proof.py shots` shoots a plan row. CHROME=<path> picks the browser.
set -eu
cd "$(dirname "$0")"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
profile=$(mktemp -d)
trap 'rm -rf "$profile"' EXIT
for page in before sent preview sending; do
  "$CHROME" --headless=new --no-first-run --no-default-browser-check --user-data-dir="$profile" --hide-scrollbars \
    --force-device-scale-factor=1 --window-size=800,420 --screenshot="$PWD/$page.png" "file://$PWD/$page.html" \
    >/dev/null 2>&1
done
