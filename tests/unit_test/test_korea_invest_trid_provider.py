import pytest
from unittest.mock import MagicMock, patch
from brokers.korea_investment.korea_invest_trid_provider import KoreaInvestTrIdProvider
from brokers.korea_investment.korea_invest_trid_keys import TrId, TrIdLeaf

@pytest.fixture
def mock_env():
    env = MagicMock()
    env.is_paper_trading = True
    return env

@pytest.fixture
def mock_tr_ids():
    return {
        "quotations": {
            "daily_itemchartprice": "TR_DAILY_CHART",
            "time_itemchartprice": "TR_TIME_CHART",
            "time_daily_itemchartprice": "TR_TIME_DAILY_CHART",
        },
        "account": {
            "inquire_balance_paper": "TR_BALANCE_PAPER",
            "inquire_balance_real": "TR_BALANCE_REAL"
        },
        "trading": {
            "order_cash_buy_paper": "TR_BUY_PAPER",
            "order_cash_buy_real": "TR_BUY_REAL",
            "order_cash_sell_paper": "TR_SELL_PAPER",
            "order_cash_sell_real": "TR_SELL_REAL"
        }
    }

def test_init_valid(mock_env, mock_tr_ids):
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)
    assert provider._tr_ids == mock_tr_ids

def test_init_invalid_tr_ids(mock_env):
    with pytest.raises(ValueError, match="tr_ids 설정이 비어 있거나 올바르지 않습니다"):
        KoreaInvestTrIdProvider(mock_env, {})
    
    with pytest.raises(ValueError):
        KoreaInvestTrIdProvider(mock_env, None)

def test_from_config_loader(mock_env):
    with patch("brokers.korea_investment.korea_invest_trid_provider.load_configs") as mock_load_configs:
        mock_load_configs.return_value = {"tr_ids": {"quotations": {"key": "val"}}}
        
        provider = KoreaInvestTrIdProvider.from_config_loader(mock_env)
        assert provider.get_by_leaf("key") == "val"

def test_from_config_loader_fallback(mock_env):
    with patch("brokers.korea_investment.korea_invest_trid_provider.load_configs") as mock_load_configs, \
         patch("brokers.korea_investment.korea_invest_trid_provider.load_config") as mock_load_config:
        
        mock_load_configs.return_value = {} # Empty merged config
        mock_load_config.return_value = {"tr_ids": {"quotations": {"fallback": "val"}}}
        
        provider = KoreaInvestTrIdProvider.from_config_loader(mock_env)
        assert provider.get_by_leaf("fallback") == "val"

def test_get_leaf_value_found(mock_env, mock_tr_ids):
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)
    assert provider._get_leaf_value("daily_itemchartprice") == "TR_DAILY_CHART"
    assert provider._get_leaf_value("inquire_balance_paper") == "TR_BALANCE_PAPER"

def test_get_leaf_value_not_found(mock_env, mock_tr_ids):
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)
    with pytest.raises(KeyError, match="tr_ids에 'unknown_key'를 찾을 수 없습니다"):
        provider._get_leaf_value("unknown_key")

def test_get_by_leaf_enum(mock_env, mock_tr_ids):
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)
    assert provider.get_by_leaf(TrIdLeaf.DAILY_ITEMCHARTPRICE) == "TR_DAILY_CHART"

def test_get_logic_trid_paper(mock_env, mock_tr_ids):
    mock_env.is_paper_trading = True
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)
    
    assert provider.get(TrId.INQUIRE_BALANCE) == "TR_BALANCE_PAPER"
    assert provider.get(TrId.ORDER_CASH_BUY) == "TR_BUY_PAPER"
    assert provider.get(TrId.ORDER_CASH_SELL) == "TR_SELL_PAPER"

def test_get_logic_trid_real(mock_env, mock_tr_ids):
    mock_env.is_paper_trading = False
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)
    
    assert provider.get(TrId.INQUIRE_BALANCE) == "TR_BALANCE_REAL"
    assert provider.get(TrId.ORDER_CASH_BUY) == "TR_BUY_REAL"
    assert provider.get(TrId.ORDER_CASH_SELL) == "TR_SELL_REAL"

def test_convenience_methods(mock_env, mock_tr_ids):
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)
    
    assert provider.quotations(TrIdLeaf.DAILY_ITEMCHARTPRICE) == "TR_DAILY_CHART"
    
    mock_env.is_paper_trading = True
    assert provider.account_inquire_balance() == "TR_BALANCE_PAPER"
    
    assert provider.trading_order_cash(is_buy=True) == "TR_BUY_PAPER"
    assert provider.trading_order_cash(is_buy=False) == "TR_SELL_PAPER"
    
    assert provider.daily_itemchartprice() == "TR_DAILY_CHART"
    assert provider.time_itemchartprice() == "TR_TIME_CHART"
    assert provider.time_daily_itemchartprice() == "TR_TIME_DAILY_CHART"