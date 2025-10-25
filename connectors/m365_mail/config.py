from pydantic import Field
from connectors.framework.base_config import BaseConnectorConfig


class M365MailConnectorConfig(BaseConnectorConfig):
    name: str = 'm365-mail-connector'

    # Graph auth (client credentials)
    tenant_id: str | None = Field(default=None, description="Azure AD tenant ID")
    client_id: str | None = Field(default=None, description="App registration (client ID)")
    client_secret: str | None = Field(default=None, description="Client secret (do not persist)")
    authority: str = Field(default="https://login.microsoftonline.com", description="OAuth authority")

    # Scope of mailboxes (initial, explicit list or comma‑separated)
    mailbox_upns: str | None = Field(default=None, description="Comma‑separated UPNs of target mailboxes")

    # Processing policies
    max_attachment_bytes: int = Field(default=50 * 1024 * 1024, description="Max attachment size to process")
    handle_reference_attachments: bool = Field(default=False, description="Download and scan cloud attachments")
    enable_actions: bool = Field(default=False, description="Allow move/delete/tag on malicious verdicts")
    client_state: str | None = Field(default=None, description="Optional clientState to verify on webhook deliveries")
    delta_run_interval_seconds: int = Field(default=600, description="Interval for delta query backfill (seconds)")
    # Action customization
    action_move_folder: str | None = Field(default=None, description="Folder display name to move malicious messages (e.g., 'Quarantine')")
    subject_tag_prefix: str | None = Field(default=None, description="Prefix to prepend to subject on malicious (e.g., '[Malicious] ') ")
    banner_html: str | None = Field(default=None, description="Optional HTML banner to prepend when stripping attachments")

    class Config:
        env_prefix = "DSXCONNECTOR_"


config = M365MailConnectorConfig()
