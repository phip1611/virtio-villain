# SPDX-License-Identifier: Apache-2.0
"""Execute suite preparation within the supervised appliance process."""

from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from villain.appliance import load_suite  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("Suite definition is required", file=sys.stderr)
        return 2
    try:
        suite = load_suite(sys.argv[1])
        suite.execute(sys.argv[2:])
        raise RuntimeError("Suite execute() returned without replacing the process")
    except (OSError, RuntimeError, subprocess.SubprocessError, ImportError,
            AttributeError, ValueError, TypeError, SyntaxError) as error:
        print(f"[INFRA] {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
