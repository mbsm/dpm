"""Command handlers for the DPM CLI. Each returns an int exit code."""

import sys
import time

from dpm.cli.formatting import (
    format_bool,
    format_cpu,
    format_host_status,
    format_mem_mb,
    format_pid,
    format_runtime,
    format_state,
    format_table,
)
from dpm.cli.wait import (
    wait_for_proc_gone,
    wait_for_proc_present,
    wait_for_state,
    wait_for_telemetry,
)


def _no_daemons():
    print("No daemons responding. Check that dpmd is running and LCM multicast is reachable.",
          file=sys.stderr)
    return 2


def _host_rows(hosts, host_filter=None):
    """Build rows for the hosts table."""
    rows = []
    for hostname, info in sorted(hosts.items()):
        if host_filter and hostname != host_filter:
            continue
        ts = getattr(info, "timestamp", 0) or 0
        interval = getattr(info, "report_interval", 0.0) or 0.0
        persist = getattr(info, "persist", False)
        rows.append([
            hostname,
            getattr(info, "ip", "") or "",
            str(getattr(info, "cpus", 0) or 0),
            format_cpu(getattr(info, "cpu_usage", 0.0) or 0.0),
            f"{(getattr(info, 'mem_usage', 0.0) or 0.0) * 100:.0f}%",
            f"{interval:.0f}s" if interval > 0 else "-",
            format_bool(persist),
            format_host_status(ts),
        ])
    return rows


def _proc_rows(procs, host_filter=None):
    """Build rows for the processes table."""
    rows = []
    for (host, name), info in sorted(procs.items()):
        if host_filter and host != host_filter:
            continue
        rows.append([
            f"{name}@{host}",
            getattr(info, "group", "") or "",
            format_state(getattr(info, "state", "")),
            format_pid(getattr(info, "pid", -1)),
            format_cpu(getattr(info, "cpu", 0.0)),
            format_mem_mb(getattr(info, "mem_rss", 0)),
            format_runtime(getattr(info, "runtime", -1)),
            format_bool(getattr(info, "auto_restart", False)),
        ])
    return rows


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status(client, args) -> int:
    if not wait_for_telemetry(client):
        return _no_daemons()

    host_filter = args.host

    hosts = client.hosts
    if host_filter and host_filter not in hosts:
        available = ", ".join(sorted(hosts.keys()))
        print(f"Host '{host_filter}' not found. Available: {available}", file=sys.stderr)
        return 1

    h_rows = _host_rows(hosts, host_filter)
    if h_rows:
        print(format_table(
            ["Host", "IP", "CPUs", "CPU%", "Mem%", "Interval", "Persist", "Status"],
            h_rows,
        ))
        print()

    p_rows = _proc_rows(client.procs, host_filter)
    if p_rows:
        print(format_table(
            ["Process@Host", "Group", "State", "PID", "CPU%", "Mem(MB)", "Runtime", "Auto"],
            p_rows,
        ))
    elif not h_rows:
        print("No processes found.")

    return 0


def cmd_hosts(client, args) -> int:
    if not wait_for_telemetry(client):
        return _no_daemons()

    h_rows = _host_rows(client.hosts)
    if h_rows:
        print(format_table(
            ["Host", "IP", "CPUs", "CPU%", "Mem%", "Interval", "Persist", "Status"],
            h_rows,
        ))
    else:
        print("No hosts found.")
    return 0


def _require_proc(client, args):
    """Common preamble: wait for telemetry, validate process exists.

    Returns (name, host) on success, or None and prints an error.
    Polls briefly for the proc to appear — tolerates the case where the
    caller just `add`-ed it and the host_procs telemetry hasn't landed yet.
    """
    if not wait_for_telemetry(client):
        _no_daemons()
        return None
    name, host = args.name, args.host
    if not wait_for_proc_present(client, name, host, timeout=3.0):
        print(f"Process '{name}@{host}' not found.", file=sys.stderr)
        return None
    return name, host


def cmd_start(client, args) -> int:
    result = _require_proc(client, args)
    if result is None:
        return 1
    name, host = result

    client.start_proc(name, host)
    confirmed = wait_for_state(client, name, host, target="R")
    if confirmed:
        print(f"Started {name}@{host}")
    else:
        print(f"Start command sent to {name}@{host} (state not yet confirmed)")
    return 0


def cmd_stop(client, args) -> int:
    result = _require_proc(client, args)
    if result is None:
        return 1
    name, host = result

    client.stop_proc(name, host)
    confirmed = wait_for_state(client, name, host, not_target="R")
    if confirmed:
        print(f"Stopped {name}@{host}")
    else:
        print(f"Stop command sent to {name}@{host} (state not yet confirmed)")
    return 0


