#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"
export DJANGO_SETTINGS_MODULE=deploy.settings
export ALLOW_LIVE_SPARK=0
exec gunicorn --chdir app config.wsgi:application --bind "0.0.0.0:${PORT:-10000}" --workers 1 --threads 4 --timeout 90 --access-logfile -
