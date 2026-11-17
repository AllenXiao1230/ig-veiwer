"""Create a consistent SQLite backup while the trend service is running."""

import argparse
from pathlib import Path

from .config import Settings
from .store import TrendStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    source = Settings.from_environment().data_dir / "ig-trends.sqlite3"
    if not source.is_file():
        parser.error("找不到趨勢資料庫")
    if source.resolve() == arguments.output.resolve():
        parser.error("備份位置必須與使用中的資料庫不同")
    TrendStore(source).backup(arguments.output)
    print(f"已建立 SQLite 備份 {arguments.output}")


if __name__ == "__main__":
    main()
