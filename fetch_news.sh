#!/bin/bash
# fetch_news.sh - Public RSS news fetch wrapper

cd "$(dirname "$0")"
python3 lib/fetch_news.py "$@"
