"""DPM command-line interface — entry point."""

import argparse
import os
import signal
import sys

from dpm.cli.commands import (
    cmd_add,
    cmd_export,
    cmd_hosts,
    cmd_import,
    cmd_launch,
    cmd_logs,
    cmd_move,
    cmd_remove,
    cmd_restart,
    cmd_set_interval,
    cmd_set_persistence,
    cmd_shutdown,
    cmd_start,
    cmd_start_all,
    cmd_start_group,
    cmd_status,
    cmd_stop,
    cmd_stop_all,
    cmd_stop_group,
)

DISPATCH = {
    "status": cmd_status,
    "hosts": cmd_hosts,
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "add": cmd_add,
    "remove": cmd_remove,
    "start-group": cmd_start_group,
    "stop-group": cmd_stop_group,
    "import": cmd_import,
    "export": cmd_export,
    "start-all": cmd_start_all,
    "stop-all": cmd_stop_all,
    "set-interval": cmd_set_interval,
    "set-persistence": cmd_set_persistence,
    "move": cmd_move,
    "logs": cmd_logs,
    "launch": cmd_launch,
    "shutdown": cmd_shutdown,
}


def parse_name_at_host(value: str):
    """Parse 'name@host' into (name, host). Both parts required."""
    if "@" not in value:
        raise argparse.ArgumentTypeError(
            f"Expected name@host, got '{value}'"
        )
    name, host = value.rsplit("@", 1)
    if not name or not host:
        raise argparse.ArgumentTypeError(
            f"Expected name@host, got '{value}'"
        )
    return name, host


def parse_at_host(value: str):
    """Parse '@host' into host string."""
    if not value.startswith("@"):
        raise argparse.ArgumentTypeError(
            f"Expected @host, got '{value}'"
        )
    host = value[1:]
    if not host:
        raise argparse.ArgumentTypeError(
            f"Expected @host, got '{value}'"
        )
    return host


def parse_name_optional_host(value: str):
    """Parse 'name' or 'name@host' into (name, host_or_none)."""
    if "@" in value:
        name, host = value.rsplit("@", 1)
        return name, host if host else None
    return value, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dpm",
        description="DPM — Distributed Process Manager CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # dpm status [@host]
    p_status = sub.add_parser("status", help="Show hosts and processes")
    p_status.add_argument("target", nargs="?", default=None,
                          help="Optional @host to filter (e.g. @jet1)")

    # dpm hosts
    sub.add_parser("hosts", help="Show hosts only")

    # dpm start name@host
    p_start = sub.add_parser("start", help="Start a process")
    p_start.add_argument("target", help="name@host")

    # dpm stop name@host
    p_stop = sub.add_parser("stop", help="Stop a process")
    p_stop.add_argument("target", help="name@host")

    # dpm restart name@host
    p_restart = sub.add_parser("restart", help="Restart a process (stop + start)")
    p_restart.add_argument("target", help="name@host")

    # dpm create name@host --cmd "command" [-g group] [--auto-restart] [--realtime]
    p_add = sub.add_parser("add", help="Register a process")
    p_add.add_argument("target", help="name@host")
    p_add.add_argument("--cmd", required=True, help="Command to execute")
    p_add.add_argument("-g", "--group", default="", help="Process group")
    p_add.add_argument("--auto-restart", action="store_true")
    p_add.add_argument("--realtime", action="store_true")
    p_add.add_argument("--rt-priority", type=int, default=0,
                       choices=range(0, 100), metavar="[1-99]",
                       help="SCHED_FIFO priority (0 = use daemon default; requires --realtime)")
    p_add.add_argument("--isolated", action="store_true",
                       help="Isolate cpuset cores from general scheduler (requires --cpuset)")
    p_add.add_argument("--work-dir", default="", help="Working directory")
    p_add.add_argument("--cpuset", default="", help="CPU set cores (e.g. 0,1,2)")
    p_add.add_argument("--cpu-limit", type=float, default=0.0,
                       help="CPU limit in cores (e.g. 1.5)")
    p_add.add_argument("--mem-limit", type=int, default=0,
                       help="Memory limit in bytes")

    # dpm remove name@host
    p_remove = sub.add_parser("remove", help="Stop and unregister a process")
    p_remove.add_argument("target", help="name@host")

    # dpm start-group group@host
    p_sg = sub.add_parser("start-group", help="Start all processes in a group")
    p_sg.add_argument("target", help="group@host")

    # dpm stop-group group@host
    p_stg = sub.add_parser("stop-group", help="Stop all processes in a group")
    p_stg.add_argument("target", help="group@host")

    # dpm import spec.yaml
    p_import = sub.add_parser("import", help="Register processes from a YAML spec")
    p_import.add_argument("path", help="Path to YAML spec file")

    # dpm export spec.yaml [--append]
    p_export = sub.add_parser("export", help="Write process state to YAML")
    p_export.add_argument("path", help="Output YAML file path")
    p_export.add_argument("--append", action="store_true",
                          help="Append to existing file instead of overwriting")

    # dpm start-all
    sub.add_parser("start-all", help="Start every known process")

    # dpm stop-all
    sub.add_parser("stop-all", help="Stop every known process")

    # dpm set-interval @host seconds  (or 'all' for broadcast)
    p_si = sub.add_parser("set-interval", help="Set agent telemetry interval")
    p_si.add_argument("target", help="@host or 'all'")
    p_si.add_argument("seconds", type=float, help="Interval in seconds (min 0.05)")

    # dpm set-persistence @host on|off  (or 'all' for broadcast)
    p_sp = sub.add_parser("set-persistence", help="Enable/disable agent process persistence")
    p_sp.add_argument("target", help="@host or 'all'")
    p_sp.add_argument("mode", choices=["on", "off"], help="Enable or disable")

    # dpm move name@host [newname@]newhost
    p_move = sub.add_parser("move", help="Move a process to another host")
    p_move.add_argument("source", help="name@host (source)")
    p_move.add_argument("dest", help="newname@newhost or @newhost (reuse name)")

    # dpm logs name[@host] [--since 10m] [--tail 200] [--follow]
    p_logs = sub.add_parser(
        "logs",
        help="Show on-disk process logs; --follow streams live output",
    )
    p_logs.add_argument("target", help="name or name@host")
    p_logs.add_argument("--since", default="",
                        help="Show entries newer than: e.g. 30s, 10m, 2h, 1d")
    p_logs.add_argument("--tail", type=int, default=200,
                        help="Last N lines (default 200; 0 = no cap)")
    p_logs.add_argument("-f", "--follow", action="store_true",
                        help="After printing history, subscribe to live output")
    p_logs.add_argument("--persistent", action="store_true",
                        help="Walk rotated history (.log.1, .log.2, ...) — overrides --tail/--since defaults")

    # dpm launch script.yaml
    p_launch = sub.add_parser("launch", help="Execute a launch script (ordered startup)")
    p_launch.add_argument("path", help="Path to YAML launch script")

    # dpm shutdown script.yaml
    p_shutdown = sub.add_parser("shutdown", help="Execute a launch script in reverse (ordered shutdown)")
    p_shutdown.add_argument("path", help="Path to YAML launch script")

    return parser


