#!/bin/sh
# Xcode Cloud, after the clone: Node and the extension's packages, which the iOS extension's first phase
# (../ffl-origin.sh) needs, and the Safari build itself, which the macOS extension bundles without building it
# (only the iOS target runs that phase). No FFL_ORIGIN here: Release builds for the live site.
set -e
brew install node pnpm
cd "$CI_PRIMARY_REPOSITORY_PATH/extension/ffl"
pnpm install --frozen-lockfile
node build.mjs --target safari
