# DPM — Distributed Process Manager

A lightweight distributed process manager for trusted Linux clusters. DPM uses [LCM](https://lcm-proj.github.io/) multicast for real-time control and telemetry, letting operators manage, monitor, and orchestrate processes across multiple hosts from a single interface.

## Features

- **Multi-host process management** — create, start, stop, restart, move, and delete processes on any host in the cluster
- **Live telemetry** — CPU, memory, and network metrics streamed over LCM multicast
- **Dependency-aware launch files** — declarative YAML startup/shutdown with parallel wave execution
- **Resource isolation** — per-process cgroups v2 with cpuset pinning, CPU bandwidth limits, and memory caps
- **Auto-restart with backoff** — exponential backoff with configurable circuit breaker
- **Process persistence** — optionally save and restore process definitions across daemon restarts
- **Multiple interfaces** — PyQt5 GUI, full-featured CLI, or build your own UI on top of the `dpm.Client` library

## Components

- **Daemon (`dpmd`)** — runs on each host, manages local processes, publishes telemetry over LCM.
- **Client** — UI-agnostic library that subscribes to daemon telemetry and dispatches commands.
- **GUI (`dpm-gui`)** — PyQt5 desktop app with host cards, process tree, live output, and launch/shutdown dialogs.
- **CLI (`dpm`)** — scriptable command-line interface for headless servers and automation.

See [docs/architecture.md](docs/architecture.md) for the full architecture.

## Quick Start

**Prerequisites:** Python 3.10+ and the [LCM library](https://lcm-proj.github.io/).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui,dev]"

# Start daemon
DPM_CONFIG=./dpm.yaml dpmd

# Start GUI (in another terminal)
DPM_CONFIG=./dpm.yaml dpm-gui
```

To run from the repo without installing:

```bash
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpmd
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.gui.main
```

## Migration (pre-1.0 naming changes)

If you're upgrading from an earlier checkout:

- Binary `dpm-agent` renamed to `dpmd` (`python -m dpmd` also works).
- Python class `Supervisor` renamed to `Client`: `from dpm import Client`.
- Systemd unit `dpm-agent.service` renamed to `dpmd.service`.
- CLI verbs renamed (old verbs still work but emit a deprecation warning):
  `create`→`add`, `delete`→`remove`, `save`→`export`, `load`→`import`.

## Documentation

- [CLI reference](docs/cli.md) — all `dpm` subcommands
- [Configuration](docs/configuration.md) — `dpm.yaml` fields and logging
- [Process lifecycle & resource isolation](docs/processes.md) — state machine, cgroups, cpuset
- [Launch files](docs/launch-files.md) — dependency-based startup/shutdown
- [Deployment & packaging](docs/deployment.md) — `.deb` packages, systemd, message protocol
- [Architecture](docs/architecture.md) — design overview and internals

## Testing

```bash
pytest                    # unit tests (no network required)
pytest -m integration     # integration tests (requires live LCM multicast)
```

## Project Structure

```
src/
  dpm/
    client.py         # Client library — telemetry aggregation, command dispatch
    cli/              # CLI tool — scriptable interface
    gui/              # PyQt5 GUI — desktop application
    config.py         # YAML config loader (shared with dpmd)
    spec_io.py        # YAML process spec save/load
  dpmd/               # Daemon — one per host
    daemon.py         # lifecycle + state
    processes.py      # process start/stop/monitor
    commands.py       # LCM command dispatch
    telemetry.py      # publish host/proc telemetry
    cgroups.py        # cgroups v2 helpers
  dpm_msgs/           # Generated LCM message bindings
```

## License

MIT — see [LICENSE](LICENSE).

## Author

Matias Bustos SM
