"""ThemeLeaderService 단위 테스트.

- 그룹 내 RS 내림차순 top_n 선정
- 그룹 강도 = 멤버 RS 중앙값, 그 기준 그룹 정렬
- 종목 출처(provenance) 전달
- 분류/ RS 데이터 미비 시 graceful (data=[])
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from common.types import ErrorCode
from services.theme_leader_service import ThemeLeaderService


def _make_service(groups, latest_date="20260620", rs_map=None):
    classification_repo = MagicMock()
    classification_repo.get_groups = AsyncMock(return_value=groups)
    rs_repo = MagicMock()
    rs_repo.get_latest_date = AsyncMock(return_value=latest_date)
    rs_repo.get_by_date = AsyncMock(return_value=rs_map or {})
    return ThemeLeaderService(
        classification_repository=classification_repo,
        rs_rating_repository=rs_repo,
        logger=MagicMock(),
    )


def _member(code, name, sources=("NAVER",)):
    return {"code": code, "name": name, "sources": list(sources)}


@pytest.mark.asyncio
async def test_no_classification_data_returns_empty():
    svc = _make_service(groups={})
    resp = await svc.get_theme_leaders()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == []


@pytest.mark.asyncio
async def test_no_rs_data_returns_empty():
    svc = _make_service(
        groups={"로봇": {"sources": ["NAVER"], "members": [_member("005930", "삼성전자")]}},
        latest_date=None,
        rs_map={},
    )
    resp = await svc.get_theme_leaders()
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == []


@pytest.mark.asyncio
async def test_leaders_sorted_by_rs_and_topn():
    groups = {
        "로봇": {
            "sources": ["NAVER"],
            "members": [
                _member("A", "에이"), _member("B", "비"),
                _member("C", "씨"), _member("D", "디"),
            ],
        }
    }
    rs_map = {"A": 50, "B": 99, "C": 70, "D": 10}
    svc = _make_service(groups=groups, rs_map=rs_map)
    resp = await svc.get_theme_leaders(top_n=2)

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    leaders = resp.data[0]["leaders"]
    assert [l["code"] for l in leaders] == ["B", "C"]  # RS 99, 70
    assert resp.data[0]["member_count"] == 4
    # 중앙값(10,50,70,99) = (50+70)/2 = 60
    assert resp.data[0]["group_rs_median"] == 60.0


@pytest.mark.asyncio
async def test_groups_sorted_by_median_strength():
    groups = {
        "약한그룹": {"sources": ["NAVER"], "members": [_member("X", "엑스"), _member("Y", "와이")]},
        "강한그룹": {"sources": ["NAVER"], "members": [_member("P", "피"), _member("Q", "큐")]},
    }
    rs_map = {"X": 20, "Y": 30, "P": 80, "Q": 90}
    svc = _make_service(groups=groups, rs_map=rs_map)
    resp = await svc.get_theme_leaders()
    assert [g["normalized_name"] for g in resp.data] == ["강한그룹", "약한그룹"]


@pytest.mark.asyncio
async def test_member_without_rs_is_excluded():
    groups = {"로봇": {"sources": ["NAVER", "KIWOOM"], "members": [
        _member("A", "에이", sources=["NAVER", "KIWOOM"]),
        _member("Z", "지", sources=["KIWOOM"]),  # RS 없음 → 제외
    ]}}
    rs_map = {"A": 88}
    svc = _make_service(groups=groups, rs_map=rs_map)
    resp = await svc.get_theme_leaders()
    leaders = resp.data[0]["leaders"]
    assert [l["code"] for l in leaders] == ["A"]
    assert leaders[0]["sources"] == ["NAVER", "KIWOOM"]  # provenance 전달
    assert resp.data[0]["member_count"] == 1


@pytest.mark.asyncio
async def test_group_with_no_scored_members_skipped():
    groups = {"빈그룹": {"sources": ["NAVER"], "members": [_member("NONE", "노알에스")]}}
    svc = _make_service(groups=groups, rs_map={"OTHER": 50})
    resp = await svc.get_theme_leaders()
    assert resp.data == []
