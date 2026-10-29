#!/bin/sh
# Clip 1 (ffl-soc.6.3.9): on the extension's Settings page, Allow Extension on, then espn.com -> Allow.
# Start state: the extension's page open, Allow Extension off, espn.com Ask. iPhone 17 Pro (402x874 points).
set -e
A=${AXE:-axe}   # AXe (brew install cameroncooke/axe/axe; xcodebuildmcp bundles one)
U=${SIM:?set SIM to the simulator udid (xcrun simctl list devices)}
OUT=${1:-clip-settings.mov}
rm -f "$OUT"
xcrun simctl io "$U" recordVideo --codec=h264 --force "$OUT" &
REC=$!
T0=$(perl -MTime::HiRes=time -e "printf q(%.3f), time")
TAPS=""
mark() { TAPS="$TAPS $(perl -MTime::HiRes=time -e "printf q(%.3f), time - $T0"),$1,$2"; }
sleep 2.0
mark 345 441; $A tap -x 345 -y 441 --tap-style physical --udid "$U" >/dev/null   # Allow Extension
sleep 1.6
mark 200 829; $A tap -x 200 -y 829 --udid "$U" >/dev/null                        # espn.com
sleep 1.4
mark 200 266; $A tap -x 200 -y 266 --udid "$U" >/dev/null                        # Allow
sleep 1.0
mark 37 84; $A tap -x 37 -y 84 --udid "$U" >/dev/null                          # back
sleep 2.2
kill -INT $REC
wait $REC || true
echo "taps (s since record start, x, y):$TAPS"
