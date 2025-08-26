import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import importlib

from shared.dsx_logging import dsx_logging
from connectors.sharepoint.config import SharepointConnectorConfig


GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class SharePointClient:
    """
    Minimal Microsoft Graph client for SharePoint drive operations.

    - Auth: MSAL client credentials flow
    - Drive resolution: resolves site_id from hostname/site_path and picks default drive or by name
    """

    def __init__(self, cfg: SharepointConnectorConfig):
        self._cfg = cfg
        self._authority = f"https://login.microsoftonline.com/{cfg.sp_tenant_id}"
        self._scopes = ["https://graph.microsoft.com/.default"]
        self._msal_app: Optional[msal.ConfidentialClientApplication] = None
        self._access_token: Optional[str] = None
        self._token_expiry_ts: float = 0.0
        self._site_id: Optional[str] = None
        self._drive_id: Optional[str] = None
        # httpx verify option
        self._verify: httpx._types.VerifyTypes
        if not self._cfg.sp_verify_tls:
            self._verify = False
        elif self._cfg.sp_ca_bundle:
            self._verify = self._cfg.sp_ca_bundle
        else:
            self._verify = True

    # ---------------------- auth ----------------------
    def _ensure_msal_app(self):
        if self._msal_app is None:
            msal = importlib.import_module("msal")
            self._msal_app = msal.ConfidentialClientApplication(
                self._cfg.sp_client_id,
                authority=self._authority,
                client_credential=self._cfg.sp_client_secret,
            )

    async def _get_token(self) -> str:
        # naive in-memory caching
        now = time.time()
        if self._access_token and now < (self._token_expiry_ts - 60):  # 60s early refresh window
            return self._access_token
        self._ensure_msal_app()
        # MSAL is sync; run in thread to avoid blocking event loop
        def _acquire():
            return self._msal_app.acquire_token_for_client(scopes=self._scopes)

        result = await asyncio.to_thread(_acquire)
        if "access_token" not in result:
            raise RuntimeError(f"Failed to acquire token: {result.get('error_description') or result}")
        self._access_token = result["access_token"]
        # expires_in is seconds from now
        self._token_expiry_ts = now + float(result.get("expires_in", 3600))
        return self._access_token

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(verify=self._verify, timeout=30.0)

    async def _headers(self) -> Dict[str, str]:
        tok = await self._get_token()
        return {"Authorization": f"Bearer {tok}"}

    # ---------------------- discovery ----------------------
    async def _ensure_site_and_drive(self):
        if self._site_id and self._drive_id:
            return
        # Resolve site
        async with await self._client() as client:
            h = await self._headers()
            site_url = (
                f"{GRAPH_BASE}/sites/{self._cfg.sp_hostname}:/sites/{self._cfg.sp_site_path}?$select=id,webUrl,displayName"
            )
            resp = await client.get(site_url, headers=h)
            resp.raise_for_status()
            site = resp.json()
            self._site_id = site["id"]

            # Resolve drive (default or by name)
            if self._cfg.sp_drive_name:
                drives_url = f"{GRAPH_BASE}/sites/{self._site_id}/drives"
                dresp = await client.get(drives_url, headers=h)
                dresp.raise_for_status()
                drives = dresp.json().get("value", [])
                match = next((d for d in drives if d.get("name") == self._cfg.sp_drive_name), None)
                if not match:
                    raise RuntimeError(f"Drive named '{self._cfg.sp_drive_name}' not found on site.")
                self._drive_id = match["id"]
            else:
                # default drive
                ddef = await client.get(f"{GRAPH_BASE}/sites/{self._site_id}/drive", headers=h)
                ddef.raise_for_status()
                self._drive_id = ddef.json()["id"]

        dsx_logging.info(f"Resolved SharePoint site={self._site_id}, drive={self._drive_id}")

    # ---------------------- operations ----------------------
    async def list_files(self, path: str = "") -> List[Dict[str, Any]]:
        await self._ensure_site_and_drive()
        async with await self._client() as client:
            h = await self._headers()
            if path and path != "/":
                url = f"{GRAPH_BASE}/drives/{self._drive_id}/root:/{path.strip('/')}: /children".replace(" : ", ":")
            else:
                url = f"{GRAPH_BASE}/drives/{self._drive_id}/root/children"
            resp = await client.get(url, headers=h)
            resp.raise_for_status()
            return resp.json().get("value", [])

    async def iter_files_recursive(self, path: str = "") -> AsyncIterator[Dict[str, Any]]:
        stack = [path]
        while stack:
            current = stack.pop()
            items = await self.list_files(current)
            for it in items:
                yield it
                if it.get("folder"):
                    # enqueue subfolder path
                    name = it.get("name")
                    sub = f"{current.strip('/')}/{name}" if current else name
                    stack.append(sub)

    async def download_file(self, identifier: str) -> httpx.Response:
        """Download by item id (preferred) or path."""
        await self._ensure_site_and_drive()
        async with await self._client() as client:
            h = await self._headers()
            if "/" in identifier or ":" in identifier:
                # treat as path
                url = f"{GRAPH_BASE}/drives/{self._drive_id}/root:/{identifier.strip('/')}: /content".replace(" : ", ":")
            else:
                # treat as item id
                url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{identifier}/content"
            # return the full Response so caller can stream
            resp = await client.get(url, headers=h, follow_redirects=True)
            resp.raise_for_status()
            return resp

    async def upload_file(self, path: str, content: bytes) -> Dict[str, Any]:
        await self._ensure_site_and_drive()
        async with await self._client() as client:
            h = await self._headers()
            url = f"{GRAPH_BASE}/drives/{self._drive_id}/root:/{path.strip('/')}: /content".replace(" : ", ":")
            resp = await client.put(url, headers=h, content=content)
            resp.raise_for_status()
            return resp.json()

    async def delete_file(self, item_id: str) -> None:
        await self._ensure_site_and_drive()
        async with await self._client() as client:
            h = await self._headers()
            url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{item_id}"
            resp = await client.delete(url, headers=h)
            resp.raise_for_status()

    async def _get_item_by_path(self, path: str) -> Optional[Dict[str, Any]]:
        await self._ensure_site_and_drive()
        async with await self._client() as client:
            h = await self._headers()
            url = f"{GRAPH_BASE}/drives/{self._drive_id}/root:/{path.strip('/')}"
            resp = await client.get(url, headers=h)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def _create_folder(self, parent_path: str, name: str) -> Dict[str, Any]:
        await self._ensure_site_and_drive()
        async with await self._client() as client:
            h = await self._headers()
            parent_path_norm = parent_path.strip('/')
            base = f"{GRAPH_BASE}/drives/{self._drive_id}/root"
            if parent_path_norm:
                base = f"{base}:/{parent_path_norm}:"
            url = f"{base}/children"
            body = {"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "replace"}
            resp = await client.post(url, headers={**h, "Content-Type": "application/json"}, json=body)
            resp.raise_for_status()
            return resp.json()

    async def ensure_folder(self, folder_path: str) -> Dict[str, Any]:
        """Ensure a nested folder path exists; return its driveItem resource."""
        # Normalize, split into segments
        folder_path = folder_path.strip('/')
        if not folder_path:
            # root
            existing = await self._get_item_by_path("")
            if existing:
                return existing
        parts = [p for p in folder_path.split('/') if p]
        current_path = ""
        parent_path = ""
        for seg in parts:
            current_path = f"{current_path}/{seg}" if current_path else seg
            item = await self._get_item_by_path(current_path)
            if item is None:
                await self._create_folder(parent_path, seg)
            parent_path = current_path
        # return final
        item = await self._get_item_by_path(current_path)
        if item is None:
            raise RuntimeError(f"Failed to ensure folder: {folder_path}")
        return item

    async def resolve_item_id(self, identifier: str) -> str:
        """Accepts item ID or path; returns item ID."""
        if "/" in identifier or ":" in identifier:
            item = await self._get_item_by_path(identifier)
            if not item:
                raise RuntimeError(f"Item not found for path: {identifier}")
            return item["id"]
        return identifier

    async def move_file(self, identifier: str, dest_folder_path: str, new_name: Optional[str] = None) -> Dict[str, Any]:
        await self._ensure_site_and_drive()
        item_id = await self.resolve_item_id(identifier)
        dest_folder = await self.ensure_folder(dest_folder_path)
        parent_id = dest_folder["id"]
        body: Dict[str, Any] = {"parentReference": {"id": parent_id}}
        if new_name:
            body["name"] = new_name
        async with await self._client() as client:
            h = await self._headers()
            url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{item_id}"
            resp = await client.patch(url, headers={**h, "Content-Type": "application/json"}, json=body)
            resp.raise_for_status()
            return resp.json()

    async def test_connection(self) -> bool:
        try:
            await self._ensure_site_and_drive()
            # simple list at root
            await self.list_files("")
            return True
        except Exception as e:
            dsx_logging.warning(f"SharePoint repo check failed: {e}")
            return False
