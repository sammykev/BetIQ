#!/bin/sh
# data/ is a persistent volume, so the trained model, predictions and synced
# CSVs survive restarts and redeploys. Files shipped with the code (league
# CSVs, backtest metrics…) are copied in when the volume lacks them or holds
# an older copy; anything the app has written since is newer and kept.
set -e
mkdir -p data
cp -R -u -p /app/seed-data/. data/
exec "$@"
