"""장전 양·음봉 및 전일 대비 종가 방향 예측 (정보 표시 전용 — 매매 게이트에 연결하지 않는다).

밤사이 미국장·VIX·환율 지표로 오늘 코스피·코스닥·SK하이닉스가 전일 종가보다 높게
마감(B)할 확률과 시가보다 높게 마감(C, 양봉)할 확률을 각각 로지스틱 회귀로 추정한다.
지표 선정 근거:
`scripts/run_premarket_direction_backtest.py` (2021-10~2026-11 표본외, 매일 재학습)
— 코스피 65.3%(기준선 54.2%, 확신일 72.0%), 코스닥 61.1%(52.4%, 67.4%),
하이닉스(+마이크론) 64.7%(50.2%, 70.8%).

주의: 전일 대비 방향 예측력의 대부분은 시가 갭에서 나온다. 양·음봉 예측은 장중 방향을
나타내지만 과거 적중률이 약 50%로 낮으므로 별도 확률·적중률로 표시한다.

정렬 규칙(미래참조 방지): 한국 거래일 D 의 특징값은 미국 세션 날짜 T < D 인 종가만 쓴다.
미국 T 세션은 KST 로 T+1 새벽에 마감하므로 D 장 시작 전에 확정된다. 값은
"한국 직전 거래일 이후 새로 확정된 미국 세션들의 누적 수익률"이다(한국 휴장 자동 처리).

데이터: 국내 지수 = 네이버 fchart, 해외 = Yahoo Finance chart API.
"""
from __future__ import annotations

import json
import logging
import math
import re
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import httpx

from services.notification_service import NotificationCategory, NotificationLevel


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    label: str
    sign: int  # +1: 상승이 호재, -1: 상승이 악재
    symbol: str = ""


US_FEATURES: tuple[FeatureSpec, ...] = (
    FeatureSpec("sox", "필라델피아 반도체(SOX)", +1, "^SOX"),
    FeatureSpec("ndx", "나스닥100", +1, "^NDX"),
    FeatureSpec("spx", "S&P500", +1, "^GSPC"),
    FeatureSpec("ewy", "EWY(미국상장 한국ETF)", +1, "EWY"),
    FeatureSpec("vix", "VIX 변화율", -1, "^VIX"),
    FeatureSpec("usdkrw", "원/달러 환율 변화율", -1, "KRW=X"),
    FeatureSpec("tnx", "미국 10년물 금리 변화율", -1, "^TNX"),
)
KR_PREV = FeatureSpec("kr_prev_ret", "전일 지수 등락(모멘텀)", +1)
GAP = FeatureSpec("gap", "시가 갭(역추세, 시가 확정 후)", -1)
# 백테스트에서 의미 없던 금리·전일 모멘텀은 운영 예측에서 뺀다.
FORECAST_FEATURES: tuple[FeatureSpec, ...] = tuple(
    f for f in US_FEATURES if f.name in ("sox", "ndx", "spx", "ewy", "vix", "usdkrw")
)
MU = FeatureSpec("mu", "마이크론(MU)", +1, "MU")


@dataclass(frozen=True)
class ForecastTarget:
    code: str  # 네이버 fchart 심볼
    name: str
    features: tuple[FeatureSpec, ...]


# 하이닉스는 메모리 동종(마이크론)을 더하면 적중이 +3.8%p 오른다. 지수는 SOX 로 충분(효과 없음).
TARGETS: tuple[ForecastTarget, ...] = (
    ForecastTarget("KOSPI", "코스피", FORECAST_FEATURES),
    ForecastTarget("KOSDAQ", "코스닥", FORECAST_FEATURES),
    ForecastTarget("000660", "SK하이닉스", FORECAST_FEATURES + (MU,)),
)


# ── 파서 ─────────────────────────────────────────────────────────────

def parse_naver_fchart(text: str) -> List[dict]:
    rows = []
    for data in re.findall(r'data="([^"]+)"', text):
        d, o, h, l, c, *_ = data.split("|")
        rows.append({
            "date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
        })
    rows.sort(key=lambda r: r["date"])
    return rows