def _resolve_args(args):
    """Post-process parsed args: split @host targets into .name and .host attributes."""
    cmd = args.command

    if cmd == "status":
        if args.target:
            args.host = parse_at_host(args.target)
        else:
            args.host = None
    elif cmd in ("start", "stop", "restart", "add", "remove"):
        args.name, args.host = parse_name_at_host(args.target)
    elif cmd in ("start-group", "stop-group"):
        args.group, args.host = parse_name_at_host(args.target)
    elif cmd in ("set-interval", "set-persistence"):
        if args.target == "all":
            args.host = ""  # empty hostname = broadcast to all agents
        else:
            args.host = parse_at_host(args.target)
    elif cmd == "move":
        args.src_name, args.src_host = parse_name_at_host(args.source)
        # dest can be "newname@newhost" or "@newhost" (reuse source name)
        if args.dest.startswith("@"):
            args.dst_host = parse_at_host(args.dest)
            args.dst_name = args.src_name
        else:
            args.dst_name, args.dst_host = parse_name_at_host(args.dest)
    elif cmd == "logs":
        args.name, args.host = parse_name_optional_host(args.target)

    return args


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args = _resolve_args(args)
    except argparse.ArgumentTypeError as e:
        parser.error(str(e))

    config_path = os.environ.get("DPM_CONFIG", "/etc/dpm/dpm.yaml")

    try:
        from dpm.client import Client
        client = Client(config_path)
        client.start()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Map SIGTERM to the same path as Ctrl+C so the existing KeyboardInterrupt
    # handling + finally cleanup runs on the main thread. A handler that calls
    # client.stop() directly could deadlock if SIGTERM arrives on the LCM
    # thread (self-join with timeout) and sys.exit from a signal handler
    # skips the finally block.
    signal.signal(signal.SIGTERM, signal.default_int_handler)

    try:
        rc = DISPATCH[args.command](client, args)
    except KeyboardInterrupt:
        rc = 0
    finally:
        client.stop()

    sys.exit(rc)


if __name__ == "__main__":
    main()
