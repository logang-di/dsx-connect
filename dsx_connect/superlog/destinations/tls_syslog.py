# dsx_connect/logging/destinations/tls_syslog.py
import logging, socket, ssl, time
from typing import Optional, Tuple

# Map stdlib levels to syslog severities (lower is more severe)
SYSLOG_SEVERITY = {
    logging.CRITICAL: 2,  # crit
    logging.ERROR: 3,  # err
    logging.WARNING: 4,  # warning
    logging.INFO: 6,  # info
    logging.DEBUG: 7,  # debug
}
# Default facility (user=1). PRI = facility*8 + severity
DEFAULT_FACILITY = 1


class TlsSyslogHandler(logging.Handler):
    """
    Minimal RFC5425 TLS syslog handler with RFC6587 octet-counting framing.
    Assumes record.getMessage() is already RFC5424-formatted (you handle formatting).
    """

    def __init__(
            self,
            address: Tuple[str, int],
            *,
            ssl_context: Optional[ssl.SSLContext] = None,
            server_hostname: Optional[str] = None,  # SNI
            timeout: float = 5.0,
            facility: int = DEFAULT_FACILITY,
            reconnect_backoff: float = 1.0,
            max_backoff: float = 30.0,
    ):
        super().__init__()
        self.address = address
        self.timeout = timeout
        self.facility = facility
        self.reconnect_backoff = reconnect_backoff
        self.max_backoff = max_backoff
        self._ctx = ssl_context or self._default_context()
        self._server_hostname = server_hostname or address[0]
        self._sock: Optional[ssl.SSLSocket] = None
        self._backoff = self.reconnect_backoff

    def _default_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def _connect(self) -> None:
        # Close any existing socket
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

        raw = socket.create_connection(self.address, timeout=self.timeout)
        self._sock = self._ctx.wrap_socket(raw, server_hostname=self._server_hostname)
        self._sock.settimeout(self.timeout)
        self._backoff = self.reconnect_backoff  # reset backoff on success

    def _send(self, payload: bytes) -> None:
        if not self._sock:
            self._connect()
        try:
            # RFC6587 octet-counting framing: "<len> <msg>"
            frame = str(len(payload)).encode("ascii") + b" " + payload
            self._sock.sendall(frame)
        except Exception:
            # reconnect and retry once
            try:
                self._connect()
                frame = str(len(payload)).encode("ascii") + b" " + payload
                self._sock.sendall(frame)
            except Exception as e:
                # exponential backoff to avoid hot loops
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, self.max_backoff)
                raise e

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)  # usually just "%(message)s"
            data = msg.encode("utf-8", errors="replace")
            self._send(data)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
        finally:
            super().close()
