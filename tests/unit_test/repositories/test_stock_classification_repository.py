"""StockClassificationRepository 단위 테스트.

- normalized_name 기준 union + 종목 출처(provenance) 보존
- alias 매핑 조회
- 빈 테이블 graceful (예외 없이 빈 결과)
"""
import pytest
from unittest.mock import MagicMock, patch

from repositories.stock_classification_repository import StockClassificationRepository


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "stock_classifications.db")
    return StockClassificationRepository(db_path=db_path, logger=MagicMock())


def _rec(source, code, name, group="로봇", normalized="로봇", cat="theme", at="2026-06-21T18:00:00"):
    return {
        "source": source, "category_type": cat, "group_name": group,
        "normalized_name": normalized, "code": code, "name": name, "collected_at": at,
    }


@pytest.mark.asyncio
async def test_empty_db_graceful(repo):
    """데이터가 없으면 빈 결과를 반환하고 예외를 던지지 않는다."""
    assert await repo.get_groups(("theme",)) == {}
    assert await repo.get_latest_collected_at() is None
    assert await repo.get_alias_map("NAVER") == {}


@pytest.mark.asyncio
async def test_upsert_and_get_groups_single_source(repo):
    n = await repo.upsert_classifications([
        _rec("NAVER", "005930", "삼성전자"),
        _rec("NAVER", "000660", "SK하이닉스"),
    ])
    assert n == 2

    groups = await repo.get_groups(("theme",))
    assert "로봇" in groups
    assert groups["로봇"]["sources"] == ["NAVER"]
    codes = {m["code"] for m in groups["로봇"]["members"]}
    assert codes == {"005930", "000660"}


@pytest.mark.asyncio
async def test_union_across_sources_with_provenance(repo):
    """두 소스를 normalized_name 으로 OR 병합하고 종목별 출처를 보존한다."""
    await repo.upsert_classifications([
        _rec("NAVER", "005930", "삼성전자"),
        _rec("KIWOOM", "005930", "삼성전자"),   # 양쪽 소스에 등장
        _rec("KIWOOM", "247540", "에코프로비엠"),  # 키움 전용
    ])

    groups = await repo.get_groups(("theme",))
    g = groups["로봇"]
    assert g["sources"] == ["KIWOOM", "NAVER"]

    by_code = {m["code"]: m for m in g["members"]}
    assert by_code["005930"]["sources"] == ["KIWOOM", "NAVER"]
    assert by_code["247540"]["sources"] == ["KIWOOM"]


@pytest.mark.asyncio
async def test_normalized_name_merges_different_raw_names(repo):
    """raw group_name 이 달라도 normalized_name 이 같으면 하나의 그룹으로 합쳐진다."""
    await repo.upsert_classifications([
        _rec("NAVER", "247540", "에코프로비엠", group="2차전지", normalized="2차전지"),
        _rec("KIWOOM", "086520", "에코프로", group="2차전지 소재", normalized="2차전지"),
    ])
    groups = await repo.get_groups(("theme",))
    assert set(groups.keys()) == {"2차전지"}
    assert len(groups["2차전지"]["members"]) == 2


@pytest.mark.asyncio
async def test_category_type_filter(repo):
    await repo.upsert_classifications([
        _rec("NAVER", "005930", "삼성전자", cat="theme", group="반도체", normalized="반도체"),
        _rec("WICS", "005930", "삼성전자", cat="industry", group="전기전자", normalized="전기전자"),
    ])
    theme_only = await repo.get_groups(("theme",))
    assert set(theme_only.keys()) == {"반도체"}

    both = await repo.get_groups(("theme", "industry"))
    assert set(both.keys()) == {"반도체", "전기전자"}


@pytest.mark.asyncio
async def test_alias_map_roundtrip(repo):
    await repo.upsert_aliases([
        {"source": "KIWOOM", "raw_name": "2차전지 소재", "normalized_name": "2차전지"},
    ])
    amap = await repo.get_alias_map("KIWOOM")
    assert amap == {"2차전지 소재": "2차전지"}
    assert await repo.get_alias_map("NAVER") == {}


