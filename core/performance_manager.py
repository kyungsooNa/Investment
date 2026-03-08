import time
import logging
from typing import Optional
from core.logger import get_performance_logger

class PerformanceManager:
    """
    시스템 전반의 성능 측정 및 로깅을 담당하는 클래스.
    주요 함수별 동작 시간을 측정하고 분석할 수 있도록 지원합니다.
    """
    def __init__(self, logger: Optional[logging.Logger] = None, enabled: bool = False, threshold: float = 0.0):
        self.logger = logger if logger else get_performance_logger()
        self.enabled = enabled
        self.threshold = threshold

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