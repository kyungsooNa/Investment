from config.config_loader import AppConfig, DartDisclosureConfig


def test_dart_disclosure_config_defaults_are_safe():
    config = DartDisclosureConfig()

    assert config.enabled is False
    assert config.api_key == ""
    assert config.poll_interval_sec == 300
    assert config.immediate_alert_score == 70


def test_app_config_accepts_dart_disclosure_section():
    config = AppConfig.model_validate(
        {
            "web": {"host": "127.0.0.1", "port": 8000},
            "dart_disclosure": {
                "enabled": True,
                "api_key": "test-key",
                "poll_interval_sec": 600,
            },
        }
    )

    assert config.dart_disclosure.enabled is True
    assert config.dart_disclosure.poll_interval_sec == 600
