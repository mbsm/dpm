# Contributing to DPM

Thanks for contributing.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

## Coding guidelines

- Keep changes focused and minimal.
- Preserve existing architecture boundaries (node/controller/gui).
- Do not hand-edit generated files in `src/dpm_msgs/`.
- If changing `lcm/*.lcm`, regenerate bindings with `./gen-types.sh`.

## Quality checks (before PR)

```bash
source .venv/bin/activate
PYTHONPATH=src flake8 src/dpm --max-line-length=120
PYTHONPATH=src pylint src/dpm --disable=import-error
PYTHONPATH=src bandit -r src/dpm -f txt
```

If your change touches runtime behavior, run a quick smoke check by launching node and GUI with `DPM_CONFIG=./dpm.yaml`.

## Commit style

Use clear commit messages:

- `fix: ...`
- `feat: ...`
- `chore: ...`
- `docs: ...`

## Pull requests

- Describe the problem and root cause.
- Describe what changed and why.
- Include validation steps and output.
- Link related issues.

## Areas of special care

- Process start/stop semantics and process groups.
- LCM reconnection and background thread behavior.
- GUI update paths and lock usage in controller snapshots.
