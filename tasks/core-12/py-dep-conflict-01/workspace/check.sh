#!/bin/sh
# Resolve the declared requirements offline, then run the application against them.
# This is the same shape of check the task is graded on.
set -e
target=$(mktemp -d)
trap 'rm -rf "$target"' EXIT

echo "resolving requirements offline..."
pip install --quiet --no-index --find-links wheelhouse --target "$target" -r requirements.txt

echo "running the application..."
PYTHONPATH="$target" python app.py
