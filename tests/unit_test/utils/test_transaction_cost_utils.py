import pytest
from utils.transaction_cost_utils import TransactionCostUtils

class TestTransactionCostUtils:
    def test_calculate_cost_buy(self):
        """매수 비용 계산 테스트 (수수료만 적용)"""
        price = 10000
        qty = 10
        # 수수료율: 0.0140527%
        expected_fee = price * qty * 0.000140527
        
        cost = TransactionCostUtils.calculate_cost(price, qty, is_sell=False)
        assert cost == pytest.approx(expected_fee)

    def test_calculate_cost_sell(self):
        """매도 비용 계산 테스트 (수수료 + 세금 적용)"""
        price = 10000
        qty = 10
        # 수수료율: 0.0140527%, 세금: 0.20%
        expected_fee = price * qty * 0.000140527
        expected_tax = price * qty * 0.002
        
        cost = TransactionCostUtils.calculate_cost(price, qty, is_sell=True)
        assert cost == pytest.approx(expected_fee + expected_tax)

    def test_get_return_rate_without_cost(self):
        """비용 미적용 수익률 계산 테스트"""
        buy_price = 10000
        sell_price = 11000
        qty = 1
        # (11000 - 10000) / 10000 * 100 = 10.0%
        ror = TransactionCostUtils.get_return_rate(buy_price, sell_price, qty, apply_cost=False)
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
        
        ror = TransactionCostUtils.get_return_rate(buy_price, sell_price, qty, apply_cost=True)
        assert ror == pytest.approx(expected_ror)
        assert ror < 10.0  # 비용이 빠지므로 10%보다 낮아야 함

    def test_get_return_rate_zero_buy_price(self):
        """매수가가 0일 때 0.0 반환 테스트"""
        assert TransactionCostUtils.get_return_rate(0, 10000) == 0.0

    # ─────────────────────────────────────────────────────────────
    # P0 0-9: net_return_pct — 라이브 stop/take_profit trigger 용
    # ─────────────────────────────────────────────────────────────

    def test_net_return_pct_breakeven_is_negative_due_to_cost(self):
        """동일 가격(buy == sell) 이면 net 수익률은 매도세/수수료만큼 음수."""
        result = TransactionCostUtils.net_return_pct(10000, 10000)
        # 매수 fee + 매도 fee + 매도세 ≈ 0.228% drag
        assert result < 0
        assert result == pytest.approx(-0.2278, abs=0.01)

    def test_net_return_pct_matches_get_return_rate_with_cost(self):
        """net_return_pct 는 get_return_rate(qty=1, apply_cost=True) 의 alias 여야 한다."""
        for buy, sell in [(10000, 10500), (12000, 10000), (5000, 6000)]:
            net = TransactionCostUtils.net_return_pct(buy, sell)
            ref = TransactionCostUtils.get_return_rate(buy, sell, qty=1, apply_cost=True)
            assert net == pytest.approx(ref)

    def test_net_return_pct_qty_invariant(self):
        """net_return_pct 는 qty 와 무관해야 한다 (비율 비교)."""
        net_qty1 = TransactionCostUtils.net_return_pct(10000, 11000)
        # 동일 비율을 100주에서도 검증 — get_return_rate 가 비율 반환이므로 같아야 함
        ref_qty100 = TransactionCostUtils.get_return_rate(10000, 11000, qty=100, apply_cost=True)
        assert net_qty1 == pytest.approx(ref_qty100)

    def test_net_return_pct_stop_triggers_earlier_than_gross(self):
        """가격이 -1.8% 하락하면 gross 는 -2% stop 미발동, net 은 발동 가능해야 한다."""
        buy = 10000
        sell = int(buy * (1 - 0.018))  # gross -1.8%
        gross = TransactionCostUtils.get_return_rate(buy, sell, qty=1, apply_cost=False)
        net = TransactionCostUtils.net_return_pct(buy, sell)
        assert gross > -2.0   # gross 기준에선 stop 미발동
        assert net <= -2.0    # net 기준에선 stop 발동 (비용 drag 추가됨)

    def test_net_return_pct_take_profit_triggers_later_than_gross(self):
        """가격이 +5% 상승하면 gross 는 +5% 익절 발동, net 은 미발동."""
        buy = 10000
        sell = int(buy * 1.05)
        gross = TransactionCostUtils.get_return_rate(buy, sell, qty=1, apply_cost=False)
        net = TransactionCostUtils.net_return_pct(buy, sell)
        assert gross >= 5.0   # gross 기준 발동
        assert net < 5.0      # net 기준 미발동 (비용 drag)

    def test_net_return_pct_zero_buy_price_safe(self):
        """매수가 0 이면 0.0 반환 (분모 보호)."""
        assert TransactionCostUtils.net_return_pct(0, 10000) == 0.0