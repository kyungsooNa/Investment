"""해외 모의 주문 TR 실동작 프로브 — 모의(VTTT...) 주문이 실제로 수락되는지 1회 확인.

`config/tr_ids_config.yaml` 에는 해외 주문의 real/paper 쌍이 모두 있고(#606에서 주간거래
TTTS603x → 정규장 TTTT100xU 로 전환하며 추가), `KoreaInvestTrIdProvider` 가
`is_paper_trading` 으로 분기한다. 다만 **모의 서버가 해외 주문을 실제로 받아주는지**
검증한 기록이 저장소에 없다. 이 스크립트가 그 한 번을 채운다.

프로브 방식:
  현재가 조회 → 현재가의 절반 가격으로 1주 지정가 매수 → ODNO 수신 확인 → 즉시 취소.
  체결될 수 없는 가격이라 포지션이 생기지 않는다(취소 실패 시에도 미체결로 남을 뿐).

**안전장치(전부 fail-closed, 주문 전송 이전에 검사)**:
  1. 실전 모드 옵션 자체가 없다 — `is_paper_trading=True` 하드코딩.
  2. base_url 이 모의 도메인(openapivts)인지 검사.
  3. 사용될 TR ID 가 모의 접두사 'V' 인지 검사(매수·정정취소 둘 다).
  4. 활성 계좌번호가 config 의 `paper_stock_account_number` 와 일치하는지 검사.
  5. 기본은 **전송하지 않음**. 위 검사 결과만 출력하고 종료한다.
     실제 전송은 `--send` 를 명시할 때만 이뤄진다.

**체결 확인 모드(`--send --fill`)**: 장중 전략을 모의계좌로 돌리려면 지정가가 실제로
*체결*되는지와 체결내역 응답 형태를 알아야 한다. 현재가 +0.5% 지정가로 1주 매수 → 체결내역
(주문번호 매칭)으로 체결 확인 → 현재가 -0.5% 지정가로 1주 매도 → 체결 확인. 정해진 시간
안에 체결되지 않으면 그 주문을 취소한다. 미국 정규장(09:30~16:00 ET)에 실행해야 한다.
안전장치는 위와 동일하게 먼저 검사한다(모의계좌 전용).

사용:
    conda activate py310
    python scripts/probe_overseas_paper_order.py                  # 검사만(전송 없음)
    python scripts/probe_overseas_paper_order.py --send           # 실제 모의 주문 전송
    python scripts/probe_overseas_paper_order.py --send --symbol AAPL
    python scripts/probe_overseas_paper_order.py --send --fill    # 1주 매수·매도 체결 확인
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.overseas_types import OverseasExchange
from common.types import ErrorCode

PAPER_URL_MARKER = "openapivts"


class GuardFailed(RuntimeError):
    """안전장치 위반 — 주문을 전송하지 않고 중단한다."""


def _dump(obj: Any) -> str:
    """응답 객체를 사람이 읽을 수 있는 JSON 으로 펼친다."""
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                obj = fn()
                break
            except Exception:
                pass
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return repr(obj)


def check_guards(env, config_dict: dict) -> dict:
    """주문 전송 전 안전장치 4종을 검사한다. 하나라도 어긋나면 GuardFailed."""
    from brokers.korea_investment.korea_invest_trid_provider import KoreaInvestTrIdProvider
    from config.config_loader import TR_IDS_CONFIG_PATH, load_config

    if env.is_paper_trading is not True:
        raise GuardFailed(f"env.is_paper_trading={env.is_paper_trading!r} — True 가 아니다.")

    base_url = env.get_base_url() or ""
    if PAPER_URL_MARKER not in base_url:
        raise GuardFailed(f"base_url 이 모의 도메인이 아니다: {base_url!r}")

    tr_ids = load_config(TR_IDS_CONFIG_PATH)["tr_ids"]
    provider = KoreaInvestTrIdProvider(env, tr_ids)
    buy_tr = provider.overseas_stock_order(is_buy=True)
    cancel_tr = provider.overseas_stock_order_rvsecncl()
    for label, tr in (("매수", buy_tr), ("정정취소", cancel_tr)):
        if not str(tr).upper().startswith("V"):
            raise GuardFailed(f"{label} TR 이 모의(V...)가 아니다: {tr!r}")

    active_account = str(env.active_config.get("stock_account_number") or "")
    paper_account = str(config_dict.get("paper_stock_account_number") or "")
    if not paper_account or active_account != paper_account:
        raise GuardFailed(
            f"활성 계좌({active_account!r})가 모의 계좌({paper_account!r})와 다르다."
        )

    return {
        "base_url": base_url,
        "buy_tr": buy_tr,
        "cancel_tr": cancel_tr,
        "account": active_account,
    }


async def probe(
    *,
    symbol: str,
    exchange: OverseasExchange,
    send: bool,
    logger,
    fill: bool = False,
) -> int:
    from scripts.run_overseas_dryrun import build_overseas_query_stack

    stack = await build_overseas_query_stack(is_paper_trading=True, logger=logger)
    broker = stack["broker"]
    config_dict = stack["config_dict"]
    env = getattr(broker, "env", None)
    if env is None:
        raise RuntimeError("broker 에서 env 를 얻지 못했습니다.")

    info = check_guards(env, config_dict)
    print("=== 안전장치 통과 ===")
    print(f"  base_url   : {info['base_url']}")
    print(f"  계좌       : {info['account']} (모의)")
    print(f"  매수 TR    : {info['buy_tr']}")
    print(f"  정정취소 TR: {info['cancel_tr']}")

    if fill:
        if not send:
            print("\n[미전송] --fill 은 실제 주문을 냅니다. --send 를 함께 붙이세요.")
            return 0
        trade_date = stack["us_clock"].get_current_kst_date_str()
        return await fill_probe(broker, symbol=symbol, exchange=exchange, trade_date=trade_date)

    price_resp = await broker.get_overseas_price(symbol, exchange=exchange)
    if getattr(price_resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
        print(f"\n[ERROR] 현재가 조회 실패: {_dump(price_resp)}")
        return 1
    summary = price_resp.data
    current = float(getattr(summary, "price", 0.0) or 0.0)
    if current <= 0:
        print(f"\n[ERROR] 현재가가 0 이다(장 시작 전이거나 심볼 오류): {_dump(summary)}")
        return 1

    # 절반 가격 지정가 매수 — 체결될 수 없어 포지션이 생기지 않는다.
    limit_price = f"{max(round(current * 0.5, 2), 0.01):.2f}"
    print(f"\n=== 프로브 주문(미체결 설계) ===")
    print(f"  종목 {symbol} @ {exchange.value} / 현재가 ${current:.2f}")
    print(f"  지정가 ${limit_price} × 1주 매수 → 체결 불가 가격")

    if not send:
        print("\n[미전송] 기본 모드는 검사만 합니다. 실제 전송은 --send 를 붙이세요.")
        return 0

    order_resp = await broker.place_overseas_limit_order(
        symbol=symbol,
        exchange=exchange,
        side="buy",
        qty=1,
        limit_price=limit_price,
    )
    print(f"\n=== 주문 응답 ===\n{_dump(order_resp)}")
    if getattr(order_resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
        print("\n[결과] 모의 해외 주문이 거부됐습니다. 위 msg1/rt_cd 를 확인하세요.")
        return 1

    order_no = str(getattr(order_resp.data, "broker_order_no", "") or "")
    print(f"\n[결과] 모의 해외 주문 수락됨. ODNO={order_no!r}")
    if not order_no:
        print("[경고] ODNO 가 비어 있어 취소를 시도할 수 없습니다. 계좌에서 직접 확인하세요.")
        return 1

    cancel_resp = await broker.cancel_overseas_order(
        symbol=symbol,
        exchange=exchange,
        original_order_no=order_no,
        qty=1,
        limit_price=limit_price,
    )
    print(f"\n=== 취소 응답 ===\n{_dump(cancel_resp)}")
    if getattr(cancel_resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
        print(f"\n[경고] 취소 실패. 모의 계좌에 미체결 주문 {order_no} 이 남아 있습니다.")
        return 1

    print(f"\n[완료] 주문 {order_no} 수락 → 취소까지 확인. 모의 해외 주문 경로 정상.")
    return 0


# KIS 해외 주문체결내역 응답의 체결수량·체결단가 후보 키(표본별로 갈려 관용 파싱).
_FILLED_QTY_KEYS = ("ft_ccld_qty", "ccld_qty", "tot_ccld_qty", "FT_CCLD_QTY", "CCLD_QTY")
_FILLED_PRICE_KEYS = ("ft_ccld_unpr3", "ft_ccld_unpr", "ccld_unpr", "FT_CCLD_UNPR3")


def _first(row: dict, keys) -> str:
    for key in keys:
        if str(row.get(key) or "").strip():
            return str(row[key]).strip()
    return ""


def _shift_date(yyyymmdd: str, days: int) -> str:
    return (datetime.strptime(yyyymmdd, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


async def _wait_fill(
    broker, *, symbol: str, exchange: OverseasExchange, order_no: str, trade_date: str,
    poll_attempts: int, poll_interval: float, sleep, out,
) -> Tuple[int, str]:
    """주문번호로 체결내역을 조회해 (체결수량, 체결단가)를 돌려준다. 미체결이면 (0, "")."""
    # 조회 기준일이 현지/한국 중 어느 쪽인지 표본이 없어 앞뒤 하루를 함께 본다.
    start, end = _shift_date(trade_date, -1), _shift_date(trade_date, 1)
    for attempt in range(1, poll_attempts + 1):
        resp = await broker.inquire_overseas_ccnl(
            symbol=symbol, exchange=exchange, start_date=start, end_date=end,
        )
        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            out(f"  [조회 {attempt}] 체결내역 조회 실패: {getattr(resp, 'msg1', '')}")
        else:
            data = getattr(resp, "data", None)
            rows = data.get("output") if isinstance(data, dict) else data
            mine = [r for r in (rows or []) if isinstance(r, dict)
                    and str(r.get("odno") or r.get("ODNO") or "").strip() == order_no]
            qty = sum(int(float(_first(r, _FILLED_QTY_KEYS) or 0)) for r in mine)
            if mine:
                out(f"  [조회 {attempt}] 체결내역 원본(주문 {order_no}):\n{_dump(mine)}")
            if qty > 0:
                return qty, _first(mine[0], _FILLED_PRICE_KEYS)
        if attempt < poll_attempts:
            await sleep(poll_interval)
    return 0, ""


async def _order_and_confirm(
    broker, *, symbol, exchange, side, limit_price, trade_date, poll_attempts, poll_interval,
    sleep, out,
) -> Tuple[Optional[int], str]:
    """1주 지정가 주문 → 체결 확인. 미체결이면 취소. 반환 (체결수량|None=주문실패, 단가)."""
    label = "매수" if side == "buy" else "매도"
    out(f"\n=== {label} {symbol} 1주 @ ${limit_price} ===")
    resp = await broker.place_overseas_limit_order(
        symbol=symbol, exchange=exchange, side=side, qty=1, limit_price=limit_price,
    )
    if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
        out(f"[실패] {label} 주문 거부: rt_cd={getattr(resp, 'rt_cd', None)} "
            f"msg={getattr(resp, 'msg1', '')}")
        return None, ""
    order_no = str(getattr(resp.data, "broker_order_no", "") or "")
    out(f"  접수 ODNO={order_no!r}")
    if not order_no:
        out("[실패] ODNO 가 비어 체결을 확인할 수 없습니다. 계좌에서 직접 확인하세요.")
        return None, ""
    qty, price = await _wait_fill(
        broker, symbol=symbol, exchange=exchange, order_no=order_no, trade_date=trade_date,
        poll_attempts=poll_attempts, poll_interval=poll_interval, sleep=sleep, out=out,
    )
    if qty > 0:
        out(f"  [체결] {label} {qty}주 @ ${price or '?'}")
        return qty, price
    out(f"  [미체결] {poll_attempts}회 조회 동안 체결 없음 → 주문 {order_no} 취소")
    cancel = await broker.cancel_overseas_order(
        symbol=symbol, exchange=exchange, original_order_no=order_no, qty=1,
        limit_price=limit_price,
    )
    if getattr(cancel, "rt_cd", None) != ErrorCode.SUCCESS.value:
        out(f"  [경고] 취소 실패 — 모의계좌에 미체결 주문 {order_no} 이 남아 있습니다.")
    return 0, ""


async def fill_probe(
    broker,
    *,
    symbol: str,
    exchange: OverseasExchange,
    trade_date: str,
    poll_attempts: int = 20,
    poll_interval: float = 3.0,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    out: Callable[[str], Any] = print,
) -> int:
    """시장성 지정가 1주 매수 → 체결 확인 → 1주 매도 → 체결 확인.

    반환: 0 = 매수·매도 모두 체결, 3 = 미체결(취소함), 1 = 주문/조회 실패.
    """
    from services.overseas_order_execution_service import OverseasOrderExecutionService as Oes

    async def _price() -> float:
        resp = await broker.get_overseas_price(symbol, exchange=exchange)
        if getattr(resp, "rt_cd", None) != ErrorCode.SUCCESS.value:
            return 0.0
        return float(getattr(resp.data, "price", 0.0) or 0.0)

    current = await _price()
    if current <= 0:
        out("[ERROR] 현재가 조회 실패 또는 0 — 정규장 시간인지 확인하세요.")
        return 1
    common = dict(broker=broker, symbol=symbol, exchange=exchange, trade_date=trade_date,
                  poll_attempts=poll_attempts, poll_interval=poll_interval, sleep=sleep, out=out)

    bought, buy_px = await _order_and_confirm(
        side="buy", limit_price=Oes._tick_price_str(current * 1.005, "buy"), **common)
    if bought is None:
        return 1
    if bought == 0:
        out("\n[결과] 매수 미체결 — 모의계좌에서 시장성 지정가가 체결되지 않았습니다.")
        return 3

    current = await _price() or current
    sold, sell_px = await _order_and_confirm(
        side="sell", limit_price=Oes._tick_price_str(current * 0.995, "sell"), **common)
    if not sold:
        out(f"\n[결과] 매수는 체결(${buy_px}), 매도는 미체결 — 모의계좌에 {symbol} 1주 보유 중."
            " 직접 정리하거나 다시 실행하세요.")
        return 1 if sold is None else 3
    out(f"\n[완료] 매수 ${buy_px} → 매도 ${sell_px} 체결 확인. 모의계좌 체결 경로 정상.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="해외 모의 주문 TR 실동작 프로브(기본: 검사만, 전송 없음)",
    )
    parser.add_argument("--symbol", default="AAPL", help="프로브 대상 심볼 (기본 AAPL)")
    parser.add_argument("--exchange", default="NASD", help="NASD | NYSE | AMEX")
    parser.add_argument(
        "--send", action="store_true",
        help="실제로 모의 주문을 전송한다. 미지정 시 안전장치 검사만 하고 종료.",
    )
    parser.add_argument(
        "--fill", action="store_true",
        help="--send 와 함께: 시장성 지정가로 1주 매수·매도해 실제 체결까지 확인한다.",
    )
    return parser


async def main_async(argv: Optional[List[str]] = None) -> int:
    from scripts.run_overseas_dryrun import _make_stdout_logger, resolve_exchange

    args = build_parser().parse_args(argv)
    logger = _make_stdout_logger()
    exchange = resolve_exchange(args.exchange)
    try:
        return await probe(
            symbol=args.symbol.strip().upper(),
            exchange=exchange,
            send=bool(args.send),
            logger=logger,
            fill=bool(args.fill),
        )
    except GuardFailed as e:
        print(f"\n[중단] 안전장치 위반 — 주문을 전송하지 않았습니다.\n  {e}")
        return 2


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
