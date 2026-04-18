"""验证：若 ruisheng-shared/src/ruisheng_shared/models|schemas/ 改动了，则：
1) CHANGELOG.md 必须有今日条目
2) 若任一条目以 `breaking:` 前缀，SHARED_SCHEMA_VERSION 必须同步升级

双模式（v1.9 Plan bug #24 fix）：
- pre-commit 上下文：`git diff --cached`（staged 变更）— pre-commit 工具自动设 PRE_COMMIT=1
- CI 上下文：`git diff HEAD^ HEAD`（已提交变更）— schema-version-guard job 用
"""

from __future__ import annotations

import datetime as _dt
import os
import pathlib
import re
import subprocess
import sys

CHANGELOG = pathlib.Path("ruisheng-shared/src/ruisheng_shared/CHANGELOG.md")
INIT = pathlib.Path("ruisheng-shared/src/ruisheng_shared/__init__.py")


def _is_precommit() -> bool:
    """pre-commit 工具调用 hook 时会自动设置 PRE_COMMIT=1 环境变量。"""
    return os.environ.get("PRE_COMMIT") == "1"


def _diff_args() -> list[str]:
    """根据上下文返回 git diff 参数（pre-commit 看 staged，CI 看已提交）。"""
    if _is_precommit():
        return ["--cached"]
    return ["HEAD^", "HEAD"]


def _schema_files_changed() -> bool:
    args = ["git", "diff", *_diff_args(), "--name-only"]
    out = subprocess.check_output(args, text=True)
    return any(
        p.startswith("ruisheng-shared/src/ruisheng_shared/models/")
        or p.startswith("ruisheng-shared/src/ruisheng_shared/schemas/")
        for p in out.splitlines()
    )


def _has_today_entry() -> bool:
    today = _dt.date.today().isoformat()
    return today in CHANGELOG.read_text(encoding="utf-8")


def _has_breaking_today() -> bool:
    today = _dt.date.today().isoformat()
    text = CHANGELOG.read_text(encoding="utf-8")
    # 匹配 "## YYYY-MM-DD" 开始的段落到下一个 "## " 或文件末尾
    section = re.search(rf"## .*{today}.*?(?=\n## |\Z)", text, re.DOTALL)
    if not section:
        return False
    return "breaking:" in section.group(0)


def _version_changed() -> bool:
    args = ["git", "diff", *_diff_args(), "--", str(INIT)]
    diff = subprocess.check_output(args, text=True)
    return "SHARED_SCHEMA_VERSION" in diff and ("+SHARED" in diff or "-SHARED" in diff)


def main() -> int:
    if not _schema_files_changed():
        return 0
    if not _has_today_entry():
        print("ERROR: shared models/schemas 改动但 CHANGELOG 无今日条目", file=sys.stderr)
        return 1
    if _has_breaking_today() and not _version_changed():
        print(
            "ERROR: 今日有 breaking 变更，SHARED_SCHEMA_VERSION 必须同时升级",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
