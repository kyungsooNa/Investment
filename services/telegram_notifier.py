import aiohttp
import html
import logging
from typing import Optional, List, Dict
from services.notification_service import NotificationEvent, NotificationCategory, NotificationLevel
import unicodedata

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """Telegram 알림을 비동기적으로 전송하는 핸들러 클래스입니다."""

    def __init__(self, strategy_bot_token: str, backlog_bot_token: str, chat_id: str):
        self.strategy_bot_token = strategy_bot_token
        self.backlog_bot_token = backlog_bot_token
        self.chat_id = chat_id
        self.strategy_api_url = f"https://api.telegram.org/bot{self.strategy_bot_token}/sendMessage"
        self.backlog_api_url = f"https://api.telegram.org/bot{self.backlog_bot_token}/sendMessage"
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
        except Exception as e:
            logger.error(f"Telegram 알림 전송 중 예외 발생: {e}")


class TelegramReporter:
    """텔레그램으로 정형화된 리포트(랭킹 등)를 전송하는 클래스입니다."""

    def __init__(self, report_bot_token: str, chat_id: str):
        self.report_bot_token = report_bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{self.report_bot_token}/sendMessage"

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
            
            # RS (상대강도) - 추후 연동 전까지 기본값 표기
            rs = s.get("rs", "-")

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
