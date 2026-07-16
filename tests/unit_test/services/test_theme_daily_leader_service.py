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


def _service(groups, snapshot_repo=None):
    repo = MagicMock()
    repo.get_groups = AsyncMock(return_value=groups)
    return ThemeDailyLeaderService(
        classification_repository=repo,
        snapshot_repository=snapshot_repo,
        logger=MagicMock(),
    ), repo


@pytest.mark.asyncio
async def test_intraday_report_calculates_recent_three_minute_value_and_delta():
    groups = {
        "반도체": {
            "sources": ["NAVER"],
            "members": [_member("A", "A"), _member("B", "B"), _member("C", "C")],
        }
    }
    snapshot_repo = MagicMock()
    snapshot_repo.save_snapshot = AsyncMock()
    snapshot_repo.get_values_at_or_before = AsyncMock(side_effect=[
        {"A": 900_000_000, "B": 1_800_000_000, "C": 2_700_000_000},
        {"A": 800_000_000, "B": 1_700_000_000, "C": 2_500_000_000},
    ])
    svc, _ = _service(groups, snapshot_repo=snapshot_repo)
    rankings = {"all_stocks": [
        _stock("A", "A", 10, 1_000_000_100),
        _stock("B", "B", 9, 2_000_000_300),
        _stock("C", "C", 8, 3_000_000_600),
    ]}

    resp = await svc.build_intraday_theme_report(
        rankings,
        report_time="20260715 10:06",
        window_minutes=3,
    )

    theme = resp.data[0]
    assert theme["recent_trading_value_won"] == 600_001_000
    assert theme["previous_trading_value_won"] == 400_000_000
    assert theme["recent_trading_value_change_won"] == 200_001_000
    assert theme["recent_coverage_count"] == 3
    assert theme["recent_window_minutes"] == 3
    assert theme["leaders"][0]["recent_trading_value_won"] == 100_000_100
    snapshot_repo.save_snapshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_intraday_report_gracefully_marks_missing_history():
    groups = {
        "로봇": {
            "sources": ["NAVER"],
            "members": [_member("A", "A"), _member("B", "B"), _member("C", "C")],
        }
    }
    snapshot_repo = MagicMock()
    snapshot_repo.save_snapshot = AsyncMock()
    snapshot_repo.get_values_at_or_before = AsyncMock(return_value={})
    svc, _ = _service(groups, snapshot_repo=snapshot_repo)

    resp = await svc.build_intraday_theme_report(
        {"all_stocks": [_stock("A", "A", 10, 100), _stock("B", "B", 9, 200), _stock("C", "C", 8, 300)]},
        report_time="20260715 09:01",
    )

    assert resp.data[0]["recent_coverage_count"] == 0
    assert resp.data[0]["recent_trading_value_won"] == 0


@pytest.mark.asyncio
async def test_intraday_report_prioritizes_leadership_score_over_recent_trading_value():
    groups = {
        "advancing_theme": {
            "sources": ["NAVER"],
            "members": [_member("U1", "U1"), _member("U2", "U2"), _member("U3", "U3")],
        },
        "falling_theme": {
            "sources": ["NAVER"],
            "members": [_member("D1", "D1"), _member("D2", "D2"), _member("D3", "D3")],
        },
    }
    snapshot_repo = MagicMock()
    snapshot_repo.save_snapshot = AsyncMock()
    snapshot_repo.get_values_at_or_before = AsyncMock(side_effect=[
        {
            "U1": 900_000_000, "U2": 900_000_000, "U3": 900_000_000,
            "D1": 1_000_000_000, "D2": 1_000_000_000, "D3": 1_000_000_000,
        },
        {
            "U1": 800_000_000, "U2": 800_000_000, "U3": 800_000_000,
            "D1": 900_000_000, "D2": 900_000_000, "D3": 900_000_000,
        },
    ])
    svc, _ = _service(groups, snapshot_repo=snapshot_repo)
    rankings = {"all_stocks": [
        _stock("U1", "U1", 3.0, 1_000_000_000),
        _stock("U2", "U2", 2.5, 1_000_000_000),
        _stock("U3", "U3", 2.0, 1_000_000_000),
        _stock("D1", "D1", -4.0, 10_000_000_000),
        _stock("D2", "D2", -5.0, 10_000_000_000),
        _stock("D3", "D3", -6.0, 10_000_000_000),
    ]}

    resp = await svc.build_intraday_theme_report(
        rankings,
        report_time="20260715 10:06",
        window_minutes=3,
    )

    assert resp.data[0]["normalized_name"] == "advancing_theme"
    assert resp.data[0]["market_leadership_score"] > resp.data[1]["market_leadership_score"]
    assert resp.data[0]["recent_trading_value_won"] < resp.data[1]["recent_trading_value_won"]


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
    assert [item["normalized_name"] for item in resp.data] == ["반도체/소부장", "우주항공"]

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
    assert liquid["leader_avg_change_rate"] > thin["leader_avg_change_rate"]
    assert thin["momentum_leaders"][0]["change_rate"] > liquid["leaders"][0]["change_rate"]
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


