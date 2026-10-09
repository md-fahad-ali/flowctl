#!/bin/sh
# Render the HTML designs in this folder to PNGs in ../img (needs Google Chrome).
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
cd "$(dirname "$0")"
for spec in hero:1600x640 workflow:1600x680 proof:1600x650 filmstrip:1600x560; do
  n=${spec%%:*}; sz=${spec##*:}; w=${sz%x*}; h=${sz#*x}
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=1 \
    --window-size=$w,$h --screenshot="../img/$n.png" "file://$PWD/$n.html" >/dev/null 2>&1
  echo "rendered $n.png"
done
