#!/bin/sh
# Encode a simulator recording into an app clip (ffl-soc.6.3.9): a tap mark at each tap, 600 px wide (2x of the
# ~300 pt it shows at), H.264, no audio, faststart; plus the last frame as the reduced-motion still.
# usage: encode-clip.sh in.mov out-basename trim-start "t,x,y t,x,y ..."  (t in seconds of the recording, x/y points)
set -e
IN=$1; OUT=$2; SS=$3; TAPS=$4
D=$(dirname "$OUT")
DOT="$D/tapdot.png"
# a finger-sized mark: 44 pt at 3x = 132 px, grey fill with a darker ring, translucent
ffmpeg -v error -y -f lavfi -i "color=c=black@0.0:s=132x132,format=rgba" -frames:v 1 \
  -vf "geq=r='90':g='90':b='96':a='if(lt(hypot(X-66,Y-66),58),95,if(lt(hypot(X-66,Y-66),64),170,0))'" "$DOT"
INPUTS="-i $IN"
CHAIN="[0:v]"
n=1
for tap in $TAPS; do
  t=${tap%%,*}; rest=${tap#*,}; x=${rest%%,*}; y=${rest#*,}
  INPUTS="$INPUTS -i $DOT"
  px=$((x * 3 - 66)); py=$((y * 3 - 66))
  CHAIN="$CHAIN[$n:v]overlay=x=$px:y=$py:enable='between(t,$t,$t+0.38)'[v$n];[v$n]"
  n=$((n + 1))
done
CHAIN="${CHAIN}fps=30,trim=start=$SS,setpts=PTS-STARTPTS,scale=600:-2:flags=lanczos,format=yuv420p[out]"
# shellcheck disable=SC2086
ffmpeg -v error -y $INPUTS -filter_complex "$CHAIN" -map "[out]" -an -c:v libx264 -preset slow -crf 26 \
  -profile:v high -movflags +faststart "$OUT.mp4"
ffmpeg -v error -y -sseof -0.1 -i "$OUT.mp4" -frames:v 1 -q:v 3 "$OUT.jpg"
ls -la "$OUT.mp4" "$OUT.jpg"
