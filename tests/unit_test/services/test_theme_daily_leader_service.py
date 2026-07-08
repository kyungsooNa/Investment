"""ThemeDailyLeaderService 단위 테스트."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.types import ErrorCode
from services.theme_daily_leader_service import ThemeDailyLeaderService


def _member(code, name, sources=("NAVER",)):
    return {"code": code, "name": name, "sources": list(sources)}


def _stock(
    code,
    name,
    rate,
    trading_value,
    foreign=0,
    inst=0,
    personal=0,
):
    return {
        "stck_shrn_iscd": code,
        "hts_kor_isnm": name,
        "stck_prpr": "10000",
        "prdy_ctrt": str(rate),
        "acml_tr_pbmn": str(trading_value),
        "frgn_ntby_tr_pbmn": str(foreign),
        "orgn_ntby_tr_pbmn": str(inst),
        "prsn_ntby_tr_pbmn": str(personal),
    }


def _program(code, amount):
    return {
        "stck_shrn_iscd": code,
        "whol_smtn_ntby_tr_pbmn": str(amount),
    }


def _service(groups):
    repo = MagicMock()
    repo.get_groups = AsyncMock(return_value=groups)
    return ThemeDailyLeaderService(classification_repository=repo, logger=MagicMock()), repo


@pytest.mark.asyncio
async def test_returns_empty_without_theme_groups():
    svc, _ = _service({})

    resp = await svc.build_daily_theme_report({"all_stocks": []}, "20260630")

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == []
    assert "테마 데이터" in resp.msg1


@pytest.mark.asyncio
async def test_builds_theme_report_from_ranking_data():
    groups = {
        "반도체/소부장": {
            "sources": ["NAVER"],
            "members": [
                _member("A", "테스"),
                _member("B", "유진테크"),
                _member("C", "피에스케이"),
                _member("D", "약한종목"),
            ],
        },
        "우주항공": {
            "sources": ["NAVER"],
            "members": [
                _member("E", "스피어"),
                _member("F", "에이치브이엠"),
                _member("G", "인텔리안테크"),
            ],
        },
    }
    svc, _ = _service(groups)
    rankings = {
        "all_stocks": [
            _stock("A", "테스", 14.9, 193_000_000_000, foreign=100, inst=200),
            _stock("B", "유진테크", 12.6, 54_300_000_000, foreign=50, inst=-10),
            _stock("C", "피에스케이", 9.5, 122_800_000_000, foreign=0, inst=30),
            _stock("D", "약한종목", -1.0, 10_000_000_000, foreign=-10, inst=-10),
            _stock("E", "스피어", 19.4, 55_300_000_000, foreign=20, inst=20),
            _stock("F", "에이치브이엠", 11.1, 23_000_000_000, foreign=10, inst=10),
            _stock("G", "인텔리안테크", 5.3, 6_100_000_000, foreign=10, inst=10),
        ],
        "program_all_stocks": [
            _program("A", 5_000_000_000),
            _program("B", 1_000_000_000),
            _program("E", -500_000_000),
        ],
    }

    resp = await svc.build_daily_theme_report(rankings, "20260630")

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert [item["normalized_name"] for item in resp.data] == ["우주항공", "반도체/소부장"]

    semi = next(item for item in resp.data if item["normalized_name"] == "반도체/소부장")
    assert semi["scored_member_count"] == 4
    assert semi["advance_count"] == 3
    assert semi["advancing_ratio"] == 75.0
    assert semi["leader_avg_change_rate"] == 12.33
    assert semi["trading_value_sum_won"] == 380_100_000_000
    assert semi["fi_net_buy_won"] == 350_000_000
    assert semi["program_net_buy_won"] == 6_000_000_000
    assert semi["flow_ratio"] == 1.67
    assert semi["value_weighted_change_rate"] == 12.41
    assert semi["zero_trading_value_ratio"] == 0.0
    assert semi["negative_trading_value_ratio"] == 2.63
    assert semi["theme_score"] == 13.21
    assert [leader["code"] for leader in semi["leaders"][:3]] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_ranks_liquid_theme_above_thin_high_change_theme():
    groups = {
        "저유동성급등": {
            "sources": ["NAVER"],
            "members": [
                _member("A", "급등1"),
                _member("B", "급등2"),
                _member("C", "급등3"),
            ],
        },
        "대금동반상승": {
            "sources": ["NAVER"],
            "members": [
                _member("D", "대금1"),
                _member("E", "대금2"),
                _member("F", "대금3"),
            ],
        },
    }
    svc, _ = _service(groups)
    rankings = {
        "all_stocks": [
            _stock("A", "급등1", 10.0, 200_000_000),
            _stock("B", "급등2", 9.0, 0),
            _stock("C", "급등3", 8.0, 0),
            _stock("D", "대금1", 5.0, 300_000_000_000),
            _stock("E", "대금2", 4.0, 250_000_000_000),
            _stock("F", "대금3", 3.0, 200_000_000_000),
        ],
        "program_all_stocks": [],
    }

    resp = await svc.build_daily_theme_report(rankings, "20260630")

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert [item["normalized_name"] for item in resp.data] == ["대금동반상승", "저유동성급등"]
    liquid, thin = resp.data
    assert liquid["leader_avg_change_rate"] < thin["leader_avg_change_rate"]
    assert liquid["theme_score"] > thin["theme_score"]
    assert thin["zero_trading_value_ratio"] == 66.67


@pytest.mark.asyncio
async def test_high_liquidity_low_momentum_does_not_dominate_stronger_theme():
    groups = {
        "대형주저탄력": {
            "sources": ["NAVER"],
            "members": [
                _member("A", "대형1"),
                _member("B", "대형2"),
                _member("C", "대형3"),
            ],
        },
        "중형주강세": {
            "sources": ["NAVER"],
            "members": [
                _member("D", "강세1"),
                _member("E", "강세2"),
                _member("F", "강세3"),
            ],
        },
    }
    svc, _ = _service(groups)
    rankings = {
        "all_stocks": [
            _stock("A", "대형1", 1.5, 10_000_000_000_000),
            _stock("B", "대형2", 1.0, 10_000_000_000_000),
            _stock("C", "대형3", 0.5, 10_000_000_000_000),
            _stock("D", "강세1", 8.0, 10_000_000_000),
            _stock("E", "강세2", 7.0, 10_000_000_000),
            _stock("F", "강세3", 6.0, 10_000_000_000),
        ],
        "program_all_stocks": [],
    }

    resp = await svc.build_daily_theme_report(rankings, "20260630")

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert [item["normalized_name"] for item in resp.data] == ["중형주강세", "대형주저탄력"]
    strong, mega = resp.data
    assert strong["leader_avg_change_rate"] > mega["leader_avg_change_rate"]
    assert strong["theme_score"] > mega["theme_score"]


@pytest.mark.asyncio
async def test_skips_theme_with_less_than_min_members():
    svc, _ = _service({
        "개별주": {
            "sources": ["NAVER"],
            "members": [_member("A", "A"), _member("B", "B")],
        }
    })
    rankings = {"all_stocks": [_stock("A", "A", 10, 100), _stock("B", "B", 9, 100)]}

    resp = await svc.build_daily_theme_report(rankings, "20260630")

    assert resp.data == []
