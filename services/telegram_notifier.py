import asyncio
from functools import wraps
from datetime import datetime
import re
from zoneinfo import ZoneInfo

import aiohttp
import html
import logging
from typing import Optional, List, Dict
from services.notification_service import NotificationEvent, NotificationCategory, NotificationLevel
import unicodedata

logger = logging.getLogger(__name__)


def _serialized_report_send(func):
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        async with self._report_send_lock:
            return await func(self, *args, **kwargs)
    return wrapper


def _telegram_html_to_parts(text: str) -> tuple[str, str]:
    plain = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [line.strip() for line in plain.splitlines() if line.strip()]
    if not lines:
        return "Telegram 알림", ""
    return lines[0], "\n".join(lines[1:])


class TelegramNotifier:
    """Telegram 알림을 비동기적으로 전송하는 핸들러 클래스입니다."""

    def __init__(self, strategy_bot_token: str, backlog_bot_token: str, chat_id: str, history_repository=None):
        self.strategy_bot_token = strategy_bot_token
        self.backlog_bot_token = backlog_bot_token
        self.chat_id = chat_id
        self.strategy_api_url = f"https://api.telegram.org/bot{self.strategy_bot_token}/sendMessage"
        self.backlog_api_url = f"https://api.telegram.org/bot{self.backlog_bot_token}/sendMessage"
        self._history_repository = history_repository
        # 허용할 카테고리 목록 설정 (기본값: STRATEGY, BACKGROUND, SYSTEM)
        self.allowed_categories = [NotificationCategory.STRATEGY, NotificationCategory.BACKGROUND, NotificationCategory.SYSTEM]

    async def handle_event(self, event: NotificationEvent) -> None:
        """NotificationService에서 호출할 비동기 콜백 메서드입니다."""
        # ★ 필터링 로직: 허용된 카테고리가 설정되어 있고, 현재 이벤트 카테고리가 거기에 없으면 무시
        if self.allowed_categories is not None and event.category not in self.allowed_categories:
            return
        
        api_url = None
        if event.category == NotificationCategory.STRATEGY:
            api_url = self.strategy_api_url
        elif event.category == NotificationCategory.BACKGROUND or \
            event.category == NotificationCategory.SYSTEM:
            api_url = self.backlog_api_url
        else:
            return
        
        # 특정 레벨(예: info, warning, error, critical)에 따라 이모지나 포맷을 다르게 할 수 있습니다.
        level_emoji = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨"
        }.get(event.level.value.lower(), "🔔")

        # 메타데이터에 수익률(return_rate) 정보가 있으면 메시지 본문에 이모지와 함께 삽입
        message_body = event.message
        if event.metadata and "return_rate" in event.metadata:
            rr = event.metadata.get("return_rate")
            if rr is not None:
                if rr > 0:
                    rt_emoji = "📈"
                elif rr < 0:
                    rt_emoji = "📉"
                else:
                    rt_emoji = "➖"
                
                if "사유:" in message_body:
                    message_body = message_body.replace("사유:", f"{rt_emoji} 수익: {rr:+.2f}%\n사유:")
                else:
                    message_body += f"\n{rt_emoji} 수익: {rr:+.2f}%"

        safe_title = html.escape(str(event.title), quote=False)
        safe_timestamp = html.escape(str(event.timestamp), quote=False)
        safe_message_body = html.escape(str(message_body), quote=False)

        # 텔레그램으로 보낼 메시지 포맷 구성
        text = (
            f"{level_emoji} <b>[{event.category.value}] {safe_title}</b>\n"
            f"시간: {safe_timestamp}\n"
            f"내용:\n{safe_message_body}"
        )

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        # 비동기 HTTP 요청으로 Telegram API 호출
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(api_url, json=payload) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        logger.error(f"Telegram 알림 전송 실패: {response.status} - {response_text}")
                    elif self._history_repository is not None:
                        try:
                            self._history_repository.record(
                                sent_at=event.timestamp,
                                source="strategy" if event.category == NotificationCategory.STRATEGY else "backlog",
                                title=event.title,
                                message=event.message,
                                level=event.level.value,
                            )
                        except Exception as e:
                            logger.error(f"Telegram 알림 이력 저장 실패: {e}")
        except Exception as e:
            logger.error(f"Telegram 알림 전송 중 예외 발생: {e}")