def cmd_restart(client, args) -> int:
    result = _require_proc(client, args)
    if result is None:
        return 1
    name, host = result

    client.stop_proc(name, host)
    wait_for_state(client, name, host, not_target="R", timeout=5.0)
    client.start_proc(name, host)
    confirmed = wait_for_state(client, name, host, target="R")
    if confirmed:
        print(f"Restarted {name}@{host}")
    else:
        print(f"Restart commands sent to {name}@{host} (state not yet confirmed)")
    return 0


def cmd_add(client, args) -> int:
    name, host = args.name, args.host
    client.create_proc(
        name, args.cmd, args.group, host, args.auto_restart, args.realtime,
        rt_priority=args.rt_priority,
        work_dir=args.work_dir, cpuset=args.cpuset,
        cpu_limit=args.cpu_limit, mem_limit=args.mem_limit,
        isolated=args.isolated,
    )

    if wait_for_telemetry(client):
        confirmed = wait_for_state(client, name, host, target="T", timeout=3.0)
        if confirmed:
            print(f"Created {name}@{host}")
            return 0

    print(f"Create command sent for {name}@{host}")
    return 0


def cmd_remove(client, args) -> int:
    result = _require_proc(client, args)
    if result is None:
        return 1
    name, host = result

    client.stop_proc(name, host)
    wait_for_state(client, name, host, not_target="R", timeout=3.0)
    client.del_proc(name, host)
    confirmed = wait_for_proc_gone(client, name, host)
    if confirmed:
        print(f"Deleted {name}@{host}")
    else:
        print(f"Delete commands sent for {name}@{host} (not yet confirmed)")
    return 0


def cmd_start_group(client, args) -> int:
    client.start_group(args.group, args.host)
    print(f"Start-group sent for '{args.group}' on {args.host}")
    return 0


def cmd_stop_group(client, args) -> int:
    client.stop_group(args.group, args.host)
    print(f"Stop-group sent for '{args.group}' on {args.host}")
    return 0


def cmd_import(client, args) -> int:
    from dpm.spec_io import load_and_create

    try:
        created, errors = load_and_create(args.path, client)
    except FileNotFoundError:
        print(f"File not found: {args.path}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Failed to load specs: {e}", file=sys.stderr)
        return 1

    for name in created:
        print(f"  Created {name}")
    for spec, err in errors:
        spec_name = (spec or {}).get("name", "<unknown>")
        print(f"  Error: {spec_name}: {err}", file=sys.stderr)

    total = len(created) + len(errors)
    print(f"Loaded {len(created)}/{total} process specs from {args.path}")
    return 1 if errors else 0


def cmd_export(client, args) -> int:
    from dpm.spec_io import save_all_process_specs

    if not wait_for_telemetry(client):
        return _no_daemons()

    try:
        written, skipped = save_all_process_specs(args.path, client, append=args.append)
    except Exception as e:
        print(f"Failed to save: {e}", file=sys.stderr)
        return 1

    print(f"Saved {written} process specs to {args.path}" +
          (f" (skipped {skipped})" if skipped else ""))
    return 0


def _broadcast_proc_action(client, verb: str, action_fn) -> int:
    """Send *action_fn* to every (host, name) pair. Returns exit code."""
    if not wait_for_telemetry(client):
        return _no_daemons()

    count = 0
    for (host, name) in sorted(client.procs.keys()):
        action_fn(name, host)
        count += 1

    print(f"{verb} sent to {count} processes")
    return 0


def cmd_start_all(client, args) -> int:
    return _broadcast_proc_action(client, "Start", client.start_proc)


def cmd_stop_all(client, args) -> int:
    return _broadcast_proc_action(client, "Stop", client.stop_proc)


def cmd_set_persistence(client, args) -> int:
    enabled = args.mode == "on"
    host = args.host

    if host:
        if not wait_for_telemetry(client):
            return _no_daemons()
        if host not in client.hosts:
            available = ", ".join(sorted(client.hosts.keys()))
            print(f"Host '{host}' not responding. Available: {available}", file=sys.stderr)
            return 1
        client.set_persistence(host, enabled)
        print(f"Persistence {'enabled' if enabled else 'disabled'} on {host}")
    else:
        client.set_persistence("", enabled)
        print(f"Persistence {'enabled' if enabled else 'disabled'} on all agents")
    return 0


