import time
import logging
import os
from contextlib import contextmanager, asynccontextmanager
from typing import Optional
from core.logger import get_performance_logger

try:
    import pyinstrument
    HAS_PYINSTRUMENT = True
except ImportError:
    HAS_PYINSTRUMENT = False


class PerformanceManager:
    """
    시스템 전반의 성능 측정 및 로깅을 담당하는 클래스.
    - 타이머: 주요 함수별 동작 시간을 측정 (start_timer / log_timer)
    - 프로파일링: Pyinstrument 기반 병목 구간 분석 (profile / profile_async)
    """
    PROFILE_OUTPUT_DIR = "logs/profile"

    def __init__(self, logger: Optional[logging.Logger] = None, enabled: bool = False, threshold: float = 0.0):
        self.logger = logger if logger else get_performance_logger()
        self.enabled = enabled
        self.threshold = threshold

    # ── 타이머 (기존) ──

    def start_timer(self) -> float:
        """타이머 시작 (현재 시간 반환). 비활성화 시 0.0 반환."""
        return time.time() if self.enabled else 0.0

    def log_timer(self, name: str, start_time: float, extra_info: str = "", threshold: Optional[float] = None):
        """
        시작 시간으로부터 현재까지의 경과 시간을 로깅.
        start_time이 0.0이면(비활성) 무시.
        threshold가 제공되면 인스턴스 기본값 대신 사용.
        """
        if not self.enabled or start_time == 0.0:
            return

        duration = time.time() - start_time

        # 호출 시 지정한 threshold가 있으면 우선 사용, 없으면 기본값 사용
        limit = threshold if threshold is not None else self.threshold

        if duration < limit:
            return

        msg = f"[Performance] {name}: {duration:.4f}s"
        if extra_info:
            msg += f" ({extra_info})"

        self.logger.info(msg)

    # ── Pyinstrument 프로파일링 ──

    def _check_pyinstrument(self):
        if not HAS_PYINSTRUMENT:
            self.logger.warning("[Profile] pyinstrument가 설치되어 있지 않습니다. pip install pyinstrument")
            return False
        return True

    def _save_profile_result(self, profiler: "pyinstrument.Profiler", name: str, save_html: bool):
        """프로파일 결과를 로그 출력 및 HTML 저장."""
        text_output = profiler.output_text(unicode=True, color=False)
        self.logger.info(f"[Profile] {name}\n{text_output}")

        if save_html:
            os.makedirs(self.PROFILE_OUTPUT_DIR, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            safe_name = name.replace(" ", "_").replace("/", "_")
            filepath = os.path.join(self.PROFILE_OUTPUT_DIR, f"{safe_name}_{timestamp}.html")
            html_output = profiler.output_html()
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_output)
            self.logger.info(f"[Profile] HTML 저장: {filepath}")

    @contextmanager
    def profile(self, name: str = "profile", save_html: bool = True):
        """
        동기 코드 블록의 병목 구간을 분석하는 컨텍스트 매니저.

        사용 예:
            with pm.profile("주문_처리"):
                execute_order(...)
        """
        if not self.enabled or not self._check_pyinstrument():
            yield
            return

        profiler = pyinstrument.Profiler()
        profiler.start()
        try:
            yield profiler
        finally:
            profiler.stop()
            self._save_profile_result(profiler, name, save_html)

    @asynccontextmanager
    async def profile_async(self, name: str = "profile", save_html: bool = True):
        """
        비동기 코드 블록의 병목 구간을 분석하는 컨텍스트 매니저.

        사용 예:
            async with pm.profile_async("API_호출_분석"):
                await fetch_stock_data(...)
        """
        if not self.enabled or not self._check_pyinstrument():
            yield
            return

        profiler = pyinstrument.Profiler(async_mode="enabled")
        profiler.start()
        try:
            yield profiler
        finally:
            profiler.stop()
            self._save_profile_result(profiler, name, save_html)