class TelegramReporter:
    """텔레그램으로 정형화된 리포트(랭킹 등)를 전송하는 클래스입니다."""

    def __init__(self, report_bot_token: str, chat_id: str, history_repository=None):
        self.report_bot_token = report_bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.report_bot_token}/sendMessage"
        self._report_send_lock = asyncio.Lock()
        self._history_repository = history_repository

    async def _send_message(self, text: str) -> bool:
        """텔레그램으로 메시지를 비동기적으로 전송하는 헬퍼 메서드입니다."""
        if not text:
            return True

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.post(self.api_url, json=payload) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        logger.error(f"Telegram 리포트 전송 실패: {response.status} - {response_text}")
                        return False
            if self._history_repository is not None:
                try:
                    title, message = _telegram_html_to_parts(text)
                    self._history_repository.record(
                        sent_at=datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
                        source="report",
                        title=title,
                        message=message,
                        level="info",
                    )
                except Exception as e:
                    logger.error(f"Telegram 리포트 이력 저장 실패: {e}")
            return True
        except Exception as e:
            logger.error(f"Telegram 리포트 전송 중 예외 발생: {e}")
            return False

    def _format_ranking_table(self, title: str, ranking_data: List[Dict], value_field: str, limit: int = 10, divisor: float = 100_000_000, show_ratio: bool = True) -> str:
        """랭킹 데이터를 HTML 포맷의 문자열로 변환합니다."""
        if not ranking_data:
            return ""

        header = f"<b>🏆 {title} (Top {limit})</b>\n"
        table = "<pre>"
        if show_ratio:
            table += "순 종목        등락 금액(억)   비중\n"
            table += "-" * 38 + "\n"
        else:
            table += "순 종목        등락 금액(억)\n"
            table += "-" * 31 + "\n"

        for i, item in enumerate(ranking_data[:limit]):
            rank = i + 1
            name = item.get('hts_kor_isnm', 'N/A')
            
            # 1. 종목명 자르기 (최대 8칸)
            display_name = ""
            display_width = 0
            for char in name:
                char_width = 2 if unicodedata.east_asian_width(char) in ('F', 'W', 'A') else 1
                if display_width + char_width > 8:
                    break
                display_name += char
                display_width += char_width
            
            name_padding = 8 - display_width
            name_str = f"{display_name}{' ' * name_padding}"

            # 2. 등락률
            try:
                rate = float(item.get('prdy_ctrt', '0') or '0')
                if rate > 0:
                    rate_str = f"+{rate:.1f}%"
                elif rate < 0:
                    rate_str = f"{rate:.1f}%"
                else:
                    rate_str = "0.0%"
            except Exception:
                rate_str = "-"

            # 3. 금액 (억)
            value_str = item.get(value_field, "0")
            raw_val = 0
            try:
                # 금액(원)을 억 단위로 변환
                raw_val = float(value_str or "0")
                val_100m = raw_val / divisor
                val_str = f"{val_100m:,.0f}"
            except (ValueError, TypeError):
                val_str = "-"

            if show_ratio:
                # 4. 비중 (순매수금액 / 총거래대금)
                ratio_str = "-"
                try:
                    acml_tr_pbmn = float(item.get('acml_tr_pbmn', '0') or '0')
                    if acml_tr_pbmn > 0:
                        # divisor=100이면 raw_val은 백만원 단위 -> 원 단위로 변환
                        net_won = raw_val * 1_000_000 if divisor == 100 else raw_val
                        
                        ratio = abs(net_won) / acml_tr_pbmn * 100
                        ratio_str = f"{ratio:.1f}%"
                except Exception:
                    pass

                table += f"{rank:<2} {name_str} {rate_str:>7} {val_str:>8} {ratio_str:>6}\n"
            else:
                table += f"{rank:<2} {name_str} {rate_str:>7} {val_str:>8}\n"

        table += "</pre>"
        return header + table

    @_serialized_report_send
    async def send_ranking_report(self, rankings: Dict[str, List[Dict]], report_date: str):
        """
        다양한 랭킹 정보를 하나의 리포트로 묶어 텔레그램에 전송합니다.
        rankings 딕셔너리는 'foreign_buy', 'all_stocks' 등의 키를 가집니다.
        """
        report_parts = []

        # 1. 개별 랭킹
        report_parts.append(self._format_ranking_table("외국인 순매수", rankings.get('foreign_buy', []), 'frgn_ntby_tr_pbmn', divisor=100))
        report_parts.append(self._format_ranking_table("기관 순매수", rankings.get('inst_buy', []), 'orgn_ntby_tr_pbmn', divisor=100))
        report_parts.append(self._format_ranking_table("프로그램 순매수", rankings.get('program_buy', []), 'whol_smtn_ntby_tr_pbmn', divisor=100_000_000))
        report_parts.append(self._format_ranking_table("외국인 순매도", rankings.get('foreign_sell', []), 'frgn_ntby_tr_pbmn', divisor=100))
        report_parts.append(self._format_ranking_table("기관 순매도", rankings.get('inst_sell', []), 'orgn_ntby_tr_pbmn', divisor=100))
        report_parts.append(self._format_ranking_table("프로그램 순매도", rankings.get('program_sell', []), 'whol_smtn_ntby_tr_pbmn', divisor=100_000_000))

        # 2. 조합 랭킹
        all_stocks = rankings.get('all_stocks')
        program_all_stocks = rankings.get('program_all_stocks')

        if all_stocks:
            # 외인+기관
            fi_combined = []
            for stock in all_stocks:
                try:
                    f_net = float(stock.get('frgn_ntby_tr_pbmn', '0') or '0')
                    i_net = float(stock.get('orgn_ntby_tr_pbmn', '0') or '0')
                    new_stock = stock.copy()
                    new_stock['fi_combined_net'] = f_net + i_net
                    fi_combined.append(new_stock)
                except (ValueError, TypeError): continue

            report_parts.append(self._format_ranking_table("외인+기관 순매수", sorted(fi_combined, key=lambda x: x['fi_combined_net'], reverse=True), 'fi_combined_net', divisor=100))
            report_parts.append(self._format_ranking_table("외인+기관 순매도", sorted(fi_combined, key=lambda x: x['fi_combined_net']), 'fi_combined_net', divisor=100))

            # 외인+기관+프로그램
            if program_all_stocks:
                prog_map = {p['stck_shrn_iscd']: float(p.get('whol_smtn_ntby_tr_pbmn', '0') or '0') for p in program_all_stocks}
                fip_combined = []
                for stock in all_stocks:
                    try:
                        f_net = float(stock.get('frgn_ntby_tr_pbmn', '0') or '0')
                        i_net = float(stock.get('orgn_ntby_tr_pbmn', '0') or '0')
                        p_net = prog_map.get(stock.get('stck_shrn_iscd'), 0.0)
                        new_stock = stock.copy()
                        new_stock['fip_combined_net'] = f_net + i_net + (p_net / 1_000_000)
                        fip_combined.append(new_stock)
                    except (ValueError, TypeError): continue

                report_parts.append(self._format_ranking_table("외인+기관+프로그램 순매수", sorted(fip_combined, key=lambda x: x['fip_combined_net'], reverse=True), 'fip_combined_net', divisor=100))
                report_parts.append(self._format_ranking_table("외인+기관+프로그램 순매도", sorted(fip_combined, key=lambda x: x['fip_combined_net']), 'fip_combined_net', divisor=100))

        # 3. 거래대금
        report_parts.append(self._format_ranking_table("거래대금 상위", rankings.get('trading_value', []), 'acml_tr_pbmn', divisor=100_000_000, show_ratio=False))

        # 메시지 분할 전송
        title = f"🔔 <b>장 마감 랭킹 리포트 ({report_date})</b>\n"
        await self._send_message(title)

        current_message = ""
        for part in filter(None, report_parts):
            if len(current_message.encode('utf-8')) + len(part.encode('utf-8')) + 2 > 4096:
                await self._send_message(current_message)
                current_message = part
            else:
                current_message += ("\n\n" + part) if current_message else part

        if current_message:
            await self._send_message(current_message)

    @_serialized_report_send
    async def send_ytd_ranking_report(
        self,
        ranking_data: List[Dict],
        report_date: str,
        limit: int = 20,
    ) -> bool:
        """주 마지막 거래일 기준 YTD 상승률 상위 종목을 전송한다."""
        if not ranking_data:
            return False

        first = ranking_data[0]
        base_date = html.escape(str(first.get("base_date") or "-"), quote=False)
        latest_date = html.escape(str(first.get("latest_date") or report_date), quote=False)
        lines = [
            f"📈 <b>주간 YTD 상승률 랭킹 ({report_date})</b>",
            f"기준: {base_date} → {latest_date}",
            "<pre>",
            "순 종목         현재가       YTD",
            "-" * 34,
        ]
        for rank, item in enumerate(ranking_data[:limit], 1):
            name = html.escape(str(item.get("name") or item.get("code") or "-"), quote=False)
            name = name[:8]
            try:
                price = f"{int(item.get('current_price') or 0):,}"
            except (TypeError, ValueError):
                price = "-"
            rate = self._format_signed_pct(item.get("ytd_return_rate"), digits=2)
            lines.append(f"{rank:<2} {name:<8} {price:>10} {rate:>9}")
        lines.append("</pre>")
        return await self._send_message("\n".join(lines))

    @staticmethod
    def _format_won_100m(value) -> str:
        try:
            amount = float(value or 0)
        except (TypeError, ValueError):
            return "-"
        if amount == 0:
            return "-"
        amount_uk = amount / 100_000_000
        if abs(amount_uk) >= 10_000:
            jo = int(amount_uk // 10_000)
            uk = int(abs(amount_uk) % 10_000)
            sign = "-" if amount_uk < 0 and jo == 0 else ""
            return f"{sign}{jo:,}조" + (f" {uk:,}억" if uk else "")
        return f"{amount_uk:,.0f}억"

    @staticmethod
    def _format_signed_pct(value, digits: int = 1) -> str:
        try:
            rate = float(value or 0)
        except (TypeError, ValueError):
            return "-"
        return f"{rate:+.{digits}f}%"

    @_serialized_report_send
    async def send_daily_theme_report(
        self,
        themes: List[Dict],
        report_date: str,
        limit: int = 10,
        show_flow_ratio: bool = True,
    ):
        """당일 주도 테마 리포트를 텔레그램에 전송한다."""
        title = f"🔥 <b>오늘의 주도 테마 ({report_date})</b>\n"
        await self._send_message(title)

        if not themes:
            await self._send_message("데이터 없음")
            return

        parts = []
        for rank, theme in enumerate(themes[:limit], 1):
            theme_name = html.escape(str(theme.get("normalized_name") or ""), quote=False)
            avg_rate = self._format_signed_pct(theme.get("leader_avg_change_rate"), digits=2)
            trading_value = self._format_won_100m(theme.get("trading_value_sum_won"))
            advancing_ratio = self._format_signed_pct(theme.get("advancing_ratio"), digits=1).replace("+", "")
            flow_ratio = self._format_signed_pct(theme.get("flow_ratio"), digits=2)
            theme_score = theme.get("market_leadership_score", theme.get("theme_score"))
            score_text = ""
            if theme_score is not None:
                try:
                    score_label = "주도점수" if "market_leadership_score" in theme else "종합점수"
                    score_text = f" | {score_label} {float(theme_score):.2f}"
                except (TypeError, ValueError):
                    score_text = ""
            member_count = theme.get("scored_member_count")
            advance_count = theme.get("advance_count")
            if member_count is not None and advance_count is not None:
                summary_parts = [f"상승 {advance_count}/{member_count} ({advancing_ratio})"]
            else:
                summary_parts = [f"상승비율 {advancing_ratio}"]
            liquidity_bonus = theme.get("liquidity_bonus")
            if liquidity_bonus is not None:
                try:
                    summary_parts.append(f"유동성 +{float(liquidity_bonus):.2f}")
                except (TypeError, ValueError):
                    pass
            if show_flow_ratio:
                summary_parts.append(f"수급비중 {flow_ratio}")
            summary_line = " | ".join(summary_parts) + score_text
            lines = [
                f"<b>{rank}. {theme_name}</b>  주도주 평균 {avg_rate}  {trading_value}",
                summary_line,
                "<pre>",
            ]
            for leader in (theme.get("leaders") or [])[:3]:
                name = html.escape(str(leader.get("name") or leader.get("code") or ""), quote=False)
                rate = self._format_signed_pct(leader.get("change_rate"), digits=1)
                tv = self._format_won_100m(leader.get("trading_value_won"))
                lines.append(f"{name} {rate} {tv}")
            momentum_leaders = theme.get("momentum_leaders") or []
            liquid_codes = {leader.get("code") for leader in (theme.get("leaders") or [])}
            thin_momentum_leaders = [
                leader for leader in momentum_leaders
                if leader.get("code") not in liquid_codes
            ]
            if thin_momentum_leaders:
                lines.append("상승률 상위(저유동성 포함)")
                for leader in thin_momentum_leaders[:3]:
                    name = html.escape(str(leader.get("name") or leader.get("code") or ""), quote=False)
                    rate = self._format_signed_pct(leader.get("change_rate"), digits=1)
                    tv = self._format_won_100m(leader.get("trading_value_won"))
                    lines.append(f"{name} {rate} {tv}")
            lines.append("</pre>")
            parts.append("\n".join(lines))

        current = ""
        for part in parts:
            chunk = part + "\n\n"
            if len((current + chunk).encode("utf-8")) > 4000:
                await self._send_message(current)
                current = chunk
            else:
                current += chunk
        if current:
            await self._send_message(current.rstrip())

    @_serialized_report_send
    async def send_newhigh_report(self, stocks: List[Dict], report_date: str):
        """52주 신고가 종목 리포트를 텔레그램에 전송합니다."""
        title = f"🚀 <b>52주 신고가 종목 리포트 ({report_date})</b>\n총 {len(stocks)}개 종목\n"
        await self._send_message(title)

        if not stocks:
            await self._send_message("신고가 종목 없음")
            return

        lines = []
        for i, s in enumerate(stocks, 1):
            name = s.get("name") or ""
            code = s.get("code") or ""
            price = s.get("current_price") or 0
            
            # 시가총액 (API: hts_avls '억원' / FDR: Marcap '원' 단위 혼재 가능성 처리)
            try:
                cap = float(s.get("market_cap") or 0)
                # 10억 '억원'(1경원) 이상일 경우 '원' 단위 데이터로 간주하고 보정
                if cap > 1_000_000_000:
                    cap = cap / 100_000_000
                
                if cap > 0:
                    if cap >= 10000:
                        jo = int(cap // 10000)
                        uk = int(cap % 10000)
                        cap_str = f"{jo:,}조" + (f" {uk:,}억" if uk > 0 else "")
                    else:
                        cap_str = f"{cap:.1f}억" if cap < 1 else f"{int(cap):,}억"
                else:
                    cap_str = "-"
            except (ValueError, TypeError):
                cap_str = "-"
            
            # 거래대금 (API acml_tr_pbmn 기준 '원' 단위) -> 억원 변환
            try:
                tv = float(s.get("trading_value") or 0)
                if tv > 0:
                    tv_100m = tv / 100_000_000
                    tv_str = f"{tv_100m:.1f}억" if tv_100m < 1 else f"{tv_100m:,.0f}억"
                else:
                    tv_str = "-"
            except (ValueError, TypeError):
                tv_str = "-"
            
            # RS (상대강도) - RS enrichment는 rs_rating 키에만 값을 주입하므로 우선 사용
            rs = s.get("rs_rating") or s.get("rs") or "-"

            try:
                rate_f = float(s.get("change_rate") or "0")
                rate_str = f"+{rate_f:.2f}%" if rate_f >= 0 else f"{rate_f:.2f}%"
            except (ValueError, TypeError):
                rate_str = "-"
            
            is_historical = s.get("is_historical_new_high", False)
            hist_badge = "👑역 " if is_historical else ""
            lines.append(f"{i:2}. {hist_badge}<b>{name}</b>({code}) {price:,}원 {rate_str} | 대금:{tv_str} | 시총:{cap_str} | RS:{rs}")

        current = ""
        for line in lines:
            chunk = line + "\n"
            if len((current + chunk).encode("utf-8")) > 4000:
                await self._send_message(current)
                current = chunk
            else:
                current += chunk
        if current:
            await self._send_message(current)

    @_serialized_report_send
    async def send_strategy_log_report(self, report_html: str, report_date: str):
        """전략 로그 분석 리포트를 텔레그램으로 전송합니다."""
        title = f"📋 <b>전략 실행 요약 리포트 ({report_date})</b>\n"
        await self._send_message(title)

        current = ""
        for line in report_html.split('\n'):
            chunk = line + '\n'
            if len((current + chunk).encode('utf-8')) > 4000:
                await self._send_message(current)
                current = chunk
            else:
                current += chunk
        if current:
            await self._send_message(current)

    @_serialized_report_send
    async def send_operational_decision_report(self, report_html: str, report_date: str):
        """운영자가 바로 볼 짧은 의사결정 요약을 텔레그램으로 전송합니다."""
        current = ""
        for line in report_html.split('\n'):
            chunk = line + '\n'
            if len((current + chunk).encode('utf-8')) > 4000:
                await self._send_message(current)
                current = chunk
            else:
                current += chunk
        if current:
            await self._send_message(current)

    @_serialized_report_send
    async def send_disclosure_alert(self, disclosure, importance, ai_summary: Optional[str] = None) -> bool:
        """관심종목 중요 공시 한 건을 즉시 전송한다.

        ai_summary 가 주어지면 규칙 판정 위에 AI 요약 블록을 덧붙인다. None 이면
        (AI 비활성/호출 실패) 기존 규칙 기반 알림과 동일하게 발송한다.
        """
        company = html.escape(str(disclosure.corp_name or disclosure.stock_code), quote=False)
        stock_code = html.escape(str(disclosure.stock_code), quote=False)
        report_name = html.escape(str(disclosure.report_name), quote=False)
        reasons = "\n".join(
            f"• {html.escape(str(reason), quote=False)}" for reason in importance.reasons
        )
        ai_block = ""
        if ai_summary:
            ai_block = f"🤖 <b>AI 요약</b>\n{html.escape(str(ai_summary), quote=False)}\n\n"
        message = (
            "🚨 <b>관심종목 중요 공시</b>\n\n"
            f"<b>{company} ({stock_code})</b>\n"
            f"공시: {report_name}\n"
            f"중요도: {html.escape(str(importance.level), quote=False)} "
            f"({int(importance.score)}점)\n\n"
            f"{ai_block}"
            f"<b>판정 근거</b>\n{reasons}\n\n"
            f"접수일: {html.escape(str(disclosure.receipt_date), quote=False)}\n"
            f'<a href="{disclosure.viewer_url}">DART 원문 보기</a>'
        )
        return await self._send_message(message)

    @_serialized_report_send
    async def send_disclosure_digest(self, stored_items, report_date: str) -> bool:
        """즉시 알림 기준 미만 공시를 일일 요약으로 전송한다."""
        if not stored_items:
            return True
        header = f"📋 <b>관심종목 공시 요약 — {html.escape(str(report_date), quote=False)}</b>\n"
        parts = []
        for item in stored_items:
            disclosure = item.disclosure
            importance = item.importance
            company = html.escape(str(disclosure.corp_name or disclosure.stock_code), quote=False)
            report_name = html.escape(str(disclosure.report_name), quote=False)
            parts.append(
                f"\n<b>{company} ({html.escape(str(disclosure.stock_code), quote=False)})</b>\n"
                f"• {report_name}\n"
                f"• 중요도: {html.escape(str(importance.level), quote=False)} ({int(importance.score)}점)\n"
                f'<a href="{disclosure.viewer_url}">원문</a>\n'
            )

        messages = []
        current = header
        for part in parts:
            if len((current + part).encode("utf-8")) > 4000:
                messages.append(current)
                current = header + part
            else:
                current += part
        if current:
            messages.append(current)

        success = True
        for message in messages:
            success = await self._send_message(message) and success
        return success

    @_serialized_report_send
    async def send_premium_watchlist_report(self, kospi: List[Dict], kosdaq: List[Dict], report_date: str, limit: int = 30):
        """전일 기준 우량주 풀 리포트를 텔레그램으로 전송합니다."""
        title = (
            f"⭐ <b>전일 기준 우량주 리포트 ({report_date})</b>\n"
            f"KOSPI {len(kospi)}개 | KOSDAQ {len(kosdaq)}개\n"
        )
        await self._send_message(title)

        for market_label, stocks in [("KOSPI", kospi), ("KOSDAQ", kosdaq)]:
            if not stocks:
                continue

            lines = [f"<b>── {market_label} ({len(stocks)}개) ──</b>"]
            for i, s in enumerate(stocks[:limit], 1):
                name = s.get("name", "")
                code = s.get("code", "")
                total_score = s.get("total_score", 0)
                rs_rating = s.get("rs_rating") or "-"
                stage = s.get("minervini_stage", 0)
                stage_badge = "★" if stage == 2 else ""

                try:
                    mcap = float(s.get("market_cap") or 0)
                    if mcap > 0:
                        mcap_uk = mcap / 100_000_000
                        if mcap_uk >= 10000:
                            jo = int(mcap_uk // 10000)
                            uk = int(mcap_uk % 10000)
                            mcap_str = f"{jo:,}조" + (f" {uk:,}억" if uk > 0 else "")
                        else:
                            mcap_str = f"{int(mcap_uk):,}억"
                    else:
                        mcap_str = "-"
                except Exception:
                    mcap_str = "-"

                try:
                    tv = float(s.get("avg_trading_value_5d") or 0)
                    tv_str = f"{tv / 100_000_000:,.0f}억" if tv > 0 else "-"
                except Exception:
                    tv_str = "-"

                score = total_score if total_score is not None else 0
                lines.append(
                    f"{i:2}. {stage_badge}<b>{name}</b>({code}) "
                    f"점수:{score:.0f} RS:{rs_rating} "
                    f"시총:{mcap_str} 대금:{tv_str}"
                )

            current = ""
            for line in lines:
                chunk = line + "\n"
                if len((current + chunk).encode("utf-8")) > 4000:
                    await self._send_message(current)
                    current = chunk
                else:
                    current += chunk
            if current:
                await self._send_message(current)

    @_serialized_report_send
    async def send_minervini_report(self, items: List[Dict], report_date: str, limit: int = 30):
        """Minervini Stage2 종목 목록을 텔레그램으로 전송합니다.

        items: 리스트 항목은 사전에 정렬되어 있다고 가정합니다. 각 항목은 최소한
        'code', 'name', 'stck_prpr'(현재가), 'rs_rating', 'market_cap', 'reason' 키를 가집니다.
        """
        if not items:
            await self._send_message(f"Minervini Stage2 리포트 ({report_date}) - 결과 없음")
            return

        title = f"🔎 <b>Minervini Stage2 리포트 ({report_date})</b> — 총 {len(items)}개\n"
        await self._send_message(title)

        lines = []
        for i, it in enumerate(items[:limit], 1):
            name = it.get('name') or ''
            code = it.get('code') or ''
            try:
                price = int(it.get('stck_prpr') or it.get('current_price') or 0)
                price_str = f"{price:,}원" if price else "-"
            except Exception:
                price_str = '-'
            rs = it.get('rs_rating') or it.get('rs') or '-'
            try:
                mcap = it.get('market_cap') or None
                if mcap:
                    mcap_val = float(mcap)
                    # if value seems large (already in won), convert to 억
                    if mcap_val > 1_000_000_000:
                        mcap_val = mcap_val / 100_000_000
                    if mcap_val >= 10000:
                        jo = int(mcap_val // 10000)
                        uk = int(mcap_val % 10000)
                        mcap_str = f"{jo:,}조" + (f" {uk:,}억" if uk > 0 else "")
                    else:
                        mcap_str = f"{mcap_val:.1f}억" if mcap_val < 1 else f"{int(mcap_val):,}억"
                else:
                    mcap_str = '-'
            except Exception:
                mcap_str = '-'
            reason = it.get('reason') or ''
            lines.append(f"{i:2}. <b>{name}</b>({code}) {price_str} | RS:{rs} | 시총:{mcap_str} {('· '+reason) if reason else ''}")

        # send in chunks to avoid Telegram message size limits
        current = ""
        for line in lines:
            chunk = line + "\n"
            if len((current + chunk).encode('utf-8')) > 4000:
                await self._send_message(current)
                current = chunk
            else:
                current += chunk

        if current:
            await self._send_message(current)

    @staticmethod
    def _format_krw_cap(value) -> str:
        try:
            won = int(float(value or 0))
        except (TypeError, ValueError):
            return "-"
        if won == 0:
            return "-"
        sign = "-" if won < 0 else ""
        abs_won = abs(won)
        jo = abs_won / 1_000_000_000_000
        if jo >= 1:
            return f"{sign}{jo:,.0f}조"
        uk = abs_won / 100_000_000
        return f"{sign}{uk:,.0f}억"

    @_serialized_report_send
    async def send_market_cap_gap_report(self, report: Dict, report_date: str, trigger_label: str, limit: int = 10):
        """삼성전자/SK하이닉스 대비 미국 주요 기업 시총갭 리포트를 전송합니다.

        US 종목을 기준으로 묶어, 각 종목 아래 국내 앵커별 배율(굵게)·갭(조)을
        인라인으로 표시한다 (모바일 텔레그램 가독성을 위한 2줄/종목 카드형).
        """
        fx = report.get("fx_rate")
        fx_text = f"{float(fx):,.2f}" if fx else "-"
        lines = [
            f"📊 <b>시총갭 ({report_date})</b>",
            f"USD/KRW {fx_text} · {html.escape(str(trigger_label), quote=False)}",
        ]

        korean = report.get("korean") or []
        if korean:
            kr_summary = " · ".join(
                f"{html.escape(str(k.get('name') or k.get('symbol') or ''), quote=False)} "
                f"{self._format_krw_cap(k.get('market_cap_krw'))}"
                for k in korean
            )
            lines += ["", f"🇰🇷 {kr_summary}"]

        adr_gap = report.get("adr_gap")
        if adr_gap:
            gap_percent = float(adr_gap.get("gap_percent") or 0)
            gap_label = "ADR 프리미엄" if gap_percent > 0 else "ADR 디스카운트" if gap_percent < 0 else "동일"
            lines += [
                "",
                "🔄 <b>SK하이닉스 ADR 가격갭</b>",
                f"본주 {html.escape(str(adr_gap.get('korean_symbol') or ''), quote=False)} "
                f"{int(adr_gap.get('korean_price_krw') or 0):,}원",
                f"ADR {html.escape(str(adr_gap.get('adr_symbol') or ''), quote=False)} "
                f"${float(adr_gap.get('adr_price_usd') or 0):,.2f}",
                f"환산 {int(adr_gap.get('implied_korean_price_krw') or 0):,}원 · "
                f"{gap_percent:+.2f}% ({gap_label})",
                "1 ADS = 본주 0.1주 · 환율·거래비용 미반영",
            ]

        comparisons = report.get("comparisons") or []
        if not comparisons:
            lines += ["", "미국 비교군 시총갭 계산 불가: 환율 또는 시총 데이터 부족"]
            await self._send_message("\n".join(lines))
            return

        lines += ["", "🇺🇸 미국 비교군  (배율 · 갭조, ✅=한국우위)"]

        us_cap = {u.get("symbol"): u.get("market_cap_krw") for u in (report.get("us") or [])}
        order = [u.get("symbol") for u in (report.get("us") or [])]
        grouped: Dict = {}
        for row in comparisons:
            grouped.setdefault(row.get("us_symbol"), []).append(row)
        if not order:
            order = list(grouped.keys())

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        blocks = []
        ranked = [s for s in order if s in grouped][:limit]
        for rank, sym in enumerate(ranked, 1):
            prefix = medals.get(rank, f"{rank}.")
            symbol_text = html.escape(str(sym or ""), quote=False)
            block = [f"{prefix} {symbol_text}  {self._format_krw_cap(us_cap.get(sym))}"]
            for row in grouped[sym]:
                kr_label = html.escape(str(row.get("korean_name") or row.get("korean_symbol") or ""), quote=False)
                try:
                    ratio_val = float(row.get("ratio"))
                    ratio_text = f"{ratio_val:.2f}x"
                except (TypeError, ValueError):
                    ratio_val = None
                    ratio_text = "-"
                gap_krw = row.get("gap_krw")
                gap_text = self._format_krw_cap(gap_krw)
                try:
                    is_korea_advantage = int(gap_krw) < 0
                except (TypeError, ValueError):
                    is_korea_advantage = False
                flag = " ✅" if is_korea_advantage else ""
                block.append(f"   {kr_label} <b>{ratio_text}</b> ({gap_text}){flag}")
            blocks.append("\n".join(block))

        # Telegram 4096 byte 제한 대비, 종목 블록 단위로 메시지를 나눠 보낸다.
        current = "\n".join(lines)
        for block in blocks:
            candidate = f"{current}\n\n{block}"
            if len(candidate.encode("utf-8")) > 4000:
                await self._send_message(current)
                current = block
            else:
                current = candidate
        await self._send_message(current)
