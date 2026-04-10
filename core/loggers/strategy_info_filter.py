import logging

class StrategyInfoFilter(logging.Filter):
    """
    전략 로거(strategy.*)의 로그는 INFO 레벨 이상만 통과시키는 필터.
    통합 로그(debug.log)에 전략의 과도한 DEBUG 로그가 쌓이는 것을 방지함.
    """
    def filter(self, record):
        if record.name.startswith("strategy."):
            return record.levelno >= logging.INFO
        return True
