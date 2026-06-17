import pytest
from unittest.mock import mock_open, patch
from config.config_loader import load_config, load_configs, AppConfig
import yaml
import json
from pydantic import ValidationError


@pytest.fixture
def fake_yaml_content():
    return """
    api_key: "test-key"
    secret_key: "secret"
    """


def test_load_config_success(fake_yaml_content):
    # yaml.safe_load가 정상적으로 동작하는 경우
    with patch("builtins.open", mock_open(read_data=fake_yaml_content)), \
            patch("yaml.safe_load", return_value={"api_key": "test-key", "secret_key": "secret"}):
        config = load_config("dummy/path/config.yaml")
        assert config["api_key"] == "test-key"
        assert config["secret_key"] == "secret"


def test_load_config_file_not_found():
    # 파일이 없을 때 FileNotFoundError 발생 여부 확인
    with patch("builtins.open", side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError, match="파일을 찾을 수 없습니다"):
            load_config("nonexistent.yaml")

def test_load_configs_success():
    """load_configs 함수가 3개의 설정 파일을 로드하고 병합하는지 테스트"""
    with patch("config.config_loader.load_config") as mock_load:
        mock_load.side_effect = [
            {
                "main": 1,
                "web": {"host": "127.0.0.1", "port": 8000},
                "cache": {"base_dir": ".cache", "memory_cache_enabled": True, "file_cache_enabled": True}
            },
            {"tr_id": 2},
            {"kis": 3}
        ]
        
        config = load_configs()
        
        assert isinstance(config, AppConfig)
        assert config.main == 1
        assert config.tr_id == 2
        assert config.kis == 3
        assert mock_load.call_count == 3

def test_load_config_json_fallback():
    """yaml.safe_load 실패(ImportError) 시 json.load 시도 테스트"""
    json_content = '{"key": "value"}'
    
    with patch("builtins.open", mock_open(read_data=json_content)) as mock_file:
        # yaml.safe_load가 ImportError를 발생시키도록 설정 (yaml 모듈이 없는 상황 시뮬레이션 등)
        with patch("yaml.safe_load", side_effect=ImportError):
            with patch("json.load", return_value={"key": "value"}) as mock_json_load:
                result = load_config("dummy.json")
                
                assert result == {"key": "value"}
                # 파일 포인터를 처음으로 되돌렸는지 확인
                mock_file.return_value.seek.assert_called_with(0)
                mock_json_load.assert_called()

def test_load_config_invalid_yaml_format():
    """잘못된 YAML 형식일 때 ValueError 발생 테스트"""
    with patch("builtins.open", mock_open(read_data="invalid: yaml: content")):
        with patch("yaml.safe_load", side_effect=yaml.YAMLError):
            with pytest.raises(ValueError, match="설정 파일 형식이 올바르지 않습니다"):
                load_config("invalid.yaml")

def test_load_config_invalid_json_format():
    """잘못된 JSON 형식일 때 ValueError 발생 테스트 (ImportError 발생 후 JSON 시도 시)"""
    with patch("builtins.open", mock_open(read_data="{invalid json")):
        with patch("yaml.safe_load", side_effect=ImportError):
            with patch("json.load", side_effect=json.JSONDecodeError("msg", "doc", 0)):
                with pytest.raises(ValueError, match="설정 파일 형식이 올바르지 않습니다"):
                    load_config("invalid.json")

def test_app_config_validation_success():
    """AppConfig 유효성 검사 성공 테스트"""
    config_data = {
        "base_url": "https://api.test.com",
        "web": {"host": "localhost", "port": 8080},
        "cache": {"base_dir": ".cache", "memory_cache_enabled": True, "file_cache_enabled": True}
    }
    config = AppConfig(**config_data)
    assert config.base_url == "https://api.test.com"
    assert config.web.port == 8080


