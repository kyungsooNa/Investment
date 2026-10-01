from pathlib import Path

from scripts.run_validation_campaign import SubprocessStrategyRunner


def test_subprocess_runner_builds_captured_candidate_backtest_command():
    runner = SubprocessStrategyRunner(python_executable="python", paper=True)

    command = runner.build_command(
        "larry_williams_vbo",
        ["20260928", "20260929"],
        Path("captures"),
        Path("result.json"),
    )

    assert command[:4] == ["python", "-m", "scripts.run_backtest", "--strategy"]
    assert command[command.index("--dates") + 1] == "20260928,20260929"
    assert command[command.index("--captured-candidate-dir") + 1] == "captures"
    assert command[-1] == "--paper"
