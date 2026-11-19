#!/usr/bin/env bash
set -o errexit -o nounset -o pipefail
python -m pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py check --deploy --fail-level WARNING
# Intentionally no migrate, discovery, SMTP, cron or data import.
