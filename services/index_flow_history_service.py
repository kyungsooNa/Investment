"""코스피/코스닥 투자자 순매수 시간차트용 표본 수집·조회.

KIS 시장별 투자자매매동향(FHPTJ04030000 포함)은 조회 시점의 누적값만 주고 시간별 이력이 없다.
그래서 장중에 누적 순매수를 주기적으로 표본으로 남겨 시간차트를 만든다.
재시작해도 당일 표본이 이어지도록 날짜별 JSON 으로 저장한다(서버가 꺼져 있던 구간은 공백).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from common.types import ErrorCode, ResCommonResponse


class IndexFlowHistoryService:
    INDEX_CODES = ("0001", "1001")
    DEFAULT_DATA_DIR = Path("data/index_flow")

    def __init__(self, *, stock_query_service, market_clock,
                 data_dir: Union[str, Path, None] = None, logger=None) -> None:
        self._query = stock_query_service
        self._market_clock = market_clock
        self._data_dir = Path(data_dir) if data_dir is not None else self.DEFAULT_DATA_DIR
        self._logger = logger or logging.getLogger(__name__)
        self._date: Optional[str] = None
        self._samples: Dict[str, List[dict]] = {}

    async def record_sample(self) -> None:
        now = self._market_clock.get_current_kst_time()
        date = now.strftime("%Y%m%d")
        time = now.strftime("%H%M")
        samples = self._samples_for(date)

        for code in self.INDEX_CODES:
            netbuy = await self._query.get_index_investor_netbuy(code)
            if not netbuy:
                continue
            points = samples.setdefault(code, [])
            # 같은 분의 재표본은 최신 누적값으로 교체한다.
            if points and points[-1]["time"] == time:
                points.pop()
            points.append({"time": time, **netbuy})

        self._save(date, samples)

    def get_history(self, index_code: str) -> ResCommonResponse:
        if index_code not in self.INDEX_CODES:
            msg = f"지원하지 않는 지수 코드: {index_code}"
            return ResCommonResponse(rt_cd=ErrorCode.INVALID_INPUT.value, msg1=msg, data=None)

        # 장 시작 전·휴장일에는 마지막으로 기록된 거래일을 보여준다.
        today = self._market_clock.get_current_kst_time().strftime("%Y%m%d")
        date = today
        points = self._samples_for(today).get(index_code) or []
        if not points:
            for path in sorted(self._data_dir.glob("*.json"), reverse=True):
                if path.stem >= today:
                    continue
                past = self._load(path.stem).get(index_code) or []
                if past:
                    date, points = path.stem, past
                    break

        return ResCommonResponse(
            rt_cd=ErrorCode.SUCCESS.value,
            msg1="지수 수급 시간별 조회 성공",
            data={"code": index_code, "date": date, "points": list(points)},
        )

    def _samples_for(self, date: str) -> Dict[str, List[dict]]:
        if self._date != date:
            self._date = date
            self._samples = self._load(date)
        return self._samples

    def _load(self, date: str) -> Dict[str, List[dict]]:
        path = self._data_dir / f"{date}.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            self._logger.warning(f"지수 수급 표본 로드 실패 ({path}): {e}")
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, date: str, samples: Dict[str, List[dict]]) -> None:
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            (self._data_dir / f"{date}.json").write_text(
                json.dumps(samples, ensure_ascii=False), encoding="utf-8",
            )
        except OSError as e:
            self._logger.warning(f"지수 수급 표본 저장 실패 ({date}): {e}")