@pytest.mark.asyncio
async def test_separates_thin_momentum_stock_from_liquid_theme_leaders():
    """10억 미만 급등주는 상승률 상위로만 보이고 유동성 주도주에서는 제외한다."""
    svc, _ = _service({
        "OLED": {
            "sources": ["NAVER"],
            "members": [_member("A", "베셀"), _member("B", "티에스이"), _member("C", "예스티")],
        }
    })
    rankings = {
        "all_stocks": [
            _stock("A", "베셀", 23.2, 900_000_000),
            _stock("B", "티에스이", 20.0, 32_300_000_000),
            _stock("C", "예스티", 18.8, 17_200_000_000),
        ],
        "program_all_stocks": [],
    }

    resp = await svc.build_daily_theme_report(rankings, "20260710")

    theme = resp.data[0]
    assert [item["code"] for item in theme["leaders"]] == ["B", "C"]
    assert [item["code"] for item in theme["momentum_leaders"]] == ["A", "B", "C"]
    assert theme["liquid_member_count"] == 2
    assert theme["is_liquid_theme"] is True


@pytest.mark.asyncio
async def test_penalizes_theme_trading_value_concentrated_in_one_stock():
    """동일한 상승률·총대금이라도 한 종목 쏠림 테마는 분산 테마보다 뒤로 보낸다."""
    svc, _ = _service({
        "단일종목쏠림": {
            "sources": ["NAVER"],
            "members": [_member("A", "쏠림1"), _member("B", "쏠림2"), _member("C", "쏠림3")],
        },
        "대금분산": {
            "sources": ["NAVER"],
            "members": [_member("D", "분산1"), _member("E", "분산2"), _member("F", "분산3")],
        },
    })
    rankings = {"all_stocks": [
        _stock("A", "쏠림1", 10, 90_000_000_000), _stock("B", "쏠림2", 10, 5_000_000_000), _stock("C", "쏠림3", 10, 5_000_000_000),
        _stock("D", "분산1", 10, 34_000_000_000), _stock("E", "분산2", 10, 33_000_000_000), _stock("F", "분산3", 10, 33_000_000_000),
    ]}

    resp = await svc.build_daily_theme_report(rankings, "20260710")

    assert [theme["normalized_name"] for theme in resp.data] == ["대금분산", "단일종목쏠림"]
    assert resp.data[1]["trading_value_concentration_ratio"] == 90.0


