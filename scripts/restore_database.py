"""把备份文件恢复到目标路径（校验完整性，默认不覆盖已有文件）。

用法示例::

    # 恢复到新目录
    uv run python -m scripts.restore_database \\
        --backup backups/app.db --target data/restored.db

    # 覆盖现有数据库（先停止应用）
    uv run python -m scripts.restore_database \\
        --backup backups/app.db --target data/app.db --force
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

from scripts._sqlite_tools import (
    DatabaseToolError,
    inspect_database,
    open_readonly,
)


def restore_database(
    backup: Path, target: Path, *, force: bool = False
) -> dict[str, object]:
    """校验备份文件并复制到目标路径，随后再次校验恢复结果。"""

    source_connection = open_readonly(backup)
    try:
        report = inspect_database(source_connection)
    finally:
        source_connection.close()
    if report["integrity"] != "ok":
        raise DatabaseToolError(f"备份文件完整性校验失败：{report['integrity']}")
    if target.exists() and not force:
        raise DatabaseToolError(f"目标已存在，如需覆盖请加 --force：{target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, target)

    target_connection = sqlite3.connect(target)
    try:
        restored = inspect_database(target_connection)
    finally:
        target_connection.close()
    if restored["integrity"] != "ok":
        raise DatabaseToolError(f"恢复结果完整性校验失败：{restored['integrity']}")
    if restored["counts"] != report["counts"]:
        raise DatabaseToolError("恢复后的表行数与备份不一致")
    return {
        "backup": str(backup),
        "target": str(target),
        "size_bytes": target.stat().st_size,
        **restored,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从备份恢复 SQLite 数据库")
    parser.add_argument("--backup", required=True, help="备份文件路径")
    parser.add_argument("--target", required=True, help="恢复目标数据库文件路径")
    parser.add_argument(
        "--force", action="store_true", help="目标已存在时覆盖（先确认已停应用）"
    )
    args = parser.parse_args(argv)
    try:
        summary = restore_database(
            Path(args.backup), Path(args.target), force=args.force
        )
    except DatabaseToolError as exc:
        print(f"恢复失败：{exc}")
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"完成：把 DATABASE_URL 指向 {summary['target']} 后启动应用即可。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
