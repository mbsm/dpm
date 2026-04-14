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
from dpm.cli.wait import wait_for_proc_gone, wait_for_state, wait_for_telemetry


def _no_agents():
    print("No agents responding. Check that dpm-agent is running and LCM multicast is reachable.",
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

def cmd_status(supervisor, args) -> int:
    if not wait_for_telemetry(supervisor):
        return _no_agents()

    host_filter = args.host

    hosts = supervisor.hosts
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

    p_rows = _proc_rows(supervisor.procs, host_filter)
    if p_rows:
        print(format_table(
            ["Process@Host", "Group", "State", "PID", "CPU%", "Mem(MB)", "Runtime", "Auto"],
            p_rows,
        ))
    elif not h_rows:
        print("No processes found.")

    return 0


def cmd_hosts(supervisor, args) -> int:
    if not wait_for_telemetry(supervisor):
        return _no_agents()

    h_rows = _host_rows(supervisor.hosts)
    if h_rows:
        print(format_table(
            ["Host", "IP", "CPUs", "CPU%", "Mem%", "Interval", "Persist", "Status"],
            h_rows,
        ))
    else:
        print("No hosts found.")
    return 0


def cmd_start(supervisor, args) -> int:
    if not wait_for_telemetry(supervisor):
        return _no_agents()

    name, host = args.name, args.host
    if (host, name) not in supervisor.procs:
        print(f"Process '{name}@{host}' not found. Use 'dpm status' to see available processes.",
              file=sys.stderr)
        return 1

    supervisor.start_proc(name, host)
    confirmed = wait_for_state(supervisor, name, host, target="R")
    if confirmed:
        print(f"Started {name}@{host}")
    else:
        print(f"Start command sent to {name}@{host} (state not yet confirmed)")
    return 0


def cmd_stop(supervisor, args) -> int:
    if not wait_for_telemetry(supervisor):
        return _no_agents()

    name, host = args.name, args.host
    if (host, name) not in supervisor.procs:
        print(f"Process '{name}@{host}' not found.", file=sys.stderr)
        return 1

    supervisor.stop_proc(name, host)
    confirmed = wait_for_state(supervisor, name, host, not_target="R")
    if confirmed:
        print(f"Stopped {name}@{host}")
    else:
        print(f"Stop command sent to {name}@{host} (state not yet confirmed)")
    return 0


def cmd_restart(supervisor, args) -> int:
    if not wait_for_telemetry(supervisor):
        return _no_agents()

    name, host = args.name, args.host
    if (host, name) not in supervisor.procs:
        print(f"Process '{name}@{host}' not found.", file=sys.stderr)
        return 1

    supervisor.stop_proc(name, host)
    wait_for_state(supervisor, name, host, not_target="R", timeout=5.0)
    supervisor.start_proc(name, host)
    confirmed = wait_for_state(supervisor, name, host, target="R")
    if confirmed:
        print(f"Restarted {name}@{host}")
    else:
        print(f"Restart commands sent to {name}@{host} (state not yet confirmed)")
    return 0


def cmd_create(supervisor, args) -> int:
    name, host = args.name, args.host
    supervisor.create_proc(name, args.cmd, args.group, host, args.auto_restart, args.realtime)

    if wait_for_telemetry(supervisor):
        # Check if it appeared
        time.sleep(0.5)
        if (host, name) in supervisor.procs:
            print(f"Created {name}@{host}")
            return 0

    print(f"Create command sent for {name}@{host}")
    return 0


def cmd_delete(supervisor, args) -> int:
    if not wait_for_telemetry(supervisor):
        return _no_agents()

    name, host = args.name, args.host
    if (host, name) not in supervisor.procs:
        print(f"Process '{name}@{host}' not found.", file=sys.stderr)
        return 1

    supervisor.stop_proc(name, host)
    time.sleep(0.5)
    supervisor.del_proc(name, host)
    confirmed = wait_for_proc_gone(supervisor, name, host)
    if confirmed:
        print(f"Deleted {name}@{host}")
    else:
        print(f"Delete commands sent for {name}@{host} (not yet confirmed)")
    return 0


def cmd_start_group(supervisor, args) -> int:
    supervisor.start_group(args.group, args.host)
    print(f"Start-group sent for '{args.group}' on {args.host}")
    return 0


def cmd_stop_group(supervisor, args) -> int:
    supervisor.stop_group(args.group, args.host)
    print(f"Stop-group sent for '{args.group}' on {args.host}")
    return 0


def cmd_load(supervisor, args) -> int:
    from dpm.spec_io import load_and_create

    try:
        created, errors = load_and_create(args.path, supervisor)
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


def cmd_save(supervisor, args) -> int:
    from dpm.spec_io import save_all_process_specs

    if not wait_for_telemetry(supervisor):
        return _no_agents()

    try:
        written, skipped = save_all_process_specs(args.path, supervisor, append=args.append)
    except Exception as e:
        print(f"Failed to save: {e}", file=sys.stderr)
        return 1

    print(f"Saved {written} process specs to {args.path}" +
          (f" (skipped {skipped})" if skipped else ""))
    return 0


def cmd_start_all(supervisor, args) -> int:
    if not wait_for_telemetry(supervisor):
        return _no_agents()

    procs = supervisor.procs
    count = 0
    for (host, name) in sorted(procs.keys()):
        supervisor.start_proc(name, host)
        count += 1

    print(f"Start sent to {count} processes")
    return 0


def cmd_stop_all(supervisor, args) -> int:
    if not wait_for_telemetry(supervisor):
        return _no_agents()

    procs = supervisor.procs
    count = 0
    for (host, name) in sorted(procs.keys()):
        supervisor.stop_proc(name, host)
        count += 1

    print(f"Stop sent to {count} processes")
    return 0


def cmd_set_persistence(supervisor, args) -> int:
    enabled = args.mode == "on"
    host = args.host

    if host:
        if not wait_for_telemetry(supervisor):
            return _no_agents()
        if host not in supervisor.hosts:
            available = ", ".join(sorted(supervisor.hosts.keys()))
            print(f"Host '{host}' not responding. Available: {available}", file=sys.stderr)
            return 1
        supervisor.set_persistence(host, enabled)
        print(f"Persistence {'enabled' if enabled else 'disabled'} on {host}")
    else:
        supervisor.set_persistence("", enabled)
        print(f"Persistence {'enabled' if enabled else 'disabled'} on all agents")
    return 0


def cmd_set_interval(supervisor, args) -> int:
    seconds = args.seconds
    if seconds < 0.05:
        print("Interval must be >= 0.05 seconds.", file=sys.stderr)
        return 1

    host = args.host
    if host:
        # Targeted: send to specific host
        if not wait_for_telemetry(supervisor):
            return _no_agents()
        if host not in supervisor.hosts:
            available = ", ".join(sorted(supervisor.hosts.keys()))
            print(f"Host '{host}' not responding. Available: {available}", file=sys.stderr)
            return 1
        supervisor.set_interval(host, seconds)
        print(f"Set interval to {seconds}s on {host}")
    else:
        # Broadcast: send with empty hostname (all agents)
        supervisor.set_interval("", seconds)
        print(f"Set interval to {seconds}s on all agents")
    return 0


def cmd_move(supervisor, args) -> int:
    if not wait_for_telemetry(supervisor):
        return _no_agents()

    src_name, src_host = args.src_name, args.src_host
    dst_name, dst_host = args.dst_name, args.dst_host

    # Validate source exists
    src_key = (src_host, src_name)
    src_proc = supervisor.procs.get(src_key)
    if src_proc is None:
        print(f"Process '{src_name}@{src_host}' not found.", file=sys.stderr)
        return 1

    # Validate destination host is reachable
    if dst_host not in supervisor.hosts:
        available = ", ".join(sorted(supervisor.hosts.keys()))
        print(f"Destination host '{dst_host}' not responding. Available: {available}",
              file=sys.stderr)
        return 1

    # Check if destination already has a process with that name
    if (dst_host, dst_name) in supervisor.procs:
        print(f"Process '{dst_name}@{dst_host}' already exists. Delete it first or use a different name.",
              file=sys.stderr)
        return 1

    # Read the spec from the source process
    exec_command = getattr(src_proc, "exec_command", "")
    group = getattr(src_proc, "group", "")
    auto_restart = bool(getattr(src_proc, "auto_restart", False))
    realtime = bool(getattr(src_proc, "realtime", False))
    was_running = getattr(src_proc, "state", "") == "R"

    label = f"{src_name}@{src_host} -> {dst_name}@{dst_host}"

    # Step 1: Stop on source if running
    if was_running:
        print(f"Stopping {src_name}@{src_host}...")
        supervisor.stop_proc(src_name, src_host)
        if not wait_for_state(supervisor, src_name, src_host, not_target="R", timeout=5.0):
            print(f"Failed to stop {src_name}@{src_host}. Move aborted.", file=sys.stderr)
            return 1

    # Step 2: Create on destination
    print(f"Creating {dst_name}@{dst_host}...")
    supervisor.create_proc(dst_name, exec_command, group, dst_host, auto_restart, realtime)
    time.sleep(1.5)  # wait for agent to process + next telemetry broadcast

    # Verify it appeared
    if (dst_host, dst_name) not in supervisor.procs:
        # Rollback: restart on source if it was running
        print(f"Failed to create on {dst_host}. Rolling back...", file=sys.stderr)
        if was_running:
            supervisor.start_proc(src_name, src_host)
        return 1

    # Step 3: Start on destination if source was running
    if was_running:
        print(f"Starting {dst_name}@{dst_host}...")
        supervisor.start_proc(dst_name, dst_host)
        if not wait_for_state(supervisor, dst_name, dst_host, target="R"):
            print(f"Warning: start on {dst_host} not confirmed, but definition was created.", file=sys.stderr)

    # Step 4: Delete from source
    print(f"Removing {src_name}@{src_host}...")
    supervisor.del_proc(src_name, src_host)

    print(f"Moved {label}")
    return 0


def cmd_launch(supervisor, args) -> int:
    from dpm.cli.launch import run_launch

    if not wait_for_telemetry(supervisor):
        return _no_agents()

    return run_launch(supervisor, args.path, reverse=False)


def cmd_shutdown(supervisor, args) -> int:
    from dpm.cli.launch import run_launch

    if not wait_for_telemetry(supervisor):
        return _no_agents()

    return run_launch(supervisor, args.path, reverse=True)


def cmd_logs(supervisor, args) -> int:
    name = args.name
    host = args.host

    if not wait_for_telemetry(supervisor):
        return _no_agents()

    # Resolve host if not provided
    if host is None:
        matches = [(h, n) for (h, n) in supervisor.procs if n == name]
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

    print(f"Streaming output for {name}@{host} (Ctrl+C to stop)...\n")

    last_gen = 0
    last_len = 0
    try:
        while True:
            gen, text, reset, cur_len = supervisor.get_proc_output_delta(
                name, last_gen, last_len
            )
            if text:
                sys.stdout.write(text)
                sys.stdout.flush()
            last_gen = gen
            last_len = cur_len
            time.sleep(0.2)
    except KeyboardInterrupt:
        print()  # clean newline after ^C
    return 0