@pytest.mark.asyncio
async def test_upsert_is_idempotent_on_pk(repo):
    """같은 (source, category_type, group_name, code) 는 갱신된다."""
    await repo.upsert_classifications([_rec("NAVER", "005930", "삼성전자", at="2026-06-20T18:00:00")])
    await repo.upsert_classifications([_rec("NAVER", "005930", "삼성전자", at="2026-06-21T18:00:00")])
    groups = await repo.get_groups(("theme",))
    assert len(groups["로봇"]["members"]) == 1
    assert await repo.get_latest_collected_at() == "2026-06-21T18:00:00"


@pytest.mark.asyncio
async def test_replace_source_drops_stale_members(repo):
    """전수 재수집 시 그룹에서 빠진 종목(stale)이 제거된다."""
    await repo.replace_source_classifications("NAVER", "theme", [
        _rec("NAVER", "005930", "삼성전자"),
        _rec("NAVER", "000660", "SK하이닉스"),
    ])
    # 2차 수집: 000660 빠지고 247540 추가
    await repo.replace_source_classifications("NAVER", "theme", [
        _rec("NAVER", "005930", "삼성전자"),
        _rec("NAVER", "247540", "에코프로비엠"),
    ])
    groups = await repo.get_groups(("theme",))
    codes = {m["code"] for m in groups["로봇"]["members"]}
    assert codes == {"005930", "247540"}


@pytest.mark.asyncio
async def test_replace_source_preserves_other_sources(repo):
    """replace 는 대상 소스만 교체하고 다른 소스 데이터는 보존한다."""
    await repo.upsert_classifications([_rec("KIWOOM", "005930", "삼성전자")])
    await repo.replace_source_classifications("NAVER", "theme", [
        _rec("NAVER", "247540", "에코프로비엠"),
    ])
    groups = await repo.get_groups(("theme",))
    by_code = {m["code"]: m for m in groups["로봇"]["members"]}
    assert by_code["005930"]["sources"] == ["KIWOOM"]
    assert "247540" in by_code


@pytest.mark.asyncio
async def test_replace_source_scoped_to_category(repo):
    """replace 는 대상 category_type 만 교체하고 다른 카테고리는 건드리지 않는다."""
    await repo.upsert_classifications([
        _rec("NAVER", "005930", "삼성전자", cat="industry", group="전기전자", normalized="전기전자"),
    ])
    await repo.replace_source_classifications("NAVER", "theme", [
        _rec("NAVER", "247540", "에코프로비엠"),
    ])
    both = await repo.get_groups(("theme", "industry"))
    assert "전기전자" in both           # industry 보존
    assert "로봇" in both


@pytest.mark.asyncio
async def test_replace_source_empty_keeps_existing(repo):
    """records 가 비면(전수 수집 실패) 기존 데이터를 지우지 않고 0을 반환한다."""
    await repo.replace_source_classifications("NAVER", "theme", [_rec("NAVER", "005930", "삼성전자")])
    n = await repo.replace_source_classifications("NAVER", "theme", [])
    assert n == 0
    groups = await repo.get_groups(("theme",))
    assert {m["code"] for m in groups["로봇"]["members"]} == {"005930"}


# ── get_code_category_map: 종목 → 그룹 역방향 조회 ─────────────
# 히트맵이 종목마다 업종을 붙일 때 쓴다. get_groups() 를 뒤집어 쓰면 매 요청마다
# 2500건짜리 그룹 구조를 재구성해야 한다.

@pytest.mark.asyncio
async def test_code_category_map_returns_code_to_group(repo):
    await repo.upsert_classifications([
        _rec("NAVER", "005930", "삼성전자", group="반도체와반도체장비",
             normalized="반도체와반도체장비", cat="industry"),
        _rec("NAVER", "035720", "카카오", group="양방향미디어와서비스",
             normalized="양방향미디어와서비스", cat="industry"),
    ])

    assert await repo.get_code_category_map("industry") == {
        "005930": "반도체와반도체장비",
        "035720": "양방향미디어와서비스",
    }


