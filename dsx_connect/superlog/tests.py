from dsx_connect.superlog.formatters.console import StructuredConsoleFormatter
from dsx_connect.superlog.destinations.console import (
    create_console_destination
)
from dsx_connect.superlog.core.chain import get_default_log_chain


dsx_logging = get_default_log_chain()
dsx_logging.add_destination(create_console_destination(formatter=StructuredConsoleFormatter()))

dsx_logging.info("message")
dsx_logging.debug("message")

# during startup
from dsx_connect.superlog.core.chain import LogChain
from dsx_connect.superlog.destinations.console import ConsoleDestination
from dsx_connect.superlog.destinations.syslog import SyslogDestination
from dsx_connect.superlog.destinations.azure_sentinel import AzureSentinelDestination
from dsx_connect.superlog.core.events import LogEvent, LogLevel

operational_chain = LogChain("operational") \
    .add_destination(ConsoleDestination(min_level=LogLevel.DEBUG, color=True)) \
    .add_destination(SyslogDestination(host="syslog", port=514, min_level=LogLevel.INFO))

event_chain = LogChain("event") \
    .add_destination(AzureSentinelDestination(
    dce_endpoint=..., dcr_id=..., table_name="DSXEvents_CL",
    min_level=LogLevel.EVENT
))

# feels like stdlib logging:
operational_chain.info(f"dsx-connect version: {version.DSX_CONNECT_VERSION}")
operational_chain.debug(f"dsx-connect configuration: {config}")
operational_chain.info("dsx-connect startup completed.")

# emit a security event (rich object OR plain message)
event_chain.event(LogEvent.from_scan_result(scan_result, original_task_id, current_task_id))
# or:
event_chain.event(message="Object quarantined", severity=LogLevel.EVENT, custom_fields={"object": key})


#
# # Create destinations using proven libraries
# formatter = JSONFormatter(include_raw_data=False)
#
#
# from your_logging.destinations.syslog import (
#     create_syslog_destination, SyslogFacility
# )
# from your_logging.core.chain import get_default_log_chain
#
# # Simple UDP syslog
# syslog_dest = create_syslog_destination(
#     "udp://syslog.company.com:514",
#     name="main_syslog",
#     facility=SyslogFacility.LOCAL0
# )
#
# # TCP with TLS
# secure_syslog = create_syslog_destination(
#     "tcp+tls://secure-syslog.company.com:6514",
#     name="secure_syslog",
#     facility=SyslogFacility.AUTHPRIV
# )
#
# # Add to your log chain
# chain = get_default_log_chain()
# chain.add_destination(syslog_dest)
# chain.add_destination(secure_syslog)
#
# # Log events will now go to syslog
# await chain.log_event(LogEvent.malware_detected(
#     file_location="/path/to/malware.exe",
#     threat_name="Win32.Malware",
#     connector_name="endpoint_scanner"
# ))
#
#
# # Splunk HEC (using splunk-handler)
# splunk_dest = create_splunk_hec_destination(
#     host="splunk.company.com",
#     token="your-hec-token",
#     formatter=formatter,
#     index="security",
#     sourcetype="dsx:security:events"
# )
#
# # Azure Sentinel (using azure-monitor-ingestion)
# azure_dest = create_azure_sentinel_destination(
#     data_collection_endpoint="https://your-dce.ingest.monitor.azure.com",
#     data_collection_rule_id="dcr-12345678",
#     stream_name="Custom-DSXEvents_CL",
#     formatter=formatter
# )
#
# # Add to your chain
# chain = get_default_log_chain()
# chain.add_destination(splunk_dest)
# chain.add_destination(azure_dest)
#
