from services.trade_trend_service import NationalTradeTrendRelease
from repositories.trade_trend_repository import TradeTrendRepository


def test_trade_trend_repository_saves_national_release_history(tmp_path):
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="2026년 8월 1일 ~ 8월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/10d",
        period_label="2026년 8월 1~10일",
        export_amount_100m_usd=301.2,
        export_yoy_pct=12.3,
        import_amount_100m_usd=250.1,
        import_yoy_pct=4.5,
        trade_balance_100m_usd=51.1,
        trade_balance_label="흑자",
        published_at="2026-08-11",
        highlights=["반도체 수출 증가"],
    )
    path = tmp_path / "trade_trend_state.json"
    repo = TradeTrendRepository(path)

    repo.mark_national_release_sent(release, sent_at="2026-08-11T09:30:00")
    loaded = TradeTrendRepository(path)

    assert loaded.has_sent(release.dedup_key)
    assert loaded.get_national_release_history()[0]["period_label"] == "2026년 8월 1~10일"
    assert loaded.get_national_release_history()[0]["source_type"] == "sent"
    assert loaded.get_national_release_history()[0]["export_amount_100m_usd"] == 301.2
    assert loaded.get_national_release_history()[0]["sent_at"] == "2026-08-11T09:30:00"


def test_trade_trend_repository_backfills_legacy_national_sent_keys(tmp_path):
    path = tmp_path / "trade_trend_state.json"
    path.write_text(
        '{"sent_keys":["national_trade:customs_20d:2026년 7월 1~20일:https://customs.example/20d"]}',
        encoding="utf-8",
    )
    repo = TradeTrendRepository(path)

    history = repo.get_national_release_history()

    assert history == [
        {
            "source": "",
            "source_type": "sent",
            "phase": "customs_20d",
            "title": "",
            "url": "https://customs.example/20d",
            "period_label": "2026년 7월 1~20일",
            "export_amount_100m_usd": None,
            "export_yoy_pct": None,
            "import_amount_100m_usd": None,
            "import_yoy_pct": None,
            "trade_balance_100m_usd": None,
            "trade_balance_label": "",
            "published_at": "",
            "highlights": [],
            "sent_at": "",
            "dedup_key": "national_trade:customs_20d:2026년 7월 1~20일:https://customs.example/20d",
        }
    ]
