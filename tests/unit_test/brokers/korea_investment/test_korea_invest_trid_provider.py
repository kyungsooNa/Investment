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
            "inquire_daily_itemchartprice": "TR_DAILY_CHART",
            "inquire_time_itemchartprice": "TR_TIME_CHART",
            "inquire_time_daily_itemchartprice": "TR_TIME_DAILY_CHART",
        },
        "account": {
            "inquire_balance_paper": "TR_BALANCE_PAPER",
            "inquire_balance_real": "TR_BALANCE_REAL",
            "inquire_daily_ccld_paper": "TR_CCLD_PAPER",
            "inquire_daily_ccld_real": "TR_CCLD_REAL"
        },
        "trading": {
            "order_cash_buy_paper": "TR_BUY_PAPER",
            "order_cash_buy_real": "TR_BUY_REAL",
            "order_cash_sell_paper": "TR_SELL_PAPER",
            "order_cash_sell_real": "TR_SELL_REAL",
            "order_rvsecncl_paper": "TR_CANCEL_PAPER",
            "order_rvsecncl_real": "TR_CANCEL_REAL",
            "inquire_psbl_rvsecncl_paper": "TR_PSBL_CANCEL_PAPER",
            "inquire_psbl_rvsecncl_real": "TR_PSBL_CANCEL_REAL"
        },
        "overseas_stock": {
            "price": "TR_OVRS_PRICE",
            "dailyprice": "TR_OVRS_DAILY",
            "inquire_balance_real": "TR_OVRS_BALANCE_REAL",
            "inquire_balance_paper": "TR_OVRS_BALANCE_PAPER",
            "inquire_ccnl_real": "TR_OVRS_CCLD_REAL",
            "inquire_ccnl_paper": "TR_OVRS_CCLD_PAPER",
            "inquire_nccs_real": "TR_OVRS_NCCS_REAL",
            "inquire_nccs_paper": "TR_OVRS_NCCS_PAPER",
            "order_buy_real": "TR_OVRS_BUY_REAL",
            "order_sell_real": "TR_OVRS_SELL_REAL",
            "order_rvsecncl_real": "TR_OVRS_CANCEL_REAL"
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
    assert provider._get_leaf_value("inquire_daily_itemchartprice") == "TR_DAILY_CHART"
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
    assert provider.get(TrId.INQUIRE_DAILY_CCLD) == "TR_CCLD_PAPER"
    assert provider.get(TrId.ORDER_CASH_BUY) == "TR_BUY_PAPER"
    assert provider.get(TrId.ORDER_CASH_SELL) == "TR_SELL_PAPER"
    assert provider.get(TrId.ORDER_RVSECNCL) == "TR_CANCEL_PAPER"

def test_get_logic_trid_real(mock_env, mock_tr_ids):
    mock_env.is_paper_trading = False
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)
    
    assert provider.get(TrId.INQUIRE_BALANCE) == "TR_BALANCE_REAL"
    assert provider.get(TrId.INQUIRE_DAILY_CCLD) == "TR_CCLD_REAL"
    assert provider.get(TrId.ORDER_CASH_BUY) == "TR_BUY_REAL"
    assert provider.get(TrId.ORDER_CASH_SELL) == "TR_SELL_REAL"
    assert provider.get(TrId.ORDER_RVSECNCL) == "TR_CANCEL_REAL"

def test_convenience_methods(mock_env, mock_tr_ids):
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)
    
    assert provider.quotations(TrIdLeaf.DAILY_ITEMCHARTPRICE) == "TR_DAILY_CHART"
    
    mock_env.is_paper_trading = True
    assert provider.account_inquire_balance() == "TR_BALANCE_PAPER"
    assert provider.account_inquire_daily_ccld() == "TR_CCLD_PAPER"
    
    assert provider.trading_order_cash(is_buy=True) == "TR_BUY_PAPER"
    assert provider.trading_order_cash(is_buy=False) == "TR_SELL_PAPER"
    assert provider.trading_order_rvsecncl() == "TR_CANCEL_PAPER"
    assert provider.account_inquire_psbl_rvsecncl() == "TR_PSBL_CANCEL_PAPER"
    
    assert provider.daily_itemchartprice() == "TR_DAILY_CHART"
    assert provider.time_itemchartprice() == "TR_TIME_CHART"
    assert provider.time_daily_itemchartprice() == "TR_TIME_DAILY_CHART"


def test_get_inquire_psbl_rvsecncl_logical_key_for_paper_and_real(mock_env, mock_tr_ids):
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)

    mock_env.is_paper_trading = True
    assert provider.get(TrId.INQUIRE_PSBL_RVSECNCL) == "TR_PSBL_CANCEL_PAPER"

    mock_env.is_paper_trading = False
    assert provider.get(TrId.INQUIRE_PSBL_RVSECNCL) == "TR_PSBL_CANCEL_REAL"


def test_get_leaf_string_delegates_to_leaf_lookup(mock_env, mock_tr_ids):
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)

    assert provider.get("inquire_daily_itemchartprice") == "TR_DAILY_CHART"


def test_overseas_stock_convenience_methods_respect_paper_and_real(mock_env, mock_tr_ids):
    provider = KoreaInvestTrIdProvider(mock_env, mock_tr_ids)

    assert provider.overseas_stock("price") == "TR_OVRS_PRICE"
    assert provider.overseas_stock_inquire_balance() == "TR_OVRS_BALANCE_PAPER"
    assert provider.overseas_stock_inquire_ccnl() == "TR_OVRS_CCLD_PAPER"
    assert provider.overseas_stock_inquire_nccs() == "TR_OVRS_NCCS_PAPER"

    mock_env.is_paper_trading = False
    assert provider.overseas_stock_inquire_balance() == "TR_OVRS_BALANCE_REAL"
    assert provider.overseas_stock_order(is_buy=True) == "TR_OVRS_BUY_REAL"
    assert provider.overseas_stock_order(is_buy=False) == "TR_OVRS_SELL_REAL"
    assert provider.overseas_stock_order_rvsecncl() == "TR_OVRS_CANCEL_REAL"
