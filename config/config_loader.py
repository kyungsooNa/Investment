# core/config_loader.py
import yaml
import os
import json
from typing import Dict, Any, Literal, Optional, List
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

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
    enabled: bool = False
    notify_only: bool = False
    daily_loss_threshold_won: int = 1_000_000
    daily_loss_threshold_pct: float = 5.0
    max_consecutive_losses: int = 3
    max_consecutive_api_errors: int = 10
    abnormal_fill_deviation_pct: float = 3.0
    state_file_path: str = "data/kill_switch_state.json"

class PositionSizingRealOverrides(BaseModel):
    """real_limited overlay (실전 제한 운영). paper 동작은 영향 없음.

    operating_profile=real_limited 시 적용. canary 와 별도로 운영 검증 단계의
    중간 한도(0.5%/3.0%)를 표현한다.
    """
    per_trade_risk_pct: float = 0.5
    max_per_position_pct: float = 3.0
    max_portfolio_open_risk_pct: float = 3.0   # R-3: 전 포지션 합산 open-risk(heat) 한도 (%)

    model_config = {"extra": "allow"}


class PositionSizingCanaryOverrides(BaseModel):
    """canary overlay (실전 소액 검증). operating_profile=canary 시 적용.

    docs/canary_procedure.md 표 기준 보수 운영값.
    """
    per_trade_risk_pct: float = 0.25       # 1주당 리스크 0.25%
    max_per_position_pct: float = 1.5      # 단일 포지션 1.5%
    max_portfolio_open_risk_pct: float = 1.0   # R-3: heat 한도 1.0%

    model_config = {"extra": "allow"}


class PositionSizingConfig(BaseModel):
    enabled: bool = True
    per_trade_risk_pct: float = 1.5        # 1주당 리스크 한도 (총자산 대비 %)
    max_per_position_pct: float = 5.0      # 단일 종목 비중 상한 (%)
    max_portfolio_open_risk_pct: float = 6.0   # R-3: 전 포지션 합산 open-risk(heat) 한도 (%, 0 이면 비활성)
    default_stop_loss_pct: float = -5.0    # 시그널 미전달 시 폴백 손절폭 (음수)
    atr_period: int = 14
    atr_multiplier: float = 2.0
    min_stop_distance_pct: float = 1.0     # 분모 보호용 최소 손절 거리 (%)
    snapshot_ttl_sec: int = 60             # 잔고 스냅샷 TTL (초)
    real_mode_overrides: PositionSizingRealOverrides = Field(default_factory=PositionSizingRealOverrides)
    canary_overrides: PositionSizingCanaryOverrides = Field(default_factory=PositionSizingCanaryOverrides)

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


class RiskGateFailOpenConfig(BaseModel):
    """RiskGate fail-open 허용 정책. real=False 이면 실전 모드 BUY 는 fail-close 된다."""
    paper: bool = True
    real: bool = False

    model_config = {"extra": "allow"}


class RiskGateRealOverrides(BaseModel):
    """real_limited overlay (실전 제한 운영). operating_profile=real_limited 시 적용.

    paper 동작은 영향 없음. canary 와 full 사이의 중간 검증 단계 한도.
    """
    max_total_exposure_pct: float = 30.0
    max_pending_orders: int = 5

    model_config = {"extra": "allow"}


class RiskGateCanaryOverrides(BaseModel):
    """canary overlay (실전 소액 검증). operating_profile=canary 시 적용.

    docs/canary_procedure.md 표 기준: 총 노출 5%, 동시 보유 2종, 1주문 1M.
    """
    max_total_exposure_pct: float = 5.0
    max_pending_orders: int = 2
    max_order_amount_won: int = 1_000_000

    model_config = {"extra": "allow"}


