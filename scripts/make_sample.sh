#!/usr/bin/env sh
# Generate a 10 s synthetic test clip (no licensing concerns). Usage: scripts/make_sample.sh [out.mp4]
set -eu
OUT="${1:-sample.mp4}"
ffmpeg -y -v error -f lavfi -i "testsrc=duration=10:size=320x240:rate=25" \
  -f lavfi -i "sine=frequency=440:duration=10" -c:v mpeg4 -c:a aac -shortest "$OUT"
echo "wrote $OUT"
