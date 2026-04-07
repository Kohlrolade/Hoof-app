#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python -m uvicorn app:app --reload
