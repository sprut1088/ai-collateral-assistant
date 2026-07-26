#!/usr/bin/env bash
# Prereq: set ANTHROPIC_API_KEY in backend/.env (or export it in your shell)
# Behind a corporate TLS proxy? Also: export ACOA_USE_TRUSTSTORE=1
cd "$(dirname "$0")/backend"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
