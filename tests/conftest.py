import pytest


@pytest.fixture(autouse=True)
def isolate_live_strategy_state_files(monkeypatch, tmp_path):
    from strategies.first_pullback_strategy import FirstPullbackStrategy
    from strategies.high_tight_flag_strategy import HighTightFlagStrategy
    from strategies.oneil_pocket_pivot_strategy import OneilPocketPivotStrategy
    from strategies.oneil_squeeze_breakout_strategy import OneilSqueezeBreakoutStrategy
    from strategies.rsi2_pullback_strategy import RSI2PullbackStrategy
    from strategies.traditional_volume_breakout_strategy import TraditionalVolumeBreakoutStrategy

    state_dir = tmp_path / "strategy_state"
    state_dir.mkdir()
    strategy_state_files = {
        FirstPullbackStrategy: "fp_position_state.json",
        HighTightFlagStrategy: "htf_position_state.json",
        OneilPocketPivotStrategy: "pp_position_state.json",
        OneilSqueezeBreakoutStrategy: "osb_position_state.json",
        RSI2PullbackStrategy: "rsi2_position_state.json",
        TraditionalVolumeBreakoutStrategy: "tvb_position_state.json",
    }

    for strategy_cls, file_name in strategy_state_files.items():
        monkeypatch.setattr(strategy_cls, "STATE_FILE", str(state_dir / file_name))
