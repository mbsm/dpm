"""dpmd entry point — ``python -m dpmd`` or the installed ``dpmd`` script."""

import os

from dpmd.daemon import Daemon


def main() -> None:
    config_path = os.environ.get("DPM_CONFIG", "/etc/dpm/dpm.yaml")
    Daemon(config_file=config_path).run()


if __name__ == "__main__":
    main()
