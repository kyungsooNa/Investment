"""운영 상태 백업과 복원 검증을 즉시 실행하는 CLI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.operational_backup_service import OperationalBackupService


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 상태 SQLite/JSON 백업 및 복원 검증")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--backup-root")
    parser.add_argument("--retention-count", type=int, default=14)
    args = parser.parse_args()
    result = OperationalBackupService(
        data_dir=args.data_dir,
        backup_root=args.backup_root,
        retention_count=args.retention_count,
    ).create_backup()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
