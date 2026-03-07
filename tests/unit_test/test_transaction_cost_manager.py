import pytest
from managers.transaction_cost_manager import TransactionCostManager

class TestTransactionCostManager:
    def test_calculate_cost_buy(self):
        """매수 비용 계산 테스트 (수수료만 적용)"""
        price = 10000
        qty = 10
        # 수수료율: 0.0140527%
        expected_fee = price * qty * 0.000140527
        
        cost = TransactionCostManager.calculate_cost(price, qty, is_sell=False)
        assert cost == pytest.approx(expected_fee)

    def test_calculate_cost_sell(self):
        """매도 비용 계산 테스트 (수수료 + 세금 적용)"""
        price = 10000
        qty = 10
        # 수수료율: 0.0140527%, 세금: 0.20%
        expected_fee = price * qty * 0.000140527
        expected_tax = price * qty * 0.002
        
        cost = TransactionCostManager.calculate_cost(price, qty, is_sell=True)
        assert cost == pytest.approx(expected_fee + expected_tax)

    def test_get_return_rate_without_cost(self):
        """비용 미적용 수익률 계산 테스트"""
        buy_price = 10000
        sell_price = 11000
        qty = 1
        # (11000 - 10000) / 10000 * 100 = 10.0%
        ror = TransactionCostManager.get_return_rate(buy_price, sell_price, qty, apply_cost=False)
        assert ror == 10.0

    def test_get_return_rate_with_cost(self):
        """비용 적용 수익률 계산 테스트"""
        buy_price = 10000
        sell_price = 11000
        qty = 1
        
        buy_cost = buy_price * qty * 0.000140527
        sell_cost = sell_price * qty * (0.000140527 + 0.002)
        
        total_invest = (buy_price * qty) + buy_cost
        total_retrieve = (sell_price * qty) - sell_cost
        
        expected_ror = ((total_retrieve - total_invest) / total_invest) * 100
        
        ror = TransactionCostManager.get_return_rate(buy_price, sell_price, qty, apply_cost=True)
        assert ror == pytest.approx(expected_ror)
        assert ror < 10.0  # 비용이 빠지므로 10%보다 낮아야 함

    def test_get_return_rate_zero_buy_price(self):
        """매수가가 0일 때 0.0 반환 테스트"""
        assert TransactionCostManager.get_return_rate(0, 10000) == 0.0