class RiskGateConfig(BaseModel):
    enabled: bool = True
    max_order_amount_won: int = 2_000_000
    max_daily_order_amount_won: int = 50_000_000
    max_pending_orders: int = 10
    max_total_exposure_pct: float = 95.0
    block_duplicate_strategy_position: bool = True
    default_strategy_limit: RiskGateStrategyLimitConfig = Field(default_factory=RiskGateStrategyLimitConfig)
    strategy_limits: Dict[str, RiskGateStrategyLimitConfig] = Field(default_factory=dict)
    fail_open_allowed: RiskGateFailOpenConfig = Field(default_factory=RiskGateFailOpenConfig)
    real_mode_overrides: RiskGateRealOverrides = Field(default_factory=RiskGateRealOverrides)
    canary_overrides: RiskGateCanaryOverrides = Field(default_factory=RiskGateCanaryOverrides)

    model_config = {"extra": "allow"}


class OrderPolicyRealOverrides(BaseModel):
    """실전(real) 모드 전용 OrderPolicy overlay. paper 동작은 영향 없음.

    fail-close 지향 canary 기본값:
    - 시장가 매수 차단 (시장 변동성에 대한 과다 노출 방지)
    - 슬리피지/스프레드 0.5% 제한
    - 1호가 잔량 대비 최대 10% 참여
    """
    allow_market_buy: bool = False
    max_market_slippage_pct: float = 0.5
    max_spread_pct: float = 0.5
    max_top_of_book_participation_pct: float = 10.0

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
    blocked_market_warning_codes: List[str] = Field(default_factory=lambda: ["2", "3"])
    blocked_sell_stock_status_codes: List[str] = Field(default_factory=lambda: ["58"])
    quote_fail_policy: str = "block"        # allow | block
    real_mode_overrides: OrderPolicyRealOverrides = Field(default_factory=OrderPolicyRealOverrides)

    model_config = {"extra": "allow"}


class OrderExecutionConfig(BaseModel):
    order_max_retries: int = Field(3, ge=1)
    order_retry_delay_sec: int = Field(3, ge=0)

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
    violation_alert_threshold: int = 5
    violation_alert_window_sec: float = 60.0

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


class DartDisclosureConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    poll_interval_sec: int = Field(300, ge=60)
    off_hours_interval_sec: int = Field(1800, ge=60)
    active_start_time: str = "07:00"
    active_end_time: str = "19:30"
    request_timeout_sec: float = Field(5.0, gt=0)
    immediate_alert_score: int = Field(70, ge=0, le=100)
    daily_digest_enabled: bool = True
    daily_digest_time: str = "19:40"
    max_pages_per_poll: int = Field(5, ge=1, le=100)

    model_config = {"extra": "allow"}


class AiAnalysisConfig(BaseModel):
    """AI 분석 공통 설정 (Gemini/Groq/Ollama OpenAI 호환 엔드포인트).

    provider 차이는 base_url/api_key/model 로 흡수한다. api_key 는 클라우드
    (Gemini/Groq)엔 필수, 로컬 Ollama 엔 불필요(빈 값 허용).
    """
    enabled: bool = False
    provider: str = "gemini"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    api_key: str = ""
    model: str = "gemini-2.5-flash"
    timeout_sec: float = Field(15.0, gt=0)
    # Gemini 2.5 계열은 thinking 토큰이 max_tokens 예산을 소비하므로, 짧게 잡으면
    # 실제 요약이 잘린다. 사고 후에도 2~3문장이 완성되도록 넉넉히 둔다.
    max_tokens: int = Field(2048, ge=1, le=8192)
    disclosure_summary_enabled: bool = True
    daily_request_limit: int = Field(100, ge=0)
    disclosure_reserve: int = Field(20, ge=0)

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


class StrategyPerformanceDegradationConfig(BaseModel):
    window_size: int = 20
    min_live_trades: int = 10
    min_baseline_trades: int = 10
    capital_base_won: Optional[float] = None
    warn_win_rate_drop_pctp: float = 15.0
    warn_avg_return_drop_pctp: float = 1.0
    warn_profit_factor_below: Optional[float] = 1.0
    critical_consecutive_losses: Optional[int] = 5
    critical_mdd_ratio_multiplier: Optional[float] = 2.0

    model_config = {"extra": "allow"}


