#!/bin/sh
# Clip 2 (ffl-soc.6.3.9): on an espn.com page in Safari, the page menu (left of the address), then the extension.
# Start state: an espn.com page, toolbar showing, no menu open. iPhone 17 Pro (402x874 points).
set -e
A=${AXE:-axe}   # AXe (brew install cameroncooke/axe/axe; xcodebuildmcp bundles one)
U=${SIM:?set SIM to the simulator udid (xcrun simctl list devices)}
OUT=${1:-clip-safari.mov}
rm -f "$OUT"
xcrun simctl io "$U" recordVideo --codec=h264 --force "$OUT" &
REC=$!
T0=$(perl -MTime::HiRes=time -e "printf q(%.3f), time")
TAPS=""
mark() { TAPS="$TAPS $(perl -MTime::HiRes=time -e "printf q(%.3f), time - $T0"),$1,$2"; }
sleep 2.0
mark 113 815; $A tap -x 113 -y 815 --udid "$U" >/dev/null   # page menu
sleep 1.8
mark 139 692; $A tap -x 139 -y 692 --udid "$U" >/dev/null   # the extension
sleep 3.5
kill -INT $REC
wait $REC || true
echo "taps (s since record start, x, y):$TAPS"
# NOTE: not used yet: the toolbar's first tap only wakes the compact address bar, and the spike's popup never
# showed in the simulator. Record this clip with the real extension (ffl-soc.6.3.9).
