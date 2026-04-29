# core/config_loader.py
import yaml
import os
import json
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field, ValidationError, field_validator

# config.yaml 및 tr_ids_config.yaml 파일 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_CONFIG_PATH = os.path.join(BASE_DIR, 'config.yaml')
TR_IDS_CONFIG_PATH = os.path.join(BASE_DIR, 'tr_ids_config.yaml')
KIS_CONFIG_PATH = os.path.join(BASE_DIR, 'kis_config.yaml')


class WebConfig(BaseModel):
    host: str
    port: int = Field(..., ge=1, le=65535, description="웹 서버 포트 (1~65535)")

class CacheConfig(BaseModel):
    base_dir: str = ".cache"
    enabled_methods: List[str] = Field(default_factory=list)
    deserializable_classes: List[str] = Field(default_factory=list)
    memory_cache_enabled: bool = True
    file_cache_enabled: bool = True

class KillSwitchConfig(BaseModel):
    enabled: bool = True
    daily_loss_threshold_won: int = 1_000_000
    daily_loss_threshold_pct: float = 5.0
    max_consecutive_losses: int = 3
    max_consecutive_api_errors: int = 10
    abnormal_fill_deviation_pct: float = 3.0
    state_file_path: str = "data/kill_switch_state.json"

class PositionSizingConfig(BaseModel):
    enabled: bool = True
    per_trade_risk_pct: float = 1.5        # 1주당 리스크 한도 (총자산 대비 %)
    max_per_position_pct: float = 10.0     # 단일 종목 비중 상한 (%)
    default_stop_loss_pct: float = -5.0    # 시그널 미전달 시 폴백 손절폭 (음수)
    atr_period: int = 14
    atr_multiplier: float = 2.0
    min_stop_distance_pct: float = 1.0     # 분모 보호용 최소 손절 거리 (%)
    snapshot_ttl_sec: int = 60             # 잔고 스냅샷 TTL (초)

    model_config = {"extra": "allow"}


class RiskGateStrategyLimitConfig(BaseModel):
    max_exposure_pct: Optional[float] = None
    max_loss_pct: Optional[float] = None
    block_duplicate_position: bool = True

    model_config = {"extra": "allow"}


class RiskGateConfig(BaseModel):
    enabled: bool = True
    max_order_amount_won: int = 10_000_000
    max_daily_order_amount_won: int = 50_000_000
    max_pending_orders: int = 10
    max_total_exposure_pct: float = 95.0
    block_duplicate_strategy_position: bool = True
    default_strategy_limit: RiskGateStrategyLimitConfig = Field(default_factory=RiskGateStrategyLimitConfig)
    strategy_limits: Dict[str, RiskGateStrategyLimitConfig] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class OrderPolicyConfig(BaseModel):
    enabled: bool = True
    allow_market_buy: bool = True
    allow_market_sell: bool = True
    allow_nxt_market_order: bool = False
    tick_size_policy: str = "adjust"        # adjust | block | ignore
    order_book_checks_enabled: bool = True
    security_status_checks_enabled: bool = True
    security_status_fail_policy: str = "block"  # allow | block
    max_market_slippage_pct: float = 1.0
    max_spread_pct: float = 1.0
    min_trading_value_won: int = 0
    min_market_cap_won: int = 0
    max_top_of_book_participation_pct: float = 100.0
    block_empty_order_book: bool = True
    block_managed_issue: bool = True
    block_investment_warning: bool = True
    block_investment_caution: bool = False
    blocked_stock_status_codes: List[str] = Field(default_factory=lambda: ["51", "52", "53", "58"])
    quote_fail_policy: str = "block"        # allow | block

    model_config = {"extra": "allow"}


class ExecutionQualityReportConfig(BaseModel):
    enabled: bool = True
    min_sample_count: int = 3
    liquidity_control_effective_date: Optional[str] = None
    warn_avg_slippage_pct: float = 0.5
    warn_p95_slippage_pct: float = 1.0
    warn_avg_first_fill_latency_sec: float = 30.0
    warn_incomplete_fill_ratio_pct: float = 20.0
    warn_avg_unfilled_ratio_pct: float = 20.0
    warn_avg_order_age_sec: float = 120.0
    candidate_avg_slippage_pct: float = 1.0
    candidate_p95_slippage_pct: float = 2.0
    candidate_avg_first_fill_latency_sec: float = 90.0
    candidate_incomplete_fill_ratio_pct: float = 40.0
    candidate_avg_unfilled_ratio_pct: float = 40.0
    candidate_avg_order_age_sec: float = 300.0
    auto_disable_enabled: bool = False

    model_config = {"extra": "allow"}


class AppConfig(BaseModel):
    # Core API keys
    api_key: Optional[str] = None
    api_secret_key: Optional[str] = None
    base_url: Optional[str] = None
    websocket_url: Optional[str] = None
    
    # Account info
    custtype: str = "P"
    stock_account_number: Optional[str] = None
    htsid: Optional[str] = None
    
    # Flags
    is_paper_trading: bool = True
    
    # Sub-configs
    web: WebConfig
    cache: CacheConfig = Field(default_factory=CacheConfig)
    kill_switch: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    risk_gate: RiskGateConfig = Field(default_factory=RiskGateConfig)
    order_policy: OrderPolicyConfig = Field(default_factory=OrderPolicyConfig)
    position_sizing: PositionSizingConfig = Field(default_factory=PositionSizingConfig)
    execution_quality_report: ExecutionQualityReportConfig = Field(default_factory=ExecutionQualityReportConfig)
    
    # Dynamic/Merged configs
    tr_ids: Dict[str, Any] = Field(default_factory=dict)
    paths: Dict[str, str] = Field(default_factory=dict)

    # ✅ 필드 추가 (기본값 False 설정)
    performance_logging: bool = False 
    performance_threshold: float = 0.1

    # Extra fields for anything else in config.yaml
    model_config = {"extra": "allow"}

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url은 'http://' 또는 'https://'로 시작해야 합니다.")
        return v

    def __getitem__(self, item):
        return getattr(self, item)

    def get(self, item, default=None):
        return getattr(self, item, default)

def load_configs() -> AppConfig:
    main_config_data = load_config(MAIN_CONFIG_PATH) or {}
    tr_ids_data = load_config(TR_IDS_CONFIG_PATH) or {}
    kis_config_data = load_config(KIS_CONFIG_PATH) or {}

    config_data = {}
    config_data.update(main_config_data)
    config_data.update(tr_ids_data)
    config_data.update(kis_config_data)

    try:
        return AppConfig(**config_data)
    except ValidationError as e:
        raise ValueError(f"설정 파일 유효성 검사 실패: {e}")


def load_config(file_path):
    """지정된 경로에서 YAML 또는 JSON 설정 파일을 로드합니다."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                return yaml.safe_load(f)
            except ImportError:
                f.seek(0)
                return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ValueError(f"설정 파일 형식이 올바르지 않습니다 ({file_path}): {e}")
