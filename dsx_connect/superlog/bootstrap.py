from functools import lru_cache

from dsx_connect.superlog.core.chain import LogChain
from dsx_connect.superlog.destinations.console import ConsoleDestination
from dsx_connect.superlog.formatters.color_console import ConsoleColorFormatter


@lru_cache(maxsize=1)
def console_logger(name: str = "console") -> LogChain:
    """
    Return a singleton LogChain configured for console output.
    - Uses ConsoleDestination (sync fast-path)
    - Honors LOG_LEVEL env automatically
    - Prints 'Log level set to ...' once on first creation
    """
    chain = LogChain(name)
    chain.add_destination(
        ConsoleDestination(
            formatter=ConsoleColorFormatter(),
            name="dsx-connect console",
            # min_level is picked up from LOG_LEVEL env inside ConsoleDestination;
            # you can still override by passing min_level=LogLevel.DEBUG, etc.
        )
    )
    return chain

dsx_logging = console_logger