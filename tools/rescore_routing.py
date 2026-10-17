"""Judge recorded routing runs again with the current observer; launches nothing.

    python tools/rescore_routing.py <run directory>...

Each directory is a routing or routing-smoke run from tools/live_checks.py.
It gets rescored.json beside its summary.json, naming the Forge revision
that judged; a working tree with changes is marked "-dirty".
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_runs  # noqa: E402


def main(directories: list[str]) -> int:
    if not directories:
        print(__doc__, file=sys.stderr)
        return 2
    git = ["git", "-C", str(live_runs.ROOT)]
    revision = subprocess.run(git + ["rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(git + ["status", "--porcelain", "--", "src", "tools", "skills"],
                           capture_output=True, text=True).stdout.strip()
    scorer = revision + ("-dirty" if dirty else "")
    for directory in directories:
        rescored = live_runs.rescore(Path(directory), scorer=scorer)
        changed = sum(result["result"] != result["recorded_result"] for result in rescored["results"])
        print(json.dumps({"run": directory, "scorer": scorer, "changed": changed, "rates": rescored["rates"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
