"""AST-based CI lint: disallow SQL on FORCE RLS tables missing usr_group.

Applied to all *.py under ruisheng-gw/src/. Each `text("...")` literal
is scanned for table names in the forbidden list; if matched without
a co-occurring 'usr_group' token (anywhere in the same string), flag.

# noqa: tenant-lint on the same line suppresses.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_NOQA_RE = re.compile(r"#\s*noqa:\s*tenant-lint", re.IGNORECASE)


def check_file(path: Path, *, forbidden_tables: set[str]) -> list[str]:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    tree = ast.parse(src, filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            sql = node.args[0].value.lower()
            line_no = node.lineno
            line = lines[line_no - 1] if line_no <= len(lines) else ""
            if _NOQA_RE.search(line):
                continue
            for tbl in forbidden_tables:
                pattern = rf"\b{re.escape(tbl.lower())}\b"
                if re.search(pattern, sql) and "usr_group" not in sql:
                    violations.append(
                        f"{path}:{line_no}: FORCE RLS table '{tbl}' missing usr_group filter"
                    )
                    break
    return violations


def main() -> int:
    forbidden = {
        "devices",
        "device_points",
        "wx_groups",
        "users",
        "scene_pages",
        "scene_views",
        "user_emails",
        "user_phone_numbers",
        "user_wx_bindings",
        "user_login_records",
        "user_control_actions",
        "soft_logs",
    }
    # File is at src/ruisheng_gw/ci_lint/tenant_filter_lint.py
    # parent      = src/ruisheng_gw/ci_lint/
    # parent.parent = src/ruisheng_gw/
    src_root = Path(__file__).parent.parent
    violations: list[str] = []
    for py in src_root.rglob("*.py"):
        violations.extend(check_file(py, forbidden_tables=forbidden))
    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