def test_app_config_defaults_to_paper_trading_when_omitted():
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
        cache={"base_dir": ".cache", "memory_cache_enabled": True, "file_cache_enabled": True},
    )

    assert config.is_paper_trading is True
    assert config.strategy_performance_degradation.window_size == 20
    assert config.strategy_performance_degradation.warn_profit_factor_below == 1.0
    assert config.strategy_profitability_gate.min_trades == 30
    assert config.strategy_profitability_gate.min_profit_factor == 1.2
    assert config.strategy_profitability_gate.block_parameter_stability_flags == [
        "spike",
        "cliff",
    ]
    assert config.strategy_profitability_gate.require_parameter_stability is False
    assert config.strategy_profitability_gate.regime_balance_required_buckets == [
        "KOSPI_BULL",
        "KOSDAQ_BULL",
        "SIDEWAYS",
        "BEAR",
    ]
    assert config.strategy_profitability_gate.regime_balance_min_trades == 5
    assert config.strategy_profitability_gate.multiple_testing_min_trials == 5
    assert config.strategy_profitability_gate.multiple_testing_top_to_median_warning_ratio == 3.0
    assert config.strategy_profitability_gate.strategy_correlation_min_overlap == 5
    assert config.strategy_profitability_gate.strategy_correlation_warning_threshold == 0.8
    assert config.strategy_profitability_gate.market_beta_min_overlap == 5
    assert config.strategy_profitability_gate.market_beta_warning_threshold == 1.5
    assert config.strategy_profitability_gate.market_beta_metric == "net_return"
    assert config.strategy_profitability_gate.market_beta_benchmark_metric == "market_return"
    assert config.strategy_profitability_gate.daily_entry_warning_threshold == 5
    assert config.strategy_profitability_gate.opening_entry_warning_threshold == 3
    assert config.strategy_profitability_gate.closing_entry_warning_threshold == 3
    assert config.strategy_profitability_gate.consecutive_loss_warning_threshold == 3
    assert config.strategy_profitability_gate.ablation_max_variant_outperformance_pct is None
    assert config.data_quality.violation_alert_threshold == 5
    assert config.data_quality.violation_alert_window_sec == 60.0
    assert config.order_execution.order_max_retries == 3
    assert config.order_execution.order_retry_delay_sec == 3
    assert config.market_mode == "domestic"
    assert config.enabled_market_modes == ["domestic"]
    assert config.overseas_stock.enabled_exchanges == ["NASD", "NYSE", "AMEX"]
    assert config.overseas_stock.default_exchange == "NASD"
    assert config.overseas_stock.currency == "USD"
    assert config.overseas_stock.manual_order_only is True
    assert config.overseas_stock.allow_live_trading is False
    assert config.overseas_stock.dryrun_slot_usd == 1000.0
    assert config.overseas_stock.dryrun_max_qty is None


def test_app_config_accepts_overseas_us_market_mode():
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
        market_mode="overseas_us",
        overseas_stock={
            "enabled_exchanges": ["NASD", "NYSE"],
            "default_exchange": "NYSE",
            "currency": "USD",
            "manual_order_only": True,
            "allow_live_trading": True,
        },
    )

    assert config.market_mode == "overseas_us"
    assert config.enabled_market_modes == ["overseas_us"]
    assert config.overseas_stock.enabled_exchanges == ["NASD", "NYSE"]
    assert config.overseas_stock.default_exchange == "NYSE"
    assert config.overseas_stock.allow_live_trading is True
    assert config.overseas_stock.dryrun_slot_usd == 1000.0


def test_app_config_accepts_overseas_dryrun_sizing_values():
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
        overseas_stock={
            "dryrun_slot_usd": 500.0,
            "dryrun_max_qty": 3,
        },
    )

    assert config.overseas_stock.dryrun_slot_usd == 500.0
    assert config.overseas_stock.dryrun_max_qty == 3


def test_app_config_rejects_invalid_market_mode():
    with pytest.raises(ValidationError):
        AppConfig(
            web={"host": "localhost", "port": 8080},
            market_mode="global",
        )


def test_app_config_accepts_domestic_and_overseas_enabled_market_modes():
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
        market_mode="domestic",
        enabled_market_modes=["domestic", "overseas_us"],
    )

    assert config.market_mode == "domestic"
    assert config.enabled_market_modes == ["domestic", "overseas_us"]


def test_app_config_rejects_invalid_enabled_market_mode():
    with pytest.raises(ValidationError):
        AppConfig(
            web={"host": "localhost", "port": 8080},
            enabled_market_modes=["domestic", "crypto"],
        )


def test_position_sizing_real_mode_overrides_defaults_are_canary_friendly():
    """P0 0-2: 실전 모드 기본 overlay 가 canary 임계 안에 있는지 회귀."""
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
    )
    overrides = config.position_sizing.real_mode_overrides
    assert overrides.per_trade_risk_pct == 0.5
    assert overrides.max_per_position_pct == 3.0


def test_risk_gate_real_mode_overrides_defaults_are_canary_friendly():
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
    )
    overrides = config.risk_gate.real_mode_overrides
    assert overrides.max_total_exposure_pct == 30.0
    assert overrides.max_pending_orders == 5


def test_order_policy_real_mode_overrides_defaults_are_canary_friendly():
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
    )
    overrides = config.order_policy.real_mode_overrides
    assert overrides.allow_market_buy is False
    assert overrides.max_market_slippage_pct == 0.5
    assert overrides.max_spread_pct == 0.5
    assert overrides.max_top_of_book_participation_pct == 10.0


