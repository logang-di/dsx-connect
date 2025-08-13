# dsx_connect/logging_setup.py
from typing import Optional
import os

# Framework imports (matching the files you added)
from dsx_connect.superlog.core.chain import LogChain
from dsx_connect.superlog.core.events import LogEvent, LogLevel
from dsx_connect.superlog.destinations.console import ConsoleDestination
# from dsx_connect.logging.destinations.syslog import SyslogDestination, SyslogTransport
# from dsx_connect.logging.formatters.syslog import SyslogFormatter
from dsx_connect.superlog.destinations.azure_sentinel import AzureSentinelDestination
from dsx_connect.superlog.destinations.aws_cloudwatch import CloudWatchDestination

from dsx_connect.superlog.formatters.color_console import ConsoleColorFormatter
from dsx_connect.superlog.formatters.json import JSONFormatter

def build_operational_chain() -> LogChain:
    chain = LogChain("operational")

    # Console destination — parity with your current colorized formatting
    console_formatter = ConsoleColorFormatter()
    console_dest = ConsoleDestination(
        formatter=console_formatter,
        name="dsx-connect console"
    )
    chain.add_destination(console_dest)

    # # Syslog destination (RFC5424, UDP by default)
    # syslog_host = os.getenv("SYSLOG_HOST", "syslog")
    # syslog_port = int(os.getenv("SYSLOG_PORT", "514"))
    #
    # syslog_formatter = SyslogFormatter(
    #     app_name=os.getenv("APP_NAME", "dsx-connect"),
    #     use_rfc5424=True,
    #     include_structured_data=True,
    # )
    # syslog_dest = SyslogDestination(
    #     formatter=syslog_formatter,
    #     name="syslog",
    #     address=(syslog_host, syslog_port),
    #     transport=SyslogTransport.UDP,
    #     min_level=LogLevel.INFO,  # INFO+ to syslog by default
    # )
    # chain.add_destination(syslog_dest)

    return chain

#
# def build_event_chain() -> LogChain:
#     chain = LogChain("event")
#
#     # Example: Azure Sentinel via DCR ingestion (KQL table/stream)
#     dce = os.getenv("AZURE_DCE_ENDPOINT")
#     dcr_id = os.getenv("AZURE_DCR_ID")
#     stream = os.getenv("AZURE_DCR_STREAM", "DSXEvents_CL")
#
#     if dce and dcr_id:
#         sentinel_dest = AzureSentinelDestination(
#             formatter=JsonFormatter(compact=True),  # JSON payloads to Sentinel
#             name="sentinel",
#             data_collection_endpoint=dce,
#             data_collection_rule_id=dcr_id,
#             stream_name=stream,
#             batch_size=int(os.getenv("AZURE_BATCH_SIZE", "50")),
#             batch_timeout=int(os.getenv("AZURE_BATCH_TIMEOUT", "5")),
#             min_level=LogLevel.EVENT,  # EVENTS only
#         )
#         chain.add_destination(sentinel_dest)
#
#     # Example: CloudWatch (optional)
#     if os.getenv("CLOUDWATCH_LOG_GROUP"):
#         cw = WatchtowerDestination(
#             formatter=JsonFormatter(compact=True),
#             name="cloudwatch",
#             log_group=os.getenv("CLOUDWATCH_LOG_GROUP"),
#             log_stream=os.getenv("CLOUDWATCH_LOG_STREAM", "dsx-events"),
#             region=os.getenv("AWS_REGION", "us-east-1"),
#             min_level=LogLevel.EVENT,
#         )
#         chain.add_destination(cw)
#
#     return chain
#
#
# If you want a global registry/helper
_CHAINS = {}


def init_log_chains():
    global _CHAINS
    _CHAINS = {
        "operational": build_operational_chain(),
        "event": build_event_chain(),
    }
    return _CHAINS


def get_chain(name: str) -> Optional[LogChain]:
    return _CHAINS.get(name)


def close_log_chains():
    # Close destinations asynchronously
    import asyncio
    async def _close():
        for chain in _CHAINS.values():
            await chain.close()

    asyncio.create_task(_close())


operational_chain = build_operational_chain()
operational_chain.info("Hello, world")
operational_chain.error("oops")
operational_chain.warning("maybe")
