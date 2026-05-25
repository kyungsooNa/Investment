from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]

ACTIVE_BUY_SIGNAL_FILES = {
    "first_pullback": PROJECT_ROOT / "strategies" / "first_pullback_strategy.py",
    "high_tight_flag": PROJECT_ROOT / "strategies" / "high_tight_flag_strategy.py",
    "larry_williams_cb": PROJECT_ROOT
    / "strategies"
    / "larry_williams_channel_breakout_strategy.py",
    "larry_williams_vbo": PROJECT_ROOT
    / "strategies"
    / "larry_williams_vbo_strategy.py",
    "oneil_pocket_pivot": PROJECT_ROOT
    / "strategies"
    / "oneil_pocket_pivot_strategy.py",
    "oneil_squeeze_breakout": PROJECT_ROOT
    / "strategies"
    / "oneil_squeeze_breakout_strategy.py",
    "rsi2_pullback": PROJECT_ROOT / "strategies" / "rsi2_pullback_strategy.py",
}

REQUIRED_BUY_KEYWORDS = {
    "entry_reason",
    "invalidation_price",
    "stop_loss_price",
    "expected_holding_period_days",
    "confidence",
    "required_data",
}


def test_active_buy_signals_fill_analysis_contract_fields():
    """Active BUY producers must fill the P3-4 analysis contract fields."""
    failures: list[str] = []

    for strategy_id, path in ACTIVE_BUY_SIGNAL_FILES.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        buy_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and _is_buy_trade_signal_call(node)
        ]
        assert buy_calls, f"{strategy_id} has no BUY TradeSignal call"

        for call in buy_calls:
            keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            missing = sorted(
                name
                for name in REQUIRED_BUY_KEYWORDS
                if name not in keywords or _is_none_literal(keywords[name])
            )
            if missing:
                failures.append(
                    f"{strategy_id}:{call.lineno} missing required fields {missing}"
                )

            if (
                "target_price" not in keywords
                or _is_none_literal(keywords["target_price"])
            ) and (
                "trailing_rule" not in keywords
                or _is_none_literal(keywords["trailing_rule"])
            ):
                failures.append(
                    f"{strategy_id}:{call.lineno} must fill target_price or trailing_rule"
                )

    assert not failures, "\n".join(failures)


def _is_buy_trade_signal_call(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Name) or node.func.id != "TradeSignal":
        return False
    for keyword in node.keywords:
        if keyword.arg == "action" and _is_buy_literal(keyword.value):
            return True
    return False


def _is_buy_literal(value: ast.AST) -> bool:
    return isinstance(value, ast.Constant) and value.value == "BUY"


def _is_none_literal(value: ast.AST) -> bool:
    return isinstance(value, ast.Constant) and value.value is None
