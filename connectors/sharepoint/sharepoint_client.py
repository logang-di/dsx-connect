import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import importlib
from urllib.parse import urlparse, parse_qs, unquote

import msal

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
        self._claims_logged: bool = False
        # httpx verify option
        self._verify: httpx._types.VerifyTypes
        if not self._cfg.sp_verify_tls:
            self._verify = False
        elif self._cfg.sp_ca_bundle:
            self._verify = self._cfg.sp_ca_bundle
        else:
            self._verify = True
        # persistent async client for connection reuse
        self._client_session: Optional[httpx.AsyncClient] = None

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
        # Optionally log decoded claims once (never log the raw token)
        if self._cfg.sp_log_token_claims and not self._claims_logged:
            try:
                hdr, claims = self._decode_jwt(result["access_token"])  # type: ignore[arg-type]
                roles = claims.get("roles")
                dsx_logging.info(
                    f"Graph token claims: aud={claims.get('aud')} appid={claims.get('appid')} tid={claims.get('tid')} roles={roles}"
                )
                self._claims_logged = True
            except Exception:
                pass
        return self._access_token

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client_session is None:
            limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
            # Enable HTTP/2 for better multiplexing against Graph, reuse connections
            self._client_session = httpx.AsyncClient(verify=self._verify, timeout=30.0, limits=limits, http2=True)
        return self._client_session

    async def aclose(self):
        if self._client_session is not None:
            await self._client_session.aclose()
            self._client_session = None

    async def _headers(self) -> Dict[str, str]:
        tok = await self._get_token()
        return {"Authorization": f"Bearer {tok}"}

    # ---------------------- discovery ----------------------
    async def _ensure_site_and_drive(self):
        if self._site_id and self._drive_id:
            return
        # Resolve site
        client = await self._get_client()
        h = await self._headers()
        # Normalize site path (accept inputs like 'sites/MySite' or '/sites/MySite' and strip prefix)
        rel_site_path = (self._cfg.sp_site_path or "").strip("/")
        if rel_site_path.lower().startswith("sites/"):
            rel_site_path = rel_site_path.split("/", 1)[1]
        site_url = (
            f"{GRAPH_BASE}/sites/{self._cfg.sp_hostname}:/sites/{rel_site_path}?$select=id,webUrl,displayName"
        )
        resp = await client.get(site_url, headers=h)
        if resp.status_code >= 400:
            body = None
            try:
                body = resp.text
            except Exception:
                pass
            raise RuntimeError(
                f"Graph site lookup failed: {resp.status_code} host={self._cfg.sp_hostname} site_path={rel_site_path} body={body}"
            )
        site = resp.json()
        self._site_id = site["id"]

        # Resolve drive (default or by name)
        drive_name = (self._cfg.sp_drive_name or "").strip()
        # If user mistakenly put a folder path here, ignore and use default drive
        if drive_name and ("/" in drive_name or "\\" in drive_name):
            dsx_logging.warning(
                f"sp_drive_name looks like a path ('{drive_name}'); ignoring override and using default drive. "
                f"Use folder parameters (e.g., SP_TEST_FOLDER) for subfolders."
            )
            drive_name = ""

        if drive_name:
            drives_url = f"{GRAPH_BASE}/sites/{self._site_id}/drives"
            dresp = await client.get(drives_url, headers=h)
            if dresp.status_code >= 400:
                raise RuntimeError(f"Graph drives list failed: {dresp.status_code}")
            drives = dresp.json().get("value", [])

            def _norm(n: str | None) -> str:
                return (n or "").strip().lower().replace(" ", "")

            want = _norm(drive_name)
            match = next((d for d in drives if _norm(d.get("name")) == want), None)
            if not match and want in {"documents", "shareddocuments"}:
                # Treat 'Documents' and 'Shared Documents' as synonyms
                match = next((d for d in drives if _norm(d.get("name")) in {"documents", "shareddocuments"}), None)
            if not match:
                raise RuntimeError(f"Drive named '{self._cfg.sp_drive_name}' not found on site.")
            self._drive_id = match["id"]
        else:
            # default drive
            ddef = await client.get(f"{GRAPH_BASE}/sites/{self._site_id}/drive", headers=h)
            if ddef.status_code >= 400:
                raise RuntimeError(f"Graph default drive lookup failed: {ddef.status_code}")
            self._drive_id = ddef.json()["id"]

        dsx_logging.info(f"Resolved SharePoint site={self._site_id}, drive={self._drive_id}")

    # ---------------------- operations ----------------------
    async def list_files(self, path: str = "") -> List[Dict[str, Any]]:
        await self._ensure_site_and_drive()
        client = await self._get_client()
        h = await self._headers()
        # slim payload and prefer larger pages
        page_size = max(1, int(getattr(self._cfg, 'sp_graph_page_size', 200) or 200))
        headers = {**h, "Prefer": f"odata.maxpagesize={page_size}", "Accept": "application/json;odata.metadata=none"}
        select = "$select=id,name,folder,parentReference"
        if path and path != "/":
            from urllib.parse import quote
            encoded = quote(path.strip('/'))
            url = f"{GRAPH_BASE}/drives/{self._drive_id}/root:/{encoded}:/children?{select}&$top=200"
        else:
            url = f"{GRAPH_BASE}/drives/{self._drive_id}/root/children?{select}&$top=200"
        items: List[Dict[str, Any]] = []
        while url:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                detail = None
                try:
                    detail = resp.text
                except Exception:
                    pass
                raise RuntimeError(f"Graph list children failed: {resp.status_code} path={path} body={detail}")
            data = resp.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return items

    async def iter_files_recursive(self, path: str = "") -> AsyncIterator[Dict[str, Any]]:
        stack = [path.strip('/')]
        while stack:
            current = stack.pop()
            items = await self.list_files(current)
            for it in items:
                # attach a synthetic 'path' inside the drive to help UI display full paths
                name = it.get("name")
                rel_path = f"{current}/{name}".strip('/') if current else (name or "")
                if rel_path:
                    it = {**it, "path": rel_path}
                yield it
                if it.get("folder"):
                    # enqueue subfolder path
                    name = it.get("name")
                    sub = f"{current}/{name}".strip('/') if current else (name or "")
                    stack.append(sub)

    async def download_file(self, identifier: str) -> httpx.Response:
        """Download by item id (preferred) or path."""
        await self._ensure_site_and_drive()
        client = await self._get_client()
        h = await self._headers()
        if "/" in identifier or ":" in identifier:
            # treat as path
            from urllib.parse import quote
            encoded = quote(identifier.strip('/'))
            url = f"{GRAPH_BASE}/drives/{self._drive_id}/root:/{encoded}:/content"
        else:
            # treat as item id
            url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{identifier}/content"
        # return the full Response so caller can stream
        resp = await client.get(url, headers={**h, "Accept": "application/json;odata.metadata=none"}, follow_redirects=True)
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph download failed: {resp.status_code}")
        return resp

    async def upload_file(self, path: str, content: bytes) -> Dict[str, Any]:
        await self._ensure_site_and_drive()
        client = await self._get_client()
        h = await self._headers()
        from urllib.parse import quote
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/root:/{quote(path.strip('/'))}:/content"
        resp = await client.put(url, headers={**h, "Accept": "application/json;odata.metadata=none"}, content=content)
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph upload failed: {resp.status_code}")
        return resp.json()

    async def get_item_by_id(self, item_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_site_and_drive()
        client = await self._get_client()
        h = await self._headers()
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{item_id}?$select=id,name,parentReference,webUrl"
        resp = await client.get(url, headers={**h, "Accept": "application/json;odata.metadata=none"})
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph get item failed: {resp.status_code}")
        return resp.json()

    async def get_item_path(self, item_id: str) -> Optional[str]:
        """Return path inside the drive for an item id, if available."""
        item = await self.get_item_by_id(item_id)
        if not item:
            return None
        # parentReference.path is like '/drive/root:/sub/folder'
        pref = (item.get("parentReference") or {})
        p = pref.get("path") or ""
        if p.startswith("/drive/root:"):
            p = p[len("/drive/root:"):]
        name = item.get("name") or ""
        rel = (p.strip('/') + "/" + name).strip('/') if p else name
        return rel or None

    async def delete_file(self, item_id: str) -> None:
        await self._ensure_site_and_drive()
        client = await self._get_client()
        h = await self._headers()
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{item_id}"
        resp = await client.delete(url, headers=h)
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph delete failed: {resp.status_code}")

    async def _get_item_by_path(self, path: str) -> Optional[Dict[str, Any]]:
        await self._ensure_site_and_drive()
        client = await self._get_client()
        h = await self._headers()
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/root:/{path.strip('/')}"
        resp = await client.get(url, headers={**h, "Accept": "application/json;odata.metadata=none"})
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph item-by-path failed: {resp.status_code}")
        return resp.json()

    async def _create_folder(self, parent_path: str, name: str) -> Dict[str, Any]:
        await self._ensure_site_and_drive()
        client = await self._get_client()
        h = await self._headers()
        parent_path_norm = parent_path.strip('/')
        base = f"{GRAPH_BASE}/drives/{self._drive_id}/root"
        if parent_path_norm:
            base = f"{base}:/{parent_path_norm}:"
        url = f"{base}/children"
        body = {"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "replace"}
        resp = await client.post(url, headers={**h, "Content-Type": "application/json", "Accept": "application/json;odata.metadata=none"}, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph create folder failed: {resp.status_code}")
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
        last_created: Optional[Dict[str, Any]] = None
        for seg in parts:
            current_path = f"{current_path}/{seg}" if current_path else seg
            item = await self._get_item_by_path(current_path)
            if item is None:
                last_created = await self._create_folder(parent_path, seg)
            parent_path = current_path
        # return final
        item = await self._get_item_by_path(current_path)
        if item is None:
            # Some test environments stub GETs; trust last creation if present.
            if last_created is not None:
                return last_created
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
        client = await self._get_client()
        h = await self._headers()
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/items/{item_id}"
        resp = await client.patch(url, headers={**h, "Content-Type": "application/json", "Accept": "application/json;odata.metadata=none"}, json=body)
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph move failed: {resp.status_code}")
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

    # ---------------------- helpers ----------------------
    @staticmethod
    def _decode_jwt(token: str) -> tuple[dict, dict]:
        """Decode a JWT without verification (for logging claims only)."""
        import base64, json
        def b64url_decode(s: str) -> bytes:
            s += '=' * (-len(s) % 4)
            return base64.urlsafe_b64decode(s.encode('utf-8'))
        parts = token.split('.')
        if len(parts) < 2:
            raise ValueError("Invalid JWT format")
        header = json.loads(b64url_decode(parts[0]).decode('utf-8', 'ignore'))
        payload = json.loads(b64url_decode(parts[1]).decode('utf-8', 'ignore'))
        return header, payload
    @staticmethod
    def parse_sharepoint_web_url(web_url: str) -> tuple[str, str, Optional[str], str]:
        """
        Parse a SharePoint web URL and return (hostname, site_path, drive_name, path_in_drive).

        Accepts forms like:
        - https://contoso.sharepoint.com/sites/MySite/Shared%20Documents/folder/sub
        - https://contoso.sharepoint.com/sites/MySite/Shared%20Documents
        - Links with AllItems.aspx?id=/sites/.../Shared%20Documents/...
        """
        u = urlparse(web_url)
        host = u.netloc
        # Prefer 'id' query param if present (AllItems.aspx links)
        q = parse_qs(u.query)
        id_path = q.get("id", [None])[0]
        path = unquote(id_path or u.path)
        parts = [p for p in path.split('/') if p]

        # Find '/sites/{site}' or '/teams/{team}' anchor
        site_idx = -1
        for i, p in enumerate(parts):
            if p.lower() in {"sites", "teams"} and i + 1 < len(parts):
                site_idx = i
                break
        if site_idx == -1:
            raise ValueError("Unrecognized SharePoint URL – missing /sites/{name} or /teams/{name}")
        site_path = parts[site_idx + 1]
        # Expect a library segment after site, else default
        drive_name = parts[site_idx + 2] if site_idx + 2 < len(parts) else None
        if drive_name and drive_name.lower().replace('%20', ' ').replace('+', ' ') in {"shared documents", "documents"}:
            drive_name = "Documents"
        # Remaining path inside the drive
        sub_parts = parts[site_idx + 3:] if drive_name else []
        drive_path = "/".join(sub_parts)
        return host, site_path, drive_name, drive_path

    # ---------------------- delta enumeration ----------------------
    async def iter_files_delta(self) -> AsyncIterator[Dict[str, Any]]:
        """
        Enumerate all items in the drive using Graph delta.

        This flattens the hierarchy and is generally faster than recursive children listing.
        For a full scan, we yield only non-deleted items and attach a synthetic 'path' derived
        from parentReference.path + name for UI display and filtering.
        """
        await self._ensure_site_and_drive()
        client = await self._get_client()
        h = await self._headers()
        page_size = max(1, int(getattr(self._cfg, 'sp_graph_page_size', 200) or 200))
        headers = {**h, "Prefer": f"odata.maxpagesize={page_size}", "Accept": "application/json;odata.metadata=none"}
        select = "$select=id,name,file,folder,parentReference,lastModifiedDateTime,eTag,webUrl"
        url = f"{GRAPH_BASE}/drives/{self._drive_id}/root/delta?{select}"
        while url:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                detail = None
                try:
                    detail = resp.text
                except Exception:
                    pass
                raise RuntimeError(f"Graph delta failed: {resp.status_code} body={detail}")
            data = resp.json()
            for it in data.get("value", []):
                # Skip deletions for full-scan enumeration
                if it.get("deleted"):
                    continue
                # parentReference.path is like '/drive/root:/sub/folder'
                pref = (it.get("parentReference") or {})
                p = pref.get("path") or ""
                if p.startswith("/drive/root:"):
                    p = p[len("/drive/root:"):]
                name = it.get("name") or ""
                rel_path = (p.strip('/') + "/" + name).strip('/') if p else name
                if rel_path:
                    it = {**it, "path": rel_path}
                yield it
            next_url = data.get("@odata.nextLink")
            if next_url:
                url = next_url
                continue
            # If only a deltaLink is present, there are no more pages for this scan
            delta_link = data.get("@odata.deltaLink")
            if delta_link:
                break
            url = None
