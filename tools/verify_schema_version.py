"""Pre-commit 钩子：当 ruisheng-shared 的 models/schemas 改动时，
检查 SHARED_SCHEMA_VERSION 是否也已上调 + CHANGELOG 是否加了新条目。

目前 stub 阶段：只检查 CHANGELOG.md 是否含今天日期。
完整实现在 Task G5（CI 完善阶段）。
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import subprocess
import sys

CHANGELOG = pathlib.Path("ruisheng-shared/src/ruisheng_shared/CHANGELOG.md")


def _git_changed_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only"],
        text=True,
    )
    return [ln for ln in out.splitlines() if ln.strip()]


def main() -> int:
    changed = _git_changed_files()
    watched = any(
        p.startswith("ruisheng-shared/src/ruisheng_shared/models/")
        or p.startswith("ruisheng-shared/src/ruisheng_shared/schemas/")
        for p in changed
    )
    if not watched:
        return 0

    today = _dt.date.today().isoformat()
    text = CHANGELOG.read_text(encoding="utf-8")
    if today not in text:
        print(
            f"ERROR: 修改了 shared 的 models/schemas，CHANGELOG.md 里还没有 {today} 的条目。",
            file=sys.stderr,
        )
        print("请追加一条，格式参考 CHANGELOG 文件头的说明。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
