"""전략 rejected reason 리포트 API 테스트."""
from __future__ import annotations

import json
import os
import pytest
from unittest.mock import MagicMock

from services.rejection_distribution_service import RejectionDistributionService


# ── 메모리 기반 응답 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rejected_reasons_from_memory(web_client, mock_web_ctx):
    """rejection_distribution_service 메모리 데이터를 반환한다."""
    svc = RejectionDistributionService(reason_labels={"insufficient_volume": "거래량 미달"})
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    mock_web_ctx.rejection_distribution_service = svc

    resp = web_client.get("/api/strategies/rejected-reasons?strategy=StrategyA&date=20260513")
    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == "20260513"
    assert data["strategy"] == "StrategyA"
    rows = data["distribution"]
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "insufficient_volume"
    assert rows[0]["count"] == 2
    assert rows[0]["label_kr"] == "거래량 미달"


@pytest.mark.asyncio
async def test_rejected_reasons_all_strategies(web_client, mock_web_ctx):
    """strategy 파라미터 없으면 전체 전략을 반환한다."""
    svc = RejectionDistributionService()
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    svc.record("StrategyB", "low_execution_strength", date="20260513")
    mock_web_ctx.rejection_distribution_service = svc

    resp = web_client.get("/api/strategies/rejected-reasons?date=20260513")
    assert resp.status_code == 200
    data = resp.json()
    strategies = {r["strategy"] for r in data["distribution"]}
    assert "StrategyA" in strategies
    assert "StrategyB" in strategies


@pytest.mark.asyncio
async def test_rejected_reasons_returns_empty_when_no_service(web_client, mock_web_ctx):
    """rejection_distribution_service가 None이면 빈 결과를 반환한다."""
    mock_web_ctx.rejection_distribution_service = None

    resp = web_client.get("/api/strategies/rejected-reasons?date=20260513")
    assert resp.status_code == 200
    assert resp.json()["distribution"] == []


# ── 파일 기반 응답 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rejected_reasons_from_file(web_client, mock_web_ctx, tmp_path, monkeypatch):
    """JSONL 파일 데이터를 읽어 반환한다."""
    svc = RejectionDistributionService()
    mock_web_ctx.rejection_distribution_service = svc

    # JSONL 파일 생성
    log_dir = str(tmp_path)
    rows = [
        {"strategy": "StrategyA", "date": "20260510", "reason_code": "insufficient_volume",
         "count": 5, "label_kr": "거래량 미달"},
        {"strategy": "StrategyA", "date": "20260510", "reason_code": "low_execution_strength",
         "count": 3, "label_kr": "체결강도 미달"},
    ]
    path = os.path.join(log_dir, "20260510.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 라우트의 DEFAULT_LOG_DIR를 tmp_path로 패치
    import view.web.routes.strategy_report as route_mod
    monkeypatch.setattr(route_mod, "_DEFAULT_LOG_DIR", log_dir)

    resp = web_client.get("/api/strategies/rejected-reasons?strategy=StrategyA&date=20260510")
    assert resp.status_code == 200
    data = resp.json()
    assert data["date"] == "20260510"
    assert len(data["distribution"]) == 2
    reason_codes = {r["reason_code"] for r in data["distribution"]}
    assert "insufficient_volume" in reason_codes
    assert "low_execution_strength" in reason_codes


@pytest.mark.asyncio
async def test_rejected_reasons_file_not_found_returns_empty(web_client, mock_web_ctx, tmp_path, monkeypatch):
    """파일이 없으면 빈 결과를 반환한다."""
    mock_web_ctx.rejection_distribution_service = None

    import view.web.routes.strategy_report as route_mod
    monkeypatch.setattr(route_mod, "_DEFAULT_LOG_DIR", str(tmp_path))

    resp = web_client.get("/api/strategies/rejected-reasons?date=19990101")
    assert resp.status_code == 200
    assert resp.json()["distribution"] == []


# ── 필드명 일관성 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rejected_reasons_field_names(web_client, mock_web_ctx):
    """응답 필드명이 format_json()의 reason/event/code 규약과 일치한다."""
    svc = RejectionDistributionService(reason_labels={"insufficient_volume": "거래량 미달"})
    svc.record("StrategyA", "insufficient_volume", date="20260513")
    mock_web_ctx.rejection_distribution_service = svc

    resp = web_client.get("/api/strategies/rejected-reasons?strategy=StrategyA&date=20260513")
    row = resp.json()["distribution"][0]
    # 필수 필드명
    assert "reason_code" in row   # format_json()의 events[].reason 에 대응
    assert "count" in row
    assert "label_kr" in row
    assert "strategy" in row


def test_diagnostic_report_archive_list(web_client, mock_web_ctx):
    mock_web_ctx.strategy_diagnostic_report_repository.list_reports.return_value = [
        {
            "id": "20260714_162151_000001_strategy_diagnostic_report.html",
            "report_date": "20260714",
            "created_at": "2026-07-14T16:21:51",
        }
    ]
    mock_web_ctx.telegram_notification_repository.list_reports.return_value = [
        {
            "id": "telegram-7",
            "report_date": "20260715",
            "created_at": "2026-07-15T17:00:00+09:00",
            "kind": "telegram",
            "title": "신고가 리포트",
            "source": "report",
        }
    ]

    response = web_client.get("/api/strategies/diagnostic-reports?limit=20")

    assert response.status_code == 200
    assert response.json()["reports"][0]["title"] == "신고가 리포트"
    assert response.json()["reports"][1]["report_date"] == "20260714"
    mock_web_ctx.strategy_diagnostic_report_repository.list_reports.assert_called_once_with(
        limit=20
    )
    mock_web_ctx.telegram_notification_repository.list_reports.assert_called_once_with(limit=20)


def test_diagnostic_report_archive_detail(web_client, mock_web_ctx):
    report_id = "20260714_162151_000001_strategy_diagnostic_report.html"
    mock_web_ctx.strategy_diagnostic_report_repository.get_report.return_value = {
        "id": report_id,
        "report_date": "20260714",
        "created_at": "2026-07-14T16:21:51",
        "content": "<b>상세 리포트</b>",
    }

    response = web_client.get(f"/api/strategies/diagnostic-reports/{report_id}")

    assert response.status_code == 200
    assert response.json()["content"] == "<b>상세 리포트</b>"


def test_diagnostic_report_archive_detail_not_found(web_client, mock_web_ctx):
    mock_web_ctx.strategy_diagnostic_report_repository.get_report.return_value = None

    response = web_client.get("/api/strategies/diagnostic-reports/missing.html")

    assert response.status_code == 404


def test_telegram_report_archive_detail(web_client, mock_web_ctx):
    mock_web_ctx.telegram_notification_repository.get_report.return_value = {
        "id": "telegram-7",
        "report_date": "20260715",
        "created_at": "2026-07-15T17:00:00+09:00",
        "kind": "telegram",
        "title": "신고가 리포트",
        "source": "report",
        "content": "신고가 종목 10개",
    }

    response = web_client.get("/api/strategies/diagnostic-reports/telegram-7")

    assert response.status_code == 200
    assert response.json()["content"] == "신고가 종목 10개"
    mock_web_ctx.telegram_notification_repository.get_report.assert_called_once_with("telegram-7")
