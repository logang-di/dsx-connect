# dsx_connect/logging/destinations/console.py
import os, sys
from dsx_connect.superlog.core.destination import LogDestination, LEVEL_MAP
from dsx_connect.superlog.core.events import LogLevel

_LEVEL_FROM_ENV = {
    "DEBUG": LogLevel.DEBUG,
    "INFO": LogLevel.INFO,
    "WARNING": LogLevel.WARNING,
    "ERROR": LogLevel.ERROR,
    "CRITICAL": LogLevel.CRITICAL,
}


class ConsoleDestination(LogDestination):
    def __init__(
            self,
            formatter,
            name: str = "console",
            *,
            min_level: LogLevel | None = None,
            env_var: str = "LOG_LEVEL",
            honor_env: bool = True,
            announce: bool = True,
            **kwargs,
    ):
        # Resolve min_level from env unless explicitly provided
        if min_level is None and honor_env:
            env_level = os.getenv(env_var, "INFO").upper()
            min_level = _LEVEL_FROM_ENV.get(env_level, LogLevel.INFO)
        elif min_level is None:
            env_level = "INFO"
            min_level = LogLevel.INFO
        else:
            env_level = str(min_level.name)

        super().__init__(formatter, name, is_sync=True, min_level=min_level, **kwargs)

        # Optional one-time banner so you see the level without extra app code
        if announce:
            banner = f"Log level set to {env_level}"
            # format via formatter so time/filename/color match normal output
            from dsx_connect.superlog.core.events import LogEvent
            event = LogEvent(message=banner, severity=LogLevel.INFO)
            payload = self.format(event)
            self.write_sync(LEVEL_MAP[LogLevel.INFO], payload, event)

    def write_sync(self, level: int, payload: str, event) -> None:
        # Errors/warnings -> stderr; others -> stdout
        sev = event.severity
        stream = sys.stderr if sev in (LogLevel.WARNING, LogLevel.ERROR, LogLevel.CRITICAL) else sys.stdout
        stream.write(payload + "\n")
        stream.flush()

    async def _write(self, level: int, payload: str, event) -> None:
        self.write_sync(level, payload, event)
