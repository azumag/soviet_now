#!/bin/bash
# fetch_market.sh - Exchange rate fetch wrapper

cd "$(dirname "$0")"
python3 lib/fetch_market.py "$@"