def test_real_mode_overrides_accept_user_yaml_values():
    """운영자가 production 등급으로 overlay 값을 풀고 싶을 때 yaml 로드가 동작하는지."""
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
        position_sizing={"real_mode_overrides": {"per_trade_risk_pct": 0.25, "max_per_position_pct": 2.0}},
        risk_gate={"real_mode_overrides": {"max_total_exposure_pct": 20.0, "max_pending_orders": 3}},
        order_policy={
            "real_mode_overrides": {
                "allow_market_buy": True,
                "max_market_slippage_pct": 0.3,
                "max_spread_pct": 0.3,
                "max_top_of_book_participation_pct": 5.0,
            }
        },
    )
    assert config.position_sizing.real_mode_overrides.per_trade_risk_pct == 0.25
    assert config.position_sizing.real_mode_overrides.max_per_position_pct == 2.0
    assert config.risk_gate.real_mode_overrides.max_total_exposure_pct == 20.0
    assert config.risk_gate.real_mode_overrides.max_pending_orders == 3
    assert config.order_policy.real_mode_overrides.allow_market_buy is True
    assert config.order_policy.real_mode_overrides.max_market_slippage_pct == 0.3


def test_strategy_profitability_gate_real_mode_overrides_defaults_are_canary_friendly():
    """P1 1-1: 실전 모드 ProfitabilityGate overlay 가 canary 임계 안에 있는지 회귀."""
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
    )
    overrides = config.strategy_profitability_gate.real_mode_overrides
    assert overrides.min_trades == 100
    assert overrides.min_profit_factor == 1.3
    assert overrides.min_payoff_ratio == 1.2
    assert overrides.min_win_rate == 0.4
    assert overrides.max_mdd_pct == 12.0
    assert overrides.min_regime_trade_count == 30
    assert overrides.require_parameter_stability is True
    assert overrides.require_monte_carlo is True
    assert overrides.require_regime_balance is True
    assert overrides.require_multiple_testing_adjustment is True
    assert overrides.multiple_testing_min_adjusted_sharpe == 0.0
    assert overrides.multiple_testing_max_pbo_probability == 0.5
    assert overrides.ablation_max_variant_outperformance_pct == 10.0
    # paper 기본값(top-level)은 보존
    assert config.strategy_profitability_gate.min_trades == 30
    assert config.strategy_profitability_gate.require_parameter_stability is False
    assert config.strategy_profitability_gate.require_monte_carlo is False
    assert config.strategy_profitability_gate.require_regime_balance is False
    assert config.strategy_profitability_gate.require_multiple_testing_adjustment is False


def test_strategy_profitability_gate_real_mode_overrides_accept_user_yaml_values():
    """운영자가 production 등급으로 ProfitabilityGate overlay 값을 풀고 싶을 때 yaml 로드가 동작."""
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
        strategy_profitability_gate={
            "real_mode_overrides": {
                "min_trades": 300,
                "min_profit_factor": 1.5,
                "require_monte_carlo": False,
            }
        },
    )
    overrides = config.strategy_profitability_gate.real_mode_overrides
    assert overrides.min_trades == 300
    assert overrides.min_profit_factor == 1.5
    assert overrides.require_monte_carlo is False
    # 명시되지 않은 필드는 canary 기본값 유지
    assert overrides.require_parameter_stability is True
    assert overrides.require_regime_balance is True
    assert overrides.require_multiple_testing_adjustment is True


def test_app_config_accepts_order_execution_retry_policy():
    config = AppConfig(
        web={"host": "localhost", "port": 8080},
        order_execution={
            "order_max_retries": 5,
            "order_retry_delay_sec": 1,
        },
    )

    assert config.order_execution.order_max_retries == 5
    assert config.order_execution.order_retry_delay_sec == 1


def test_app_config_rejects_invalid_order_execution_retry_policy():
    with pytest.raises(ValidationError) as excinfo:
        AppConfig(
            web={"host": "localhost", "port": 8080},
            order_execution={
                "order_max_retries": 0,
                "order_retry_delay_sec": -1,
            },
        )

    assert "order_max_retries" in str(excinfo.value)
    assert "order_retry_delay_sec" in str(excinfo.value)

def test_app_config_validation_invalid_url():
    """AppConfig base_url 유효성 검사 실패 테스트"""
    config_data = {
        "base_url": "ftp://invalid.com",
        "web": {"host": "localhost", "port": 8080},
        "cache": {"base_dir": ".cache", "memory_cache_enabled": True, "file_cache_enabled": True}
    }
    with pytest.raises(ValidationError) as excinfo:
        AppConfig(**config_data)
    assert "base_url" in str(excinfo.value)

