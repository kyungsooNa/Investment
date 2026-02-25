# strategies/base_strategy_config.py
from dataclasses import dataclass

@dataclass
class BaseStrategyConfig:
    """모든 전략 공통 설정."""
    use_fixed_qty: bool = True  # [테스트용] True면 자금 관리 로직 무시하고 무조건 1주 매수
