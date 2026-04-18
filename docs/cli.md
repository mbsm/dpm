# CLI Reference

The `dpm` command is a scriptable interface for headless servers and automation. No PyQt5 dependency.

## Status & monitoring

```bash
dpm status                              # all hosts and processes
dpm status @jet1                        # filter to one host
dpm hosts                               # hosts only
dpm logs camera@jet1                    # stream output (Ctrl+C to stop)
```

## Process control

```bash
dpm add camera@jet1 --cmd "cam-node" -g perception --auto-restart
dpm start camera@jet1
dpm stop camera@jet1
dpm restart camera@jet1
dpm remove camera@jet1                  # stop and unregister
dpm move camera@jet1 @jet2              # migrate to another host
```

## Group operations

```bash
dpm start-group perception@jet1
dpm stop-group perception@jet1
dpm start-all
dpm stop-all
```

## Spec files

```bash
dpm export snapshot.yaml                # write current state to YAML
dpm import system.yaml                  # register processes from YAML
```

## Launch files

```bash
dpm launch startup.yaml                 # dependency-based startup
dpm shutdown startup.yaml               # reverse-order shutdown
```

See [launch-files.md](launch-files.md) for the launch file format.

## Daemon configuration

```bash
dpm set-interval @jet1 2                # telemetry interval (seconds)
dpm set-persistence @jet1 on            # enable process persistence
```

Run `dpm <command> --help` for full option listings.
