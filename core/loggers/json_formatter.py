import logging
import json

class JsonFormatter(logging.Formatter):
    """
    로그 레코드를 JSON 형식으로 변환하는 포맷터.
    """
    def format(self, record):
        log_object = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
        }
        # message가 dict 형태이면, 그대로 data 필드로 추가
        if isinstance(record.msg, dict):
            log_object["data"] = record.msg
        else:
            log_object["message"] = record.getMessage()

        # 예외 정보가 있으면 추가
        if record.exc_info:
            log_object['exc_info'] = self.formatException(record.exc_info)

        return json.dumps(log_object, ensure_ascii=False, default=str)
