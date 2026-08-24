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


def _service(groups, snapshot_repo=None, theme_member_exclusions=None):
    repo = MagicMock()
    repo.get_groups = AsyncMock(return_value=groups)
    return ThemeDailyLeaderService(
        classification_repository=repo,
        snapshot_repository=snapshot_repo,
        logger=MagicMock(),
        theme_member_exclusions=theme_member_exclusions,
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
        {"A": 2_900_000_000, "B": 3_800_000_000, "C": 4_700_000_000},
        {"A": 2_800_000_000, "B": 3_700_000_000, "C": 4_500_000_000},
    ])
    svc, _ = _service(groups, snapshot_repo=snapshot_repo)
    rankings = {"all_stocks": [
        _stock("A", "A", 10, 3_000_000_100),
        _stock("B", "B", 9, 4_000_000_300),
        _stock("C", "C", 8, 5_000_000_600),
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
        {"all_stocks": [
            _stock("A", "A", 10, 10_000_000_000),
            _stock("B", "B", 9, 10_000_000_000),
            _stock("C", "C", 8, 10_000_000_000),
        ]},
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
        "slower_theme": {
            "sources": ["NAVER"],
            "members": [_member("D1", "D1"), _member("D2", "D2"), _member("D3", "D3")],
        },
    }
    snapshot_repo = MagicMock()
    snapshot_repo.save_snapshot = AsyncMock()
    snapshot_repo.get_values_at_or_before = AsyncMock(side_effect=[
        {
            "U1": 900_000_000, "U2": 900_000_000, "U3": 900_000_000,
            "D1": 5_000_000_000, "D2": 5_000_000_000, "D3": 5_000_000_000,
        },
        {
            "U1": 800_000_000, "U2": 800_000_000, "U3": 800_000_000,
            "D1": 4_000_000_000, "D2": 4_000_000_000, "D3": 4_000_000_000,
        },
    ])
    svc, _ = _service(groups, snapshot_repo=snapshot_repo)
    rankings = {"all_stocks": [
        _stock("U1", "U1", 10.0, 10_000_000_000),
        _stock("U2", "U2", 9.0, 10_000_000_000),
        _stock("U3", "U3", 8.0, 10_000_000_000),
        _stock("D1", "D1", 1.0, 100_000_000_000),
        _stock("D2", "D2", 1.0, 100_000_000_000),
        _stock("D3", "D3", 1.0, 100_000_000_000),
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
async def test_intraday_report_ranks_liquid_theme_above_thin_high_score_theme():
    groups = {
        "의료AI": {
            "sources": ["NAVER"],
            "members": [_member("A", "셀바스AI"), _member("B", "셀바스헬스케어"), _member("C", "시선AI")],
        },
        "AI 챗봇": {
            "sources": ["NAVER"],
            "members": [_member("A", "셀바스AI"), _member("D", "NAVER"), _member("E", "솔트룩스")],
        },
    }
    snapshot_repo = MagicMock()
    snapshot_repo.save_snapshot = AsyncMock()
    snapshot_repo.get_values_at_or_before = AsyncMock(side_effect=[
        {"A": 5_000_000_000, "B": 200_000_000, "C": 400_000_000, "D": 130_000_000_000, "E": 900_000_000},
        {"A": 4_800_000_000, "B": 100_000_000, "C": 300_000_000, "D": 129_000_000_000, "E": 800_000_000},
    ])
    svc, _ = _service(groups, snapshot_repo=snapshot_repo)
    rankings = {"all_stocks": [
        _stock("A", "셀바스AI", 29.2, 6_200_000_000),
        _stock("B", "셀바스헬스케어", 15.3, 300_000_000),
        _stock("C", "시선AI", 13.4, 500_000_000),
        _stock("D", "NAVER", 8.2, 143_000_000_000),
        _stock("E", "솔트룩스", 18.1, 1_200_000_000),
    ]}

    resp = await svc.build_intraday_theme_report(
        rankings,
        report_time="20260727 10:10",
        window_minutes=3,
    )

    assert [theme["normalized_name"] for theme in resp.data] == ["AI 챗봇"]
    assert resp.data[0]["is_liquid_theme"] is True


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
    assert [item["normalized_name"] for item in resp.data] == ["대금동반상승"]
    liquid = resp.data[0]
    assert liquid["leader_avg_change_rate"] == 4.0
    assert liquid["theme_score"] > 0


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
async def test_filters_weak_relative_themes_without_market_leadership():
    """약세장 상대 순위만 높은 테마는 주도 테마로 반환하지 않는다."""
    svc, _ = _service({
        "반도체장비": {
            "sources": ["NAVER"],
            "members": [_member("A", "씨피시스템"), _member("B", "주성엔지니어링"), _member("C", "원익IPS")],
        },
        "로봇": {
            "sources": ["NAVER"],
            "members": [
                _member("D", "씨피시스템"), _member("E", "코스모로보틱스"), _member("F", "기아"),
                _member("G", "하락1"), _member("H", "하락2"), _member("I", "하락3"), _member("J", "하락4"),
            ],
        },
    })
    rankings = {"all_stocks": [
        _stock("A", "씨피시스템", 17.2, 104_900_000_000),
        _stock("B", "주성엔지니어링", -12.5, 53_400_000_000),
        _stock("C", "원익IPS", -13.4, 30_200_000_000),
        _stock("D", "씨피시스템", 17.2, 104_900_000_000),
        _stock("E", "코스모로보틱스", 4.2, 56_500_000_000),
        _stock("F", "기아", -5.1, 34_400_000_000),
        _stock("G", "하락1", -7.0, 30_000_000_000),
        _stock("H", "하락2", -8.0, 30_000_000_000),
        _stock("I", "하락3", -9.0, 30_000_000_000),
        _stock("J", "하락4", -10.0, 30_000_000_000),
    ]}

    resp = await svc.build_daily_theme_report(rankings, "20260728")

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data == []


@pytest.mark.asyncio
async def test_separates_thin_momentum_stock_from_liquid_theme_leaders():
    """30억 미만 급등주는 상승률 상위로만 보이고 유동성 주도주에서는 제외한다."""
    svc, _ = _service({
        "OLED": {
            "sources": ["NAVER"],
            "members": [_member("A", "베셀"), _member("B", "티에스이"), _member("C", "예스티")],
        }
    })
    rankings = {
        "all_stocks": [
            _stock("A", "베셀", 23.2, 2_900_000_000),
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
async def test_excludes_configured_off_theme_members_before_scoring():
    """명백히 부자연스러운 편입 종목은 테마 평균과 대표주에서 제외한다."""
    svc, _ = _service(
        {
            "의료기기": {
                "sources": ["NAVER"],
                "members": [
                    _member("A", "나노엔텍"),
                    _member("B", "의료기기2"),
                    _member("C", "의료기기3"),
                    _member("D", "삼성전자"),
                ],
            }
        },
        theme_member_exclusions={"의료기기": {"names": ["삼성전자"]}},
    )
    rankings = {"all_stocks": [
        _stock("A", "나노엔텍", 29.9, 5_000_000_000),
        _stock("B", "의료기기2", 10.0, 4_000_000_000),
        _stock("C", "의료기기3", 8.0, 3_000_000_000),
        _stock("D", "삼성전자", 5.3, 3_477_700_000_000),
    ]}

    resp = await svc.build_daily_theme_report(rankings, "20260722")

    theme = resp.data[0]
    assert [member["name"] for member in theme["members"]] == ["나노엔텍", "의료기기2", "의료기기3"]
    assert [leader["name"] for leader in theme["leaders"]] == ["나노엔텍", "의료기기2", "의료기기3"]
    assert theme["excluded_member_count"] == 1


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

    assert resp.data == []


# --- 값 변환 헬퍼 -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [(None, 0.0), ("", 0.0), ("1,050.5", 1050.5), (7, 7.0), ("숫자아님", 0.0), (object(), 0.0)],
)
def test_float_coercion(raw, expected):
    from services.theme_daily_leader_service import _to_float

    assert _to_float(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [(None, 0), ("", 0), ("1,050", 1050), ("7.9", 7), ("숫자아님", 0), (object(), 0)],
)
def test_int_coercion(raw, expected):
    from services.theme_daily_leader_service import _to_int

    assert _to_int(raw) == expected


def test_field_getter_supports_dicts_and_objects():
    from types import SimpleNamespace

    from services.theme_daily_leader_service import _get

    assert _get({"a": 1}, "a") == 1
    assert _get({"a": 1}, "b", "기본") == "기본"
    assert _get(SimpleNamespace(a=1), "a") == 1
    assert _get(SimpleNamespace(), "b", "기본") == "기본"


# --- 예외 및 설정 경로 ------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_error_is_reported_as_unknown_error():
    service, repo = _service({})
    repo.get_groups = AsyncMock(side_effect=RuntimeError("분류 DB 오류"))

    result = await service.build_daily_theme_report({"all_stocks": []}, report_date="20260801")

    assert result.rt_cd == ErrorCode.UNKNOWN_ERROR.value
    assert "분류 DB 오류" in result.msg1
    service._logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_report_is_empty_without_ranking_rows():
    service, _ = _service({"반도체": {"sources": ["NAVER"], "members": [_member("005930", "삼성전자")]}})

    result = await service.build_daily_theme_report({"all_stocks": []}, report_date="20260801")

    assert result.rt_cd == ErrorCode.SUCCESS.value
    assert result.data == []


def test_exclusion_config_is_empty_without_a_path():
    service, _ = _service({})

    assert service._load_theme_member_exclusions("") == {}
    assert service._load_theme_member_exclusions("없는파일.yaml") == {}


def test_exclusion_config_load_failure_is_logged_and_ignored(tmp_path):
    service, _ = _service({})
    broken = tmp_path / "exclusions.yaml"
    broken.write_text("exclusions: [불완전한 yaml", encoding="utf-8")

    assert service._load_theme_member_exclusions(str(broken)) == {}
    service._logger.warning.assert_called_once()


def test_exclusion_config_is_read_from_yaml(tmp_path):
    service, _ = _service({})
    cfg = tmp_path / "exclusions.yaml"
    cfg.write_text(
        "exclusions:\n  반도체:\n    codes: ['005930']\n    names: ['삼성전자']\n",
        encoding="utf-8",
    )

    loaded = service._load_theme_member_exclusions(str(cfg))

    assert loaded["반도체"]["codes"] == {"005930"}
    assert loaded["반도체"]["names"] == {"삼성전자"}


def test_exclusion_normalizer_ignores_unusable_shapes():
    service, _ = _service({})
    normalize = service._normalize_theme_member_exclusions

    assert normalize(None) == {}
    assert normalize("설정 아님") == {}
    assert normalize({"반도체": "규칙 아님"}) == {}
    assert normalize({"반도체": {"codes": ["005930", ""], "names": []}}) == {
        "반도체": {"codes": {"005930"}, "names": set()}
    }


def test_report_time_parser_accepts_datetime_and_string():
    from datetime import datetime

    service, _ = _service({})

    parsed = service._parse_report_time(datetime(2026, 8, 1, 10, 30, 45, 500))
    assert (parsed.second, parsed.microsecond) == (0, 0)

    assert service._parse_report_time("20260801 10:30") == datetime(2026, 8, 1, 10, 30)


def test_liquidity_bonus_is_zero_below_the_threshold():
    service, _ = _service({})
    cls = type(service)

    assert cls._build_liquidity_bonus(1, 10.0) == 0.0
    # 저탄력(5% 미만) 테마는 보너스를 1.5 로 제한한다.
    huge = cls.MIN_LIQUID_THEME_TRADING_VALUE_WON * 10_000
    assert cls._build_liquidity_bonus(huge, 1.0) == 1.5
    assert cls._build_liquidity_bonus(huge, 9.0) > 1.5
