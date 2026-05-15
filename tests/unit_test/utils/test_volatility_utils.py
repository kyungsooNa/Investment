"""volatility_utils.annualized_return_std 단위 테스트.

20일 종가 수익률의 표준편차를 sqrt(252) 로 연환산한 값. 입력 부족/비양수 close 는 None.
"""
import math

import pytest

from utils.volatility_utils import (
    DEFAULT_LOOKBACK,
    TRADING_DAYS_PER_YEAR,
    annualized_return_std,
)


def test_returns_none_when_insufficient_closes():
    closes = [100.0] * (DEFAULT_LOOKBACK)  # need lookback + 1
    assert annualized_return_std(closes) is None


def test_zero_variance_yields_zero():
    closes = [100.0] * (DEFAULT_LOOKBACK + 1)
    result = annualized_return_std(closes)
    assert result == pytest.approx(0.0, abs=1e-12)


def test_uses_only_last_lookback_plus_one_closes():
    # 앞쪽 다른 패턴은 무시되어야 한다.
    noisy_prefix = [100.0, 50.0, 200.0, 75.0]
    flat_tail = [100.0] * (DEFAULT_LOOKBACK + 1)
    assert annualized_return_std(noisy_prefix + flat_tail) == pytest.approx(0.0, abs=1e-12)


def test_known_two_step_alternating_returns():
    """Lookback 4 로 축소해 손계산 검증."""
    closes = [100.0, 110.0, 100.0, 110.0, 100.0]  # log returns: +ln(1.1), -ln(1.1), +ln(1.1), -ln(1.1)
    expected_std = abs(math.log(1.1))  # sample stdev of {+a, -a, +a, -a} = sqrt(sum((r-0)^2 / 3)) = a*sqrt(4/3)
    n = 4
    a = math.log(1.1)
    sample_stdev = math.sqrt(sum((v - 0.0) ** 2 for v in [a, -a, a, -a]) / (n - 1))
    expected = sample_stdev * math.sqrt(TRADING_DAYS_PER_YEAR)
    result = annualized_return_std(closes, lookback=4)
    assert result == pytest.approx(expected, rel=1e-9)


def test_filters_nonpositive_and_none_then_recounts():
    closes = [None, 0, -5, 100.0, 102.0, 101.0]  # only 3 valid closes → 2 returns; need lookback+1=4 → None
    assert annualized_return_std(closes, lookback=3) is None


def test_invalid_lookback_raises():
    with pytest.raises(ValueError):
        annualized_return_std([100.0, 101.0], lookback=1)
