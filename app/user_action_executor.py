# self.app/user_action_executor.py
from __future__ import annotations
from typing import TYPE_CHECKING

from pandas.io.common import get_handle

from config.DynamicConfig import DynamicConfig

if TYPE_CHECKING:
    from app.trading_app import TradingApp

from strategies.GapUpPullback_strategy import GapUpPullbackStrategy
from strategies.momentum_strategy import MomentumStrategy
from strategies.strategy_executor import StrategyExecutor
from common.types import ErrorCode, ResCommonResponse, ResTopMarketCapApiItem
from collections import OrderedDict

# === choice별 핸들러 함수 정의 ===
class UserActionExecutor:
    # 번호 -> (카테고리, 라벨, 핸들러명)
    COMMANDS: dict[str, tuple[str, str, str]] = {
        '0':  ('기본 기능',      '거래 환경 변경',                          'handle_change_environment'),
        '1':  ('기본 기능',      '현재가 조회',                              'handle_get_current_price'),
        '2':  ('기본 기능',      '계좌 잔고 조회',                          'handle_account_balance'),
        '3':  ('기본 기능',      '주식 매수',                                'handle_buy_stock'),
        '4':  ('기본 기능',      '주식 매도',                                'handle_sell_stock'),

        '5':  ('시세 조회',      '전일대비 등락률 조회',                    'handle_stock_change_rate'),
        '6':  ('시세 조회',      '시가대비 등락률 조회',                    'handle_open_vs_current_rate'),
        '7':  ('시세 조회',      '실시간 호가 조회',                        'handle_asking_price'),
        '8':  ('시세 조회',      '시간대별 체결가 조회',                    'handle_time_conclude'),
        '10': ('시세 조회',      'ETF 정보 조회',                         'handle_etf_info'),
        '11': ('시세 조회',      'OHLCV(차트) 조회',                      'handle_ohlcv'),
        '12': ('시세 조회',      '최근 일봉 120개 조회',                   'handle_fetch_recnt_daily_ohlcv'),

        '13': ('랭킹/필터링',    '시가총액 상위 조회 (실전 전용)',           'handle_top_market_cap_stocks'),
        '14': ('랭킹/필터링',    '시가총액 상위 10개 현재가 (실전 전용)',     'handle_top_10_market_cap_stocks'),
        '15': ('랭킹/필터링',    '전일 상한가 종목 (상위 500) (실전 전용)',   'handle_yesterday_upper_limit_500'),
        '16': ('랭킹/필터링',    '전일 상한가 종목 (상위) (실전 전용)',       'handle_yesterday_upper_limit'),
        '17': ('랭킹/필터링',    '현재 상한가 종목 (실전 전용)',              'handle_current_upper_limit'),

        '18': ('실시간 구독',    '실시간 체결가/호가 구독',                  'handle_realtime_stream'),

        '30': ('랭킹/필터링2',   '거래량 상위 랭킹 (~30) (실전 전용)',        'handle_top_volume_30'),
        '31': ('랭킹/필터링2',   '상승률 상위 랭킹 (~30) (실전 전용)',        'handle_top_rise_30'),
        '32': ('랭킹/필터링2',   '하락률 상위 랭킹 (~30) (실전 전용)',        'handle_top_fall_30'),

        # ⬇️ 실행기의 번호(100/101/102)를 그대로 사용해 메뉴와 동기화
        '100': ('전략 실행', '모멘텀 전략 실행', 'handle_momentum_strategy'),
        '101': ('전략 실행', '모멘텀 백테스트', 'handle_momentum_backtest'),
        '102': ('전략 실행', 'GapUpPullback 전략 실행', 'handle_gapup_pullback'),

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

    async def handle_get_current_price(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
        await self.app.stock_query_service.handle_get_current_stock_price(stock_code)

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
        await self.app.order_execution_service.handle_buy_stock(stock_code, qty, price)

    async def handle_sell_stock(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("매도할 종목 코드를 입력하세요: ")
        qty = await self.app.cli_view.get_user_input("매도할 수량을 입력하세요: ")
        price = await self.app.cli_view.get_user_input("매도 가격을 입력하세요 (시장가: 0): ")
        await self.app.order_execution_service.handle_sell_stock(stock_code, qty, price)

    async def handle_stock_change_rate(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
        await self.app.stock_query_service.handle_display_stock_change_rate(stock_code)

    async def handle_open_vs_current_rate(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("조회할 종목 코드를 입력하세요 (삼성전자: 005930): ")
        await self.app.stock_query_service.handle_display_stock_vs_open_price(stock_code)

    async def handle_asking_price(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("호가를 조회할 종목 코드를 입력하세요(삼성전자: 005930): ")
        await self.app.stock_query_service.handle_get_asking_price(stock_code)

    async def handle_time_conclude(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("시간대별 체결가를 조회할 종목 코드를 입력하세요(삼성전자: 005930): ")
        await self.app.stock_query_service.handle_get_time_concluded_prices(stock_code)

    async def handle_invalidate_token(self) -> None:
        self.app.env.invalidate_token()
        self.app.cli_view.display_token_invalidated_message()

    async def handle_etf_info(self) -> None:
        etf_code = await self.app.cli_view.get_user_input("정보를 조회할 ETF 코드를 입력하세요(나스닥 ETF: 133690): ")
        await self.app.stock_query_service.handle_get_etf_info(etf_code)

    async def handle_ohlcv(self) -> None:
        stock_code = await self.app.cli_view.get_user_input("종목코드(예: 005930): ")
        period = await self.app.cli_view.get_user_input("기간코드(D=일봉, M=분봉) [기본: D]: ")

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
        limit_in = await self.app.cli_view.get_user_input(f"최근 몇 개 일봉? [기본: {DynamicConfig.OHLCV.MAX_RANGE}]: ")

        # 숫자 파싱 (기본 100)
        try:
            limit = int(limit_in) if limit_in else DynamicConfig.OHLCV.MAX_RANGE
            if limit <= 0:
                self.app.cli_view.display_invalid_input_warning("0 이하 불가. 기본값 100 사용.")
                limit = DynamicConfig.OHLCV.MAX_RANGE
        except ValueError:
            self.app.cli_view.display_invalid_input_warning("숫자가 아님. 기본값 100 사용.")
            limit = DynamicConfig.OHLCV.MAX_RANGE

        # 서비스 호출 → 결과 출력은 전부 cli_view로 위임
        resp = await self.app.stock_query_service.get_recent_daily_ohlcv(stock_code, limit=limit)
        ok = bool(resp) and str(resp.rt_cd) == str(ErrorCode.SUCCESS.value)

        if ok:
            self.app.cli_view.display_ohlcv(stock_code, resp.data or [])
        else:
            msg = (resp.msg1 if resp else "응답 없음")
            self.app.cli_view.display_ohlcv_error(stock_code, msg)

    async def handle_top_market_cap_stocks(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("시가총액 상위 종목 조회")
        else:
            await self.app.stock_query_service.handle_get_top_market_cap_stocks_code("0000")

    async def handle_top_10_market_cap_stocks(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("시가총액 1~10위 종목 조회")
        else:
            await self.app.stock_query_service.handle_get_top_10_market_cap_stocks_with_prices()

    async def handle_yesterday_upper_limit_500(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("전일 상한가 종목 조회 (상위 500)")
        else:
            await self.app.stock_query_service.handle_upper_limit_stocks("0000", limit=500)

    async def handle_yesterday_upper_limit(self) -> None:
        if self.app.env.is_paper_trading:
            self.app.cli_view.display_warning_paper_trading_not_supported("전일 상한가 종목 조회 (상위)")
        else:
            await self.app.stock_query_service.handle_yesterday_upper_limit_stocks()

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

    async def handle_exit(self) -> bool:
        self.app.cli_view.display_exit_message()
        return False

    async def handle_invalid_choice(self) -> None:
        self.app.cli_view.display_invalid_menu_choice()


