#!/bin/sh
set -eu
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec "${PYTHON:-python3.12}" "$script_dir/run.py" "$@"