def parse_yahoo_chart(text: str) -> List[dict]:
    result = json.loads(text)["chart"]["result"][0]
    tz = timezone(timedelta(seconds=result["meta"].get("gmtoffset", 0)))
    closes = result["indicators"]["quote"][0]["close"]
    rows = {}
    for ts, c in zip(result.get("timestamp") or [], closes):
        if c is None:
            continue
        rows[datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")] = float(c)
    return [{"date": d, "close": rows[d]} for d in sorted(rows)]


# ── 데이터셋 ─────────────────────────────────────────────────────────

def asof_return(series: Sequence[dict], prev_date: str, date: str) -> Optional[float]:
    """date 미만 마지막 종가 / prev_date 미만 마지막 종가 - 1."""
    dates = [r["date"] for r in series]
    base_idx = bisect_left(dates, prev_date) - 1
    now_idx = bisect_left(dates, date) - 1
    if base_idx < 0 or now_idx < 0:
        return None
    return series[now_idx]["close"] / series[base_idx]["close"] - 1.0


def build_dataset(kr_rows: Sequence[dict], us: Dict[str, Sequence[dict]]) -> List[dict]:
    rows = []
    for i in range(1, len(kr_rows)):
        prev, cur = kr_rows[i - 1], kr_rows[i]
        row = {"date": cur["date"]}
        for name, series in us.items():
            row[name] = asof_return(series, prev["date"], cur["date"])
        row[KR_PREV.name] = prev["close"] / kr_rows[i - 2]["close"] - 1.0 if i >= 2 else None
        row[GAP.name] = cur["open"] / prev["close"] - 1.0
        row["up_close"] = int(cur["close"] > prev["close"])
        row["up_body"] = int(cur["close"] > cur["open"])
        row["up_gap"] = int(cur["open"] > prev["close"])
        rows.append(row)
    return rows


def _fit_logit(X: List[List[float]], y: List[int], iters: int = 400, lr: float = 0.5, l2: float = 1e-2):
    import numpy as np

    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    w = np.zeros(Z.shape[1])
    b = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(Z @ w + b)))
        g = p - y
        w -= lr * (Z.T @ g / len(y) + l2 * w)
        b -= lr * g.mean()
    return mu, sd, w, b




# ── 운영 예측 ─────────────────────────────────────────────────────────

UP_THRESHOLD = 0.6
DOWN_THRESHOLD = 0.4


def build_forecast_features(us: Dict[str, Sequence[dict]], prev_date: str, date: str) -> Dict[str, Optional[float]]:
    """오늘(date) 장 시작 전에 확정된 미국 세션의 직전 한국 거래일(prev_date) 대비 변화."""
    return {name: asof_return(series, prev_date, date) for name, series in us.items()}


TRACK_WINDOW = 20


def actual_changes(rows: Sequence[dict], before: str) -> Dict[str, float]:
    """일봉 → {날짜: 종가/전일종가-1}, before 미만 날짜만.

    장중 조회 시 오늘 일봉의 '종가'는 현재가라 확정값이 아니므로 오늘(before)은 제외한다.
    """
    return {rows[i]["date"]: rows[i]["close"] / rows[i - 1]["close"] - 1.0
            for i in range(1, len(rows)) if rows[i]["date"] < before}


def actual_body_changes(rows: Sequence[dict], before: str) -> Dict[str, float]:
    """일봉 → {날짜: 종가/시가-1}, before 미만 날짜만."""
    return {row["date"]: row["close"] / row["open"] - 1.0
            for row in rows if row["date"] < before and row.get("open")}


def _is_hit(probability: float, actual_change: float) -> bool:
    return (probability > 0.5) == (actual_change > 0)


def _is_body_hit(probability: float, actual_change: float) -> bool:
    return actual_change != 0 and _is_hit(probability, actual_change)


def describe(probability: float) -> str:
    """우세한 쪽 기준 표기 — '7%' 를 '7% 하락'으로 오해하지 않도록 '▼ 하락 마감 93%' 로 쓴다."""
    if probability >= 0.5:
        return f"▲ 상승 마감 {probability * 100:.0f}%"
    return f"▼ 하락 마감 {(1 - probability) * 100:.0f}%"


def describe_body(probability: float) -> str:
    if probability >= 0.5:
        return f"양봉 {probability * 100:.0f}%"
    return f"음봉 {(1 - probability) * 100:.0f}%"


def _body_outlook(probability: float) -> str:
    if probability >= UP_THRESHOLD:
        return "양봉 우세"
    if probability <= DOWN_THRESHOLD:
        return "음봉 우세"
    return "중립"


