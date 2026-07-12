import asyncio
import html
import inspect
import json
import time
import logging
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Awaitable, Callable

from repositories.program_trading_repo import ProgramTradingRepo


class ProgramTradingStreamService:
    """
    프로그램매매 실시간 데이터의 수신, 저장(SQLite), 클라이언트 전송을 담당하는 서비스.

    역할 구분:
      - ProgramTradingRepo          : SQLite 저장·버퍼링·구독 영속 (데이터 레이어)
      - ProgramTradingStreamService : 데이터 수신·메모리 캐시·SSE 브로드캐스트 (스트림 레이어)

    하위 호환성:
      _conn / _executor / _flush_task / _write_buffer / _buffer_lock / _lock 등
      테스트에서 직접 접근하는 내부 속성은 property로 _repo에 포워딩.
    """

    RETENTION_DAYS = 7
    FLUSH_INTERVAL_SEC = 1.0
    FLUSH_BATCH_SIZE = 100
    HOURLY_TICK_ALERT_INTERVAL_SEC = 60 * 60
    REGULAR_SESSION_CLOSE_TIME = "15:30"
    CLOSING_AUCTION_START_TIME = "15:20"

    def __init__(
        self,
        logger=None,
        after_market_runner: Callable[..., Awaitable[None]] | None = None,
    ):
        self.logger = logger if logger else logging.getLogger(__name__)
        self._after_market_runner = after_market_runner

        # 메모리 캐시 (성능 최적화)
        self._pt_history: dict = {}
        self._last_tick_ts_by_code: dict[str, float] = {}
        self.last_data_ts = 0.0

        # 클라이언트 스트리밍 큐
        self._pt_queues: list = []
        self._alert_tasks: list[asyncio.Task] = []
        self._last_db_check_report_date: str | None = None
        self._streaming_stock_repo = None
        self._telegram_reporter = None
        self._market_calendar_service = None
        self._market_clock = None
        self._stock_code_repository = None
        self._program_trade_provider = None

        # SQLite 레이어는 ProgramTradingRepo에 위임
        self._repo = ProgramTradingRepo(
            base_dir=self._get_base_dir(),
            logger=self.logger,
        )

        # 시작 시 DB에서 금일 히스토리 복원
        self._load_pt_history()

    # ── 기본 디렉토리 (테스트에서 patch 가능하도록 메서드로 유지) ─────

    def _get_base_dir(self):
        return "data/program_subscribe"

    # ── 내부 속성 포워딩 (하위 호환성 — 기존 테스트 무변경) ──────────

    @property
    def _conn(self):
        return self._repo._conn

    @_conn.setter
    def _conn(self, value):
        self._repo._conn = value

    @property
    def _executor(self):
        return self._repo._executor

    @property
    def _flush_task(self):
        return self._repo._flush_task

    @_flush_task.setter
    def _flush_task(self, value):
        self._repo._flush_task = value

    @property
    def _write_buffer(self):
        return self._repo._write_buffer

    @property
    def _buffer_lock(self):
        return self._repo._buffer_lock

    @property
    def _lock(self):
        return self._repo._lock

    # ── 내부 메서드 포워딩 ───────────────────────────────────────────

    @contextmanager
    def _get_connection(self):
        with self._repo._get_connection() as conn:
            yield conn

    def _load_pt_history(self):
        self._pt_history = self._repo.load_today_history()

    def _bulk_insert_to_db(self, batch: list):
        self._repo._bulk_insert_to_db(batch)

    async def _flush_write_buffer(self):
        await self._repo._flush_write_buffer()

    def flush_write_buffer_sync(self):
        """동기 플러시 — 테스트 또는 종료 시 잔여 데이터를 즉시 DB에 기록."""
        self._repo.flush_write_buffer_sync()

    async def _flush_loop(self):
        await self._repo._flush_loop()

    def _cleanup_old_data(self):
        self._repo._cleanup_old_data()

    def _cleanup_old_files(self):
        self._repo._cleanup_old_files()

    # ── 데이터 처리 ──────────────────────────────────────────────────

    @staticmethod
    def _safe_int(val):
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _safe_float(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    def on_data_received(self, data: dict):
        """웹소켓 등에서 수신한 데이터를 처리 (버퍼 저장 및 브로드캐스트).

        DB 쓰기는 버퍼에 적재 후 백그라운드에서 일괄 처리하여
        이벤트 루프 블로킹을 방지한다.
        """
        code = data.get('유가증권단축종목코드')
        if not code:
            return

        now = time.time()
        if self.last_data_ts > 0 and (now - self.last_data_ts) > 10.0:
            self.logger.info(f"실시간 데이터 수신 재개 (Gap: {now - self.last_data_ts:.1f}초)")

        self.last_data_ts = now
        self._last_tick_ts_by_code[code] = now

        # 1. 메모리 저장 (기존 프론트엔드/백엔드 호환용 원본 유지)
        self._pt_history.setdefault(code, []).append(data)

        # 2. 버퍼에 적재 (이벤트 루프 블로킹 방지) — repo에 위임
        self._repo.add_record_to_buffer(data, created_at=now)

        # 3. 클라이언트 브로드캐스트 (Dict 대신 Array 전송으로 네트워크 트래픽 80% 절감)
        payload = [
            code,
            data.get('주식체결시간', ''),
            self._safe_int(data.get('price', 0)),
            self._safe_float(data.get('rate', 0.0)),
            data.get('change', 0),
            data.get('sign', ''),
            self._safe_int(data.get('매도체결량', 0)),
            self._safe_int(data.get('매수2체결량', 0)),
            self._safe_int(data.get('순매수체결량', 0)),
            self._safe_int(data.get('순매수거래대금', 0)),
            self._safe_int(data.get('매도호가잔량', 0)),
            self._safe_int(data.get('매수호가잔량', 0)),
        ]

        json_payload = json.dumps(payload, ensure_ascii=False)
        for q in list(self._pt_queues):
            try:
                q.put_nowait(json_payload)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(json_payload)
                except Exception:
                    pass
            except Exception:
                pass

    # ── 클라이언트 큐 관리 ───────────────────────────────────────────

    def create_subscriber_queue(self) -> asyncio.Queue:
        """새로운 구독자(웹 클라이언트)를 위한 큐 생성 및 등록."""
        queue = asyncio.Queue(maxsize=200)
        self._pt_queues.append(queue)
        return queue

    def remove_subscriber_queue(self, queue: asyncio.Queue):
        """구독자 큐 제거."""
        if queue in self._pt_queues:
            self._pt_queues.remove(queue)

    def get_history_data(self):
        """현재 메모리에 있는 히스토리 데이터 반환."""
        return self._pt_history

    # ── 스냅샷 저장/로드 (repo 위임) ─────────────────────────────────

    def save_snapshot(self, data_dict: dict):
        return self._repo.save_snapshot(data_dict)

    def load_snapshot(self) -> dict:
        return self._repo.load_snapshot()

    # ── DB 상태 조회 (repo 위임 + 메모리 상태 추가) ────────────────────

    def inspect_db_status(self) -> dict:
        """DB 상태(마지막 저장 시간, 데이터 건수 등)를 조회하여 반환 (디버깅용)."""
        status = self._repo.inspect_db_status()
        if self.last_data_ts > 0:
            status["memory"]["last_received_at"] = datetime.fromtimestamp(
                self.last_data_ts
            ).strftime("%Y-%m-%d %H:%M:%S")
        return status

    def wire_streaming_stock_repo(self, streaming_stock_repo) -> None:
        """StreamingStockRepo를 사후 주입하여 desired flush를 repo의 flush_loop에 통합한다."""
        self._streaming_stock_repo = streaming_stock_repo
        self._repo._streaming_stock_repo = streaming_stock_repo

    def wire_alert_dependencies(
        self,
        telegram_reporter=None,
        market_calendar_service=None,
        market_clock=None,
        stock_code_repository=None,
        program_trade_provider=None,
    ) -> None:
        """운영 알림에 필요한 외부 의존성을 사후 주입한다."""
        self._telegram_reporter = telegram_reporter
        self._market_calendar_service = market_calendar_service
        self._market_clock = market_clock
        if stock_code_repository is not None:
            self._stock_code_repository = stock_code_repository
        if program_trade_provider is not None:
            self._program_trade_provider = program_trade_provider

    # ── 운영 알림 ──────────────────────────────────────────────────

    def _get_subscribed_program_codes(self) -> list[str]:
        if not self._streaming_stock_repo:
            return []
        try:
            from repositories.streaming_stock_repo import StreamingType

            return sorted(self._streaming_stock_repo.get_desired(StreamingType.PROGRAM_TRADING))
        except Exception as e:
            self.logger.warning(f"프로그램매매 구독 목록 조회 실패: {e}")
            return []

    @staticmethod
    def _format_int(value) -> str:
        try:
            return f"{int(str(value).replace(',', '')):,}"
        except (TypeError, ValueError):
            return "0"

    @staticmethod
    def _format_rate(value) -> str:
        try:
            return f"{float(value):+.2f}%"
        except (TypeError, ValueError):
            return "0.00%"

    @staticmethod
    def _format_eok(value) -> str:
        try:
            amount = int(str(value).replace(',', ''))
        except (TypeError, ValueError):
            amount = 0
        return f"{amount / 100_000_000:,.1f}억"

    def _format_stock_label(self, code: str) -> str:
        if self._stock_code_repository is None:
            return code
        try:
            name = self._stock_code_repository.get_name_by_code(code)
            return name if name and name != code else code
        except Exception as e:
            self.logger.warning(f"프로그램매매 종목명 조회 실패: code={code}, error={e}")
            return code

    @staticmethod
    def _normalize_subscription_source(source) -> str:
        if source == "program":
            return "program"
        if source == "manual":
            return "manual"
        return "legacy"

    def _get_pt_subscription_sources(self) -> dict[str, str]:
        if not self._streaming_stock_repo:
            return {}
        getter = getattr(self._streaming_stock_repo, "get_pt_subscription_sources", None)
        if not callable(getter):
            return {}
        try:
            sources = getter()
        except Exception as e:
            self.logger.warning(f"프로그램매매 구독 출처 조회 실패: {e}")
            return {}
        if not isinstance(sources, dict):
            return {}
        return {
            str(code): self._normalize_subscription_source(source)
            for code, source in sources.items()
        }

    def _sort_codes_by_subscription_source(
        self,
        codes: list[str],
        sources: dict[str, str],
    ) -> list[str]:
        return [
            code
            for _, code in sorted(
                enumerate(codes),
                key=lambda item: (
                    {"manual": 0, "program": 1, "legacy": 2}.get(sources.get(item[1]), 2),
                    item[0],
                ),
            )
        ]

    def _format_tick_alert_line(
        self,
        code: str,
        label: str,
        body: str,
        sources: dict[str, str],
    ) -> str:
        source = sources.get(code)
        if source == "manual":
            return f"<b>[수동] {html.escape(label)}: {body}</b>"
        if source == "program":
            return f"[프로그램] {html.escape(label)}: {body}"
        return f"[기존] {html.escape(label)}: {body}"

    def _extract_latest_program_snapshot(self, data: dict) -> dict | None:
        if not isinstance(data, dict):
            return None

        net_amount = self._safe_int(
            data.get("whol_smtn_ntby_tr_pbmn")
            or data.get("pgtr_ntby_tr_pbmn")
            or data.get("순매수거래대금")
        )
        if net_amount == 0:
            return None

        snapshot = {"순매수거래대금": str(net_amount)}
        price = self._safe_int(data.get("stck_clpr") or data.get("price"))
        if price > 0:
            snapshot["price"] = str(price)
        if data.get("prdy_ctrt") is not None:
            snapshot["rate"] = data.get("prdy_ctrt")
        return snapshot

    async def _get_latest_program_snapshot(self, code: str) -> dict | None:
        provider = self._program_trade_provider
        if provider is None:
            return None

        getter = getattr(provider, "get_program_trade_by_stock_daily", None)
        if getter is None:
            return None

        try:
            resp = getter(code)
            if inspect.isawaitable(resp):
                resp = await resp
            if getattr(resp, "rt_cd", None) != "0":
                return None
            return self._extract_latest_program_snapshot(getattr(resp, "data", None))
        except Exception as e:
            self.logger.warning(f"프로그램매매 REST 최신값 조회 실패: code={code}, error={e}")
            return None

    async def _format_last_tick_report_async(self, codes: list[str]) -> str:
        sources = self._get_pt_subscription_sources()
        ordered_codes = self._sort_codes_by_subscription_source(codes, sources)
        lines = [
            "<b>프로그램매매 구독 Tick 상태</b>",
            f"생성: {html.escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}",
            f"구독 종목: {len(codes)}개",
            "",
        ]
        for code in ordered_codes:
            label = self._format_stock_label(code)
            history = self._pt_history.get(code) or []
            if not history:
                lines.append(self._format_tick_alert_line(code, label, "수신 없음", sources))
                continue

            tick = dict(history[-1])
            refreshed = False
            latest_snapshot = await self._get_latest_program_snapshot(code)
            if latest_snapshot:
                tick.update(latest_snapshot)
                refreshed = True
            received_ts = self._last_tick_ts_by_code.get(code)
            received_at = (
                datetime.fromtimestamp(received_ts).strftime("%H:%M:%S")
                if received_ts else "-"
            )
            trade_time = str(tick.get("주식체결시간", "") or "-")
            price = self._format_int(tick.get("price", 0))
            rate = self._format_rate(tick.get("rate", 0.0))
            net_amt = self._format_eok(tick.get("순매수거래대금", 0))
            source = " REST보정" if refreshed else ""
            body = (
                f"{price}원 ({rate}) "
                f"체결:{html.escape(trade_time)} 수신:{received_at}{source} 누적 순매수대금:{net_amt}"
            )
            lines.append(self._format_tick_alert_line(code, label, body, sources))
        return "\n".join(lines)

    def _format_last_tick_report(self, codes: list[str]) -> str:
        """하위 호환용 동기 포맷터. 운영 전송은 REST 보정 가능한 async 경로를 사용한다."""
        sources = self._get_pt_subscription_sources()
        ordered_codes = self._sort_codes_by_subscription_source(codes, sources)
        lines = [
            "<b>프로그램매매 구독 Tick 상태</b>",
            f"생성: {html.escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}",
            f"구독 종목: {len(codes)}개",
            "",
        ]
        for code in ordered_codes:
            label = self._format_stock_label(code)
            history = self._pt_history.get(code) or []
            if not history:
                lines.append(self._format_tick_alert_line(code, label, "수신 없음", sources))
                continue

            tick = history[-1]
            received_ts = self._last_tick_ts_by_code.get(code)
            received_at = (
                datetime.fromtimestamp(received_ts).strftime("%H:%M:%S")
                if received_ts else "-"
            )
            trade_time = str(tick.get("주식체결시간", "") or "-")
            price = self._format_int(tick.get("price", 0))
            rate = self._format_rate(tick.get("rate", 0.0))
            net_amt = self._format_eok(tick.get("순매수거래대금", 0))
            body = (
                f"{price}원 ({rate}) "
                f"체결:{html.escape(trade_time)} 수신:{received_at} 누적 순매수대금:{net_amt}"
            )
            lines.append(self._format_tick_alert_line(code, label, body, sources))
        return "\n".join(lines)

    async def _send_telegram_message(self, message: str) -> bool:
        if not self._telegram_reporter:
            return False
        sender = getattr(self._telegram_reporter, "_send_message", None)
        if sender is None:
            self.logger.warning("TelegramReporter에 _send_message가 없어 PT 알림을 전송할 수 없습니다.")
            return False
        try:
            result = sender(message)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception as e:
            self.logger.error(f"프로그램매매 텔레그램 알림 전송 실패: {e}", exc_info=True)
            return False

    async def send_subscribed_last_tick_alert(self) -> bool:
        """구독 중인 PT 종목들의 마지막 수신 tick을 텔레그램으로 전송한다."""
        codes = self._get_subscribed_program_codes()
        if not codes:
            return False
        message = await self._format_last_tick_report_async(codes)
        return await self._send_telegram_message(message)

    def _build_trading_window(self, trading_date: str):
        digits = "".join(ch for ch in str(trading_date) if ch.isdigit())
        if len(digits) < 8:
            digits = datetime.now().strftime("%Y%m%d")
        digits = digits[:8]
        day = datetime.strptime(digits, "%Y%m%d")

        open_time = getattr(self._market_clock, "market_open_time_str", "09:00")
        close_time = getattr(self._market_clock, "market_close_time_str", "15:40")
        regular_close_time = self.REGULAR_SESSION_CLOSE_TIME
        if close_time > regular_close_time:
            close_time = regular_close_time
        open_hour, open_minute = map(int, open_time.split(":"))
        close_hour, close_minute = map(int, close_time.split(":"))
        start_dt = datetime(day.year, day.month, day.day, open_hour, open_minute)
        end_dt = datetime(day.year, day.month, day.day, close_hour, close_minute)

        timezone = getattr(self._market_clock, "market_timezone", None)
        if timezone is not None:
            start_dt = timezone.localize(start_dt)
            end_dt = timezone.localize(end_dt)

        expected_minutes = []
        cursor = start_dt.replace(second=0, microsecond=0)
        end_cursor = end_dt.replace(second=0, microsecond=0)
        while cursor <= end_cursor:
            expected_minutes.append(cursor.strftime("%H:%M"))
            cursor += timedelta(minutes=1)

        return start_dt, end_dt, expected_minutes, timezone

    @staticmethod
    def _minute_from_trade_time(trade_time) -> str | None:
        digits = "".join(ch for ch in str(trade_time or "") if ch.isdigit())
        if len(digits) < 4:
            return None
        hour = int(digits[:2])
        minute = int(digits[2:4])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return f"{hour:02d}:{minute:02d}"

    def _history_minutes_from_memory(self, code: str, expected_set: set[str]) -> set[str]:
        minutes: set[str] = set()
        for tick in self._pt_history.get(code) or []:
            minute = self._minute_from_trade_time(tick.get("주식체결시간"))
            if minute in expected_set:
                minutes.add(minute)
        return minutes

    def build_db_minute_persistence_status(self, codes: list[str], trading_date: str) -> dict:
        """구독 종목별로 수신 분봉과 DB 저장 분봉을 분리해 계산한다."""
        start_dt, end_dt, expected_minutes, timezone = self._build_trading_window(trading_date)
        records = self._repo.get_history_records_for_persistence_by_code(
            codes,
            start_dt.timestamp(),
            (end_dt + timedelta(seconds=59)).timestamp(),
        )
        expected_set = set(expected_minutes)

        status = {
            "date": trading_date,
            "window": {
                "start": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "end": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "expected_minute_count": len(expected_minutes),
            "codes": {},
        }
        for code in codes:
            saved_minutes = set()
            for record in records.get(code, []):
                minute = self._minute_from_trade_time(record.get("trade_time"))
                if minute is None:
                    created_at = record.get("created_at")
                    dt = datetime.fromtimestamp(created_at, tz=timezone) if timezone else datetime.fromtimestamp(created_at)
                    minute = dt.strftime("%H:%M")
                if minute in expected_set:
                    saved_minutes.add(minute)
            received_minutes = self._history_minutes_from_memory(code, expected_set)
            if not received_minutes:
                received_minutes = set(saved_minutes)
            missing = [minute for minute in expected_minutes if minute not in saved_minutes]
            unsaved_received = [
                minute for minute in expected_minutes
                if minute in received_minutes and minute not in saved_minutes
            ]
            no_tick = [
                minute for minute in expected_minutes
                if minute not in received_minutes
            ]
            status["codes"][code] = {
                "ok": not missing,
                "saved_minute_count": len(saved_minutes & expected_set),
                "received_minute_count": len(received_minutes & expected_set),
                "missing_minute_count": len(missing),
                "missing_minutes": missing,
                "unsaved_received_minute_count": len(unsaved_received),
                "unsaved_received_minutes": unsaved_received,
                "no_tick_minute_count": len(no_tick),
                "no_tick_minutes": no_tick,
            }
        return status

    def _format_db_persistence_report(self, status: dict) -> str:
        lines = [
            f"<b>프로그램매매 DB 저장 점검 ({html.escape(str(status.get('date', '')))}일)</b>",
            f"구간: {html.escape(status['window']['start'])} ~ {html.escape(status['window']['end'])}",
            f"기대 분봉: {status['expected_minute_count']}분",
            "기준: 체결시간 기준, 정규장 프로그램매매 수신 분봉 대비 DB 저장 여부",
            "",
        ]
        for code, item in status["codes"].items():
            label = self._format_stock_label(code)
            saved = item["saved_minute_count"]
            expected = status["expected_minute_count"]
            if item["ok"]:
                lines.append(f"{html.escape(label)}: OK ({saved}/{expected})")
            else:
                display_missing_minutes = [
                    minute for minute in item["missing_minutes"]
                    if minute < self.CLOSING_AUCTION_START_TIME
                ]
                sample = ", ".join(display_missing_minutes[:10])
                extra = "" if len(display_missing_minutes) <= 10 else f" 외 {len(display_missing_minutes) - 10}분"
                unsaved = item.get("unsaved_received_minute_count", 0)
                no_tick = item.get("no_tick_minute_count", item["missing_minute_count"])
                line = (
                    f"{html.escape(label)}: 누락 {item['missing_minute_count']}분 "
                    f"(DB 미저장 {unsaved}분, 수신없음 {no_tick}분, 저장 {saved}/{expected})"
                )
                if sample:
                    line += f" 예: {html.escape(sample)}{extra}"
                lines.append(line)
        return "\n".join(lines)

    def get_background_task_status(self) -> dict:
        """system.py에서 서비스 내부 백그라운드 루프 상태를 노출하기 위한 요약."""
        flush_task = self._flush_task
        alert_tasks = [task for task in self._alert_tasks if not task.done()]

        def _is_coro_running(name: str) -> bool:
            return any(getattr(task.get_coro(), "__name__", "") == name for task in alert_tasks)

        last_received_at = None
        if self.last_data_ts > 0:
            last_received_at = datetime.fromtimestamp(self.last_data_ts).strftime("%Y-%m-%d %H:%M:%S")

        return {
            "running": bool((flush_task and not flush_task.done()) or alert_tasks),
            "flush_loop_alive": bool(flush_task and not flush_task.done()),
            "alert_task_count": len(alert_tasks),
            "hourly_tick_alert_alive": _is_coro_running("_hourly_tick_alert_loop"),
            "db_check_alive": _is_coro_running("_after_market_db_check_loop"),
            "last_db_check_report_date": self._last_db_check_report_date,
            "last_received_at": last_received_at,
        }

    async def send_db_persistence_report(self, trading_date: str) -> bool:
        """장마감 후 구독 종목의 1분 단위 DB 저장 여부를 텔레그램으로 전송한다."""
        codes = self._get_subscribed_program_codes()
        if not codes:
            return False
        await self._flush_write_buffer()
        status = self.build_db_minute_persistence_status(codes, trading_date)
        message = self._format_db_persistence_report(status)
        return await self._send_telegram_message(message)

    async def _is_market_open_for_tick_alert(self) -> bool:
        if not self._market_calendar_service:
            return True
        try:
            return bool(await self._market_calendar_service.is_market_open_now())
        except Exception as e:
            self.logger.warning(f"프로그램매매 tick 알림 장중 여부 확인 실패: {e}")
            return False

    async def _hourly_tick_alert_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.HOURLY_TICK_ALERT_INTERVAL_SEC)
                if await self._is_market_open_for_tick_alert():
                    await self.send_subscribed_last_tick_alert()
                else:
                    self.logger.debug("장중이 아니므로 프로그램매매 tick 상태 알림을 생략합니다.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.error(f"프로그램매매 시간별 tick 알림 루프 종료: {e}", exc_info=True)

    async def _after_market_db_check_loop(self) -> None:
        async def _on_market_closed(latest_trading_date: str) -> None:
            if self._last_db_check_report_date == latest_trading_date:
                return
            sent = await self.send_db_persistence_report(latest_trading_date)
            if sent:
                self._last_db_check_report_date = latest_trading_date

        if self._after_market_runner is None:
            self.logger.warning("장마감 실행기가 주입되지 않아 프로그램매매 DB 점검을 생략합니다.")
            return

        await self._after_market_runner(
            mcs=self._market_calendar_service,
            market_clock=self._market_clock,
            logger=self.logger,
            on_market_closed=_on_market_closed,
            label="ProgramTradingDbCheck",
        )

    def _start_alert_tasks(self) -> None:
        self._alert_tasks = [task for task in self._alert_tasks if not task.done()]
        if not self._telegram_reporter:
            return

        hourly_running = any(
            getattr(task.get_coro(), "__name__", "") == "_hourly_tick_alert_loop"
            for task in self._alert_tasks
        )
        if not hourly_running:
            self._alert_tasks.append(asyncio.create_task(self._hourly_tick_alert_loop()))

        if self._market_calendar_service and self._market_clock:
            db_check_running = any(
                getattr(task.get_coro(), "__name__", "") == "_after_market_db_check_loop"
                for task in self._alert_tasks
            )
            if not db_check_running:
                self._alert_tasks.append(asyncio.create_task(self._after_market_db_check_loop()))

    # ── 생명주기 관리 ────────────────────────────────────────────────

    def start_background_tasks(self):
        """백그라운드 태스크 시작 (데이터 정리 + 버퍼 플러시 루프)."""
        self._repo.start_flush_loop()
        self._start_alert_tasks()
        self.logger.info("ProgramTradingStreamService: 초기화 완료 (버퍼 기반 일괄 저장 모드)")

    async def shutdown(self):
        """서비스 종료 처리."""
        for task in self._alert_tasks:
            if not task.done():
                task.cancel()
        if self._alert_tasks:
            await asyncio.gather(*self._alert_tasks, return_exceptions=True)
        self._alert_tasks.clear()
        await self._repo.shutdown()
        self.logger.info("ProgramTradingStreamService: 종료 완료")
