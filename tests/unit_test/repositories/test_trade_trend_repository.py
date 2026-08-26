from services.trade_trend_service import NationalTradeTrendRelease, TradeStatItem
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
        export_daily_avg_100m_usd=30.1,
        working_days_current=10.0,
        semiconductor_export_amount_100m_usd=101.0,
        semiconductor_yoy_pct=55.0,
        semiconductor_daily_avg_100m_usd=10.1,
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
    assert loaded.get_national_release_history()[0]["semiconductor_export_amount_100m_usd"] == 101.0
    assert loaded.get_national_release_history()[0]["working_days_current"] == 10.0
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
            "export_mom_change_100m_usd": None,
            "export_mom_pct": None,
            "export_daily_avg_100m_usd": None,
            "export_daily_avg_mom_pct": None,
            "import_amount_100m_usd": None,
            "import_yoy_pct": None,
            "import_mom_change_100m_usd": None,
            "import_mom_pct": None,
            "trade_balance_100m_usd": None,
            "trade_balance_label": "",
            "trade_balance_mom_change_100m_usd": None,
            "semiconductor_export_amount_100m_usd": None,
            "semiconductor_yoy_pct": None,
            "semiconductor_mom_change_100m_usd": None,
            "semiconductor_mom_pct": None,
            "semiconductor_daily_avg_100m_usd": None,
            "semiconductor_daily_avg_mom_pct": None,
            "working_days_current": None,
            "working_days_previous_year": None,
            "published_at": "",
            "highlights": [],
            "sent_at": "",
            "dedup_key": "national_trade:customs_20d:2026년 7월 1~20일:https://customs.example/20d",
        }
    ]


def test_trade_trend_repository_skips_legacy_key_when_period_history_exists(tmp_path):
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="2026년 8월 1일 ~ 8월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/10d",
        period_label="2026년 8월 1~10일",
        export_amount_100m_usd=213,
        import_amount_100m_usd=195,
        trade_balance_100m_usd=18,
        trade_balance_label="흑자",
        published_at="2026-08-11",
    )
    path = tmp_path / "trade_trend_state.json"
    repo = TradeTrendRepository(path)
    repo.mark_sent(
        "national_trade:customs_10d:2026년 8월 1~10일:https://tradedata.example/10d"
    )
    repo.mark_national_release_sent(release, sent_at="2026-08-11T17:27:41+09:00")

    history = TradeTrendRepository(path).get_national_release_history()

    assert len(history) == 1
    assert history[0]["dedup_key"] == release.dedup_key
    assert history[0]["export_amount_100m_usd"] == 213


def test_trade_trend_repository_backfill_saves_release_without_sent_at(tmp_path):
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="2026년 6월 1일 ~ 6월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/20260610",
        period_label="2026년 6월 1~10일",
        export_amount_100m_usd=250,
        import_amount_100m_usd=210,
        trade_balance_100m_usd=40,
        trade_balance_label="흑자",
        published_at="2026-06-11",
    )
    path = tmp_path / "trade_trend_state.json"
    repo = TradeTrendRepository(path)

    repo.save_national_release(release, source_type="backfill")
    loaded = TradeTrendRepository(path)
    history = loaded.get_national_release_history()

    assert loaded.has_sent(release.dedup_key)
    assert history[0]["source_type"] == "backfill"
    assert history[0]["sent_at"] == ""
    assert history[0]["period_label"] == "2026년 6월 1~10일"


def test_trade_trend_repository_backfill_preserves_existing_sent_marker(tmp_path):
    release = NationalTradeTrendRelease(
        source="customs",
        phase="customs_10d",
        title="2026년 8월 1일 ~ 8월 10일 수출입 현황 [잠정치]",
        url="https://customs.example/10d",
        period_label="2026년 8월 1~10일",
        export_amount_100m_usd=213,
        import_amount_100m_usd=195,
        trade_balance_100m_usd=18,
        trade_balance_label="흑자",
        published_at="2026-08-11",
    )
    path = tmp_path / "trade_trend_state.json"
    repo = TradeTrendRepository(path)
    repo.mark_national_release_sent(release, sent_at="2026-08-11T17:27:41+09:00")

    repo.save_national_release(release, source_type="backfill")
    history = TradeTrendRepository(path).get_national_release_history()

    assert history[0]["source_type"] == "sent"
    assert history[0]["sent_at"] == "2026-08-11T17:27:41+09:00"


def test_trade_trend_repository_saves_customs_trade_cache(tmp_path):
    path = tmp_path / "trade_trend_state.json"
    repo = TradeTrendRepository(path)
    rows = [
        TradeStatItem(
            "2026.07",
            "제주",
            "-",
            167678,
            83536,
            84143,
            export_weight=11,
            import_weight=22,
        )
    ]

    repo.save_customs_trade_items("sido_total", "202607", rows)
    loaded = TradeTrendRepository(path)

    cached = loaded.get_customs_trade_items("sido_total", "202607")
    assert cached == rows
    assert loaded.get_customs_trade_items("sido_total", "202606") is None
