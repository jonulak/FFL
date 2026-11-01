#!/bin/sh
# Builds the extension's files (extension/ffl/dist/safari) before Xcode bundles them (the iOS extension target's first
# phase). The server is chosen in the extension's popup now, so the build bakes none in. The app's Info.plist FFLOrigin
# ($(FFL_ORIGIN:default=$(FFL_ORIGIN_DEFAULT))) is only the "Open FFL" button's default.
set -e
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # Xcode's scripts run with a bare PATH: node is Homebrew's
cd "$SRCROOT/../../ffl"
node build.mjs --target safari
