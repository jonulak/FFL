#!/bin/sh
# Renders ffl-icon.svg into every icon the extension and its Safari wrapper ship (outputs committed; re-run after
# changing the SVG): the extension's toolbar/store icons, the app's AppIcon (iOS 1024 opaque, macOS 16-1024), the
# launch screen's LargeIcon and the container page's Icon.png. Needs rsvg-convert and magick (Homebrew).
set -e
cd "$(dirname "$0")"
SVG=ffl-icon.svg
APP="../spike-safari/FFL Cookie Spike/Shared (App)"
png() { rsvg-convert -w "$1" -h "$1" "$SVG" | magick - -alpha off "$2"; }   # opaque: App Store refuses an alpha icon

mkdir -p ../ffl/src/icons
for s in 16 32 48 128; do png $s "../ffl/src/icons/icon-$s.png"; done

SET="$APP/Assets.xcassets/AppIcon.appiconset"
png 1024 "$SET/icon-1024.png"
mac=""
for s in 16 32 128 256 512; do
  png $s "$SET/mac-$s.png"
  png $((s * 2)) "$SET/mac-$s@2x.png"
  mac="$mac{\"idiom\":\"mac\",\"scale\":\"1x\",\"size\":\"${s}x$s\",\"filename\":\"mac-$s.png\"},"
  mac="$mac{\"idiom\":\"mac\",\"scale\":\"2x\",\"size\":\"${s}x$s\",\"filename\":\"mac-$s@2x.png\"},"
done
printf '{"images":[{"idiom":"universal","platform":"ios","size":"1024x1024","filename":"icon-1024.png"},%s],"info":{"author":"xcode","version":1}}\n' \
  "${mac%,}" > "$SET/Contents.json"

LARGE="$APP/Assets.xcassets/LargeIcon.imageset"
for x in 1 2 3; do png $((128 * x)) "$LARGE/large@${x}x.png"; done
printf '{"images":[{"idiom":"universal","scale":"1x","filename":"large@1x.png"},{"idiom":"universal","scale":"2x","filename":"large@2x.png"},{"idiom":"universal","scale":"3x","filename":"large@3x.png"}],"info":{"author":"xcode","version":1}}\n' \
  > "$LARGE/Contents.json"

png 384 "$APP/Resources/Icon.png"
echo "icons written"
