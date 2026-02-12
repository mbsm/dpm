# Code Inspection Guide

This document defines the repeatable inspection workflow for DPM.

## Goals

- Keep process control behavior safe and predictable.
- Catch regressions in node/controller/gui contracts early.
- Maintain minimum baseline for style, quality, and security.

## Scope of each inspection

Run inspection on:
- `src/dpm/node/`
- `src/dpm/controller/`
- `src/dpm/gui/`
- `src/dpm/spec_io.py`

Include config and packaging review when behavior changes:
- `dpm.yaml`
- `packaging/systemd/dpm-node.service`
- `install.sh`

## Required static checks

```bash
source .venv/bin/activate
PYTHONPATH=src flake8 src/dpm --max-line-length=120
PYTHONPATH=src pylint src/dpm --disable=import-error
PYTHONPATH=src bandit -r src/dpm -f txt
```

## Runtime smoke checks

### Controller + Node object initialization

```bash
source .venv/bin/activate
PYTHONPATH=src python - <<'PY'
from dpm.controller.controller import Controller
from dpm.node.node import NodeAgent

Controller('dpm.yaml')
NodeAgent(config_file='dpm.yaml')
print('SMOKE_OK')
PY
```

### Manual launch check

```bash
DPM_CONFIG=./dpm.yaml dpm-node
DPM_CONFIG=./dpm.yaml dpm-gui
```

## Inspection checklist

- Process lifecycle:
  - create/start/stop/delete works
  - start_group/stop_group covers grouped and ungrouped processes
- Telemetry and outputs:
  - host info updates
  - process snapshot updates
  - output windows stream deltas without crashes
- Error handling:
  - no silent exception swallowing in hot paths
  - startup and config errors are actionable
- Logging:
  - avoid expensive string interpolation in high-frequency paths

## Reporting format

Each inspection report should include:

1. Executive summary
2. Critical findings
3. High/medium/low findings
4. Validation output snapshots
5. Suggested remediation priority

Reference report template example: `CODE_REVIEW_REPORT.md`

## Quality gates (recommended)

- Flake8: no errors
- Bandit: no medium/high severity findings
- Pylint: non-blocking, track trend over time
- Smoke test: pass

## Notes

- `src/dpm_msgs/` files are generated from `lcm/*.lcm`.
- Do not hand-edit generated message bindings.
- If LCM schema changes, regenerate with `./gen-types.sh`.
