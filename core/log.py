import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name,
                "msg": record.getMessage()}
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
