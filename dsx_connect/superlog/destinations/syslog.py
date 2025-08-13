# dsx_connect/logging/destinations/syslog.py
import logging, logging.handlers, socket
import ssl
from enum import Enum
from dsx_connect.superlog.core.destination import LogDestination
from dsx_connect.superlog.destinations.tls_syslog import TlsSyslogHandler


class SyslogTransport(Enum):
    UDP = "udp"  # RFC3164/5424 over UDP (514)
    TCP = "tcp"  # RFC3164/5424 over TCP (plain)
    TCP_TLS = "tcp_tls"  # RFC5425 over TLS (6514)


class SyslogDestination(LogDestination):
    def __init__(
            self,
            formatter,
            name: str = "syslog",
            address: tuple[str, int] = ("localhost", 514),
            transport: SyslogTransport = SyslogTransport.UDP,
            facility: int | None = None,
            ssl_context: ssl.SSLContext | None = None,
            server_hostname: str | None = None,
            **kwargs,
    ):
        super().__init__(formatter, name, **kwargs)
        self._logger = logging.getLogger("dsx-syslog")
        self._logger.setLevel(0)

        if transport is SyslogTransport.TCP_TLS:
            handler = TlsSyslogHandler(
                address=address,
                ssl_context=ssl_context,
                server_hostname=server_hostname or address[0],
            )
        else:
            socktype = socket.SOCK_DGRAM if transport is SyslogTransport.UDP else socket.SOCK_STREAM
            handler = logging.handlers.SysLogHandler(
                address=address,
                socktype=socktype,
                facility=facility or logging.handlers.SysLogHandler.LOG_USER,
            )

        handler.setFormatter(logging.Formatter("%(message)s"))  # payload already formatted
        self._logger.addHandler(handler)
        self._handler = handler

    async def _write(self, level: int, payload: str, event) -> None:
        self._logger.log(level, payload)

    async def close(self) -> None:
        try:
            self._handler.close()
        except Exception:
            pass