class StrategyProfitabilityGateRealOverrides(BaseModel):
    """실전(real) 모드 전용 ProfitabilityGate overlay. paper/backtest 동작은 영향 없음.

    canary 임계로 default 값을 잡았다. 운영 검증 후 더 느슨한 값을 쓰려면 yaml 에서 명시.

    fail-close 지향: missing evidence(monte carlo / regime balance / parameter stability) 도
    real 모드에서는 block 으로 승격한다. 데이터가 갖춰지지 않은 신규 전략은 자동 차단된다.
    """
    min_trades: int = 100
    min_profit_factor: Optional[float] = 1.3
    min_payoff_ratio: Optional[float] = 1.2
    min_win_rate: Optional[float] = 0.4
    max_mdd_pct: Optional[float] = 12.0
    min_regime_trade_count: int = 30
    require_parameter_stability: bool = True
    require_monte_carlo: bool = True
    require_regime_balance: bool = True
    require_multiple_testing_adjustment: bool = True
    multiple_testing_min_adjusted_sharpe: Optional[float] = 0.0
    multiple_testing_max_pbo_probability: Optional[float] = 0.5
    ablation_max_variant_outperformance_pct: Optional[float] = 10.0

    model_config = {"extra": "allow"}


class StrategyProfitabilityGateConfig(BaseModel):
    min_trades: int = 30
    min_profit_factor: Optional[float] = 1.2
    min_payoff_ratio: Optional[float] = 1.0
    min_win_rate: Optional[float] = 0.35
    min_avg_net_return: Optional[float] = 0.0
    require_positive_total_net_pnl: bool = True
    max_mdd_pct: Optional[float] = 20.0
    capital_base_won: Optional[float] = None
    max_monte_carlo_ruin_probability: Optional[float] = 0.05
    max_monte_carlo_worst_mdd_pct: Optional[float] = 30.0
    min_regime_trade_count: int = 5
    require_non_negative_regime_pnl: bool = True
    block_parameter_stability_flags: List[str] = Field(
        default_factory=lambda: ["spike", "cliff"]
    )
    require_parameter_stability: bool = False
    require_monte_carlo: bool = False
    require_regime_balance: bool = False
    regime_balance_required_buckets: List[str] = Field(
        default_factory=lambda: ["KOSPI_BULL", "KOSDAQ_BULL", "SIDEWAYS", "BEAR"]
    )
    regime_balance_min_trades: int = 5
    multiple_testing_min_trials: int = 5
    multiple_testing_top_to_median_warning_ratio: float = 3.0
    multiple_testing_primary_metric: str = "total_net_pnl"
    require_multiple_testing_adjustment: bool = False
    multiple_testing_min_adjusted_sharpe: Optional[float] = None
    multiple_testing_max_pbo_probability: Optional[float] = None
    multiple_testing_sharpe_metric: str = "sharpe_ratio"
    multiple_testing_in_sample_metric: str = "in_sample_net_pnl"
    multiple_testing_out_of_sample_metric: str = "out_of_sample_net_pnl"
    strategy_correlation_min_overlap: int = 5
    strategy_correlation_warning_threshold: float = 0.8
    strategy_correlation_metric: str = "net_return"
    market_beta_min_overlap: int = 5
    market_beta_warning_threshold: float = 1.5
    market_beta_metric: str = "net_return"
    market_beta_benchmark_metric: str = "market_return"
    daily_entry_warning_threshold: int = 5
    opening_entry_warning_threshold: int = 3
    closing_entry_warning_threshold: int = 3
    consecutive_loss_warning_threshold: int = 3
    ablation_max_variant_outperformance_pct: Optional[float] = None
    real_mode_overrides: StrategyProfitabilityGateRealOverrides = Field(
        default_factory=StrategyProfitabilityGateRealOverrides
    )

    model_config = {"extra": "allow"}


