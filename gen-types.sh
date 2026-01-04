#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

# Generate Python bindings into ./src so "import dpm_msgs" works with PYTHONPATH=src
cd "$ROOT/src"
lcm-gen -p ../lcm/*.lcm