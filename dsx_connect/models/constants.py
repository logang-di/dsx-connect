class DSXConnectAPIEndpoints:
    NOTIFICATIONS_SCAN_RESULT = "/dsx-connect/notifications/scan-result"
    NOTIFICATIONS_CONNECTOR_REGISTERED = "/dsx-connect/notifications/connector-registered"
    SCAN_REQUEST = "/dsx-connect/scan-request"
    SCAN_REQUEST_TEST = "/dsx-connect/test/scan-request"
    SCAN_RESULTS = "/dsx-connect/scan-results"
    SCAN_STATS = "/dsx-connect/scan-stats"
    CONNECTION_TEST = "/dsx-connect/test/connection"
    DSXA_CONNECTION_TEST = "/dsx-connect/test/dsxa-connection"
    CONFIG = "/dsx-connect/config"
    VERSION = "/version"
    LIST_CONNECTORS = "/dsx-connect/connectors"
    REGISTER_CONNECTORS = "/dsx-connect/connectors/register"
    UNREGISTER_CONNECTORS = "/dsx-connect/connectors/unregister/{connector_uuid}"
    INVOKE_FULLSCAN_CONNECTOR = "/dsx-connect/connectors/full_scan/{connector_uuid}"
    INVOKE_CONFIG_CONNECTOR = "/dsx-connect/connectors/config/{connector_uuid}"


class ConnectorEndpoints:
    READ_FILE = "/read_file"
    ITEM_ACTION = "/item_action"
    FULL_SCAN = "/full_scan"
    WEBHOOK_EVENT = "/webhook/event"
    REPO_CHECK = "/repo_check"
    CONFIG = "/config"
    HEARTBEAT = "/heartbeat"

