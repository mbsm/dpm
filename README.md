# DPM — Distributed Process Manager

A lightweight distributed process manager for trusted Linux clusters, written in Python.
DPM runs a small daemon (`dpmd`) on each host and exposes a scriptable command-line
interface (`dpm`) for managing processes across the cluster from a single terminal.
Real-time control and telemetry travel over [LCM](https://lcm-proj.github.io/) multicast.

DPM is **CLI-first**. Operators drive the system from the shell; automation drives
it from scripts. A PyQt5 GUI is available as an optional convenience and is not
required to use any feature.

## Features

- **Multi-host process control** — add, start, stop, restart, move, and remove processes on any host in the cluster.
- **Live telemetry** — CPU, memory, and network metrics streamed over LCM multicast.
- **Dependency-aware launch files** — declarative YAML startup and shutdown with parallel wave execution.
- **Resource isolation** — per-process cgroups v2 with cpuset pinning, CPU bandwidth limits, and memory caps.
- **Auto-restart with backoff** — exponential backoff with a configurable circuit breaker.
- **Process persistence** — optionally save and restore process definitions across daemon restarts.
- **Embeddable client library** — build custom tooling on top of `dpm.Client` (Python).

## Architecture

- **`dpmd`** — the daemon. One per host. Manages local processes, publishes telemetry, receives commands.
- **`dpm`** — the command-line interface. The primary way to operate the system.
- **`dpm.Client`** — the Python client library underlying `dpm`. Use it directly when building bespoke automation.
- **`dpm-gui`** *(optional)* — a PyQt5 desktop app for visual monitoring. Built on top of `dpm.Client`; could live in a separate package.

See [docs/architecture.md](docs/architecture.md) for internals and the LCM message protocol.

## Requirements

- Python 3.10+
- [LCM](https://lcm-proj.github.io/) (the C library and its Python bindings)
- Linux with cgroups v2 (optional, for resource isolation)
- PyQt5 (optional, only for the GUI)

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"          # core + test tooling
pip install -e ".[gui,dev]"      # also install the GUI
```

Or run from the repo without installing:

```bash
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpmd
PYTHONPATH=src DPM_CONFIG=./dpm.yaml python -m dpm.cli.cli status
```

## Quick start

Start the daemon on each host:

```bash
DPM_CONFIG=/etc/dpm/dpm.yaml dpmd
```

Then, from any machine that can reach the LCM multicast group:

```bash
dpm status                                      # hosts and processes
dpm add camera@jet1 --cmd "cam-node" \
    -g perception --auto-restart                # register a process
dpm start camera@jet1                           # run it
dpm logs camera@jet1                            # stream its output
dpm export snapshot.yaml                        # save current state
```

Run `dpm --help` or `dpm <command> --help` for the full reference, or see
[docs/cli.md](docs/cli.md).

## Using the library

The `dpm.Client` class is the Python API that `dpm` itself is built on. Use it to
script higher-level workflows or embed DPM in a larger application:

```python
from dpm import Client

client = Client("/etc/dpm/dpm.yaml")
client.start()
try:
    client.start_proc("camera", "jet1")
    # ... your logic ...
finally:
    client.stop()
```

## GUI (optional)

A PyQt5 GUI is provided for interactive monitoring with host cards, a process tree,
live output, and launch/shutdown dialogs. It is an optional convenience layered on
top of `dpm.Client` and is not required for any CLI workflow.

```bash
pip install -e ".[gui]"
DPM_CONFIG=./dpm.yaml dpm-gui
```

## Migration (pre-1.0 renames)

If you are upgrading from an earlier checkout, note the following one-time
renames (no compatibility shims are provided):

- Binary: `dpm-agent` → `dpmd`.
- Systemd unit: `dpm-agent.service` → `dpmd.service`.
- Python class: `Supervisor` → `Client` (`from dpm import Client`).
- CLI verbs: `create` → `add`, `delete` → `remove`, `save` → `export`, `load` → `import`.
- LCM channel: `proc_outputs_channel` → `log_chunks_channel` (and the
  `proc_output_t` type was replaced by `log_chunk_t`). The `dpm logs`
  command now reads from on-disk per-process log files
  (`/var/log/dpm/processes/<name>.log`) by default; pass `--follow` to
  subscribe for live output. Live output is silent on the wire unless
  a client is actively subscribed.

## Documentation

- [CLI reference](docs/cli.md) — every `dpm` subcommand
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

## Project layout

```
src/
  dpm/                 # client library, CLI, GUI
    client.py          # Client — telemetry aggregation, command dispatch
    cli/               # CLI entry point and command handlers
    gui/               # optional PyQt5 GUI
    config.py          # YAML config loader (shared with dpmd)
    spec_io.py         # YAML process spec import/export
  dpmd/                # daemon — one process per host
    daemon.py          # lifecycle and state
    processes.py       # process start/stop/monitor
    commands.py        # LCM command dispatch
    telemetry.py       # publish host and process telemetry
    cgroups.py         # cgroups v2 helpers
  dpm_msgs/            # generated LCM message bindings
```

## License

MIT — see [LICENSE](LICENSE).

## Author

Matias Bustos SM
