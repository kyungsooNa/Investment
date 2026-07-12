from dataclasses import dataclass


@dataclass
class BaseStrategyConfig:
    """모든 전략 공통 설정."""

    use_fixed_qty: bool = True
