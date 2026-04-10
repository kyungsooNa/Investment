from logging.handlers import QueueHandler

class DictPreservingQueueHandler(QueueHandler):
    def prepare(self, record):
        original_msg = record.msg
        record = super().prepare(record)
        if isinstance(original_msg, dict):
            record.msg = original_msg
        return record