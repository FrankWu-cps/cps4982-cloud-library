#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"
export DJANGO_SETTINGS_MODULE=deploy.settings
export ALLOW_LIVE_SPARK=0
python -m pip install -r requirements-web.txt
python scripts/deploy_restore.py
python app/manage.py collectstatic --noinput
python app/manage.py check --deploy
