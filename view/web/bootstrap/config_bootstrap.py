"""ConfigBootstrap — `WebAppContext.load_config_and_env()` 본문을 전담한다.

`WebAppContext` 인스턴스를 받아 환경 설정 로드와 인프라 서비스
(NotificationService, KillSwitchService, MarketCalendarService 등) 생성
까지 수행한다. 추가 책임은 두지 않는다 — `_bootstrap_services` 분리는
후속 PR에서 진행한다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from config.config_loader import KillSwitchConfig, load_configs
from brokers.korea_investment.korea_invest_env import KoreaInvestApiEnv
from core.market_clock import MarketClock
from services.kill_switch_service import KillSwitchService
from services.market_calendar_service import MarketCalendarService
from services.notification_service import NotificationService
from services.operator_alert_service import OperatorAlertService
from services.rejection_distribution_service import RejectionDistributionService
from services.strategy_log_report_service import _REASON_KR
from services.telegram_notifier import TelegramNotifier, TelegramReporter

if TYPE_CHECKING:  # pragma: no cover
    from view.web.web_app_initializer import WebAppContext


class ConfigBootstrap:
    """`WebAppContext` 의 환경 로드 단계를 캡슐화한 method object."""

    def __init__(self, context: "WebAppContext") -> None:
        self._ctx = context

    def run(self) -> None:
        ctx = self._ctx

        config_data = load_configs()
        ctx.full_config = config_data
        ctx.market_mode = getattr(config_data, "market_mode", "domestic")

        config_dict = config_data
        if hasattr(config_data, "model_dump"):
            config_dict = config_data.model_dump()
        elif hasattr(config_data, "dict"):
            config_dict = config_data.dict()

        ctx.env = KoreaInvestApiEnv(config_dict, ctx.logger)
        ctx.market_clock = MarketClock(
            market_open_time=config_dict.get("market_open_time", "09:00"),
            market_close_time=config_dict.get("market_close_time", "15:40"),
            timezone=config_dict.get("market_timezone", "Asia/Seoul"),
            logger=ctx.logger,
        )
        ctx.virtual_repo.tm = ctx.market_clock
        ctx.virtual_trade_service.tm = ctx.market_clock
        ctx.notification_service = NotificationService(ctx.market_clock)
        ctx.operator_alert_service = OperatorAlertService(
            notification_service=ctx.notification_service,
            market_clock=ctx.market_clock,
            logger=ctx.logger,
        )
        ctx.kill_switch_service = KillSwitchService(
            config=getattr(ctx.full_config, "kill_switch", None) or KillSwitchConfig(),
            notification_service=ctx.notification_service,
            logger=ctx.logger,
            operator_alert_service=ctx.operator_alert_service,
        )
        ctx.rejection_distribution_service = RejectionDistributionService(
            reason_labels=_REASON_KR,
        )
        ctx.rejection_distribution_service.attach_to_strategy_logger()

        self._register_telegram(ctx, config_dict)

        ctx._load_position_sizing_state()
        ctx.logger.info(f"웹 앱: 환경 설정 로드 완료. market_mode={ctx.market_mode}")

        ctx._mcs = MarketCalendarService(
            ctx.market_clock, ctx.logger, performance_profiler=ctx.pm
        )

    @staticmethod
    def _register_telegram(ctx: "WebAppContext", config_dict: dict) -> None:
        telegram_backlog_bot_token = config_dict.get("telegram_backlog_bot_token")
        telegram_strategy_bot_token = config_dict.get("telegram_strategy_bot_token")
        telegram_report_bot_token = config_dict.get("telegram_report_bot_token")
        telegram_chat_id = config_dict.get("telegram_chat_id")

        notifications_cfg = config_dict.get("notifications") or {}
        telegram_cfg = (
            notifications_cfg.get("telegram", {})
            if isinstance(notifications_cfg, dict)
            else {}
        )
        telegram_enabled = (
            telegram_cfg.get("enabled", True) if isinstance(telegram_cfg, dict) else True
        )

        if (
            telegram_enabled
            and telegram_backlog_bot_token
            and telegram_strategy_bot_token
            and telegram_report_bot_token
            and telegram_chat_id
        ):
            ctx.telegram_notifier = TelegramNotifier(
                backlog_bot_token=telegram_backlog_bot_token,
                strategy_bot_token=telegram_strategy_bot_token,
                chat_id=telegram_chat_id,
            )
            ctx.notification_service.register_external_handler(
                ctx.telegram_notifier.handle_event
            )
            ctx.logger.info("텔레그램 외부 알림 핸들러가 성공적으로 등록되었습니다.")

            ctx.telegram_reporter = TelegramReporter(
                report_bot_token=telegram_report_bot_token, chat_id=telegram_chat_id
            )
            ctx.logger.info("텔레그램 리포터가 초기화되었습니다.")
        else:
            ctx.logger.info("텔레그램 설정이 누락되어 알림 핸들러를 등록하지 않습니다.")