@pytest.mark.asyncio
async def test_code_category_map_ignores_other_category_types(repo):
    await repo.upsert_classifications([
        _rec("NAVER", "005930", "삼성전자", group="반도체와반도체장비",
             normalized="반도체와반도체장비", cat="industry"),
        _rec("NAVER", "005930", "삼성전자", group="로봇", normalized="로봇", cat="theme"),
    ])

    assert await repo.get_code_category_map("industry") == {"005930": "반도체와반도체장비"}


@pytest.mark.asyncio
async def test_code_category_map_empty_when_not_collected(repo):
    assert await repo.get_code_category_map("industry") == {}


@pytest.mark.asyncio
async def test_empty_batches_are_a_noop(repo):
    assert await repo.upsert_classifications([]) == 0
    assert await repo.upsert_aliases([]) == 0


@pytest.mark.asyncio
async def test_get_groups_without_category_types_returns_empty(repo):
    assert await repo.get_groups(category_types=()) == {}


@pytest.mark.asyncio
async def test_get_groups_can_be_filtered_by_source(repo):
    await repo.upsert_classifications([
        _rec("NAVER", "005930", "삼성전자"),
        _rec("KRX", "000660", "SK하이닉스"),
    ])

    only_naver = await repo.get_groups(sources=("NAVER",))

    assert [m["code"] for m in only_naver["로봇"]["members"]] == ["005930"]
    assert only_naver["로봇"]["sources"] == ["NAVER"]


@pytest.mark.asyncio
async def test_get_latest_collected_at_returns_max_and_none_when_empty(repo):
    assert await repo.get_latest_collected_at() is None

    await repo.upsert_classifications([
        _rec("NAVER", "005930", "삼성전자", at="2026-06-20T18:00:00"),
        _rec("NAVER", "000660", "SK하이닉스", at="2026-06-21T18:00:00"),
    ])

    assert await repo.get_latest_collected_at() == "2026-06-21T18:00:00"


@pytest.mark.asyncio
async def test_close_releases_both_connections(repo):
    await repo.upsert_classifications([_rec("NAVER", "005930", "삼성전자")])
    await repo.get_groups()
    assert repo._write_conn is not None
    assert repo._read_conn is not None

    await repo.close()

    assert repo._write_conn is None
    assert repo._read_conn is None

    await repo.close()  # 두 번 호출해도 안전해야 한다


def test_init_failure_is_logged_without_raising(tmp_path):
    logger = MagicMock()

    with patch(
        "repositories.stock_classification_repository.sqlite3.connect",
        side_effect=RuntimeError("DB 초기화 실패"),
    ):
        StockClassificationRepository(db_path=str(tmp_path / "x.db"), logger=logger)

    logger.error.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method_name, args, expected",
    [
        ("upsert_classifications", ([_rec("NAVER", "005930", "삼성전자")],), 0),
        (
            "replace_source_classifications",
            ("NAVER", "theme", [_rec("NAVER", "005930", "삼성전자")]),
            0,
        ),
        (
            "upsert_aliases",
            ([{"source": "NAVER", "raw_name": "로보틱스", "normalized_name": "로봇"}],),
            0,
        ),
        ("get_alias_map", ("NAVER",), {}),
        ("get_groups", (), {}),
        ("get_code_category_map", ("industry",), {}),
        ("get_latest_collected_at", (), None),
    ],
)
async def test_db_errors_are_logged_and_return_fallback_values(repo, method_name, args, expected):
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("DB 연결 끊김")

    repo._get_write_conn = _boom
    repo._get_read_conn = _boom

    assert await getattr(repo, method_name)(*args) == expected
    repo._logger.error.assert_called_once()
