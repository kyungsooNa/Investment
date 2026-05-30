# app/stock_query_service.py
from __future__ import annotations
import time
from common.market_snapshot import ConclusionSnapshot, MarketSnapshot
from common.types import ErrorCode, ResCommonResponse, ResTopMarketCapApiItem, ResBasicStockInfo, \
    ResStockFullInfoApiOutput, Exchange
from config.DynamicConfig import DynamicConfig
from typing import List, Dict, Optional, Tuple, Literal
from core.performance_profiler import PerformanceProfiler
from services.data_quality_service import DataQualityService
from services.notification_service import NotificationService, NotificationCategory, NotificationLevel
from services.market_data_service import MarketDataService


class StockQueryService:
    """
    주식 현재가, 계좌 잔고, 시가총액 조회 등 데이터 조회 관련 핸들러를 관리하는 클래스입니다.
    MarketDataService, BrokerAPIWrapper 등 인스턴스를 주입받아 사용합니다.
    """

    def __init__(self, market_data_service: MarketDataService, logger, market_clock, indicator_service=None,
                 ranking_task=None, performance_profiler: Optional[PerformanceProfiler] = None,
                 notification_service: Optional[NotificationService] = None,
                 broker_api_wrapper=None,
                 streaming_logger=None,
                 price_stream_service=None,
                 price_subscription_service=None,
                 snapshot_max_age_sec: float = 5.0):
        self.broker = broker_api_wrapper
        self.market_data_service = market_data_service
        self.logger = logger
        self.market_clock = market_clock
        self.indicator_service = indicator_service
        self.ranking_task = ranking_task
        self.pm = performance_profiler if performance_profiler else PerformanceProfiler(enabled=False)
        self._notification_service = notification_service
        self._streaming_logger = streaming_logger
        self.price_stream_service = price_stream_service
        self.price_subscription_service = price_subscription_service
        self._snapshot_max_age_sec = snapshot_max_age_sec
        self._price_lookup_stats: Dict[str, int] = {
            "snapshot_hit": 0,
            "no_tick_fallback": 0,
            "stale_fallback": 0,
            "rest_fallback": 0,
            "force_fresh_bypass": 0,
            "full_output_required": 0,
            "stream_unavailable_fallback": 0,
            "conclusion_hit": 0,
            "conclusion_stale_fallback": 0,
            "conclusion_missing_fallback": 0,
            "batch_prefetch_call": 0,
            "batch_prefetch_backfill": 0,
            "batch_prefetch_skip_fresh": 0,
            "batch_prefetch_failure": 0,
            "batch_prefetch_circuit_open": 0,
        }
        self._multi_price_prefetch_failure_threshold = 3
        self._multi_price_prefetch_cooldown_sec = 300.0
        self._multi_price_prefetch_consecutive_failures = 0
        self._multi_price_prefetch_disabled_until = 0.0

    def _count_price_lookup(self, key: str, enabled: bool = True) -> None:
        """운영 현재가 조회 통계를 선택적으로 증가시킨다."""
        if not enabled:
            return
        self._price_lookup_stats[key] = self._price_lookup_stats.get(key, 0) + 1

    def _is_multi_price_prefetch_circuit_open(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return now < self._multi_price_prefetch_disabled_until

    def _record_multi_price_prefetch_success(self) -> None:
        self._multi_price_prefetch_consecutive_failures = 0
        self._multi_price_prefetch_disabled_until = 0.0

    def _record_multi_price_prefetch_failure(self, *, count_stats: bool = True) -> None:
        self._count_price_lookup("batch_prefetch_failure", count_stats)
        self._multi_price_prefetch_consecutive_failures += 1
        if self._multi_price_prefetch_consecutive_failures < self._multi_price_prefetch_failure_threshold:
            return

        self._multi_price_prefetch_disabled_until = time.time() + self._multi_price_prefetch_cooldown_sec
        self.logger.info(
            {
                "event": "batch_prefetch_circuit_opened",
                "failures": self._multi_price_prefetch_consecutive_failures,
                "cooldown_sec": self._multi_price_prefetch_cooldown_sec,
            }
        )

    def price_lookup_stats_snapshot(self) -> Dict[str, int]:
        """현재가 조회/캐시 지표 카운터 사본을 반환한다 (P2 2-2 2차).

        호출자(예: StrategyScheduler)가 cycle 진입/완료 시점의 snapshot 을 비교해
        cycle 단위 delta 를 산출하는 데 사용한다. 반환 dict 변형은 내부 상태에 영향 없음.
        """
        return dict(self._price_lookup_stats)

    async def sync_price_subscriptions(
        self,
        codes: List[str],
        category_key: str,
        priority=None,
    ) -> bool:
        """전략 후보군을 실시간 현재가 구독 대상으로 동기화한다.

        구독 서비스가 없거나 실패해도 전략 스캔 자체는 계속 진행한다.
        """
        sub_svc = self.price_subscription_service
        if sub_svc is None:
            return False

        unique_codes: List[str] = []
        seen = set()
        for code in codes or []:
            code_str = str(code or "").strip()
            if not code_str or code_str in seen:
                continue
            seen.add(code_str)
            unique_codes.append(code_str)
        if not unique_codes:
            return False

        if priority is None:
            from services.price_subscription_service import SubscriptionPriority
            priority = SubscriptionPriority.MEDIUM

        sync_subscriptions = getattr(sub_svc, "sync_subscriptions", None)
        if not callable(sync_subscriptions):
            return False

        try:
            await sync_subscriptions(unique_codes, category_key, priority)
            return True
        except Exception as e:
            self.logger.warning({
                "event": "price_subscription_sync_failed",
                "category_key": category_key,
                "count": len(unique_codes),
                "error": str(e),
            })
            return False

    def _get_sign_from_code(self, sign_code):
        """API 응답의 부호 코드(1,2,3,4,5)를 실제 부호 문자열로 변환합니다."""
        if sign_code == '1' or sign_code == '2':  # 1:상한, 2:상승
            return "+"
        elif sign_code == '4' or sign_code == '5':  # 4:하한, 5:하락
            return "-"
        else:  # 3:보합 (또는 기타)
            return ""

    def _build_snapshot_response(self, snap: dict) -> ResCommonResponse:
        """PriceStreamService 캐시 dict → ResCommonResponse(output=ResStockFullInfoApiOutput) 변환.

        snapshot에 없는 필드는 ""로 채워진다. 호출자가 per/pbr 같은
        REST 전용 필드가 필요하면 force_fresh=True를 사용한다.
        """
        def _snapshot_value(value, default: str = "0") -> str:
            if value in (None, "", "N/A"):
                return default
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)

        fields = {name: "" for name in ResStockFullInfoApiOutput.model_fields}
        fields.update({
            "stck_prpr": _snapshot_value(snap.get("price")),
            "prdy_vrss": _snapshot_value(snap.get("change")),
            "prdy_ctrt": _snapshot_value(snap.get("rate"), "0.00"),
            "prdy_vrss_sign": _snapshot_value(snap.get("sign"), "3"),
            "acml_vol": _snapshot_value(snap.get("acml_vol")),
            "acml_tr_pbmn": _snapshot_value(snap.get("acml_tr_pbmn")),
            "stck_hgpr": _snapshot_value(snap.get("high")),
            "stck_lwpr": _snapshot_value(snap.get("low")),
            "stck_oprc": _snapshot_value(snap.get("open")),
        })
        output = ResStockFullInfoApiOutput.model_validate(fields)
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="snapshot",
            data={"output": output},
        )

    async def get_current_price(
        self,
        stock_code: str,
        exchange: Exchange = Exchange.KRX,
        count_stats: bool = True,
        caller: str = "unknown",
        force_fresh: bool = False,
        allow_snapshot: bool = True,
    ) -> ResCommonResponse:
        """현재가 조회. WebSocket snapshot이 신선하면 우선 사용하고 REST는 fallback으로 제한.

        우선순위:
          1) PriceStreamService snapshot (received_at이 snapshot_max_age_sec 이내)
          2) REST (MarketDataService → StockRepository 캐시 → broker API)

        force_fresh=True 또는 price_stream_service 미주입 시 항상 REST.
        REST 성공 시 snapshot 캐시를 backfill해 다음 호출에서 hit 가능하게 한다.

        per/pbr/eps 같이 snapshot에 없는 REST 전용 필드가 필요하면 allow_snapshot=False를 지정한다.
        """
        fallback_force_fresh = force_fresh
        unhealthy_stream_reason: Optional[str] = None

        if force_fresh:
            self._count_price_lookup("force_fresh_bypass", count_stats)

        if not force_fresh and self.price_stream_service is not None:
            snap = self.price_stream_service.get_cached_price(stock_code)
            if snap is None:
                # 구독 중이나 tick 미수신 → REST fallback
                self._count_price_lookup("no_tick_fallback", count_stats)
                self.logger.debug({"event": "price_lookup_no_tick", "code": stock_code, "caller": caller})
                fallback_force_fresh = True
                subscription_age = 0.0
                get_subscription_age = getattr(self.price_stream_service, "get_subscription_age", None)
                if callable(get_subscription_age):
                    try:
                        subscription_age = float(get_subscription_age(stock_code) or 0.0)
                    except (TypeError, ValueError):
                        subscription_age = 0.0
                if subscription_age >= self._snapshot_max_age_sec:
                    unhealthy_stream_reason = "no_tick"
            else:
                received_at = snap.get("received_at", 0.0) or 0.0
                age = time.time() - received_at
                if age <= self._snapshot_max_age_sec and allow_snapshot:
                    self._count_price_lookup("snapshot_hit", count_stats)
                    return self._build_snapshot_response(snap)
                else:
                    if age > self._snapshot_max_age_sec:
                        self._count_price_lookup("stale_fallback", count_stats)
                        self.logger.debug({"event": "price_lookup_stale", "code": stock_code,
                                           "age_sec": round(age, 2), "caller": caller})
                        fallback_force_fresh = True
                        unhealthy_stream_reason = "stale_snapshot"
                    else:
                        self._count_price_lookup("full_output_required", count_stats)
                        self.logger.debug({
                            "event": "price_lookup_snapshot_skipped",
                            "code": stock_code,
                            "caller": caller,
                            "reason": "full_output_required",
                        })
        elif not force_fresh:
            self._count_price_lookup("stream_unavailable_fallback", count_stats)

        self._count_price_lookup("rest_fallback", count_stats)
        resp = await self.market_data_service.get_current_price(
            stock_code, exchange=exchange, count_stats=count_stats, caller=caller, force_fresh=fallback_force_fresh
        )

        if unhealthy_stream_reason and self.price_subscription_service is not None:
            drop_subscription = getattr(
                self.price_subscription_service,
                "drop_unhealthy_price_subscription",
                None,
            )
            if callable(drop_subscription):
                try:
                    await drop_subscription(stock_code, reason=unhealthy_stream_reason)
                except Exception as e:
                    self.logger.warning(
                        {
                            "event": "price_subscription_drop_failed",
                            "code": stock_code,
                            "reason": unhealthy_stream_reason,
                            "error": str(e),
                        }
                    )

        # REST 성공 응답을 snapshot 캐시에 backfill (다음 동일 종목 조회 hit 유도)
        if (self.price_stream_service is not None
                and resp is not None
                and resp.rt_cd == ErrorCode.SUCCESS.value):
            try:
                output = (resp.data or {}).get("output")

                def _opt(v) -> Optional[str]:
                    s = str(v) if v is not None else ""
                    return s if s and s not in ("0", "N/A") else None

                if isinstance(output, ResStockFullInfoApiOutput):
                    self.price_stream_service.cache_price_snapshot(
                        stock_code,
                        price=str(output.stck_prpr or ""),
                        change=str(output.prdy_vrss or "0"),
                        rate=str(output.prdy_ctrt or "0.00"),
                        sign=str(output.prdy_vrss_sign or "3"),
                        volume=str(output.acml_vol or "0"),
                        acml_tr_pbmn=str(output.acml_tr_pbmn or "0"),
                        high=_opt(output.stck_hgpr),
                        low=_opt(output.stck_lwpr),
                        open_price=_opt(output.stck_oprc),
                    )
                elif isinstance(output, dict):
                    self.price_stream_service.cache_price_snapshot(
                        stock_code,
                        price=str(output.get("stck_prpr", "") or ""),
                        change=str(output.get("prdy_vrss", "0") or "0"),
                        rate=str(output.get("prdy_ctrt", "0.00") or "0.00"),
                        sign=str(output.get("prdy_vrss_sign", "3") or "3"),
                        volume=str(output.get("acml_vol", "0") or "0"),
                        acml_tr_pbmn=str(output.get("acml_tr_pbmn", "0") or "0"),
                        high=_opt(output.get("stck_hgpr")),
                        low=_opt(output.get("stck_lwpr")),
                        open_price=_opt(output.get("stck_oprc")),
                    )
            except Exception as e:
                self.logger.debug({"event": "snapshot_backfill_skipped", "code": stock_code, "error": str(e)})

        return resp

    async def get_multi_price(self, stock_codes: list[str]) -> ResCommonResponse:
        """복수종목 현재가 조회 (최대 30종목, MarketDataService 래퍼)."""
        return await self.market_data_service.get_multi_price(stock_codes)

    async def prefetch_prices(self, codes: List[str], *, count_stats: bool = True) -> int:
        """후보군 현재가를 batch(get_multi_price)로 미리 snapshot 캐시에 채운다 (P2 2-5).

        전략 scan 진입 직전에 호출하면, 신선한 WebSocket snapshot 이 없는 후보를
        종목당 개별 REST(get_current_price fallback) 대신 30종목 batch 로 한 번에 보강한다.
        이미 신선한 snapshot 이 있는 종목은 건너뛴다.

        get_multi_price 실패가 반복되면 batch prefetch 만 잠시 쉬고,
        개별 get_current_price 가 기존처럼 REST fallback 으로 동작한다.

        반환: snapshot 캐시에 backfill 된 종목 수.
        """
        if self.price_stream_service is None:
            return 0

        # 중복/공백 제거하며 입력 순서 보존
        seen: set = set()
        unique_codes: List[str] = []
        for c in codes or []:
            c = str(c).strip() if c is not None else ""
            if c and c not in seen:
                seen.add(c)
                unique_codes.append(c)
        if not unique_codes:
            return 0

        # 신선 snapshot 보유 종목은 batch 대상에서 제외
        now = time.time()
        stale_codes: List[str] = []
        for code in unique_codes:
            snap = self.price_stream_service.get_cached_price(code)
            if snap is not None:
                received_at = snap.get("received_at", 0.0) or 0.0
                if now - received_at <= self._snapshot_max_age_sec:
                    self._count_price_lookup("batch_prefetch_skip_fresh", count_stats)
                    continue
            stale_codes.append(code)
        if not stale_codes:
            return 0
        if self._is_multi_price_prefetch_circuit_open(now):
            self._count_price_lookup("batch_prefetch_circuit_open", count_stats)
            return 0

        def _opt(v) -> Optional[str]:
            s = str(v) if v is not None else ""
            return s if s and s not in ("0", "N/A") else None

        backfilled = 0
        for i in range(0, len(stale_codes), 30):
            if self._is_multi_price_prefetch_circuit_open():
                self._count_price_lookup("batch_prefetch_circuit_open", count_stats)
                break
            chunk = stale_codes[i:i + 30]
            self._count_price_lookup("batch_prefetch_call", count_stats)
            try:
                resp = await self.get_multi_price(chunk)
            except Exception as e:
                self.logger.debug({"event": "batch_prefetch_failed", "error": str(e), "count": len(chunk)})
                self._record_multi_price_prefetch_failure(count_stats=count_stats)
                continue
            if resp is None or resp.rt_cd != ErrorCode.SUCCESS.value or not resp.data:
                self._record_multi_price_prefetch_failure(count_stats=count_stats)
                continue
            self._record_multi_price_prefetch_success()
            items = resp.data if isinstance(resp.data, list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("stck_shrn_iscd") or item.get("mksc_shrn_iscd") or "").strip()
                price = item.get("stck_prpr")
                if not code or price in (None, "", "0"):
                    continue
                try:
                    self.price_stream_service.cache_price_snapshot(
                        code,
                        price=str(price),
                        change=str(item.get("prdy_vrss", "0") or "0"),
                        rate=str(item.get("prdy_ctrt", "0.00") or "0.00"),
                        sign=str(item.get("prdy_vrss_sign", "3") or "3"),
                        volume=str(item.get("acml_vol", "0") or "0"),
                        acml_tr_pbmn=_opt(item.get("acml_tr_pbmn")),
                        high=_opt(item.get("stck_hgpr")),
                        low=_opt(item.get("stck_lwpr")),
                        open_price=_opt(item.get("stck_oprc")),
                    )
                    self._count_price_lookup("batch_prefetch_backfill", count_stats)
                    backfilled += 1
                except Exception as e:
                    self.logger.debug(
                        {"event": "batch_prefetch_backfill_skipped", "code": code, "error": str(e)}
                    )
        return backfilled

    async def get_top_trading_value_stocks(self) -> ResCommonResponse:
        """거래대금 상위 종목 조회 (MarketDataService 래퍼)."""
        return await self.market_data_service.get_top_trading_value_stocks()

    async def get_top_rise_fall_stocks(self, rise: bool = True) -> ResCommonResponse:
        """상승/하락 상위 종목 조회 (MarketDataService 래퍼)."""
        return await self.market_data_service.get_top_rise_fall_stocks(rise)

    async def get_top_volume_stocks(self) -> ResCommonResponse:
        """거래량 상위 종목 조회 (MarketDataService 래퍼)."""
        return await self.market_data_service.get_top_volume_stocks()

    async def get_financial_ratio(self, stock_code: str) -> ResCommonResponse:
        """재무비율 조회 (MarketDataService 래퍼)."""
        return await self.market_data_service.get_financial_ratio(stock_code)

    async def get_stock_conclusion(self, stock_code: str) -> ResCommonResponse:
        """체결 정보 조회 (MarketDataService 래퍼)."""
        return await self.market_data_service.get_stock_conclusion(stock_code)

    def get_market_snapshot(
        self,
        code: str,
        max_age_sec: Optional[float] = None,
        force_fresh: bool = False,
    ) -> Tuple[Optional[MarketSnapshot], Optional[str]]:
        """PriceStreamService 캐시에서 MarketSnapshot 을 반환한다.

        반환값: (snapshot, reason)
          - snapshot: MarketSnapshot 또는 None
          - reason: None(신선) / REASON_SNAPSHOT_MISSING / REASON_SNAPSHOT_STALE
        stale 인 경우에도 snapshot 자체는 반환한다(호출자가 fallback 여부 판단).
        force_fresh=True 이면 항상 (None, REASON_SNAPSHOT_STALE) 반환.
        """
        if self.price_stream_service is None:
            return None, DataQualityService.REASON_SNAPSHOT_MISSING

        if force_fresh:
            return None, DataQualityService.REASON_SNAPSHOT_STALE

        snap = self.price_stream_service.get_market_snapshot(code)
        if snap is None:
            return None, DataQualityService.REASON_SNAPSHOT_MISSING

        effective_max_age = max_age_sec if max_age_sec is not None else self._snapshot_max_age_sec
        age = time.time() - snap.received_at
        if age > effective_max_age:
            return snap, DataQualityService.REASON_SNAPSHOT_STALE

        return snap, None

    async def get_conclusion_snapshot(
        self,
        code: str,
        max_age_sec: float = 10.0,
        force_fresh: bool = False,
    ) -> Tuple[Optional[ConclusionSnapshot], Optional[str]]:
        """체결강도 snapshot 을 캐시 우선으로 반환한다. cache miss/stale 시 REST fallback 후 backfill.

        반환값: (snapshot, reason)
          - reason: None(신선) / REASON_CONCLUSION_MISSING / REASON_CONCLUSION_STALE
        """
        if self.price_stream_service is None:
            self._price_lookup_stats["conclusion_missing_fallback"] += 1
            return None, DataQualityService.REASON_CONCLUSION_MISSING

        if not force_fresh:
            cached = self.price_stream_service.get_conclusion_snapshot(code)
            if cached is not None:
                age = time.time() - cached.received_at
                if age <= max_age_sec:
                    self._price_lookup_stats["conclusion_hit"] += 1
                    return cached, None
                self._price_lookup_stats["conclusion_stale_fallback"] += 1
            else:
                self._price_lookup_stats["conclusion_missing_fallback"] += 1

        resp = await self.market_data_service.get_stock_conclusion(code)
        if resp is None or resp.rt_cd != ErrorCode.SUCCESS.value:
            return None, DataQualityService.REASON_CONCLUSION_MISSING

        try:
            output = (resp.data or {}).get("output")
            if isinstance(output, list) and output:
                output = output[0]
            if isinstance(output, dict):
                strength_raw = output.get("tday_rltv") or output.get("cgld") or "0"
            else:
                strength_raw = getattr(output, "tday_rltv", None) or getattr(output, "cgld", None) or "0"
            strength = float(strength_raw) if strength_raw and strength_raw != "N/A" else 0.0
        except (ValueError, TypeError, AttributeError):
            strength = 0.0

        self.price_stream_service.cache_conclusion_snapshot(code, strength)
        snap = self.price_stream_service.get_conclusion_snapshot(code)
        return snap, None

    async def handle_get_current_stock_price(
        self,
        stock_code,
        caller: str = "unknown",
        exchange: Exchange = Exchange.KRX,
        force_fresh: bool = False,
        allow_snapshot: bool = False,
    ):
        """주식 현재가 및 상세 정보 조회 요청 및 결과 출력."""
        self.logger.info(f"Stock_Query_Service - {stock_code} 현재가 및 상세 정보 조회 요청")
        resp: ResCommonResponse = await self.get_current_price(
            stock_code,
            exchange=exchange,
            caller=caller,
            force_fresh=force_fresh,
            allow_snapshot=allow_snapshot and not force_fresh,
        )

        if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
            msg = resp.msg1 if resp else "응답 없음"
            self.logger.error(f"{stock_code} 현재가 및 상세 정보 조회 실패: {msg}")
            if self._streaming_logger:
                self._streaming_logger.log_missing_reason(stock_code, "rest_failed")
            if self._notification_service:
                await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.WARNING, "현재가 조회 실패",
                                    f"{stock_code} - {msg}",
                                    metadata={"code": stock_code})
            return ResCommonResponse(
                rt_cd=(resp.rt_cd if resp else ErrorCode.API_ERROR.value),
                msg1=msg,
                data={"code": stock_code},
            )

        # --- output 추출 및 통일화(ResStockFullInfoApiOutput) ---
        output = (resp.data or {}).get("output") if isinstance(resp.data, dict) else None

        if not isinstance(output, ResStockFullInfoApiOutput):
            self.logger.error(f"잘못된 응답 데이터 타입 또는 output 없음: {type(output)}")
            return ResCommonResponse(
                rt_cd=ErrorCode.PARSING_ERROR.value,
                msg1=f"잘못된 응답 데이터 타입 또는 output 없음: {type(output)}",
                data={"code": stock_code},
            )

        status_code_map = {
            "51": "관리종목", "52": "투자위험", "53": "투자경고", "54": "투자주의",
            "55": "신용가능", "57": "증거금 100%", "58": "거래정지", "59": "단기과열"
        }
        status_description = status_code_map.get(output.iscd_stat_cls_code, "정보 없음")

        # 부호 처리 로직 추가
        change_val = output.prdy_vrss
        sign_code = output.prdy_vrss_sign
        actual_sign = self._get_sign_from_code(sign_code)

        display_change = change_val
        try:
            f = float(change_val)
            if f != 0:
                display_change = f"{abs(int(f))}"
            else:
                display_change = "0"
        except (ValueError, TypeError):
            pass

        view = {
            # 기본 정보
            "code": stock_code,
            "name": await self.market_data_service.get_name_by_code(stock_code),
            "is_new_high": output.is_new_high, # 신고가 여부 추가
            "is_new_low": output.is_new_low,   # 신저가 여부 추가
            "price": output.stck_prpr,
            "change": output.prdy_vrss,
            "change_absolute": display_change,
            "rate": output.prdy_ctrt,
            "sign": actual_sign,
            "time": self.market_clock.get_current_kst_time().strftime("%H:%M:%S"),
            "bstp_kor_isnm": output.bstp_kor_isnm,
            "iscd_stat_cls_code_desc": f"{status_description} ({output.iscd_stat_cls_code})",

            # 거래 정보
            "acml_tr_pbmn": output.acml_tr_pbmn,
            "acml_vol": output.acml_vol,
            "prdy_vrss_vol_rate": output.prdy_vrss_vol_rate,
            "frgn_ntby_qty": output.frgn_ntby_qty,
            "pgtr_ntby_qty": output.pgtr_ntby_qty,

            # 당일 가격 정보
            "open": output.stck_oprc,
            "high": output.stck_hgpr,
            "low": output.stck_lwpr,
            "prev_close": output.stck_sdpr, # 기준가

            # 투자 지표
            "per": output.per,
            "pbr": output.pbr,
            "eps": output.eps,
            "bps": output.bps,
            "hts_avls": output.hts_avls,

            # 250일 정보
            "d250_hgpr": output.d250_hgpr,
            "d250_hgpr_date": output.d250_hgpr_date,
            "d250_hgpr_vrss_prpr_rate": output.d250_hgpr_vrss_prpr_rate,
            "d250_lwpr": output.d250_lwpr,
            "d250_lwpr_date": output.d250_lwpr_date,
            "d250_lwpr_vrss_prpr_rate": output.d250_lwpr_vrss_prpr_rate,

            # 연중 정보
            "dryy_hgpr": output.stck_dryy_hgpr,
            "dryy_hgpr_vrss_prpr_rate": output.dryy_hgpr_vrss_prpr_rate,
            "dryy_hgpr_date": output.dryy_hgpr_date,
            "dryy_lwpr": output.stck_dryy_lwpr,
            "dryy_lwpr_vrss_prpr_rate": output.dryy_lwpr_vrss_prpr_rate,
            "dryy_lwpr_date": output.dryy_lwpr_date,

            # 52주 정보
            "w52_hgpr": output.w52_hgpr,
            "w52_hgpr_vrss_prpr_ctrt": output.w52_hgpr_vrss_prpr_ctrt,
            "w52_hgpr_date": output.w52_hgpr_date,
            "w52_lwpr": output.w52_lwpr,
            "w52_lwpr_vrss_prpr_ctrt": output.w52_lwpr_vrss_prpr_ctrt,
            "w52_lwpr_date": output.w52_lwpr_date,

            # 기타 상태
            "crdt_able_yn": "가능" if output.crdt_able_yn == "Y" else "불가능",
            "short_over_yn": "예" if output.short_over_yn == "Y" else "아니오",
            "sltr_yn": "예" if output.sltr_yn == "Y" else "아니오",
            "mang_issu_cls_code": "예" if output.mang_issu_cls_code and output.mang_issu_cls_code.strip() else "아니오",
        }
        self.logger.info(f"{stock_code} 현재가 및 상세 정보 조회 성공")
        if self._notification_service:
            name = view.get("name", stock_code)
            sign_str = actual_sign if actual_sign == "+" else ""
            await self._notification_service.emit(NotificationCategory.API, NotificationLevel.INFO, "현재가 조회",
                                f"{name}({stock_code}) {view['price']}원 ({sign_str}{view['rate']}%)",
                                metadata={"code": stock_code, "price": view["price"]})
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=view)

    async def handle_get_account_balance(self, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """계좌 잔고 조회 요청 및 결과 출력."""
        resp = await self.broker.get_account_balance(exchange=exchange)
        if self._notification_service:
            if resp and resp.rt_cd == ErrorCode.SUCCESS.value:
                await self._notification_service.emit(NotificationCategory.API, NotificationLevel.INFO, "잔고 조회 완료", "계좌 잔고 조회 성공")
            else:
                msg = resp.msg1 if resp else "응답 없음"
                await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.WARNING, "잔고 조회 실패", msg)
        return resp

    async def handle_get_top_market_cap_stocks_code(self, market_code: str = "0000", limit: int = 30) -> ResCommonResponse:
        """
        시가총액 상위 종목 중 상한가 도달 종목 조회 (출력 X).
        data: List[dict(code,name,price,change_rate)]
        """
        self.logger.debug(f"상한가 스캔 요청 (시장={market_code}, limit={limit})")


        try:
            top_res: ResCommonResponse = await self.market_data_service.get_top_market_cap_stocks_code(market_code, limit)
            if not top_res or top_res.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.error(f"상위 종목 목록 조회 실패: {top_res}")
                return ResCommonResponse(
                    rt_cd=ErrorCode.API_ERROR.value,
                    msg1="상위 종목 목록 조회 실패",
                    data=None
                )

            top_list: List[ResTopMarketCapApiItem] = top_res.data or []
            if not top_list:
                self.logger.debug("상위 종목 없음")
                return ResCommonResponse(
                    rt_cd=ErrorCode.SUCCESS.value,
                    msg1="조회 성공 (종목 없음)",
                    data=[]
                )

            targets = top_list[:limit]
            found: list[dict] = []

            for item in targets:
                # dataclass(ResTopMarketCapApiItem)와 dict 모두 지원
                get = (lambda k: getattr(item, k, None)) if not isinstance(item, dict) else item.get

                code = get("mksc_shrn_iscd") or get("iscd")
                name = get("hts_kor_isnm")
                prdy_vrss_sign = get("prdy_vrss_sign")
                stck_prpr = get("stck_prpr")
                prdy_ctrt = get("prdy_ctrt")

                if not code:
                    self.logger.warning(f"유효하지 않은 종목코드: {item}")
                    continue

                # 정책: prdy_vrss_sign == '1'이면 상한으로 간주
                if prdy_vrss_sign == "1":
                    found.append({
                        "code": code,
                        "name": name,
                        "price": str(stck_prpr) if stck_prpr is not None else None,
                        "change_rate": str(prdy_ctrt) if prdy_ctrt is not None else None,
                    })
                    self.logger.debug(f"상한가 발견: {name}({code}) {stck_prpr}원 {prdy_ctrt}%")
                else:
                    # 필요시 디버그 로그만
                    self.logger.debug(f"상한가 아님: {name}({code}) sign={prdy_vrss_sign}")

            self.logger.info("시가총액 상위 종목 조회 성공")
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="조회 성공",
                data=found  # 빈 리스트 허용
            )

        except Exception as e:
                self.logger.exception("상한가 조회 중 예외")
                return ResCommonResponse(
                    rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                    msg1=f"예외 발생: {e}",
                    data=None
                )

    async def get_stock_change_rate(self, stock_code: str) -> ResCommonResponse:
        """
        전일대비 등락률 조회. 출력 없음. 계산/포맷만 수행하여 ResCommonResponse로 반환.
        data 예시:
          {
            "stock_code": "005930",
            "current_price": "70400",
            "change_value_display": "+500",   # 부호/0 처리 적용된 표시값
            "change_rate": "0.71"             # API 그대로 문자열 유지
          }
        """
        res: ResCommonResponse = await self.market_data_service.get_current_price(stock_code, caller="StockQueryService")
        if not (res and res.rt_cd == ErrorCode.SUCCESS.value):
            self.logger.error(f"{stock_code} 전일대비 등락률 조회 실패: {res}")
            # 실패도 통일된 형태로 반환
            return ResCommonResponse(rt_cd="1", msg1="조회 실패", data={"stock_code": stock_code})

        output = res.data.get("output") or {}
        current_price = output.stck_prpr
        change_val_str = output.prdy_vrss
        change_sign_code = output.prdy_vrss_sign
        change_rate_str = output.prdy_ctrt

        actual_sign = self._get_sign_from_code(change_sign_code)

        display_change_val = change_val_str
        try:
            f = float(change_val_str)
            if f != 0:
                display_change_val = f"{actual_sign}{abs(int(f))}"
            elif f == 0:
                display_change_val = "0"
        except (ValueError, TypeError):
            # 숫자 아님 → 그대로 노출
            pass

        data = {
            "stock_code": stock_code,
            "current_price": current_price,
            "change_value_display": display_change_val,
            "change_rate": change_rate_str,
        }
        self.logger.info(
            f"{stock_code} 전일대비 등락률 조회 성공: 현재가={current_price}, "
            f"전일대비={display_change_val}, 등락률={change_rate_str}%"
        )
        return ResCommonResponse(rt_cd="0", msg1="정상", data=data)

    async def get_open_vs_current(self, stock_code: str) -> ResCommonResponse:
        """
        시가 대비 등락률/금액 계산 후 반환. 출력 없음.
        data 예시:
          {
            "stock_code": "005930",
            "current_price": "70400",
            "open_price": "70000",
            "vs_open_value_display": "+400",   # 금액 부호/0 처리
            "vs_open_rate_display": "+0.57%"   # 퍼센트 부호/0 처리
          }
        """
        res: ResCommonResponse = await self.market_data_service.get_current_price(stock_code, caller="StockQueryService")
        if not (res and res.rt_cd == ErrorCode.SUCCESS.value):
            self.logger.error(f"{stock_code} 시가대비 조회 실패: {res}")
            return ResCommonResponse(rt_cd="1", msg1="조회 실패", data={"stock_code": stock_code})

        output = res.data.get("output") or {}
        cur_str = output.stck_prpr
        open_str = output.stck_oprc

        try:
            cur = float(cur_str) if cur_str not in (None, "N/A") else None
            opn = float(open_str) if open_str not in (None, "N/A") else None
        except (ValueError, TypeError):
            self.logger.warning(
                f"{stock_code} 시가대비 조회 실패: 가격 파싱 오류 (현재가={cur_str}, 시가={open_str})"
            )
            return ResCommonResponse(rt_cd="1", msg1="가격 파싱 오류", data={"stock_code": stock_code})

        vs_val_disp = "N/A"
        vs_rate_disp = "N/A"

        if cur is not None and opn is not None:
            diff = cur - opn
            vs_val_disp = "0" if diff == 0 else f"{diff:+.0f}"
            if opn != 0:
                vs_rate_disp = f"{(diff / opn) * 100:+.2f}%"
            else:
                vs_rate_disp = "N/A"

        data = {
            "stock_code": stock_code,
            "current_price": cur_str,
            "open_price": open_str,
            "vs_open_value_display": vs_val_disp,
            "vs_open_rate_display": vs_rate_disp,
        }
        self.logger.info(
            f"{stock_code} 시가대비 조회 성공: 현재가={cur_str}, 시가={open_str}, "
            f"시가대비={vs_val_disp} ({vs_rate_disp})"
        )
        return ResCommonResponse(rt_cd="0", msg1="정상", data=data)

    async def handle_upper_limit_stocks(self, market_code: str = "0000", limit: int = 500):
        """
        시가총액 상위 종목 조회 (출력 X). TradingService 결과를 표준 스키마로 반환.
        data: List[ResTopMarketCapApiItem]
        """

        try:
            res: ResCommonResponse = await self.market_data_service.get_top_market_cap_stocks_code(market_code, limit)
            if not res or res.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.error(f"시가총액 상위 종목 조회 실패: {res}")
                return ResCommonResponse(
                    rt_cd=ErrorCode.API_ERROR.value,
                    msg1="시가총액 상위 종목 조회 실패",
                    data=None
                )
            # 성공
            self.logger.info(f"시가총액 상위 종목 조회 성공 (시장: {market_code}, 개수={len(res.data) if res.data else 0})")
            return ResCommonResponse(
                rt_cd=ErrorCode.SUCCESS.value,
                msg1="조회 성공",
                data=res.data,   # 그대로 전달 (List[ResTopMarketCapApiItem])
            )
        except Exception as e:
            self.logger.exception("시가총액 상위 종목 조회 중 예외")
            return ResCommonResponse(
                rt_cd=ErrorCode.UNKNOWN_ERROR.value,
                msg1=f"예외 발생: {e}",
                data=None
            )

    async def handle_current_upper_limit_stocks(self):
        """
        전체 종목 중 현재 상한가에 도달한 종목을 조회하여 출력합니다.
        """
        self.logger.info("Service - 현재 상한가 종목 조회 요청 ")

        try:
            rise_res: ResCommonResponse = await self.market_data_service.get_top_rise_fall_stocks(rise=True)
            if rise_res.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.warning("상승률 조회 실패.")
                return rise_res

            upper_limit_stocks: ResCommonResponse = await self.market_data_service.get_current_upper_limit_stocks(
                rise_res.data)

            if upper_limit_stocks.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.info("현재 상한가 종목 없음.")

            return upper_limit_stocks

        except Exception as e:
            self.logger.error(f"현재 상한가 종목 조회 중 오류 발생: {e}", exc_info=True)
            raise

    async def handle_get_asking_price(self, stock_code: str, depth: int = 10):
        """종목의 실시간 호가 정보 조회 및 출력."""
        self.logger.info(f"Service - {stock_code} 호가 정보 조회 요청")
        response = await self.market_data_service.get_asking_price(stock_code)

        if not response or response.rt_cd != ErrorCode.SUCCESS.value:
            msg = response.msg1 if response else "응답 없음"
            self.logger.error(f"{stock_code} 호가 정보 조회 실패: {msg}")
            return ResCommonResponse(
                rt_cd=(response.rt_cd if response else ErrorCode.API_ERROR.value),
                msg1=msg,
                data={"code": stock_code},
            )

        raw1 = (response.data or {}).get("output1") or {}
        # 일부 구현에서 list로 줄 수도 있으니 방어
        if isinstance(raw1, list):
            raw1 = raw1[0] if raw1 else {}

        rows = []
        for i in range(1, depth + 1):
            rows.append({
                "level": i,
                "ask_price": raw1.get(f"askp{i}", "N/A"),
                "ask_rem":   raw1.get(f"askp_rsqn{i}", "N/A"),
                "bid_price": raw1.get(f"bidp{i}", "N/A"),
                "bid_rem":   raw1.get(f"bidp_rsqn{i}", "N/A"),
            })

        view_model = {
            "code": stock_code,
            "rows": rows,
            # 필요시 추가 필드들(예: 현재가/참고값 등)
            "meta": {
                "prpr": raw1.get("stck_prpr"),
                "time": raw1.get("aplm_hour") or raw1.get("stck_cntg_hour"),
            }
        }

        self.logger.info(f"{stock_code} 호가 정보 조회 성공")
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=view_model)

    async def handle_get_time_concluded_prices(self, stock_code: str):
        """종목의 시간대별 체결가 정보 조회 및 출력."""
        self.logger.info(f"Service - {stock_code} 시간대별 체결가 조회 요청")
        response = await self.market_data_service.get_time_concluded_prices(stock_code)

        if not response or response.rt_cd != ErrorCode.SUCCESS.value:
            msg = response.msg1 if response else "응답 없음"
            self.logger.error(f"{stock_code} 시간대별 체결가 조회 실패: {msg}")
            return ResCommonResponse(
                rt_cd=(response.rt_cd if response else ErrorCode.API_ERROR.value),
                msg1=msg,
                data={"code": stock_code},
            )

        raw = (response.data or {}).get("output") or []
        if isinstance(raw, dict):
            raw = [raw]

        rows = []
        for item in raw:
            rows.append({
                "time":   item.get("stck_cntg_hour", "N/A"),
                "price":  item.get("stck_prpr", "N/A"),
                "change": item.get("prdy_vrss", "N/A"),
                "volume": item.get("cntg_vol", "N/A"),
            })

        view_model = {"code": stock_code, "rows": rows}
        self.logger.info(f"{stock_code} 시간대별 체결가 조회 성공")
        return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="정상", data=view_model)

    async def handle_get_top_stocks(self, category: str) -> ResCommonResponse:
        """상위 종목 조회 및 출력 (상승률, 하락률, 거래량, 외국인순매수 등)."""
        t_start = self.pm.start_timer()
        # (title, func, param, is_sync) — is_sync=True이면 동기 함수 호출
        # 장마감 후 캐시된 기본 랭킹이 있으면 우선 사용 (trading_value 제외 — ranking_task에서 처리)
        basic_categories = ("rise", "fall", "volume")
        if category in basic_categories and self.ranking_task:
            cached = self.ranking_task.get_basic_ranking_cache(category)
            if cached is not None:
                self.logger.info(f"Handler - {category} 캐시 히트 (장마감 후 캐시)")
                return cached

        category_map = {
            "rise": ("상승률", self.market_data_service.get_top_rise_fall_stocks, True, False),
            "fall": ("하락률", self.market_data_service.get_top_rise_fall_stocks, False, False),
            "volume": ("거래량", self.market_data_service.get_top_volume_stocks, None, False),
            "trading_value": ("거래대금", self.market_data_service.get_top_trading_value_stocks, None, False),
        }

        # 랭킹 태스크 카테고리 (동기 함수)
        # 거래대금: 장마감 후에는 투자자 데이터(acml_tr_pbmn) 기반으로 전환
        if self.ranking_task:
            category_map["trading_value"] = (
                "거래대금", self.ranking_task.get_trading_value_ranking, None, False
            )
            category_map["foreign_buy"] = (
                "외인 순매수", self.ranking_task.get_foreign_net_buy_ranking, None, False
            )
            category_map["foreign_sell"] = (
                "외인 순매도", self.ranking_task.get_foreign_net_sell_ranking, None, False
            )
            category_map["inst_buy"] = (
                "기관 순매수", self.ranking_task.get_inst_net_buy_ranking, None, False
            )
            category_map["inst_sell"] = (
                "기관 순매도", self.ranking_task.get_inst_net_sell_ranking, None, False
            )
            category_map["prsn_buy"] = (
                "개인 순매수", self.ranking_task.get_prsn_net_buy_ranking, None, False
            )
            category_map["prsn_sell"] = (
                "개인 순매도", self.ranking_task.get_prsn_net_sell_ranking, None, False
            )
            category_map["program_buy"] = (
                "프로그램 순매수", self.ranking_task.get_program_net_buy_ranking, None, False
            )
            category_map["program_sell"] = (
                "프로그램 순매도", self.ranking_task.get_program_net_sell_ranking, None, False
            )

        if category not in category_map:
            self.logger.error(f"지원하지 않는 카테고리: {category}")
            return ResCommonResponse(
                rt_cd=ErrorCode.INVALID_INPUT.value,
                msg1=f"지원하지 않는 카테고리: {category}",
                data=None,
            )

        title, service_func, param, is_sync = category_map[category]
        self.logger.info(f"Handler - {title} 상위 종목 조회 요청")

        if is_sync:
            response = service_func(param) if param is not None else service_func()
        else:
            response = await (service_func(param) if param is not None else service_func())

        if response and response.rt_cd == ErrorCode.SUCCESS.value:
            self.logger.info(f"{title} 상위 종목 조회 성공")
            if self._notification_service:
                cnt = len(response.data) if response.data else 0
                await self._notification_service.emit(NotificationCategory.API, NotificationLevel.INFO, f"{title} 랭킹 조회",
                                    f"{title} 상위 {cnt}개 종목 조회 완료",
                                    metadata={"category": category})
        else:
            msg = response.msg1 if response else "응답 없음"
            self.logger.error(f"{title} 상위 종목 조회 실패: {msg}")
            if self._notification_service:
                await self._notification_service.emit(NotificationCategory.SYSTEM, NotificationLevel.WARNING, f"{title} 랭킹 조회 실패", msg,
                                    metadata={"category": category})

        self.pm.log_timer(f"StockQueryService.handle_get_top_stocks({category})", t_start, threshold=0.5)
        return response

    async def handle_get_etf_info(self, etf_code: str):
        """
        ETF 정보를 TradingService에서 받아와 출력용 뷰모델로 가공하여 반환만 한다.
        출력은 cli_view에 위임한다.
        """
        self.logger.info(f"Service - {etf_code} ETF 정보 조회 요청")

        response = await self.market_data_service.get_etf_info(etf_code)

        # 실패면 그대로 전달 (cli_view에서 실패 출력)
        if not response or response.rt_cd != ErrorCode.SUCCESS.value:
            msg = response.msg1 if response else "응답 없음"
            self.logger.error(f"{etf_code} ETF 정보 조회 실패: {msg}")
            # data에는 최소한 식별 정보만 넣어두면 뷰에서 에러 메시지에 활용 가능
            return ResCommonResponse(
                rt_cd=response.rt_cd if response else ErrorCode.API_ERROR.value,
                msg1=msg,
                data={"code": etf_code}
            )

        # 성공: 출력용 뷰모델로 가공
        raw = response.data.get("output", {}) if response.data else {}
        view_model = {
            "code": etf_code,
            "name": raw.get("etf_rprs_bstp_kor_isnm", "N/A"),
            "price": raw.get("stck_prpr", "N/A"),
            "nav": raw.get("nav", "N/A"),
            "market_cap": raw.get("stck_llam", "N/A"),
        }

        self.logger.info(f"{etf_code} ETF 정보 조회 성공")
        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="정상",
            data=view_model
        )


    async def get_ohlcv(self, stock_code: str, period: str = "D", caller: str = "unknown", exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """
        OHLCV 데이터를 반환합니다.
        """
        self.logger.info(f"ServiceHandler - {stock_code} OHLCV 데이터 요청 period={period}")
        return await self.market_data_service.get_ohlcv(stock_code, period=period, caller=caller, exchange=exchange)

    async def get_ohlcv_range(self, stock_code: str, period: str = "D", start_date: str = None, end_date: str = None, exchange: Exchange = Exchange.KRX) -> ResCommonResponse:
        """
        특정 기간의 OHLCV 데이터를 조회합니다.
        """
        return await self.market_data_service.get_ohlcv_range(stock_code, period, start_date, end_date, exchange=exchange)

    async def get_ohlcv_with_indicators(self, stock_code: str, period: str = "D", caller: str = "unknown") -> ResCommonResponse:
        """
        OHLCV 데이터를 1회 조회한 후, 해당 데이터로 MA5/10/20/60/120 + 볼린저밴드 + RS를 한번에 계산하여 반환.
        차트 렌더링 시 7개 API 호출을 1개로 통합하기 위한 메서드.
        """
        t_start = self.pm.start_timer()
        self.logger.info(f"ServiceHandler - {stock_code} OHLCV+지표 통합 조회 period={period}")
        try:
            # 1. OHLCV 1회 조회
            t0 = self.pm.start_timer()
            resp = await self.market_data_service.get_ohlcv(stock_code, period=period, caller=caller)
            self.pm.log_timer(f"{stock_code} OHLCV 조회", t0)

            if not resp or resp.rt_cd != ErrorCode.SUCCESS.value:
                return resp or ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="OHLCV 조회 실패", data=None)

            if not resp.data:
                return ResCommonResponse(rt_cd=ErrorCode.API_ERROR.value, msg1="OHLCV 조회 실패", data=None)

            ohlcv_data = resp.data

            # 2. 지표 계산 (OHLCV 데이터를 직접 전달하여 API 재호출 방지)
            indicator_service = self.indicator_service
            t2 = self.pm.start_timer()
            
            # [최적화] 통합 지표 계산 메서드 호출 (DataFrame 변환 1회)
            indicators_resp = await indicator_service.get_chart_indicators(stock_code, ohlcv_data)
            
            self.pm.log_timer(f"{stock_code} 지표 통합 계산", t2)

            if indicators_resp.rt_cd != ErrorCode.SUCCESS.value:
                self.logger.error(f"지표 계산 실패: {indicators_resp.msg1}")
                indicators_data = {"ma5": [], "ma10": [], "ma20": [], "ma60": [], "ma120": [], "bb": [], "rs": []}
            else:
                indicators_data = indicators_resp.data

            result = {
                "ohlcv": ohlcv_data,
                "indicators": indicators_data
            }
            self.pm.log_timer(f"{stock_code} get_ohlcv_with_indicators 전체", t_start, threshold=0.5)

            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1=f"OHLCV+지표 {len(ohlcv_data)}건", data=result)

        except Exception as e:
            self.logger.error(f"{stock_code} OHLCV+지표 통합 조회 중 오류: {e}", exc_info=True)
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1=str(e), data=None)

    async def get_recent_daily_ohlcv(self, stock_code: str, limit: int = DynamicConfig.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE, end_date: Optional[str] = None) -> ResCommonResponse:
        """
        타겟 종목의 최근 일봉을 limit개 반환.
        TradingService.get_recent_daily_ohlcv를 래핑하여 ResCommonResponse 형태로 통일.
        """
        try:
            rows = await self.market_data_service.get_recent_daily_ohlcv(stock_code, limit=limit, end_date=end_date)
            if not rows:
                return ResCommonResponse(rt_cd=ErrorCode.EMPTY_VALUES.value, msg1="데이터 없음", data=[])
            return ResCommonResponse(rt_cd=ErrorCode.SUCCESS.value, msg1="성공", data=rows)
        except Exception as e:
            self.logger.error(f"[OHLCV] {stock_code} 조회 실패: {e}", exc_info=True)
            return ResCommonResponse(rt_cd=ErrorCode.EMPTY_VALUES.value, msg1=str(e), data=[])

    async def get_investor_trade_daily_multi(self, stock_code: str, date: str = None, days: int = 3) -> ResCommonResponse:
        """종목별 투자자 매매동향 다중일 조회 (실전 전용).

        Returns:
            data: list[dict] — 최대 days개, 각 항목 {frgn_ntby_tr_pbmn, orgn_ntby_tr_pbmn, acml_tr_pbmn, stck_bsop_date, ...}
                  단위: frgn/orgn_ntby_tr_pbmn 은 백만원, acml_tr_pbmn 은 원.
        """
        if not self.broker:
            return ResCommonResponse(rt_cd=ErrorCode.UNKNOWN_ERROR.value, msg1="broker 미설정", data=[])
        return await self.broker.get_investor_trade_by_stock_daily_multi(stock_code, date, days)

    async def get_intraday_minutes_today(self, stock_code: str, *, input_hour_1: str) -> ResCommonResponse:
        """
        당일 분봉 조회. MarketDataService 위임.
        """
        return await self.market_data_service.get_intraday_minutes_today(
            stock_code=stock_code, input_hour_1=input_hour_1
        )

    async def get_intraday_minutes_by_date(
        self, stock_code: str, *, input_date_1: str, input_hour_1: str = ""
    ) -> ResCommonResponse:
        """
        일별(특정 일자) 분봉 조회. MarketDataService 위임.
        """
        return await self.market_data_service.get_intraday_minutes_by_date(
            stock_code=stock_code, input_date_1=input_date_1, input_hour_1=input_hour_1
        )

    async def get_day_intraday_minutes_list(
        self,
        stock_code: str,
        *,
        date_ymd: Optional[str] = None,                                    # None이면 '오늘'(KST) 조회
        session: Literal["REGULAR", "EXTENDED"] = "REGULAR",                # REGULAR=09:00~15:40, EXTENDED=08:00~20:00
        start_hhmmss: Optional[str] = None,
        end_hhmmss: Optional[str] = None,
        max_batches: int = 200
    ) -> List[Dict]:
        """
        하루치 분봉(분봉 행 dict)의 '정규화된 리스트'를 반환한다. (출력은 호출부/cli_view에서)
        - date_ymd=None: 오늘(KST) → get_intraday_minutes_today(배치당 30개; 모의/실전 모두 가능)
        - date_ymd=YYYYMMDD: 지정일 → get_intraday_minutes_by_date(배치당 100개; 실전 전용)
        - 시간 범위: session 프리셋으로 선택하거나 start/end를 직접 지정 가능
        - 반환: 시간 오름차순(HHMMSS) 정렬된 리스트. 각 행은 최소 다음 키를 포함:
          'stck_bsop_date'(YYYYMMDD), 'stck_cntg_hour'(HHMMSS), 나머지는 원본 필드 유지
        """
        t_start = self.pm.start_timer()
        # 세션 범위 결정
        if not start_hhmmss or not end_hhmmss:
            if session.upper() == "EXTENDED":
                start_hhmmss = start_hhmmss or "080000"
                end_hhmmss   = end_hhmmss   or "200000"
            else:
                start_hhmmss = start_hhmmss or "090000"
                end_hhmmss   = end_hhmmss   or "153000"

        start_hhmmss = self.market_clock.to_hhmmss(start_hhmmss)
        end_hhmmss   = self.market_clock.to_hhmmss(end_hhmmss)

        # 조회 날짜
        if date_ymd:
            ymd = date_ymd
        else:
            now_kst = self.market_clock.get_current_kst_time()
            ymd = now_kst.strftime("%Y%m%d")

        # 배치 호출 함수 선택
        async def _fetch_batch(cursor_hhmmss: str):
            cursor_hhmmss = self.market_clock.to_hhmmss(cursor_hhmmss)
            if not date_ymd:
                # 오늘(모의/실전; 배치당 30개)
                return await self.get_intraday_minutes_today(
                    stock_code, input_hour_1=cursor_hhmmss
                )
            else:
                # 지정일(실전 전용; 배치당 100개)
                return await self.get_intraday_minutes_by_date(
                    stock_code, input_date_1=ymd, input_hour_1=cursor_hhmmss
                )

        def _extract_rows(resp_obj) -> list[dict]:
            """resp.data가 list 또는 dict(output2/rows/data 키)인 모든 경우를 수용."""
            data = getattr(resp_obj, "data", None)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                rows = data.get("output2") or data.get("rows") or data.get("data") or []
                return rows if isinstance(rows, list) else []
            return []

        # 커서: end부터 과거로 내려가며 수집
        cursor = end_hhmmss
        seen: set[tuple[str, str]] = set()   # (date, hhmmss)
        collected: List[Dict] = []
        batches = 0

        while batches < max_batches:
            batches += 1
            resp = await _fetch_batch(cursor)
            if not resp or str(getattr(resp, "rt_cd", "1")) != "0":
                break

            rows = _extract_rows(resp)
            if not rows:
                break

            min_time_in_batch = None
            added = 0

            for row in rows:
                d = str(row.get("stck_bsop_date") or ymd)
                t = self.market_clock.to_hhmmss(row.get("stck_cntg_hour") or "")

                if (min_time_in_batch is None) or (t < min_time_in_batch):
                    min_time_in_batch = t

                # 범위 필터
                if t < start_hhmmss or t > end_hhmmss:
                    continue
                key = (d, t)
                if key in seen:
                    continue
                seen.add(key)

                norm = dict(row)
                norm["stck_bsop_date"] = d
                norm["stck_cntg_hour"] = t
                collected.append(norm)
                added += 1

            if added == 0:
                if min_time_in_batch:
                    cursor = self.market_clock.dec_minute(min_time_in_batch, 1)
                    if cursor < start_hhmmss:
                        break
                    continue
                break

            if min_time_in_batch:
                cursor = self.market_clock.dec_minute(min_time_in_batch, 1)
                if cursor < start_hhmmss:
                    break
            else:
                break

        # 최종 정렬(과거→현재)
        collected.sort(key=lambda r: r.get("stck_cntg_hour", ""))

        self.pm.log_timer(f"StockQueryService.get_day_intraday_minutes_list({stock_code}, {batches}배치)", t_start, threshold=1.0)
        return collected
