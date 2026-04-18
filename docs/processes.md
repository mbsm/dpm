# Process Lifecycle & Resource Isolation

## State machine

```
                    ┌──────────────────────────────────┐
                    │                                  │
  create ──► READY ──► start ──► RUNNING               │
               ▲                   │                   │
               │         ┌─────────┼──────────┐        │
               │         ▼         ▼          ▼        │
               │     exit(0)   exit(!=0)   SIGKILL     │
               │         │         │          │        │
               │         ▼         ▼          ▼        │
               └──── READY      FAILED     KILLED      │
                                  │                    │
                                  ├── auto_restart ────┘
                                  │   (with backoff)
                                  │
                                  └── max_restarts exceeded
                                          │
                                          ▼
                                      SUSPENDED
                                          │
                                          └── manual start ──► RUNNING
```

| State | Code | Description |
|-------|------|-------------|
| **READY** | `T` | Created or cleanly stopped (exit code 0 or graceful stop) |
| **RUNNING** | `R` | Process is alive |
| **FAILED** | `F` | Exited with non-zero code or failed to start |
| **KILLED** | `K` | Forcefully killed (SIGKILL after stop timeout) |
| **SUSPENDED** | `S` | Auto-restart attempts exhausted (circuit breaker tripped) |

Auto-restart triggers only on FAILED with exponential backoff (1 s → 2 s → 4 s → … capped at 60 s). The backoff counter resets on clean exit (code 0). When `max_restarts` is configured and exceeded, the process enters SUSPENDED. A manual `dpm start` clears the counter and resumes normal operation.

## Resource isolation

Processes can be assigned dedicated CPU cores and resource limits using cgroups v2:

```bash
dpm create slam@jet1 --cmd "slam-node" \
    --cpuset 2,3 \
    --isolated \
    --cpu-limit 2.0 \
    --mem-limit 4294967296
```

| Flag | cgroup control | Example | Description |
|------|----------------|---------|-------------|
| `--cpuset` | `cpuset.cpus` | `0,1` | Pin to specific cores |
| `--isolated` | `cpuset.cpus.partition` | — | Reserve cores exclusively (no sharing) |
| `--cpu-limit` | `cpu.max` | `1.5` | CPU bandwidth limit in cores |
| `--mem-limit` | `memory.max` | `4294967296` | Memory limit in bytes (4 GB) |

### CPU isolation

The `--isolated` flag reserves cores exclusively for a process:

- **CPU affinity** — the process is pinned to the specified cores via `cpuset.cpus`
- **Exclusive reservation** — no other isolated process can claim the same cores (validated at start time)
- **Realtime scheduling** — combine with `--realtime` for `SCHED_FIFO` priority on the pinned cores

This provides low-preemption execution suitable for latency-sensitive workloads such as motion controllers or localization nodes.

For full kernel-level scheduler isolation, add `isolcpus=<cores>` to the kernel command line (e.g., `isolcpus=20-23`). DPM's cpuset affinity then ensures only your processes run on those reserved cores.

The daemon creates cgroup directories under `/sys/fs/cgroup/dpm/<process>/` and cleans them up on stop/delete.

**Requirements:** cgroups v2 unified hierarchy and `Delegate=yes` in the systemd service unit (included in the `dpmd` package). On development machines without cgroup access, processes run without limits (graceful degradation).