class OpeningPositionReconcileConfig(BaseModel):
    enabled: bool = True
    check_interval_sec: int = 30
    open_delay_sec: int = 60
    run_window_min: int = 10

    model_config = {"extra": "allow"}


class OverseasStockConfig(BaseModel):
    enabled_exchanges: List[Literal["NASD", "NYSE", "AMEX"]] = Field(
        default_factory=lambda: ["NASD", "NYSE", "AMEX"]
    )
    default_exchange: Literal["NASD", "NYSE", "AMEX"] = "NASD"
    currency: Literal["USD"] = "USD"
    manual_order_only: bool = True
    allow_live_trading: bool = False
    dryrun_slot_usd: float = Field(1000.0, gt=0)
    dryrun_max_qty: Optional[int] = Field(default=None, gt=0)

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

    # Operating profile (P0 0-7): canary 5% 노출 vs real_limited 30% vs real_full 95% 운영 한도 분리.
    # paper 모드에서는 base 값을 사용하므로 profile 무관. real 모드에서 overlay 선택.
    operating_profile: Literal["canary", "real_limited", "real_full"] = "canary"

    # Product/market surface. This is orthogonal to paper/real and runtime mode.
    market_mode: Literal["domestic", "overseas_us"] = "domestic"
    enabled_market_modes: Optional[List[Literal["domestic", "overseas_us"]]] = None

    # Sub-configs
    web: WebConfig
    cache: CacheConfig = Field(default_factory=CacheConfig)
    kill_switch: KillSwitchConfig = Field(default_factory=KillSwitchConfig)
    risk_gate: RiskGateConfig = Field(default_factory=RiskGateConfig)
    order_policy: OrderPolicyConfig = Field(default_factory=OrderPolicyConfig)
    order_execution: OrderExecutionConfig = Field(default_factory=OrderExecutionConfig)
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    dart_disclosure: DartDisclosureConfig = Field(default_factory=DartDisclosureConfig)
    ai_analysis: AiAnalysisConfig = Field(default_factory=AiAnalysisConfig)
    position_sizing: PositionSizingConfig = Field(default_factory=PositionSizingConfig)
    execution_quality_report: ExecutionQualityReportConfig = Field(default_factory=ExecutionQualityReportConfig)
    strategy_performance_degradation: StrategyPerformanceDegradationConfig = Field(
        default_factory=StrategyPerformanceDegradationConfig
    )
    strategy_profitability_gate: StrategyProfitabilityGateConfig = Field(
        default_factory=StrategyProfitabilityGateConfig
    )
    opening_position_reconcile: OpeningPositionReconcileConfig = Field(default_factory=OpeningPositionReconcileConfig)
    overseas_stock: OverseasStockConfig = Field(default_factory=OverseasStockConfig)
    
    # Dynamic/Merged configs
    tr_ids: Dict[str, Any] = Field(default_factory=dict)
    paths: Dict[str, str] = Field(default_factory=dict)

    # ✅ 필드 추가 (기본값 False 설정)
    performance_logging: bool = False
    performance_threshold: float = 0.1

    # 시총갭 리포트 세션별 on/off (기본 둘 다 on)
    market_cap_gap_report_kr_enabled: bool = True
    market_cap_gap_report_us_enabled: bool = True

    # Extra fields for anything else in config.yaml
    model_config = {"extra": "allow"}

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, v: Optional[str]) -> Optional[str]:
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("base_url은 'http://' 또는 'https://'로 시작해야 합니다.")
        return v

    @model_validator(mode="after")
    def normalize_enabled_market_modes(self):
        modes = self.enabled_market_modes
        if modes is None:
            modes = [self.market_mode]
        normalized = []
        for mode in modes:
            if mode not in normalized:
                normalized.append(mode)
        if self.market_mode not in normalized:
            normalized.append(self.market_mode)
        self.enabled_market_modes = normalized
        return self

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
