import multiprocessing
import sys
from pathlib import Path
from typing import Sequence

from stemlift.cli import main as cli_main
from stemlift.desktop_worker import (
    _configure_frozen_environment,
    _run_frozen_demucs,
)
from stemlift.desktop_worker import (
    main as worker_main,
)


def main(argv: Sequence[str] | None = None) -> int:
    multiprocessing.freeze_support()
    arguments = list(argv if argv is not None else sys.argv[1:])
    _configure_frozen_environment()
    if _run_frozen_demucs(arguments):
        return 0
    if Path(sys.executable).name.startswith("stemlift-worker"):
        return worker_main(arguments)
    return cli_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
