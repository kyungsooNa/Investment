from __future__ import annotations

from typing import Any, Mapping


def compute_portfolio_concentration_summary(
    positions: Mapping[str, Any],
    *,
    capital_basis: float,
    warn_total_exposure_pct: float | None = 80.0,
    warn_position_concentration_pct: float | None = 20.0,
    warn_strategy_concentration_pct: float | None = 40.0,
) -> dict[str, Any]:
    position_exposures: dict[str, dict[str, Any]] = {}
    strategy_amounts: dict[str, float] = {}

    for code, position in positions.items():
        total_cost = _position_value(position, "total_cost")
        strategy = str(_position_attr(position, "strategy") or "")
        position_exposures[str(code)] = {
            "exposure_won": total_cost,
            "exposure_pct": _pct(total_cost, capital_basis),
            "strategy": strategy,
        }
        if strategy:
            strategy_amounts[strategy] = strategy_amounts.get(strategy, 0.0) + total_cost

    strategy_exposures = {
        strategy: {
            "exposure_won": amount,
            "exposure_pct": _pct(amount, capital_basis),
        }
        for strategy, amount in strategy_amounts.items()
    }
    total_exposure = sum(item["exposure_won"] for item in position_exposures.values())
    max_position = _max_position(position_exposures)
    max_strategy = _max_strategy(strategy_exposures)
    warnings = _warnings(
        capital_basis=capital_basis,
        total_exposure_pct=_pct(total_exposure, capital_basis),
        max_position=max_position,
        max_strategy=max_strategy,
        warn_total_exposure_pct=warn_total_exposure_pct,
        warn_position_concentration_pct=warn_position_concentration_pct,
        warn_strategy_concentration_pct=warn_strategy_concentration_pct,
    )

    return {
        "capital_basis": capital_basis,
        "total_exposure_won": total_exposure,
        "total_exposure_pct": _pct(total_exposure, capital_basis),
        "position_exposures": position_exposures,
        "strategy_exposures": strategy_exposures,
        "max_position": max_position,
        "max_strategy": max_strategy,
        "warnings": warnings,
    }


def _position_value(position: Any, key: str) -> float:
    value = _position_attr(position, key)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _position_attr(position: Any, key: str) -> Any:
    if isinstance(position, Mapping):
        return position.get(key)
    return getattr(position, key, None)


def _pct(amount: float, capital_basis: float) -> float | None:
    if capital_basis <= 0:
        return None
    return round(amount / capital_basis * 100.0, 4)


def _max_position(position_exposures: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    if not position_exposures:
        return None
    code, exposure = max(
        position_exposures.items(),
        key=lambda item: item[1].get("exposure_won") or 0.0,
    )
    return {
        "code": code,
        "exposure_won": exposure.get("exposure_won") or 0.0,
        "exposure_pct": exposure.get("exposure_pct"),
    }


def _max_strategy(strategy_exposures: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    if not strategy_exposures:
        return None
    strategy, exposure = max(
        strategy_exposures.items(),
        key=lambda item: item[1].get("exposure_won") or 0.0,
    )
    return {
        "strategy": strategy,
        "exposure_won": exposure.get("exposure_won") or 0.0,
        "exposure_pct": exposure.get("exposure_pct"),
    }


def _warnings(
    *,
    capital_basis: float,
    total_exposure_pct: float | None,
    max_position: Mapping[str, Any] | None,
    max_strategy: Mapping[str, Any] | None,
    warn_total_exposure_pct: float | None,
    warn_position_concentration_pct: float | None,
    warn_strategy_concentration_pct: float | None,
) -> list[str]:
    if capital_basis <= 0:
        return ["portfolio_concentration_unknown_capital"]

    warnings: list[str] = []
    if _exceeds(total_exposure_pct, warn_total_exposure_pct):
        warnings.append("portfolio_total_exposure_high")
    if _exceeds((max_position or {}).get("exposure_pct"), warn_position_concentration_pct):
        warnings.append("single_position_concentration_high")
    if _exceeds((max_strategy or {}).get("exposure_pct"), warn_strategy_concentration_pct):
        warnings.append("strategy_concentration_high")
    return warnings


def _exceeds(value: Any, threshold: float | None) -> bool:
    return threshold is not None and value is not None and float(value) > threshold
