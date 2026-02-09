# self.app/user_action_executor.py
from __future__ import annotations
from typing import TYPE_CHECKING

from config.DynamicConfig import DynamicConfig

if TYPE_CHECKING:
    from app.trading_app import TradingApp

from strategies.volume_breakout_strategy import VolumeBreakoutStrategy
from strategies.GapUpPullback_strategy import GapUpPullbackStrategy
from strategies.momentum_strategy import MomentumStrategy
from strategies.strategy_executor import StrategyExecutor
from common.types import ErrorCode, ResCommonResponse, ResTopMarketCapApiItem
from collections import OrderedDict

# === choice별 핸들러 함수 정의 ===
class UserActionExecutor:
    # 번호 -> (카테고리, 라벨, 핸들러명)
    COMMANDS: dict[str, tuple[str, str, str]] = {
        '0':  ('기본 기능',      '거래 환경 변경',                         'handle_change_environment'),
        '1':  ('기본 기능',      '현재가 조회',                            'handle_get_current_price'),
        '2':  ('기본 기능',      '계좌 잔고 조회',                         'handle_account_balance'),
        '3':  ('기본 기능',      '주식 매수',                             'handle_buy_stock'),
        '4':  ('기본 기능',      '주식 매도',                             'handle_sell_stock'),


        '20': ('시세 조회',      '전일대비 등락률 조회',                    'handle_stock_change_rate'),
        '21': ('시세 조회',      '시가대비 등락률 조회',                    'handle_open_vs_current_rate'),
        '22': ('시세 조회',      '실시간 호가 조회',                       'handle_asking_price'),
        '23': ('시세 조회',      '시간대별 체결가 조회',                    'handle_time_conclude'),
        '24': ('시세 조회',      'ETF 정보 조회',                         'handle_etf_info'),
        '25': ('시세 조회',      'OHLCV(차트) 조회',                      'handle_ohlcv'),
        '26': ('시세 조회',      '최근 일봉 조회',                         'handle_fetch_recnt_daily_ohlcv'),
        '27': ('시세 조회',      '당일 분봉 조회',                         'handle_intraday_minutes_today'),
        '28': ('시세 조회',      '일별 분봉 조회 (실전 전용)',               'handle_intraday_minutes_by_date'),
        '29': ('시세 조회',      '하루 분봉 조회',                         'handle_day_intraday_minutes'),



        '50': ('랭킹/필터링', '시가총액 상위 조회 (실전 전용)',             'handle_top_market_cap_stocks'),
        '51': ('랭킹/필터링', '시가총액 상위 10개 현재가 (실전 전용)',      'handle_top_10_market_cap_stocks'),

        '54': ('랭킹/필터링', '현재 상한가 종목 (실전 전용)',              'handle_current_upper_limit'),
        '55': ('랭킹/필터링', '거래량 상위 랭킹 (~30) (실전 전용)',        'handle_top_volume_30'),
        '56': ('랭킹/필터링', '상승률 상위 랭킹 (~30) (실전 전용)',        'handle_top_rise_30'),
        '57': ('랭킹/필터링', '하락률 상위 랭킹 (~30) (실전 전용)',        'handle_top_fall_30'),


        '70': ('실시간 구독', '실시간 체결가/호가 구독', 'handle_realtime_stream'),


        # ⬇️ 실행기의 번호(100/101/102)를 그대로 사용해 메뉴와 동기화
        '100': ('전략 실행', '거래량 돌파 백테스트(단일종목)', 'handle_backtest_intraday_open_threshold'),
        '101': ('전략 실행', '거래량 돌파 백테스트(거래량,상승률 상위30)', 'handle_backtest_top30_volume_rise'),
        '102': ('전략 실행', '거래량 돌파 백테스트(거래량+상승률 상위30)', 'handle_backtest_ranked_universe_open_threshold'),
        # '101': ('전략 실행', '모멘텀 백테스트', 'handle_momentum_backtest'),
        # '102': ('전략 실행', 'GapUpPullback 전략 실행', 'handle_gapup_pullback'),

        '998': ('기타',          '토큰 무효화',                               'handle_invalidate_token'),
        '999': ('기타',          '종료',                                     'handle_exit'),
    }

    def __init__(self, app: 'TradingApp'):
        self.app = app

    # === dispatcher 함수 ===

    async def execute(self,choice: str) -> bool:
        """사용자 입력(choice)에 따라 대응하는 액션 함수를 실행"""
        handler = self.get_handler(choice)
        result = await handler()
        return result if isinstance(result, bool) else True


    def get_handler(self, choice: str):
        cmd = self.COMMANDS.get(choice)
        if not cmd:
            # 등록되지 않은 번호면 기본 무효 처리 핸들러 반환
            return self.handle_invalid_choice

        method_name = cmd[2]
        # 혹시 method_name이 비었거나 오타가 있어도 안전하게 처리
        if not isinstance(method_name, str) or not method_name:
            return self.handle_invalid_choice

        return getattr(self, method_name, self.handle_invalid_choice)

    def build_menu_items(self) -> dict[str, OrderedDict[str, str]]:
        """CLIView에 넘길 메뉴 딕셔너리 생성 (카테고리별 그룹화, 번호 오름차순)."""
        grouped: dict[str, OrderedDict[str, str]] = {}
        # 번호 정렬(숫자형으로) 보장
        for num in sorted(self.COMMANDS.keys(), key=lambda x: int(x)):
            category, label, _ = self.COMMANDS[num]
            grouped.setdefault(category, OrderedDict())[num] = label
        return grouped

    async def handle_change_environment(self) -> bool:
        self.app.logger.info("거래 환경 변경을 시작합니다.")
        return await self.app.select_environment()

    async def handle_exit(self) -> bool:
        self.app.cli_view.display_exit_message()
        return False

    async def handle_invalid_choice(self) -> None:
        self.app.cli_view.display_invalid_menu_choice()

    async def handle_get_current_price(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")

        self.app.logger.info(f"Handler - {stock_code} 현재가 조회 요청")
        result = await self.app.stock_query_service.handle_get_current_stock_price(stock_code)
        if result and result.rt_cd == ErrorCode.SUCCESS.value:
            self.app.cli_view.display_current_stock_price(result.data)
        else:
            msg = result.msg1 if result else "응답 없음"
            code = (result.data or {}).get("code", stock_code)
            self.app.cli_view.display_current_stock_price_error(code, msg)
            self.app.logger.error(f"{stock_code} 현재가 조회 실패: {msg}")

    async def handle_account_balance(self) -> None:
        balance_response = await self.app.stock_query_service.handle_get_account_balance()
        if balance_response is None:
            self.app.cli_view.display_account_balance_failure("잔고 조회 실패: 응답 없음")
            self.app.logger.warning("계좌 잔고 조회 실패 - 응답이 None입니다.")
            return
        if balance_response.rt_cd == "0":
            self.app.cli_view.display_account_balance(balance_response.data)
            self.app.logger.info(f"계좌 잔고 조회 성공: {balance_response.data}")
        else:
            self.app.cli_view.display_account_balance_failure(balance_response.msg1)
            self.app.logger.warning(f"계좌 잔고 조회 실패: {balance_response.msg1}")

    async def handle_buy_stock(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("매수할 종목 코드를 입력하세요: ")
        qty = await self.app.cli_view.get_user_input("매수할 수량을 입력하세요: ")
        price = await self.app.cli_view.get_user_input("매수 가격을 입력하세요 (시장가: 0): ")
        response : ResCommonResponse = await self.app.order_execution_service.handle_buy_stock(stock_code, qty, price)
        if response.rt_cd == ErrorCode.SUCCESS.value:
            self.app.cli_view.display_order_success(order_type="buy",stock_code=stock_code,qty=qty,response=response)
        else:
            self.app.cli_view.display_order_failure(order_type="buy", stock_code=stock_code, response=response)

    async def handle_sell_stock(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("매도할 종목 코드를 입력하세요: ")
        qty = await self.app.cli_view.get_user_input("매도할 수량을 입력하세요: ")
        price = await self.app.cli_view.get_user_input("매도 가격을 입력하세요 (시장가: 0): ")
        response : ResCommonResponse = await self.app.order_execution_service.handle_sell_stock(stock_code, qty, price)
        if response.rt_cd == ErrorCode.SUCCESS.value:
            self.app.cli_view.display_order_success(order_type="sell",stock_code=stock_code,qty=qty,response=response)
        else:
            self.app.cli_view.display_order_failure(order_type="sell", stock_code=stock_code, response=response)

    async def handle_stock_change_rate(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
        res: ResCommonResponse = await self.app.stock_query_service.get_stock_change_rate(stock_code)

        if res and res.rt_cd == ErrorCode.SUCCESS.value:
            d = res.data
            self.app.cli_view.display_stock_change_rate_success(
                d["stock_code"],
                d["current_price"],
                d["change_value_display"],
                d["change_rate"],
            )
        else:
            self.app.cli_view.display_stock_change_rate_failure(stock_code)

    async def handle_open_vs_current_rate(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
        res: ResCommonResponse = await self.app.stock_query_service.get_open_vs_current(stock_code)

        if res and res.rt_cd == ErrorCode.SUCCESS.value:
            d = res.data
            self.app.cli_view.display_stock_vs_open_price_success(
                d["stock_code"],
                d["current_price"],
                d["open_price"],
                d["vs_open_value_display"],
                d["vs_open_rate_display"],
            )
        else:
            self.app.cli_view.display_stock_vs_open_price_failure(stock_code)

    async def handle_asking_price(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("호가를 조회할 종목 코드를 입력하세요(삼성전자: 005930): ")
        self.app.logger.info(f"Handler - {stock_code} 호가 정보 조회 요청")
        result = await self.app.stock_query_service.handle_get_asking_price(stock_code)
        if result and result.rt_cd == ErrorCode.SUCCESS.value:
            self.app.cli_view.display_asking_price(result.data)
        else:
            msg = result.msg1 if result else "응답 없음"
            code = (result.data or {}).get("code", stock_code)
            self.app.cli_view.display_asking_price_error(code, msg)

    async def handle_time_conclude(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("시간대별 체결가를 조회할 종목 코드를 입력하세요(삼성전자: 005930): ")
        self.app.logger.info(f"Handler - {stock_code} 시간대별 체결가 조회 요청")
        result = await self.app.stock_query_service.handle_get_time_concluded_prices(stock_code)
        if result and result.rt_cd == ErrorCode.SUCCESS.value:
            self.app.cli_view.display_time_concluded_prices(result.data)
        else:
            msg = result.msg1 if result else "응답 없음"
            code = (result.data or {}).get("code", stock_code)
            self.app.cli_view.display_time_concluded_error(code, msg)

    async def handle_invalidate_token(self) -> None:
        self.app.env.invalidate_token()
        self.app.cli_view.display_token_invalidated_message()

    async def handle_etf_info(self) -> None:
        etf_code = await self.app.cli_view.get_user_input("정보를 조회할 ETF 코드를 입력하세요(나스닥 ETF: 133690): ")
        self.app.logger.info(f"Handler - {etf_code} ETF 정보 조회 요청")

        result = await self.app.stock_query_service.handle_get_etf_info(etf_code)

        if result and result.rt_cd == ErrorCode.SUCCESS.value:
            self.app.cli_view.display_etf_info(etf_code, result.data)
        else:
            msg = result.msg1 if result else "응답 없음"
            code = (result.data or {}).get("code", etf_code)
            self.app.cli_view.display_etf_info_error(code, msg)

    async def handle_ohlcv(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("종목코드(예: 005930): ")
        period = await self.app.cli_view.get_user_input("기간코드(D=일봉, W=주봉, M=월봉, Y=년봉) [기본: D]: ")

        period = (period or "D").strip().upper()

        resp = await self.app.stock_query_service.get_ohlcv(stock_code, period=period)

        # 3) 성공/실패 판단 후 출력은 전부 viewer로 위임
        ok = bool(resp) and str(resp.rt_cd) == str(ErrorCode.SUCCESS.value)
        if ok:
            self.app.cli_view.display_ohlcv(stock_code, resp.data or [])
        else:
            msg = (resp.msg1 if resp else "응답 없음")
            self.app.cli_view.display_ohlcv_error(stock_code, msg)

    async def handle_fetch_recnt_daily_ohlcv(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("종목코드(예: 005930): ")
        limit_in = await self.app.cli_view.get_user_input(f"최근 몇 개 일봉? [기본: {DynamicConfig.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE}]: ")

        # 숫자 파싱 (기본 100)
        try:
            limit = int(limit_in) if limit_in else DynamicConfig.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE
            if limit <= 0:
                self.app.cli_view.display_invalid_input_warning("0 이하 불가. 기본값 100 사용.")
                limit = DynamicConfig.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE
        except ValueError:
            self.app.cli_view.display_invalid_input_warning("숫자가 아님. 기본값 100 사용.")
            limit = DynamicConfig.OHLCV.DAILY_ITEMCHARTPRICE_MAX_RANGE

        # 서비스 호출 → 결과 출력은 전부 cli_view로 위임
        resp = await self.app.stock_query_service.get_recent_daily_ohlcv(stock_code, limit=limit)
        ok = bool(resp) and str(resp.rt_cd) == str(ErrorCode.SUCCESS.value)

        if ok:
            self.app.cli_view.display_ohlcv(stock_code, resp.data or [])
        else:
            msg = (resp.msg1 if resp else "응답 없음")
            self.app.cli_view.display_ohlcv_error(stock_code, msg)

    async def handle_intraday_minutes_today(self) -> None:
        code = await self.app.cli_view.get_user_input("종목코드(예: 005930): ")

        # 입력 시간(옵션). 미입력 시 현재 KST 기준 'HHMMSS'로 자동 생성
        hour_in = await self.app.cli_view.get_user_input("기준시간(옵션, 예: HHMMSS). 공란=현재시각: ")
        tm = self.app.time_manager
        hour_in = tm.to_hhmmss(hour_in) if hour_in else tm.to_hhmmss(None)

        resp = await self.app.stock_query_service.get_intraday_minutes_today(code, input_hour_1=hour_in)

        ok = bool(resp) and str(resp.rt_cd) == str(ErrorCode.SUCCESS.value)
        if ok:
            self.app.cli_view.display_intraday_minutes(code, resp.data or [], title="당일 분봉")
        else:
            msg = (resp.msg1 if resp else "응답 없음")
            self.app.cli_view.display_intraday_error(code, msg)

    async def handle_intraday_minutes_by_date(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("일별 분봉 조회")
            return
        
        code = await self.app.cli_view.get_user_input("종목코드(예: 005930): ")
        ymd  = await self.app.cli_view.get_user_input("조회일자(YYYYMMDD, 예: 20241023): ")

        # 입력 시간1(옵션) — 일부 환경은 HHMMSS 또는 HH만 사용. 공란 허용.
        hour_in = await self.app.cli_view.get_user_input("입력시간(옵션, 예: HHMMSS). 공란=현재시각: ")
        tm = self.app.time_manager
        hour_in = tm.to_hhmmss(hour_in) if hour_in else tm.to_hhmmss(None)

        resp = await self.app.stock_query_service.get_intraday_minutes_by_date(
            code, input_date_1=ymd, input_hour_1=hour_in
        )

        ok = bool(resp) and str(resp.rt_cd) == str(ErrorCode.SUCCESS.value)
        if ok:
            self.app.cli_view.display_intraday_minutes(code, resp.data or [], title=f"일별 분봉({ymd})")
        else:
            msg = (resp.msg1 if resp else "응답 없음")
            self.app.cli_view.display_intraday_error(code, msg)

    async def handle_day_intraday_minutes(self) -> None:
        """
        하루 전체 분봉(09:00~15:30 또는 08:00~20:00)을 list로 받아 CLI에 위임 출력.
        - 실전: get_intraday_minutes_by_date(100개/배치)
        - 모의: get_intraday_minutes_today(30개/배치)
        """
        code = await self.app.cli_view.get_user_input("종목코드(예: 005930): ")
        range_choice = await self.app.cli_view.get_user_input("시간범위 1) 09:00~15:30  2) 08:00~20:00  [기본: 1]: ") or "1"
        session = "EXTENDED" if str(range_choice).strip() == "2" else "REGULAR"

        ymd_in = await self.app.cli_view.get_user_input("조회일(YYYYMMDD, 공란=오늘): ")
        if self.app.env.is_paper_trading and ymd_in:
            # 모의에서는 by_date 불가 → 오늘로 강제
            self.app.cli_view.display_warning_paper_trading_not_supported("일자 지정 하루 분봉 조회(모의) - 오늘로 조회합니다.")
            ymd_in = ""

        # 서비스 함수로 하루치 분봉 list 확보
        rows = await self.app.stock_query_service.get_day_intraday_minutes_list(
            stock_code=code,
            date_ymd=(ymd_in or None),       # None이면 오늘(Today API)
            session=session
        )

        title_suffix = "08:00~20:00" if session == "EXTENDED" else "09:00~15:30"
        title = f"하루 분봉 ({(ymd_in or '오늘')} {title_suffix})"
        if rows:
            # 표시는 CLI에 위임
            # 전용 타이틀 메서드가 있으면 사용, 없으면 기본 출력 사용
            if hasattr(self.app.cli_view, "display_intraday_minutes_full_day"):
                self.app.cli_view.display_intraday_minutes_full_day(code, rows, (ymd_in or "오늘"), session)
            else:
                self.app.cli_view.display_intraday_minutes(code, rows, title=title)
        else:
            self.app.cli_view.display_intraday_error(code, "데이터가 없습니다.")

    async def handle_top_market_cap_stocks(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("시가총액 상위 종목 조회")
        else:
            count = await self.app.cli_view.get_user_input("조회 종목 개수 :")

            res: ResCommonResponse = await self.app.stock_query_service.handle_get_top_market_cap_stocks_code(market_code="0000",limit=count)
            if res and res.rt_cd == ErrorCode.SUCCESS.value:
                items = res.data or []
                if items:
                    self.app.cli_view.display_top_market_cap_stocks_success(items)
                else:
                    self.app.cli_view.display_top_market_cap_stocks_empty()
            else:
                self.app.cli_view.display_top_market_cap_stocks_failure(res.msg1 if res else "조회 실패")

    async def handle_top_10_market_cap_stocks(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("시가총액 1~10위 종목 조회")
        else:
            res: ResCommonResponse = await self.app.stock_query_service.handle_get_top_market_cap_stocks_code(market_code="0000",limit=10)
            if res and res.rt_cd == ErrorCode.SUCCESS.value:
                items = res.data or []
                if items:
                    self.app.cli_view.display_top10_market_cap_prices_success(items)
                else:
                    self.app.cli_view.display_top10_market_cap_prices_empty()
            else:
                self.app.cli_view.display_top10_market_cap_prices_failure(res.msg1 if res else "조회 실패")

    async def handle_current_upper_limit(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("전일 상한가 종목 조회 (전체)")
        else:
            upper_limit_stocks: ResCommonResponse = await self.app.stock_query_service.handle_current_upper_limit_stocks()
            if upper_limit_stocks.rt_cd == ErrorCode.SUCCESS.value:
                self.app.cli_view.display_current_upper_limit_stocks(upper_limit_stocks.data)
            else:
                self.app.cli_view.display_no_current_upper_limit_stocks()

    async def handle_realtime_stream(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("구독할 종목 코드를 입력하세요: ")
        field_input = await self.app.cli_view.get_user_input("구독할 데이터 타입을 입력하세요 (price, quote 중 택1 또는 쉼표로 구분): ")
        stock_codes = [stock_code]
        fields = [field.strip() for field in field_input.split(",") if field.strip() in {"price", "quote"}]
        if not fields:
            self.app.cli_view.display_strategy_error("올바른 필드를 입력하세요. (price, quote 중 선택)")
            return
        await self.app.stock_query_service.handle_realtime_stream(stock_codes, fields, duration=30)

    async def handle_momentum_strategy(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("모멘텀")
            self.app.logger.warning("모의투자환경에서 전략 실행 시도")
            return

        if not self.app.time_manager.is_market_open():
            self.app.cli_view.display_warning_strategy_market_closed()
            self.app.logger.warning("시장 미개장 상태에서 전략 실행 시도")
            return

        self.app.cli_view.display_strategy_running_message("모멘텀")

        try:
            top_codes_response: ResCommonResponse = await self.app.stock_query_service.handle_get_top_market_cap_stocks_code("0000")
            if top_codes_response.rt_cd != ErrorCode.SUCCESS.value:
                self.app.cli_view.display_top_stocks_failure(top_codes_response.msg1 or "알 수 없는 오류")
                self.app.logger.warning(f"시가총액 조회 실패. 응답: {top_codes_response}")
                return

            top_codes_list: list[ResTopMarketCapApiItem] = getattr(top_codes_response, 'data', [])
            if not top_codes_list:
                self.app.cli_view.display_top_stocks_failure("시가총액 상위 종목 데이터 없음.")
                self.app.logger.warning("시가총액 상위 종목 데이터 없음.")
                return

            self.app.cli_view.display_top_stocks_success()

            top_stock_codes = [item.mksc_shrn_iscd for item in top_codes_list[:30] if item.mksc_shrn_iscd]
            if not top_stock_codes:
                self.app.cli_view.display_no_stocks_for_strategy()
                return

            strategy = MomentumStrategy(
                broker=self.app.broker,
                min_change_rate=10.0,
                min_follow_through=3.0,
                min_follow_through_time=10,
                mode="live",
                backtest_lookup=None,
                logger=self.app.logger
            )
            executor = StrategyExecutor(strategy)
            result = await executor.execute(top_stock_codes)

            self.app.cli_view.display_strategy_results("모멘텀", result)
            self.app.cli_view.display_follow_through_stocks(result.get("follow_through", []))
            self.app.cli_view.display_not_follow_through_stocks(result.get("not_follow_through", []))

        except Exception as e:
            self.app.logger.error(f"모멘텀 전략 실행 중 오류 발생: {e}", exc_info=True)
            self.app.cli_view.display_strategy_error(f"전략 실행 실패: {e}")

    async def handle_momentum_backtest(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("모멘텀 백테스트")
            self.app.logger.warning("모의투자환경에서 전략 실행 시도")
            return

        self.app.cli_view.display_strategy_running_message("모멘텀 백테스트")

        try:
            count_input = await self.app.cli_view.get_user_input("시가총액 상위 몇 개 종목을 조회할까요? (기본값: 30): ")
            try:
                count = int(count_input) if count_input else 30
                if count <= 0:
                    self.app.cli_view.display_invalid_input_warning("0 이하의 수는 허용되지 않으므로 기본값 30을 사용합니다.")
                    count = 30
            except ValueError:
                self.app.cli_view.display_invalid_input_warning("숫자가 아닌 값이 입력되어 기본값 30을 사용합니다.")
                count = 30

            top_codes_response: ResCommonResponse = await self.app.stock_query_service.handle_get_top_market_cap_stocks_code("0000",
                                                                                                                             count=count)
            if top_codes_response.rt_cd != ErrorCode.SUCCESS.value:
                self.app.cli_view.display_top_stocks_failure(top_codes_response.msg1 or "알 수 없는 오류")
                self.app.logger.warning(f"시가총액 조회 실패. 응답: {top_codes_response}")
                return

            top_codes_list: list[ResTopMarketCapApiItem] = getattr(top_codes_response, "data", [])
            if not top_codes_list:
                self.app.cli_view.display_top_stocks_failure("시가총액 상위 종목 데이터 없음 (백테스트).")
                self.app.logger.warning("시가총액 상위 종목 데이터 없음 (백테스트).")
                return

            top_stock_codes = [item.get("mksc_shrn_iscd") for item in top_codes_list if
                               isinstance(item, dict) and "mksc_shrn_iscd" in item]
            if not top_stock_codes:
                self.app.cli_view.display_no_stocks_for_strategy()
                return

            strategy = MomentumStrategy(
                broker=self.app.broker,
                min_change_rate=10.0,
                min_follow_through=3.0,
                min_follow_through_time=10,
                mode="backtest",
                backtest_lookup=self.app.backtest_data_provider.realistic_price_lookup,
                logger=self.app.logger
            )
            executor = StrategyExecutor(strategy)
            result = await executor.execute(top_stock_codes)

            self.app.cli_view.display_strategy_results("백테스트", result)
            self.app.cli_view.display_follow_through_stocks(result.get("follow_through", []))
            self.app.cli_view.display_not_follow_through_stocks(result.get("not_follow_through", []))

        except Exception as e:
            self.app.logger.error(f"[백테스트] 전략 실행 중 오류 발생: {e}")
            self.app.cli_view.display_strategy_error(f"전략 실행 실패: {e}")

    async def handle_gapup_pullback(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("GapUpPullback")
            self.app.logger.warning("모의투자환경에서 전략 실행 시도")
            return

        self.app.cli_view.display_strategy_running_message("GapUpPullback")

        try:
            count_input = await self.app.cli_view.get_user_input("시가총액 상위 몇 개 종목을 조회할까요? (기본값: 30): ")
            try:
                count = int(count_input) if count_input else 30
                if count <= 0:
                    self.app.cli_view.display_invalid_input_warning("0 이하의 수는 허용되지 않으므로 기본값 30을 사용합니다.")
                    count = 30
            except ValueError:
                self.app.cli_view.display_invalid_input_warning("숫자가 아닌 값이 입력되어 기본값 30을 사용합니다.")
                count = 30

            top_codes_response: ResCommonResponse = await self.app.stock_query_service.handle_get_top_market_cap_stocks_code("0000",
                                                                                                     count=count)

            if top_codes_response.rt_cd != ErrorCode.SUCCESS.value:
                self.app.cli_view.display_top_stocks_failure(top_codes_response.msg1 or "응답 형식 오류")
                self.app.logger.warning(f"GapUpPullback 시가총액 조회 실패: {top_codes_response}")
                return

            top_codes_list: list[ResTopMarketCapApiItem] = getattr(top_codes_response, 'data', [])
            if not top_codes_list:
                self.app.cli_view.display_no_stocks_for_strategy("시가총액 상위 종목 데이터 없음 (백테스트).")
                self.app.logger.warning("시가총액 상위 종목 데이터 없음 (백테스트).")
                return

            top_stock_codes = [item.get("mksc_shrn_iscd") for item in top_codes_list if
                               isinstance(item, dict) and "mksc_shrn_iscd" in item]
            if not top_stock_codes:
                self.app.cli_view.display_no_stocks_for_strategy()
                return

            strategy = GapUpPullbackStrategy(
                broker=self.app.broker,
                min_gap_rate=5.0,
                max_pullback_rate=2.0,
                rebound_rate=2.0,
                mode="live",
                logger=self.app.logger
            )
            executor = StrategyExecutor(strategy)
            result = await executor.execute(top_stock_codes)

            self.app.cli_view.display_strategy_results("GapUpPullback", result)
            self.app.cli_view.display_gapup_pullback_selected_stocks(result.get("gapup_pullback_selected", []))
            self.app.cli_view.display_gapup_pullback_rejected_stocks(result.get("gapup_pullback_rejected", []))

        except Exception as e:
            self.app.logger.error(f"[GapUpPullback] 전략 실행 오류: {e}")
            self.app.cli_view.display_strategy_error(f"전략 실행 실패: {e}")

    async def handle_top_volume_30(self) -> None:
        title = 'volume'
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("거래량 ~30위 종목 조회")
        else:
            res: ResCommonResponse = await self.app.stock_query_service.handle_get_top_stocks(title)
            if res.rt_cd == ErrorCode.SUCCESS.value:
                self.app.cli_view.display_top_stocks_ranking(title, res.data)
            else:
                self.app.cli_view.display_top_stocks_ranking_error(title, res.msg1)

    async def handle_top_rise_30(self) -> None:
        title = 'rise'
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("상승률 ~30위 종목 조회")
        else:
            res: ResCommonResponse = await self.app.stock_query_service.handle_get_top_stocks(title)
            if res.rt_cd == ErrorCode.SUCCESS.value:
                self.app.cli_view.display_top_stocks_ranking(title, res.data)
            else:
                self.app.cli_view.display_top_stocks_ranking_error(title, res.msg1)

    async def handle_top_fall_30(self) -> None:
        title = 'fall'
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("하락률 ~30위 종목 조회")
        else:
            res: ResCommonResponse = await self.app.stock_query_service.handle_get_top_stocks(title)
            if res.rt_cd == ErrorCode.SUCCESS.value:
                self.app.cli_view.display_top_stocks_ranking(title, res.data)
            else:
                self.app.cli_view.display_top_stocks_ranking_error(title, res.msg1)

    async def handle_backtest_intraday_open_threshold(self) -> None:
        """사용자가 입력한 종목코드와 날짜로 단일일자 분봉 백테스트 실행"""

        # 1) 사용자 입력 받기
        code = await self.app.cli_view.get_user_input("종목코드(예: 005930): ")
        ymd  = await self.app.cli_view.get_user_input("조회일(YYYYMMDD, 공란=오늘): ")
        # code = '008040' # await self.app.cli_view.get_user_input("종목코드(예: 005930): ")
        # ymd  = '20250912' # await self.app.cli_view.get_user_input("조회일(YYYYMMDD, 공란=오늘): ")

        # 2) 모의투자 환경에서 과거 날짜 입력 방지
        if self.app.env.is_paper_trading and ymd:
            self.app.cli_view.display_warning_paper_trading_not_supported("모의환경에서는 지정일 분봉 불가 - 오늘로 조회합니다.")
            ymd = ""

        # 3) VolumeBreakoutStrategy 인스턴스 생성 후 백테스트 실행
        strategy = VolumeBreakoutStrategy(
            stock_query_service=self.app.stock_query_service,
            time_manager=self.app.time_manager,
            logger=self.app.logger,
        )
        result = await strategy.backtest_open_threshold_intraday(
            stock_code=code,
            date_ymd=(ymd or None),
            session="REGULAR",
        )

        # 4) 결과 출력 및 로그 기록
        self.app.cli_view._print_common_header()
        if not result.get("ok"):
            msg = f"❌ 백테스트 실패: {result.get('message')}"
            print(f"\n{msg}")
            self.app.logger.info(msg)
            return

        header_msg = f"--- Intraday Open-Threshold Backtest ---\n종목: {result['stock_code']} 날짜: {result['date']}"
        print(f"\n{header_msg}")
        self.app.logger.info(header_msg)

        trades = result.get("trades", [])
        if not trades:
            msg = "트레이드 없음 (트리거 미발생)"
            print(msg)
            self.app.logger.info(msg)
            return

        t = trades[0]
        entry_msg = f"진입시각: {t['entry_time']} 진입가: {t['entry_px']}"
        exit_msg = f"청산시각: {t['exit_time']} 청산가: {t['exit_px']} 결과: {t['outcome']}"
        ret_msg = (f"수익률: {t['ret_pct']}% (시가: {t['open0']}, "
                   f"트리거/TP/SL={t['trigger_pct']}/{t['tp_pct']}/{t['sl_pct']}%)")

        print(entry_msg)
        print(exit_msg)
        print(ret_msg)

        self.app.logger.info(entry_msg)
        self.app.logger.info(exit_msg)
        self.app.logger.info(ret_msg)

    async def handle_backtest_top30_volume_rise(self, *_, **__) -> None:
        """거래량 상위 30종목과 상승률 상위 30종목에 대해 하루치 백테스트 실행"""
        # 1) 조회일 입력 받기
        ymd = await self.app.cli_view.get_user_input("조회일(YYYYMMDD, 공란=오늘): ")
        if self.app.env.is_paper_trading and ymd:
            self.app.cli_view.display_warning_paper_trading_not_supported("모의환경에서는 지정일 분봉 불가 - 오늘로 조회합니다.")
            ymd = ""
        date_label = ymd or None

        # 2) 상위 30 종목 리스트 가져오기 (거래량, 상승률)
        # 거래량 상위 30
        vol_res = await self.app.stock_query_service.handle_get_top_stocks('volume')
        vol_codes = []
        if vol_res.rt_cd == ErrorCode.SUCCESS.value:
            items = vol_res.data
        if isinstance(items, dict) and 'output' in items:
            items = items['output']
        for item in items[:30]:
            code = getattr(item, 'mksc_shrn_iscd', None) or (
                item.get('mksc_shrn_iscd') if isinstance(item, dict) else None)
            if code:
                vol_codes.append(code)

        # 상승률 상위 30
        rise_res = await self.app.stock_query_service.handle_get_top_stocks('rise')
        rise_codes = []
        if rise_res.rt_cd == ErrorCode.SUCCESS.value:
            items = rise_res.data
        if isinstance(items, dict) and 'output' in items:
            items = items['output']
        for item in items[:30]:
            code = item.stck_shrn_iscd or (
                item.get('stck_shrn_iscd') if isinstance(item, dict) else None)
            if code:
                rise_codes.append(code)

        # vol_list = vol_res.data if hasattr(vol_res, 'data') else []
        # rise_list = rise_res.data if hasattr(rise_res, 'data') else []
        # vol_codes = [x.get('mksc_shrn_iscd') if isinstance(x, dict) else getattr(x, 'mksc_shrn_iscd', None) for x in vol_list][:30]
        # rise_codes = [x.get('mksc_shrn_iscd') if isinstance(x, dict) else getattr(x, 'mksc_shrn_iscd', None) for x in rise_list][:30]
        codes = list(set(vol_codes + rise_codes))

        # 3) VolumeBreakoutStrategy 사용하여 각 종목 백테스트 실행
        strategy = VolumeBreakoutStrategy(
            stock_query_service=self.app.stock_query_service,
            time_manager=self.app.time_manager,
            logger=self.app.logger,
        )

        self.app.cli_view._print_common_header()
        header_msg = f"거래량 상위30 및 상승률 상위30 종목 백테스트 (날짜: {ymd or '오늘'})"
        print(f"\n{header_msg}")
        self.app.logger.info(header_msg)

        for code in codes:
            result = await strategy.backtest_open_threshold_intraday(
                stock_code=code,
                date_ymd=date_label,
                session="REGULAR",
            )
            if not result.get("ok"):
                msg = f"❌ {code}: 백테스트 실패 - {result.get('message')}"
                print(msg)
                self.app.logger.info(msg)
                continue
            trades = result.get("trades", [])
            if not trades:
                msg = f"{code}: 트리거 미발생"
                print(msg)
                self.app.logger.info(msg)
                continue
            t = trades[0]
            name = await self.app.broker.get_name_by_code(code)
            ret_msg = (f"{code}({name}): 진입 {t['entry_time']}({t['entry_px']}), "
                       f"청산 {t['exit_time']}({t['exit_px']}), 결과 {t['outcome']}, 수익률 {t['ret_pct']}%")
            print(ret_msg)
            self.app.logger.info(ret_msg)

    async def handle_backtest_ranked_universe_open_threshold(self) -> None:
        """거래량 상위 30 + 상승률 상위 30 유니버스 백테스트 (CLI 출력과 로그 info 둘 다 기록)"""
        ymd = await self.app.cli_view.get_user_input("조회일(YYYYMMDD, 공란=오늘): ")
        if self.app.env.is_paper_trading and ymd:
            self.app.cli_view.display_warning_paper_trading_not_supported("모의환경에서는 지정일 분봉 불가 - 오늘로 조회합니다.")
            ymd = ""

        # 2) 상위 30 종목 리스트 가져오기 (거래량, 상승률)
        # 거래량 상위 30
        vol_res = await self.app.stock_query_service.handle_get_top_stocks('volume')
        vol_codes = []
        if vol_res.rt_cd == ErrorCode.SUCCESS.value:
            items = vol_res.data
        if isinstance(items, dict) and 'output' in items:
            items = items['output']
        for item in items[:30]:
            code = getattr(item, 'mksc_shrn_iscd', None) or (
                item.get('mksc_shrn_iscd') if isinstance(item, dict) else None)
            if code:
                vol_codes.append(code)

        # 상승률 상위 30
        rise_res = await self.app.stock_query_service.handle_get_top_stocks('rise')
        rise_codes = []
        if rise_res.rt_cd == ErrorCode.SUCCESS.value:
            items = rise_res.data
        if isinstance(items, dict) and 'output' in items:
            items = items['output']
        for item in items[:30]:
            code = item.stck_shrn_iscd or (
                item.get('stck_shrn_iscd') if isinstance(item, dict) else None)
            if code:
                rise_codes.append(code)

        seen = set()
        universe = []
        for code in list(vol_codes) + list(rise_codes):
            if code and code not in seen:
                seen.add(code)
                universe.append(code)

        strategy = VolumeBreakoutStrategy(
            stock_query_service=self.app.stock_query_service,
            time_manager=self.app.time_manager,
            logger=self.app.logger,
        )

        header_msg = (
            f"--- Ranked Universe Backtest (Open-Threshold) ---\n"
            f"조회일: {ymd or self.app.time_manager.get_current_kst_time().strftime('%Y%m%d')}\n"
            f"유니버스 크기: {len(universe)} (VolTop30 ∪ RiseTop30)"
        )
        print("    " + header_msg)
        self.app.logger.info(header_msg)

        rows = []
        wins = losses = closes = triggers = 0
        equity = 1.0

        for idx, code in enumerate(universe, 1):
            try:
                result = await strategy.backtest_open_threshold_intraday(
                    stock_code=code,
                    date_ymd=(ymd or None),
                    session="REGULAR",
                )
            except Exception as e:
                msg = f"[{idx:02d}] {code}: 백테스트 예외 {e}"
                print(msg)
                self.app.logger.info(msg)
                continue

            if not result.get("ok"):
                msg = f"[{idx:02d}] {code}: 실패 - {result.get('message')}"
                print(msg)
                self.app.logger.info(msg)
                continue

            trade = (result.get("trades") or [None])[0]
            if not trade:
                msg = f"[{idx:02d}] {code}: 트리거 미발생"
                print(msg)
                self.app.logger.info(msg)
                continue

            triggers += 1
            outcome = trade["outcome"]
            ret_pct = trade["ret_pct"]
            equity *= (1.0 + trade["ret"])

            if outcome == "take_profit":
                wins += 1
            elif outcome == "stop_loss":
                losses += 1
            else:
                closes += 1

            line = f"[{idx:02d}] {code}: outcome={outcome:>11}  ret={ret_pct:>7.3f}%  entry={trade['entry_px']} -> exit={trade['exit_px']}"
            print(line)
            self.app.logger.info(line)
            rows.append((code, outcome, ret_pct))

        total = len(universe)
        trig_rate = (triggers / total * 100.0) if total else 0.0
        win_rate = (wins / triggers * 100.0) if triggers else 0.0
        avg_ret = (sum(r for _, _, r in rows) / len(rows)) if rows else 0.0

        summary = (
            f"총 종목: {total}, 트리거 발생: {triggers} ({trig_rate:.1f}%)\n"
            f"TP: {wins}, SL: {losses}, CloseExit: {closes}, 승률: {win_rate:.1f}%\n"
            f"평균 수익률: {avg_ret:.3f}%\n"
            f"누적 자본: {equity:.4f}"
        )
        print(summary)
        self.app.logger.info(summary)
