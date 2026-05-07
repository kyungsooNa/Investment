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
RISK_GATE_CONFIG_PATH = os.path.join(BASE_DIR, 'risk_gate_config.yaml')


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
    # 거래대금/유동성 필터 (StrategyExecutor 단계에서 사전 필터링)
    min_trading_value_won: Optional[int] = None   # 최소 일중 누적 거래대금 (원)
    min_avg_volume: Optional[int] = None          # 최소 일중 누적 거래량 (주)
    # 전략별 자본 할당 한도
    capital_allocation_pct: Optional[float] = None  # 총 자산 대비 이 전략 최대 투자 비율 (%)
    # 전략별 Kill Switch
    max_consecutive_losses_for_kill: Optional[int] = None  # 연속 손실 n회 시 전략 단독 정지
    daily_loss_won_for_kill: Optional[int] = None          # 일일 손실 n원 초과 시 전략 단독 정지

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
    trade_flow_checks_enabled: bool = True
    trade_flow_fail_policy: str = "block"       # allow | block
    trade_flow_cache_ttl_sec: float = 3.0
    trade_flow_sample_window_sec: int = 60
    max_last_trade_age_sec: float = 60.0
    min_recent_trade_count: int = 1
    min_trade_value_per_min_won: int = 0
    min_execution_strength_pct: float = 0.0
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


class DataQualityConfig(BaseModel):
    enabled: bool = True
    max_tick_age_sec: float = 30.0
    max_rest_age_sec: float = 10.0
    max_price_jump_pct: float = 15.0
    paper_max_tick_age_sec: Optional[float] = 60.0
    paper_max_rest_age_sec: Optional[float] = 15.0
    paper_max_price_jump_pct: Optional[float] = 20.0
    real_max_tick_age_sec: Optional[float] = 30.0
    real_max_rest_age_sec: Optional[float] = 10.0
    real_max_price_jump_pct: Optional[float] = 15.0
    block_on_stale_price: bool = True
    block_on_invalid_api_response: bool = True
    alert_cooldown_sec: float = 60.0

    model_config = {"extra": "allow"}


class NotificationTelegramConfig(BaseModel):
    enabled: bool = True
    route_levels: Dict[str, List[str]] = Field(default_factory=lambda: {
        "SYSTEM": ["error", "critical"],
        "TRADE": ["warning", "error", "critical"],
        "BACKGROUND": ["error", "critical"],
        "STRATEGY": ["warning", "error", "critical"],
        "API": ["error", "critical"],
    })

    model_config = {"extra": "allow"}


class NotificationsConfig(BaseModel):
    telegram: NotificationTelegramConfig = Field(default_factory=NotificationTelegramConfig)

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


class OpeningPositionReconcileConfig(BaseModel):
    enabled: bool = True
    detect_only: bool = True
    auto_buy_missing_local: bool = False
    auto_sell_extra_broker: bool = False
    allow_sell_unknown_broker: bool = False
    check_interval_sec: int = 30
    open_delay_sec: int = 60
    run_window_min: int = 10

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
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    position_sizing: PositionSizingConfig = Field(default_factory=PositionSizingConfig)
    execution_quality_report: ExecutionQualityReportConfig = Field(default_factory=ExecutionQualityReportConfig)
    opening_position_reconcile: OpeningPositionReconcileConfig = Field(default_factory=OpeningPositionReconcileConfig)
    
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

    # risk_gate_config.yaml 선택적 로드 — 없으면 스킵, 있으면 strategy_limits 딥 머지
    if os.path.exists(RISK_GATE_CONFIG_PATH):
        rg_file_data = load_config(RISK_GATE_CONFIG_PATH) or {}
        rg_section = rg_file_data.get("risk_gate", {})
        if rg_section:
            extra_limits = rg_section.pop("strategy_limits", {})
            base_rg = config_data.setdefault("risk_gate", {})
            base_rg.update(rg_section)
            base_limits = base_rg.setdefault("strategy_limits", {})
            base_limits.update(extra_limits)

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