def _summarize_metric(
    history: Dict[str, dict],
    code: str,
    *,
    probability_key: str,
    actual_key: str,
    window: Optional[int],
    hit_fn=_is_hit,
) -> Dict[str, int]:
    records = [
        history[d][code] for d in sorted(history, reverse=True)
        if code in history[d]
        and history[d][code].get(probability_key) is not None
        and history[d][code].get(actual_key) is not None
    ][:window]
    confident = [
        r for r in records
        if r[probability_key] >= UP_THRESHOLD or r[probability_key] <= DOWN_THRESHOLD
    ]
    return {
        "n": len(records),
        "hits": sum(hit_fn(r[probability_key], r[actual_key]) for r in records),
        "confident_n": len(confident),
        "confident_hits": sum(hit_fn(r[probability_key], r[actual_key]) for r in confident),
    }


def summarize_track(history: Dict[str, dict], code: str,
                    window: Optional[int] = TRACK_WINDOW) -> Dict[str, int]:
    """실제 종가가 확정된 최근 window 일(None 이면 전체)의 적중 수 (확신 = 확률 ≥UP 또는 ≤DOWN 인 날)."""
    return _summarize_metric(
        history, code, probability_key="probability", actual_key="actual_change",
        window=window,
    )


def summarize_body_track(history: Dict[str, dict], code: str,
                         window: Optional[int] = TRACK_WINDOW) -> Dict[str, int]:
    """실제 양·음봉이 확정된 최근 window 일의 별도 적중 수."""
    return _summarize_metric(
        history, code, probability_key="body_probability", actual_key="actual_body_change",
        window=window, hit_fn=_is_body_hit,
    )


def _describe_error(exc: Exception) -> str:
    """httpx.ReadTimeout 처럼 메시지가 빈 예외는 타입 이름이라도 남긴다."""
    return str(exc) or type(exc).__name__


def _outlook(probability: float) -> str:
    if probability >= UP_THRESHOLD:
        return "상승 우세"
    if probability <= DOWN_THRESHOLD:
        return "하락 우세"
    return "중립"


class DailyHistoryFetcher:
    """국내 지수(네이버 fchart)·해외 지표(Yahoo chart) 일봉 수집기."""

    _NAVER_URL = "https://fchart.stock.naver.com/sise.nhn"
    _YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    _HEADERS = {"User-Agent": "Mozilla/5.0"}

    def __init__(self, timeout_sec: float = 15.0, yahoo_range: str = "7y"):
        self._timeout_sec = timeout_sec
        self._yahoo_range = yahoo_range

    async def fetch_kr_index(self, symbol: str, count: int) -> List[dict]:
        async with httpx.AsyncClient(timeout=self._timeout_sec, headers=self._HEADERS) as client:
            resp = await client.get(
                self._NAVER_URL,
                params={"symbol": symbol, "timeframe": "day", "count": count, "requestType": 0},
            )
            resp.raise_for_status()
            return parse_naver_fchart(resp.content.decode("euc-kr", errors="replace"))

    async def fetch_us(self, symbol: str) -> List[dict]:
        async with httpx.AsyncClient(timeout=self._timeout_sec, headers=self._HEADERS) as client:
            resp = await client.get(
                self._YAHOO_URL.format(symbol=symbol),
                params={"range": self._yahoo_range, "interval": "1d"},
            )
            resp.raise_for_status()
            return parse_yahoo_chart(resp.text)


