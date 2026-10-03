"""기동 degrade 상태를 운영자 알림으로 남긴다 (todo M-12).

#976 이후 외부 의존(KRX 종목목록·KIS 토큰)이 실패해도 웹은 뜬다. 조용히 degrade 로
뜨면 비어 있는 유니버스로 전략이 도는 상태를 못 알아채므로, 빠진 의존을 운영자 알림
(대시보드 active alert + 텔레그램)으로 올리고 정상 기동 시에는 지난 기동의 알림을 해제한다.
"""
from __future__ import annotations

from common.operator_alert_types import AlertSource

STOCK_CODE_DB_KEY = "startup:stock_code_db"
BROKER_KEY = "startup:broker"


async def report_startup_health(operator_alert_service, stock_code_repository, *, services_ready: bool, logger=None) -> None:
    if operator_alert_service is None:
        return
    try:
        if getattr(stock_code_repository, "is_minimal_fallback", False):
            await operator_alert_service.report(
                AlertSource.STARTUP,
                STOCK_CODE_DB_KEY,
                "error",
                "종목코드 DB 최소 폴백으로 기동",
                "KRX 종목목록을 받지 못해 빈 종목코드 DB로 떴습니다. 유니버스·종목명 조회가 비어 있으니 종목코드 DB를 갱신한 뒤 재시작하세요.",
            )
        else:
            await operator_alert_service.resolve(AlertSource.STARTUP, STOCK_CODE_DB_KEY, "종목코드 DB 정상 로드")

        if not services_ready:
            await operator_alert_service.report(
                AlertSource.STARTUP,
                BROKER_KEY,
                "critical",
                "브로커 미연결 상태로 기동",
                "토큰 발급/브로커 초기화에 실패했습니다. 시세·주문·전략 서비스가 비활성입니다. 웹 UI 환경 전환으로 재시도하세요.",
            )
        else:
            await operator_alert_service.resolve(AlertSource.STARTUP, BROKER_KEY, "브로커 초기화 성공")
    except Exception as e:
        if logger:
            logger.warning(f"기동 상태 운영자 알림 기록 실패: {e}")
