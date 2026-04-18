# Launch Files

Launch files define a declarative dependency graph for multi-host startup and shutdown. Groups start in parallel waves resolved from their dependencies.

```yaml
name: "AGV1 Full System"
timeout: 30                  # default wait timeout per group (seconds)

# Optional: create processes before launching (skipped on shutdown)
processes:
  - name: VLP 16 Node
    host: sensor-host
    cmd: /opt/agv/bin/vlp16_node
    group: Sensors
  - name: SLAM Node
    host: perception-host
    cmd: /opt/agv/bin/slam_node
    group: Perception

# Dependency graph
groups:
  Sensors: {}                          # no deps → wave 1
  Input: {}                            # no deps → wave 1
  Simulation: {}                       # no deps → wave 1
  Perception:
    requires: [Sensors]                # hard: fails if Sensors fails
  Planning:
    requires: [Perception]             # hard chain: Sensors → Perception → Planning
    after: [Input]                     # soft: waits for Input, continues on failure
```

## Dependency types

| Directive | Behavior |
|-----------|----------|
| `requires: [A, B]` | Hard dependency + ordering. If A or B fail, this group and all dependents fail. |
| `after: [A, B]` | Soft ordering. Wait for A and B, but continue even if they fail. |

Both accept a list of group names. Groups with neither directive start immediately (wave 1). `requires` implies `after`.

## Execution model

**Launch** resolves the dependency graph via topological sort into parallel waves:

```
Wave 1: Sensors, Input, Simulation    (no dependencies — parallel)
Wave 2: Perception                    (requires Sensors — now running)
Wave 3: Planning                      (requires Perception, after Input — both ready)
```

Each wave starts all groups in parallel, then waits for every process to reach RUNNING before proceeding.

**Shutdown** reverses wave order: Planning stops first, then Perception, then Sensors/Input/Simulation in parallel.

## Failure behavior

- `requires` chain failure → all downstream groups aborted
- `after` chain failure → dependent groups continue with a warning
- Shutdown timeouts produce warnings but do not block the sequence