class PremarketDirectionService:
    """장전 1회 양·음봉과 전일 대비 종가 방향 확률을 각각 계산·보관·알림한다."""

    def __init__(
        self,
        *,
        fetcher,
        market_clock,
        notification_service=None,
        logger: Optional[logging.Logger] = None,
        history_count: int = 1500,
        min_train_days: int = 250,
        history_path: Union[str, Path, None] = None,
    ):
        self._fetcher = fetcher
        self._clock = market_clock
        self._notification_service = notification_service
        self._logger = logger or logging.getLogger(__name__)
        self._history_count = history_count
        self._min_train_days = min_train_days
        # None 이면 적중 기록을 남기지 않는다(테스트·스크립트용). 운영 조립은 경로를 넘긴다.
        self._history_path = Path(history_path) if history_path is not None else None
        self._latest: Optional[dict] = None

    def get_latest(self) -> Optional[dict]:
        return self._latest

    async def get_or_compute(self) -> dict:
        """오늘 성공 결과가 없으면 알림 없이 계산한다 (장전 태스크를 놓친 날의 웹 조회용)."""
        today = self._clock.get_current_kst_time().strftime("%Y-%m-%d")
        latest = self._latest
        if latest is not None and latest.get("date") == today and latest.get("ok"):
            return self._latest
        return await self.refresh(notify=False)

    async def refresh(self, notify: bool = True) -> dict:
        today = self._clock.get_current_kst_time().strftime("%Y-%m-%d")
        try:
            result = await self._compute(today)
        except Exception as exc:
            result = {"ok": False, "date": today, "error": _describe_error(exc)}
            self._logger.warning("장전 양음봉·종가 방향 예측 실패: %s", result["error"])
        self._latest = result
        if notify and result["ok"] and self._notification_service is not None:
            await self._notify(result)
        return result

    async def _compute(self, today: str) -> dict:
        specs = list(dict.fromkeys(f for t in TARGETS for f in t.features))
        us = {f.name: await self._fetcher.fetch_us(f.symbol) for f in specs}
        empty = [f.name for f in specs if not us[f.name]]
        if empty:
            raise ValueError(f"시세 수신 실패: {', '.join(empty)}")

        targets, feats, base_date, actuals, body_actuals = [], {}, None, {}, {}
        for target in TARGETS:
            rows = await self._fetcher.fetch_kr_index(target.code, self._history_count)
            kr = [r for r in rows if r["date"] < today]
            if not kr:
                raise ValueError(f"시세 수신 실패: {target.code}")
            names = [f.name for f in target.features]
            prediction, target_feats = self._predict(kr, {n: us[n] for n in names}, names, today)
            targets.append({"code": target.code, "name": target.name, **prediction})
            feats.update(target_feats)
            actuals[target.code] = actual_changes(rows, before=today)
            body_actuals[target.code] = actual_body_changes(rows, before=today)
            base_date = base_date or kr[-1]["date"]
        self._track(today, targets, actuals, body_actuals)
        return {
            "ok": True,
            "date": today,
            "base_date": base_date,
            "targets": targets,
            "features": [{"name": f.name, "label": f.label, "value": feats[f.name]} for f in specs],
        }

    def _track(
        self,
        today: str,
        targets: List[dict],
        actuals: Dict[str, Dict[str, float]],
        body_actuals: Dict[str, Dict[str, float]],
    ) -> None:
        """오늘 두 예측을 기록하고 지난 예측의 실제 결과와 별도 적중 요약을 붙인다."""
        history = self._load_history()
        for date, record in history.items():
            for code, entry in record.items():
                if entry.get("actual_change") is None and date in actuals.get(code, {}):
                    entry["actual_change"] = actuals[code][date]
                if (entry.get("body_probability") is not None
                        and entry.get("actual_body_change") is None
                        and date in body_actuals.get(code, {})):
                    entry["actual_body_change"] = body_actuals[code][date]
        today_record = history.setdefault(today, {})
        for t in targets:
            entry = today_record.setdefault(
                t["code"], {"actual_change": None, "actual_body_change": None},
            )
            entry["probability"] = t["probability"]
            entry["body_probability"] = t["body_probability"]
        self._save_history(history)

        for t in targets:
            t["track"] = summarize_track(history, t["code"])
            t["body_track"] = summarize_body_track(history, t["code"])
            past = [d for d in sorted(history, reverse=True)
                    if d < today and history[d].get(t["code"], {}).get("actual_change") is not None]
            t["previous"] = {"date": past[0], **history[past[0]][t["code"]]} if past else None

    def _load_history(self) -> Dict[str, dict]:
        if self._history_path is None or not self._history_path.exists():
            return {}
        try:
            data = json.loads(self._history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self._logger.warning("장전 방향 예측 기록 로드 실패 (%s): %s", self._history_path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _save_history(self, history: Dict[str, dict]) -> None:
        if self._history_path is None:
            return
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history_path.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as exc:
            self._logger.warning("장전 방향 예측 기록 저장 실패 (%s): %s", self._history_path, exc)

    def _predict(self, kr: List[dict], us: Dict[str, List[dict]], names: List[str], today: str):
        train = [r for r in build_dataset(kr, us) if all(r.get(n) is not None for n in names)]
        if len(train) < self._min_train_days:
            raise ValueError(f"학습 데이터 부족: {len(train)}일 < {self._min_train_days}일")
        feats = build_forecast_features(us, prev_date=kr[-1]["date"], date=today)
        missing = [n for n in names if feats.get(n) is None]
        if missing:
            raise ValueError(f"오늘 지표 누락: {', '.join(missing)}")

        def predict(label: str) -> float:
            mu, sd, w, b = _fit_logit(
                [[r[n] for n in names] for r in train], [r[label] for r in train],
            )
            z = [(feats[n] - mu[i]) / sd[i] for i, n in enumerate(names)]
            return 1.0 / (1.0 + math.exp(-(sum(w[i] * z[i] for i in range(len(names))) + b)))

        probability = predict("up_close")
        body_probability = predict("up_body")
        prediction = {
            "probability": probability,
            "outlook": _outlook(probability),
            "body_probability": body_probability,
            "body_outlook": _body_outlook(body_probability),
            "train_days": len(train),
        }
        return prediction, feats

    async def report_close(self, date: str, notify: bool = True) -> dict:
        """장 마감 후: date 의 예측을 확정 종가로 채점하고 결과·누적 성적을 알린다."""
        try:
            result = await self._score_close(date)
        except Exception as exc:
            error = _describe_error(exc)
            self._logger.warning("장마감 종가 예측 채점 실패 (%s): %s", date, error)
            return {"ok": False, "date": date, "error": error}
        if notify and result.get("targets") and self._notification_service is not None:
            await self._notify_result(result)
        return result

    async def _score_close(self, date: str) -> dict:
        history = self._load_history()
        record = history.get(date)
        if not record:
            return {"ok": True, "date": date, "skipped": "no_prediction"}
        # 장 마감 후라 date 당일 종가도 확정값이다 — 다음 날 미만까지 채운다.
        until = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        names = {t.code: t.name for t in TARGETS}
        pending = []
        for code, entry in record.items():
            rows = await self._fetcher.fetch_kr_index(code, 30)
            actual = actual_changes(rows, before=until)
            body_actual = actual_body_changes(rows, before=until)
            for d, rec in history.items():
                if code in rec and rec[code].get("actual_change") is None and d in actual:
                    rec[code]["actual_change"] = actual[d]
                if (code in rec and rec[code].get("body_probability") is not None
                        and rec[code].get("actual_body_change") is None and d in body_actual):
                    rec[code]["actual_body_change"] = body_actual[d]
            if entry.get("actual_change") is None:
                pending.append(code)
            if entry.get("body_probability") is not None and entry.get("actual_body_change") is None:
                pending.append(code)
        self._save_history(history)
        if pending:
            raise ValueError(f"확정 종가 미수신: {', '.join(dict.fromkeys(pending))}")
        targets = []
        for code, entry in record.items():
            direction_hit = _is_hit(entry["probability"], entry["actual_change"])
            target = {
                "code": code,
                "name": names.get(code, code),
                "probability": entry["probability"],
                "actual_change": entry["actual_change"],
                "hit": direction_hit,  # 기존 소비자 호환
                "direction_hit": direction_hit,
                "track": summarize_track(history, code),
                "total": summarize_track(history, code, window=None),
            }
            if entry.get("body_probability") is not None:
                body_hit = _is_body_hit(entry["body_probability"], entry["actual_body_change"])
                target.update({
                    "body_probability": entry["body_probability"],
                    "actual_body_change": entry["actual_body_change"],
                    "body_hit": body_hit,
                    "body_track": summarize_body_track(history, code),
                    "body_total": summarize_body_track(history, code, window=None),
                    "score": {"hits": int(direction_hit) + int(body_hit), "total": 2},
                })
            else:
                target["score"] = {"hits": int(direction_hit), "total": 1}
            targets.append(target)
        return {"ok": True, "date": date, "targets": targets}

    async def _notify_result(self, result: dict) -> None:
        def rate(t):
            return f"{t['hits']}/{t['n']}({t['hits'] / t['n'] * 100:.0f}%)" if t["n"] else "0/0"

        lines = []
        for t in result["targets"]:
            if "body_probability" in t:
                actual_body = "양봉" if t["actual_body_change"] > 0 else "음봉" if t["actual_body_change"] < 0 else "도지"
                lines.append(
                    f"• {t['name']} {describe_body(t['body_probability'])} → 실제 "
                    f"{actual_body}({t['actual_body_change'] * 100:+.2f}%) "
                    f"{'적중' if t['body_hit'] else '실패'}\n"
                    f"  전일 대비 {describe(t['probability'])} → 실제 {t['actual_change'] * 100:+.2f}% "
                    f"{'적중' if t['direction_hit'] else '실패'} — {t['score']['hits']}/{t['score']['total']}"
                )
            else:
                lines.append(
                    f"• {t['name']} 전일 대비 {describe(t['probability'])} → 실제 "
                    f"{t['actual_change'] * 100:+.2f}% {'적중' if t['direction_hit'] else '실패'}"
                )
        lines.append("• 누적: " + " · ".join(
            (
                f"{t['name']} 양음봉 전체 {rate(t['body_total'])}, 전일방향 전체 {rate(t['total'])}"
                if "body_total" in t else f"{t['name']} 전일방향 전체 {rate(t['total'])}"
            ) for t in result["targets"]
        ))
        hits = sum(t["score"]["hits"] for t in result["targets"])
        total = sum(t["score"]["total"] for t in result["targets"])
        await self._notification_service.emit(
            category=NotificationCategory.STRATEGY,
            level=NotificationLevel.INFO,
            title=f"장마감 양음봉·종가 예측 결과 ({result['date']}) — {hits}/{total} 적중",
            message="\n".join(lines),
            metadata={
                "force_external": True,
                "event": "premarket_direction_result",
                "hits": {
                    t["code"]: {
                        "body": t.get("body_hit"),
                        "direction": t["direction_hit"],
                    } for t in result["targets"]
                },
            },
        )

    async def _notify(self, result: dict) -> None:
        lines = [f"오늘 예측 ({result['base_date']} 종가 기준)"]
        for t in result["targets"]:
            lines.append(
                f"• {t['name']}\n"
                f"  양음봉: {describe_body(t['body_probability'])} ({t['body_outlook']})\n"
                f"  전일 대비: {describe(t['probability'])} ({t['outlook']})"
            )
        lines.append("\n밤사이 입력 지표")
        lines.extend(
            "• " + " · ".join(f"{f['label']} {f['value'] * 100:+.2f}%" for f in group)
            for group in (result["features"][i:i + 3] for i in range(0, len(result["features"]), 3))
        )
        previous = [t for t in result["targets"] if t.get("previous")]
        if previous:
            prev_date = previous[0]["previous"]["date"]
            lines.append(f"\n전 거래일 결과 ({prev_date})")
            for t in previous:
                past = t["previous"]
                if past.get("body_probability") is not None and past.get("actual_body_change") is not None:
                    actual_body = "양봉" if past["actual_body_change"] > 0 else "음봉" if past["actual_body_change"] < 0 else "도지"
                    lines.append(
                        f"• {t['name']}\n"
                        f"  양음봉: {describe_body(past['body_probability'])} → 실제 {actual_body} "
                        f"{'적중' if _is_body_hit(past['body_probability'], past['actual_body_change']) else '실패'}\n"
                        f"  전일 대비: {describe(past['probability'])} → 실제 "
                        f"{past['actual_change'] * 100:+.2f}% "
                        f"{'적중' if _is_hit(past['probability'], past['actual_change']) else '실패'}"
                    )
                else:
                    lines.append(
                        f"• {t['name']}: 전일 대비 {describe(past['probability'])} → 실제 "
                        f"{past['actual_change'] * 100:+.2f}% "
                        f"{'적중' if _is_hit(past['probability'], past['actual_change']) else '실패'}"
                    )
        tracked = [t for t in result["targets"] if t.get("track", {}).get("n") or t.get("body_track", {}).get("n")]
        if tracked:
            lines.append(f"\n최근 최대 {TRACK_WINDOW}회 예측 적중 (기록된 날 기준)")
            for t in tracked:
                body = t.get("body_track", {"hits": 0, "n": 0})
                lines.append(
                    f"• {t['name']}: 양음봉 {body['hits']}/{body['n']}일 · "
                    f"전일 대비 {t['track']['hits']}/{t['track']['n']}일"
                )
        message = "\n".join(lines) + (
            "\n※ 양음봉은 종가>시가, 전일 대비는 종가>전일종가 기준. 확률은 모델 추정치이며 각각 별도 모델·적중률. "
            "양음봉 예측은 과거 약 50% 수준이라 단독 진입 근거 아님"
        )
        await self._notification_service.emit(
            category=NotificationCategory.STRATEGY,
            level=NotificationLevel.INFO,
            title="장전 양음봉·종가 방향 예측",
            message=message,
            metadata={
                # 기본 route_levels 는 STRATEGY/info 를 외부로 보내지 않는다 — 마켓타이밍 알림처럼 강제 전송.
                "force_external": True,
                "event": "premarket_direction_forecast",
                "targets": {t["code"]: round(t["probability"], 4) for t in result["targets"]},
            },
        )