def cmd_set_interval(client, args) -> int:
    seconds = args.seconds
    if seconds < 0.05:
        print("Interval must be >= 0.05 seconds.", file=sys.stderr)
        return 1

    host = args.host
    if host:
        # Targeted: send to specific host
        if not wait_for_telemetry(client):
            return _no_daemons()
        if host not in client.hosts:
            available = ", ".join(sorted(client.hosts.keys()))
            print(f"Host '{host}' not responding. Available: {available}", file=sys.stderr)
            return 1
        client.set_interval(host, seconds)
        print(f"Set interval to {seconds}s on {host}")
    else:
        # Broadcast: send with empty hostname (all agents)
        client.set_interval("", seconds)
        print(f"Set interval to {seconds}s on all agents")
    return 0


def cmd_move(client, args) -> int:
    if not wait_for_telemetry(client):
        return _no_daemons()

    from dpm.operations import StdoutProgress, move_process

    ok, message = move_process(
        client,
        args.src_name, args.src_host,
        args.dst_name, args.dst_host,
        progress=StdoutProgress(),
    )
    if ok:
        print(message)
        return 0
    print(message, file=sys.stderr)
    return 1


def _run_launch_script(client, path: str, reverse: bool) -> int:
    from dpm.operations import StdoutProgress, parse_launch_file, run_launch

    if not wait_for_telemetry(client):
        return _no_daemons()

    try:
        script = parse_launch_file(path)
    except (OSError, ValueError) as e:
        print(f"Invalid launch file: {e}", file=sys.stderr)
        return 1

    ok, message = run_launch(client, script, reverse=reverse, progress=StdoutProgress())
    if message:
        print(f"\n{message}")
    return 0 if ok else 1


def cmd_launch(client, args) -> int:
    return _run_launch_script(client, args.path, reverse=False)


def cmd_shutdown(client, args) -> int:
    return _run_launch_script(client, args.path, reverse=True)


_SINCE_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_since(spec: str) -> int:
    """Parse a 'since' shorthand (10s, 30m, 2h, 1d) into a µs-since-epoch lower bound.

    Empty string returns 0 (no filter). Invalid inputs raise ValueError.
    """
    if not spec:
        return 0
    spec = spec.strip().lower()
    unit = spec[-1]
    if unit not in _SINCE_UNIT_SECONDS:
        raise ValueError(f"--since must end in s/m/h/d (got {spec!r})")
    try:
        magnitude = float(spec[:-1])
    except ValueError as e:
        raise ValueError(f"--since: bad number in {spec!r}") from e
    delta_s = magnitude * _SINCE_UNIT_SECONDS[unit]
    return int((time.time() - delta_s) * 1_000_000)


def cmd_logs(client, args) -> int:
    name = args.name
    host = args.host

    if not wait_for_telemetry(client):
        return _no_daemons()

    # Resolve host if not provided
    if host is None:
        matches = [(h, n) for (h, n) in client.procs if n == name]
        if len(matches) == 0:
            print(f"Process '{name}' not found. Use 'dpm status' to see available processes.",
                  file=sys.stderr)
            return 1
        if len(matches) > 1:
            hosts_str = ", ".join(f"{name}@{h}" for h, _ in matches)
            print(f"Process '{name}' exists on multiple hosts: {hosts_str}\n"
                  f"Specify the host: dpm logs {name}@<host>", file=sys.stderr)
            return 1
        host = matches[0][0]

    try:
        since_us = _parse_since(args.since)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # --persistent walks rotated history — meaningful only when no tail cap.
    tail = 0 if args.persistent else args.tail

    history = client.read_log(
        name, host, since_us=since_us, tail_lines=tail, timeout=5.0,
    )
    if history:
        sys.stdout.write(history)
        if not history.endswith("\n"):
            sys.stdout.write("\n")
        sys.stdout.flush()

    if not args.follow:
        return 0

    # Live tail: keep the subscription warm with periodic renewals.
    # On Ctrl+C the subscription expires naturally on the daemon side.
    print(f"--- following {name}@{host} (Ctrl+C to stop) ---", file=sys.stderr)
    last_gen = 0
    last_len = 0
    next_renew = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_renew:
                client.subscribe_output(name, host, ttl_seconds=5)
                next_renew = now + 2.0
            gen, text, _reset, cur_len = client.get_proc_output_delta(
                name, last_gen, last_len
            )
            if text:
                sys.stdout.write(text)
                sys.stdout.flush()
            last_gen = gen
            last_len = cur_len
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()
    return 0
