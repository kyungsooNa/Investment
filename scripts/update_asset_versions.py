"""템플릿이 참조하는 정적 자산의 (버전, 내용 해시) 잠금을 갱신한다.

JS/CSS 를 고치고 템플릿의 `?v=` 를 올린 뒤 실행한다:

    python scripts/update_asset_versions.py

잠금은 버전을 자동으로 올려주지 않는다. `?v=` 상승은 사람이 템플릿에서 한다.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.unit_test.view.web.test_static_asset_versions import (  # noqa: E402
    LOCK_PATH,
    STATIC_DIR,
    _iter_refs,
    _sha256,
)


def main() -> int:
    lock = {}
    for _, path, version in _iter_refs():
        if version is None:
            continue
        asset = STATIC_DIR / path
        if not asset.exists():
            print(f"[warn] 참조된 자산이 없음: /static/{path}")
            continue
        lock[path] = {"version": version, "sha256": _sha256(asset)}

    LOCK_PATH.write_text(
        json.dumps(dict(sorted(lock.items())), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"{LOCK_PATH} 갱신: {len(lock)}개 자산")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