@pytest.mark.asyncio
async def test_ranks_high_liquidity_theme_above_thin_higher_momentum_theme():
    """시장 주도 순위는 소수 급등 테마보다 대금이 크게 붙은 테마를 우선한다."""
    svc, _ = _service({
        "통신장비": {
            "sources": ["NAVER"],
            "members": [_member("A", "기가레인"), _member("B", "빛과전자"), _member("C", "주성코퍼레이션")],
        },
        "반도체장비": {
            "sources": ["NAVER"],
            "members": [_member("D", "저스템"), _member("E", "피에스케이"), _member("F", "유진테크")],
        },
    })
    rankings = {"all_stocks": [
        _stock("A", "기가레인", 29.9, 2_100_000_000), _stock("B", "빛과전자", 29.9, 16_200_000_000), _stock("C", "주성코퍼레이션", 19.1, 17_900_000_000),
        _stock("D", "저스템", 24.1, 20_500_000_000), _stock("E", "피에스케이", 23.3, 177_700_000_000), _stock("F", "유진테크", 20.0, 1_240_500_000_000),
    ]}

    resp = await svc.build_daily_theme_report(rankings, "20260710")

    assert [theme["normalized_name"] for theme in resp.data] == ["반도체장비", "통신장비"]
    semi, telecom = resp.data
    assert semi["market_leadership_score"] > telecom["market_leadership_score"]
    assert telecom["leader_avg_change_rate"] > semi["leader_avg_change_rate"]


@pytest.mark.asyncio
async def test_liquidity_bonus_uses_advancing_trading_value_only():
    """하락 대형주의 거래대금은 테마 유동성 보너스를 만들지 않는다."""
    svc, _ = _service({
        "대형주혼합": {
            "sources": ["NAVER"],
            "members": [_member("A", "상승1"), _member("B", "상승2"), _member("C", "하락대형주")],
        }
    })
    rankings = {"all_stocks": [
        _stock("A", "상승1", 10.0, 20_000_000_000),
        _stock("B", "상승2", 5.0, 20_000_000_000),
        _stock("C", "하락대형주", -1.0, 1_000_000_000_000),
    ]}

    resp = await svc.build_daily_theme_report(rankings, "20260714")

    theme = resp.data[0]
    assert theme["advancing_trading_value_sum_won"] == 40_000_000_000
    assert theme["liquidity_bonus"] == 1.2
    assert theme["is_liquid_theme"] is True


@pytest.mark.asyncio
async def test_theme_requires_two_liquid_advancers_and_half_breadth():
    """거래대금이 커도 상승 확산 조건이 부족하면 주도 테마로 인정하지 않는다."""
    svc, _ = _service({
        "단일상승": {
            "sources": ["NAVER"],
            "members": [_member("A", "상승1"), _member("B", "하락1"), _member("C", "하락2")],
        },
        "낮은확산": {
            "sources": ["NAVER"],
            "members": [
                _member("D", "상승2"), _member("E", "상승3"), _member("F", "하락3"),
                _member("G", "하락4"), _member("H", "하락5"),
            ],
        },
    })
    rankings = {"all_stocks": [
        _stock("A", "상승1", 20.0, 100_000_000_000),
        _stock("B", "하락1", -1.0, 100_000_000_000),
        _stock("C", "하락2", -1.0, 100_000_000_000),
        _stock("D", "상승2", 10.0, 100_000_000_000),
        _stock("E", "상승3", 9.0, 100_000_000_000),
        _stock("F", "하락3", -1.0, 100_000_000_000),
        _stock("G", "하락4", -1.0, 100_000_000_000),
        _stock("H", "하락5", -1.0, 100_000_000_000),
    ]}

    resp = await svc.build_daily_theme_report(rankings, "20260714")

    by_name = {theme["normalized_name"]: theme for theme in resp.data}
    assert by_name["단일상승"]["liquid_advancing_member_count"] == 1
    assert by_name["단일상승"]["is_liquid_theme"] is False
    assert by_name["단일상승"]["liquidity_bonus"] == 0.0
    assert by_name["낮은확산"]["liquid_advancing_member_count"] == 2
    assert by_name["낮은확산"]["advancing_ratio"] == 40.0
    assert by_name["낮은확산"]["is_liquid_theme"] is False
    assert by_name["낮은확산"]["liquidity_bonus"] == 0.0
