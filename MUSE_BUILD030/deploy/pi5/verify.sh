#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 NEW_OUTPUT_DIRECTORY" >&2
    exit 2
fi
case "$1" in
    /*) output=$1 ;;
    *) output=$PWD/$1 ;;
esac
cd "$(dirname "$0")/../.."
sha256sum -c SHA256SUMS.txt --quiet
exec "${PYTHON_BIN:-python3}" -m qualification_030.verify --output "$output"
