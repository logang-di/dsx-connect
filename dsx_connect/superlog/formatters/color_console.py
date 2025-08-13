# dsx_connect/logging/formatters/console_color.py
import logging

from dsx_connect.superlog.core.formatter import LogFormatter

try:
    import colorlog
except ImportError as e:
    raise ImportError("Install colorlog: pip install colorlog") from e

from dsx_connect.superlog.core.events import LogEvent
from dsx_connect.superlog.core.destination import LEVEL_MAP  # your base's map


class ConsoleColorFormatter(LogFormatter):
    def __init__(
            self,
            default_format='%(log_color)s%(asctime)s %(levelname)-8s %(filename)-20s: %(message)s',
            error_format='%(log_color)s%(asctime)s %(levelname)-8s %(filename)-20s:%(lineno)d: %(message)s',
            log_colors=None,
    ):
        self.log_colors = log_colors or {
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'bold_red',
            'CRITICAL': 'bold_red,bg_white',
        }
        self._default = colorlog.ColoredFormatter(default_format, log_colors=self.log_colors)
        self._error = colorlog.ColoredFormatter(error_format, log_colors=self.log_colors)

    def format(self, event: LogEvent) -> str:
        level = LEVEL_MAP.get(event.severity, logging.INFO)
        use = self._error if level >= logging.ERROR else self._default
        # Build a synthetic LogRecord so colorlog can fill %(asctime)s, etc.
        filename = getattr(event, "source", "unknown.py")
        lineno = getattr(event, "lineno", 0)
        record = logging.LogRecord(
            name="dsx-console", level=level, pathname=filename, lineno=lineno,
            msg=getattr(event, "message", ""), args=(), exc_info=None
        )
        return use.format(record)
