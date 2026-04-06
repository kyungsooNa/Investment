# tests/unit_test/utils/test_korea_invest_price_utils.py
import pytest
from utils.korea_invest_price_utils import get_tick_size, adjust_price


class TestGetTickSize:
    """KRX 공식 호가단위 반환 테스트"""

    def test_under_2000(self):
        assert get_tick_size(1) == 1
        assert get_tick_size(999) == 1
        assert get_tick_size(1999) == 1

    def test_2000_to_4999(self):
        assert get_tick_size(2000) == 5
        assert get_tick_size(3000) == 5
        assert get_tick_size(4999) == 5

    def test_5000_to_19999(self):
        assert get_tick_size(5000) == 10
        assert get_tick_size(10000) == 10
        assert get_tick_size(19999) == 10

    def test_20000_to_49999(self):
        assert get_tick_size(20000) == 50
        assert get_tick_size(35000) == 50
        assert get_tick_size(49999) == 50

    def test_50000_to_199999(self):
        assert get_tick_size(50000) == 100
        assert get_tick_size(100000) == 100
        assert get_tick_size(199999) == 100

    def test_200000_to_499999(self):
        assert get_tick_size(200000) == 500
        assert get_tick_size(350000) == 500
        assert get_tick_size(499999) == 500

    def test_500000_and_above(self):
        assert get_tick_size(500000) == 1000
        assert get_tick_size(1000000) == 1000
        assert get_tick_size(9999999) == 1000


class TestAdjustPrice:
    """호가단위 보정 함수 테스트"""

    def test_market_order_zero_unchanged(self):
        """시장가 주문(price=0)은 그대로 반환"""
        assert adjust_price(0) == 0

    def test_negative_price_unchanged(self):
        """음수 가격은 그대로 반환"""
        assert adjust_price(-1) == -1

    def test_already_on_tick(self):
        """이미 호가단위에 맞는 가격은 변경 없음"""
        assert adjust_price(2000) == 2000
        assert adjust_price(5000) == 5000
        assert adjust_price(50000) == 50000
        assert adjust_price(100000) == 100000

    def test_rounds_down_to_tick(self):
        """호가단위에 맞지 않으면 내림 보정"""
        # 1원 단위 구간 (2000 미만)
        assert adjust_price(1500) == 1500  # 이미 맞음 (tick=1)
        assert adjust_price(1001) == 1001  # tick=1, 그대로

        # 5원 단위 구간 (2000~4999)
        assert adjust_price(2003) == 2000
        assert adjust_price(4998) == 4995
        assert adjust_price(2001) == 2000

        # 10원 단위 구간 (5000~19999)
        assert adjust_price(5007) == 5000
        assert adjust_price(19995) == 19990
        assert adjust_price(10001) == 10000

        # 50원 단위 구간 (20000~49999)
        assert adjust_price(20030) == 20000
        assert adjust_price(49999) == 49950
        assert adjust_price(25051) == 25050

        # 100원 단위 구간 (50000~199999)
        assert adjust_price(50050) == 50000
        assert adjust_price(100099) == 100000
        assert adjust_price(150150) == 150100

        # 500원 단위 구간 (200000~499999)
        assert adjust_price(200300) == 200000
        assert adjust_price(499999) == 499500
        assert adjust_price(350250) == 350000

        # 1000원 단위 구간 (500000 이상)
        assert adjust_price(500500) == 500000
        assert adjust_price(1000999) == 1000000

    def test_boundary_values(self):
        """경계값 테스트"""
        # 1999 → tick=1 → 1999 (변화 없음)
        assert adjust_price(1999) == 1999
        # 2000 → tick=5 → 2000 (이미 맞음)
        assert adjust_price(2000) == 2000
        # 2001 → tick=5 → 2000 (내림)
        assert adjust_price(2001) == 2000
        # 4999 → tick=5 → 4995 (내림)
        assert adjust_price(4999) == 4995
        # 5000 → tick=10 → 5000 (이미 맞음)
        assert adjust_price(5000) == 5000
