#!/usr/bin/env bash
# Assemble the Hugging Face Space: the backend, the legacy CSVs it trains on,
# the Dockerfile at the root (where Spaces look for it) and the Space card.
#   bash deploy/huggingface/build-space.sh OUT_DIR
set -euo pipefail
cd "$(dirname "$0")/../.."
out="${1:?usage: build-space.sh OUT_DIR}"

rm -rf "$out"
mkdir -p "$out"
git ls-files backend epl-final.csv 'champions-league-*.csv' \
  | grep -v '^backend/tests/' \
  | while read -r f; do mkdir -p "$out/$(dirname "$f")"; cp -p "$f" "$out/$f"; done
cp backend/Dockerfile "$out/Dockerfile"
cp deploy/huggingface/space-README.md "$out/README.md"

echo "Space assembled in $out: $(find "$out" -type f | wc -l) files, $(du -sh "$out" | cut -f1)"
