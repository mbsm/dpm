# DPM — Distributed Process Manager

A lightweight distributed process manager for trusted Linux clusters. DPM uses [LCM](https://lcm-proj.github.io/) multicast for real-time control and telemetry, letting operators manage, monitor, and orchestrate processes across multiple hosts from a single interface.

## Features

- **Multi-host process management** — create, start, stop, restart, move, and delete processes on any host in the cluster
- **Live telemetry** — CPU, memory, and network metrics streamed over LCM multicast
- **Dependency-aware launch files** — declarative YAML startup/shutdown with parallel wave execution
- **Resource isolation** — per-process cgroups v2 with cpuset pinning, CPU bandwidth limits, and memory caps
- **Auto-restart with backoff** — exponential backoff with configurable circuit breaker
- **Process persistence** — optionally save and restore process definitions across agent restarts
- **Multiple interfaces** — PyQt5 GUI, full-featured CLI, or build your own client on top of the Supervisor library

## Components

- **Agent (`dpm-agent`)** — runs on each host, manages local processes, publishes telemetry over LCM.
- **Supervisor** — UI-agnostic library that subscribes to agent telemetry and dispatches commands.
- **GUI (`dpm-gui`)** — PyQt5 desktop app with host cards, process tree, live output, and launch/shutdown dialogs.
- **CLI (`dpm`)** — scriptable command-line interface for headless servers and automation.

See [docs/architecture.md](docs/architecture.md) for the full architecture.

## Quick Start

**Prerequisites:** Python 3.10+ and the [LCM library](https://lcm-proj.github.io/).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui,dev]"

# Start agent
DPM_CONFIG=./dpm.yaml dpm-agent

# Start GUI (in another terminal)
DPM_CONFIG=./dpm.yaml dpm-gui
```

To run from the repo without installing:

```bash
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.agent.agent
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.gui.main
```

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
    agent/            # Agent daemon — one per host
    supervisor/       # Supervisor library — telemetry aggregation, command dispatch
    cli/              # CLI tool — scriptable interface
    gui/              # PyQt5 GUI — desktop application
    utils/            # Shared utilities
    spec_io.py        # YAML process spec save/load
  dpm_msgs/           # Generated LCM message bindings
```

## License

MIT — see [LICENSE](LICENSE).

## Author

Matias Bustos SM
