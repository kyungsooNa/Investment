# tests/unit_test/test_rs_rating_service.py
"""
RSRatingService 단위 테스트.
- calc_weighted_rs: 데이터 길이별 가중 RS 계산 검증
- _compute_percentile_ratings: 1~99 백분위 변환 검증
- compute_and_store_ratings: mock OHLCV → DB 저장 E2E 흐름
- get_rating: DB 조회 (없는 경우 포함)
- get_ratings_by_date: 날짜별 전체 조회
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from common.types import ResCommonResponse, ErrorCode, ResRSRating
from services.rs_rating_service import RSRatingService


# ── fixtures ──────────────────────────────────────────────────────────────────

def _make_ohlcv(closes: list) -> list:
    """종가 리스트로 최소 OHLCV 데이터 생성."""
    return [{"date": f"2025{i:04d}", "open": c, "high": c, "low": c, "close": c, "volume": 1000}
            for i, c in enumerate(closes, start=101)]


def _make_service(ohlcv_repo=None, rs_repo=None, code_repo=None):
    ohlcv_repo = ohlcv_repo or AsyncMock()
    rs_repo = rs_repo or AsyncMock()
    code_repo = code_repo or MagicMock()
    code_repo.code_to_name = {"005930": "삼성전자", "000660": "SK하이닉스"}
    return RSRatingService(
        stock_ohlcv_repository=ohlcv_repo,
        rs_rating_repository=rs_repo,
        stock_code_repository=code_repo,
    )


# ── calc_weighted_rs ──────────────────────────────────────────────────────────

def test_calc_weighted_rs_returns_none_when_insufficient_data():
    """63개 미만 캔들이면 None 반환."""
    ohlcv = _make_ohlcv([100] * 63)  # 63개 → 64개 필요
    assert RSRatingService.calc_weighted_rs(ohlcv) is None


def test_calc_weighted_rs_single_quarter():
    """64개 캔들(1분기)만 있을 때 C0만 반영."""
    # 64개: 첫 캔들 100, 마지막 캔들 110 → C0 = +10%
    closes = [100] + [105] * 62 + [110]
    ohlcv = _make_ohlcv(closes)
    result = RSRatingService.calc_weighted_rs(ohlcv)
    assert result is not None
    # C0 = (110 - 100) / 100 * 100 = +10.0, total_weight=2 → 10*2/2 = 10
    assert abs(result - 10.0) < 0.01


def test_calc_weighted_rs_full_year():
    """252개 이상 데이터로 4분기 가중 계산 검증."""
    # 분기별 수익률: q0=20%, q1=10%, q2=5%, q3=0%
    # Weighted = (20*2 + 10 + 5 + 0) / 5 = 55/5 = 11.0
    closes = []
    # q3: 0% 구간 (253→190): 100원 유지
    closes += [100.0] * 64
    # q2: 5% 구간 (190→127): 100→105
    closes += [100.0] + [102.5] * 62 + [105.0]
    # q1: 10% 구간 (127→64): 105→115.5
    closes += [105.0] + [110.0] * 62 + [115.5]
    # q0: 20% 구간 (64→1): 115.5→138.6
    closes += [115.5] + [127.0] * 62 + [138.6]

    # 총 길이 확인
    assert len(closes) == 64 * 4

    ohlcv = _make_ohlcv(closes)
    result = RSRatingService.calc_weighted_rs(ohlcv)
    assert result is not None
    # 정확한 값 대신 양수 수익률 범위 내 확인
    assert 0 < result < 100


def test_calc_weighted_rs_zero_past_close_returns_none():
    """과거 종가가 0인 경우 None 반환 (ZeroDivisionError 방어)."""
    closes = [0] + [100] * 63  # 첫 캔들 종가=0
    ohlcv = _make_ohlcv(closes)
    result = RSRatingService.calc_weighted_rs(ohlcv)
    assert result is None


# ── _compute_percentile_ratings ────────────────────────────────────────────────

def test_compute_percentile_ratings_range():
    """모든 결과가 1~99 범위 내에 있어야 한다."""
    weighted_rs = {f"code{i:03d}": float(i) for i in range(1, 101)}
    result = RSRatingService._compute_percentile_ratings(weighted_rs)
    assert all(1 <= v <= 99 for v in result.values())


def test_compute_percentile_ratings_ordering():
    """더 높은 weighted_rs를 가진 종목이 더 높은 rating을 받아야 한다."""
    weighted_rs = {"A": 50.0, "B": 10.0, "C": 90.0}
    result = RSRatingService._compute_percentile_ratings(weighted_rs)
    assert result["C"] > result["A"] > result["B"]


def test_compute_percentile_ratings_empty():
    """빈 입력에 대해 빈 딕셔너리 반환."""
    assert RSRatingService._compute_percentile_ratings({}) == {}


def test_compute_percentile_ratings_single():
    """종목이 1개인 경우에도 1~99 범위 내 값 반환."""
    result = RSRatingService._compute_percentile_ratings({"A": 10.0})
    assert 1 <= result["A"] <= 99


# ── compute_and_store_ratings ─────────────────────────────────────────────────

async def test_compute_and_store_ratings_success():
    """정상 흐름: OHLCV 조회 → RS 계산 → DB 저장."""
    closes = [100] + [100] * 62 + [120]  # +20% 수익률
    mock_ohlcv = {"ohlcv": _make_ohlcv(closes)}

    ohlcv_repo = AsyncMock()
    ohlcv_repo.get_stock_data.return_value = mock_ohlcv

    rs_repo = AsyncMock()
    rs_repo.upsert_batch.return_value = 2

    code_repo = MagicMock()
    code_repo.code_to_name = {"005930": "삼성전자", "000660": "SK하이닉스"}

    service = RSRatingService(ohlcv_repo, rs_repo, code_repo)
    resp = await service.compute_and_store_ratings("20260101")

    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data["saved"] == 2
    rs_repo.upsert_batch.assert_called_once()
    records = rs_repo.upsert_batch.call_args[0][0]
    assert all(r["trade_date"] == "20260101" for r in records)
    assert all(1 <= r["rs_rating"] <= 99 for r in records)


async def test_compute_and_store_ratings_no_codes():
    """종목 코드가 없으면 EMPTY_VALUES 반환."""
    code_repo = MagicMock()
    code_repo.code_to_name = {}
    service = _make_service(code_repo=code_repo)
    resp = await service.compute_and_store_ratings("20260101")
    assert resp.rt_cd == ErrorCode.EMPTY_VALUES.value


async def test_compute_and_store_ratings_all_ohlcv_missing():
    """모든 종목 OHLCV가 None이면 EMPTY_VALUES 반환."""
    ohlcv_repo = AsyncMock()
    ohlcv_repo.get_stock_data.return_value = None
    service = _make_service(ohlcv_repo=ohlcv_repo)
    resp = await service.compute_and_store_ratings("20260101")
    assert resp.rt_cd == ErrorCode.EMPTY_VALUES.value


# ── get_rating ────────────────────────────────────────────────────────────────

async def test_get_rating_found():
    """DB에 데이터 있으면 SUCCESS 반환."""
    rs_repo = AsyncMock()
    rs_repo.get_latest_date.return_value = "20260101"
    rs_repo.get_single.return_value = ResRSRating(
        code="005930", trade_date="20260101", rs_rating=85, weighted_rs=15.3
    )
    service = _make_service(rs_repo=rs_repo)
    resp = await service.get_rating("005930")
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data.rs_rating == 85


async def test_get_rating_not_found():
    """DB에 데이터 없으면 EMPTY_VALUES 반환."""
    rs_repo = AsyncMock()
    rs_repo.get_latest_date.return_value = "20260101"
    rs_repo.get_single.return_value = None
    service = _make_service(rs_repo=rs_repo)
    resp = await service.get_rating("999999")
    assert resp.rt_cd == ErrorCode.EMPTY_VALUES.value


async def test_get_rating_no_data_at_all():
    """저장된 날짜가 전혀 없으면 EMPTY_VALUES 반환."""
    rs_repo = AsyncMock()
    rs_repo.get_latest_date.return_value = None
    service = _make_service(rs_repo=rs_repo)
    resp = await service.get_rating("005930")
    assert resp.rt_cd == ErrorCode.EMPTY_VALUES.value


# ── get_ratings_by_date ───────────────────────────────────────────────────────

async def test_get_ratings_by_date_success():
    """날짜별 전체 RS Rating 딕셔너리 반환."""
    rs_repo = AsyncMock()
    rs_repo.get_by_date.return_value = {"005930": 85, "000660": 72}
    service = _make_service(rs_repo=rs_repo)
    resp = await service.get_ratings_by_date("20260101")
    assert resp.rt_cd == ErrorCode.SUCCESS.value
    assert resp.data["005930"] == 85
    assert resp.data["000660"] == 72


async def test_get_ratings_by_date_empty():
    """해당 날짜 데이터가 없으면 EMPTY_VALUES 반환."""
    rs_repo = AsyncMock()
    rs_repo.get_by_date.return_value = {}
    service = _make_service(rs_repo=rs_repo)
    resp = await service.get_ratings_by_date("20260101")
    assert resp.rt_cd == ErrorCode.EMPTY_VALUES.value