def test_app_config_validation_invalid_port():
    """AppConfig web.port 범위 검사 실패 테스트"""
    config_data = {
        "web": {"host": "localhost", "port": 99999},
        "cache": {"base_dir": ".cache", "memory_cache_enabled": True, "file_cache_enabled": True}
    }
    with pytest.raises(ValidationError) as excinfo:
        AppConfig(**config_data)
    assert "port" in str(excinfo.value)

def test_app_config_dict_access():
    """AppConfig 딕셔너리 호환성 테스트 (__getitem__, get)"""
    config = AppConfig(
        api_key="key",
        web={"host": "localhost", "port": 8000},
        cache={"base_dir": ".cache", "memory_cache_enabled": True, "file_cache_enabled": True}
    )
    # __getitem__
    assert config["api_key"] == "key"
    # get
    assert config.get("api_key") == "key"
    assert config.get("non_existent", "default") == "default"

def test_load_configs_validation_error_missing_required_field():
    """필수 필드(예: web) 누락 시 ValueError(ValidationError 포장) 발생 테스트"""
    with patch("config.config_loader.load_config") as mock_load:
        # web 섹션 누락 (AppConfig에서 web은 필수)
        mock_load.side_effect = [
            {
                "api_key": "test",
                # "web": ... missing
                "cache": {"base_dir": ".cache"}
            },
            {},
            {}
        ]
        
        with pytest.raises(ValueError, match="설정 파일 유효성 검사 실패"):
            load_configs()

def test_load_configs_missing_optional_field_defaults():
    """선택적 필드(예: cache) 누락 시 기본값으로 성공하는지 테스트"""
    with patch("config.config_loader.load_config") as mock_load:
        # cache 섹션 누락 (AppConfig에서 cache는 default_factory 존재)
        mock_load.side_effect = [
            {
                "web": {"host": "127.0.0.1", "port": 8000},
                # "cache": ... missing
            },
            {},
            {}
        ]
        
        config = load_configs()
        assert config.web.port == 8000
        # cache가 없어도 기본값으로 생성되어야 함
        assert config.cache.base_dir == ".cache"
        assert config.cache.memory_cache_enabled is True


# ── P0 0-7: operating_profile + profile-specific overrides ──────────────────


def _minimal_app_config(**overrides):
    base = {"web": {"host": "127.0.0.1", "port": 8000}}
    base.update(overrides)
    return AppConfig(**base)


def test_app_config_default_operating_profile_is_canary():
    cfg = _minimal_app_config()
    assert cfg.operating_profile == "canary"


def test_app_config_accepts_real_limited_profile():
    cfg = _minimal_app_config(operating_profile="real_limited")
    assert cfg.operating_profile == "real_limited"


def test_app_config_accepts_real_full_profile():
    cfg = _minimal_app_config(operating_profile="real_full")
    assert cfg.operating_profile == "real_full"


def test_app_config_rejects_invalid_profile():
    with pytest.raises(ValidationError):
        _minimal_app_config(operating_profile="canary_full")


def test_risk_gate_canary_overrides_defaults_match_canary_procedure():
    """canary profile 기본값: docs/canary_procedure.md 운영 한도와 일치."""
    from config.config_loader import RiskGateConfig
    cfg = RiskGateConfig()
    assert cfg.canary_overrides.max_total_exposure_pct == 5.0
    assert cfg.canary_overrides.max_pending_orders == 2
    assert cfg.canary_overrides.max_order_amount_won == 1_000_000


def test_position_sizing_canary_overrides_defaults_match_canary_procedure():
    """canary profile 기본값: 1주당 리스크 0.25%, 단일 포지션 1.5%."""
    from config.config_loader import PositionSizingConfig
    cfg = PositionSizingConfig()
    assert cfg.canary_overrides.per_trade_risk_pct == 0.25
    assert cfg.canary_overrides.max_per_position_pct == 1.5


def test_risk_gate_real_mode_overrides_still_default_real_limited():
    """real_mode_overrides 의 기본값은 그대로 (real_limited overlay) — backward compat."""
    from config.config_loader import RiskGateConfig
    cfg = RiskGateConfig()
    assert cfg.real_mode_overrides.max_total_exposure_pct == 30.0
    assert cfg.real_mode_overrides.max_pending_orders == 5


def test_position_sizing_real_mode_overrides_still_default_real_limited():
    from config.config_loader import PositionSizingConfig
    cfg = PositionSizingConfig()
    assert cfg.real_mode_overrides.per_trade_risk_pct == 0.5
    assert cfg.real_mode_overrides.max_per_position_pct == 3.0
