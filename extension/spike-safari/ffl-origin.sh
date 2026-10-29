#!/bin/sh
# Builds the extension's five files (extension/ffl/dist/safari) for this build's FFL server, before Xcode bundles them
# (the iOS extension target's first phase). The server: FFL_ORIGIN from the environment
# (`FFL_ORIGIN=https://... xcodebuild ...`, or `launchctl setenv FFL_ORIGIN ...` before opening Xcode), else the
# configuration's FFL_ORIGIN_DEFAULT build setting: Debug the dev test server, Release the live site. The app reads the
# same choice from its Info.plist (FFLOrigin = $(FFL_ORIGIN:default=$(FFL_ORIGIN_DEFAULT))), so the two never disagree.
set -e
ORIGIN="${FFL_ORIGIN:-$FFL_ORIGIN_DEFAULT}"
[ -n "$ORIGIN" ] || { echo "error: no FFL_ORIGIN and no FFL_ORIGIN_DEFAULT build setting"; exit 1; }
echo "FFL origin: $ORIGIN"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # Xcode's scripts run with a bare PATH: node is Homebrew's
cd "$SRCROOT/../../ffl"
node build.mjs --target safari --origin "$ORIGIN"
