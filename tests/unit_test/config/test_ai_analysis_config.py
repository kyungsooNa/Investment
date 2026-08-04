from config.config_loader import AiAnalysisConfig


def test_defaults_are_disabled_and_gemini_flash():
    cfg = AiAnalysisConfig()

    assert cfg.enabled is False
    assert cfg.api_key == ""
    assert "chat/completions" not in cfg.base_url
    assert cfg.model
    assert cfg.timeout_sec > 0
    assert cfg.disclosure_summary_enabled is True
    assert cfg.daily_request_limit == 100
    assert cfg.disclosure_reserve == 20
    # Gemini 2.5 thinking 토큰이 출력 예산을 갉아먹으므로 요약이 잘리지 않게 넉넉히
    assert cfg.max_tokens >= 1024
    # thinking 예산을 제한하지 않으면 max_tokens 를 다 쓰고 본문이 잘린다(2026-08-04 실측)
    assert cfg.reasoning_effort == "low"


def test_accepts_ollama_local_overrides():
    cfg = AiAnalysisConfig.model_validate(
        {
            "enabled": True,
            "provider": "ollama",
            "base_url": "http://localhost:11434/v1",
            "api_key": "",
            "model": "qwen2.5",
            "timeout_sec": 30,
            "reasoning_effort": "",
        }
    )

    assert cfg.enabled is True
    assert cfg.base_url == "http://localhost:11434/v1"
    assert cfg.model == "qwen2.5"
    # 빈 값은 파라미터 자체를 보내지 않는다는 뜻 (미지원 provider 보호)
    assert cfg.reasoning_effort == ""


def test_unknown_keys_are_tolerated():
    cfg = AiAnalysisConfig.model_validate({"future_flag": "x", "enabled": False})

    assert cfg.enabled is False
