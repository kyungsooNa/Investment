"""정적 자산 캐시버스팅(`?v=N`) 계약 테스트.

`/static` 은 `Cache-Control` 없이 마운트되어 브라우저 휴리스틱 캐싱이 걸리므로,
템플릿의 `?v=` 상승이 사실상 유일한 캐시 무효화 수단이다. JS/CSS 를 고치고
`?v=` 를 안 올리면 이미 그 페이지를 연 적 있는 브라우저는 옛 파일을 계속 받는다.
(실제 사고: 지수 차트 기준선 색 분리가 캐시 때문에 화면에 안 나왔다)

`view/web/asset_versions.json` 이 (버전, 내용 해시) 잠금이다.
자산을 고쳤으면 템플릿의 `?v=` 를 올리고 `python scripts/update_asset_versions.py` 를 돌린다.
"""

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

TEMPLATES_DIR = Path("view/web/templates")
STATIC_DIR = Path("view/web/static")
LOCK_PATH = Path("view/web/asset_versions.json")

_UPDATE_HINT = "템플릿의 ?v= 를 올린 뒤 `python scripts/update_asset_versions.py` 를 실행하라."

# 템플릿이 참조하는 정적 자산: /static/<경로>[?v=<정수>]
_REF_RE = re.compile(r'(?:href|src)="/static/(?P<path>[A-Za-z0-9_./-]+\.(?:js|css))(?:\?v=(?P<version>\d+))?"')


def _iter_refs():
    """(템플릿 파일, 자산 경로, 버전 or None) 목록."""
    for template in sorted(TEMPLATES_DIR.rglob("*.html")):
        text = template.read_text(encoding="utf-8")
        for match in _REF_RE.finditer(text):
            version = match.group("version")
            yield template, match.group("path"), int(version) if version else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_static_assets_are_cache_busted():
    """모든 JS/CSS 참조는 ?v= 를 달아야 한다 (없으면 영구 캐시 위험)."""
    missing = sorted({
        f"{template.name}: /static/{path}"
        for template, path, version in _iter_refs() if version is None
    })

    assert not missing, "캐시버스팅 ?v= 가 없는 정적 자산 참조:\n" + "\n".join(missing)


def test_same_asset_uses_one_version_everywhere():
    """같은 파일을 페이지마다 다른 ?v= 로 부르면 낮은 쪽 페이지가 옛 캐시를 받는다."""
    versions = defaultdict(set)
    for _, path, version in _iter_refs():
        if version is not None:
            versions[path].add(version)

    drifted = {path: sorted(found) for path, found in versions.items() if len(found) > 1}

    assert not drifted, f"같은 자산이 템플릿마다 다른 버전을 씀: {drifted}"


def test_asset_lock_matches_referenced_assets():
    """잠금 파일과 템플릿 참조 목록이 어긋나면 잠금이 무의미해진다."""
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    referenced = {path for _, path, version in _iter_refs() if version is not None}

    assert set(lock) == referenced, (
        f"잠금 파일과 템플릿 참조가 다름 "
        f"(잠금에만: {sorted(set(lock) - referenced)}, 템플릿에만: {sorted(referenced - set(lock))}). "
        + _UPDATE_HINT
    )


def test_changed_asset_requires_version_bump():
    """내용이 바뀐 자산은 ?v= 도 함께 올라가 있어야 한다 (이 테스트가 본체다)."""
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    current = {path: version for _, path, version in _iter_refs() if version is not None}

    stale = []
    for path, entry in sorted(lock.items()):
        asset = STATIC_DIR / path
        if not asset.exists():
            stale.append(f"/static/{path}: 파일이 없음")
            continue
        digest = _sha256(asset)
        if digest == entry["sha256"]:
            continue
        if current.get(path) == entry["version"]:
            stale.append(
                f"/static/{path}: 내용이 바뀌었는데 ?v={entry['version']} 그대로임 "
                f"→ ?v={entry['version'] + 1} 로 올려라"
            )
        else:
            stale.append(f"/static/{path}: ?v= 는 올랐으나 잠금이 옛 해시임")

    assert not stale, "캐시버스팅 누락:\n" + "\n".join(stale) + "\n" + _UPDATE_HINT
