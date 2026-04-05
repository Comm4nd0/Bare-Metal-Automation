"""Content library setup and template upload.

Creates a local content library in vCenter and uploads VM templates.
Supports OVA/OVF uploads as well as ISO files for OS installations.

Standard templates uploaded by BMA:
  - Windows Server 2022 Datacenter (ws2022-datacenter.ova)
  - Ubuntu Server 22.04 LTS (ubuntu-22.04-server.ova)

Usage::

    mgr = ContentLibraryManager(
        vcenter_host="10.100.1.10",
        username="administrator@vsphere.local",
        password="VMware1!",
    )
    mgr.setup(
        library_name="BMA-Templates",
        datastore_name="vsanDatastore",
        templates=[
            LibraryTemplate(
                name="ws2022-datacenter",
                description="Windows Server 2022 Datacenter",
                local_path="/opt/bma/templates/ws2022-datacenter.ova",
            ),
            LibraryTemplate(
                name="ubuntu-22.04-server",
                description="Ubuntu Server 22.04 LTS",
                local_path="/opt/bma/templates/ubuntu-22.04-server.ova",
            ),
        ],
    )
"""

from __future__ import annotations

import logging
import ssl
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class LibraryTemplate:
    """A single template to upload to the content library."""

    name: str
    local_path: str
    description: str = ""
    item_type: str = "ovf"   # ovf | iso


class ContentLibraryManager:
    """Create a content library and upload templates via the vSphere Content Library API."""

    def __init__(
        self,
        vcenter_host: str,
        username: str,
        password: str,
        port: int = 443,
    ) -> None:
        self.vcenter_host = vcenter_host
        self.username = username
        self.password = password
        self.port = port
        self._si = None
        self._session_id: str | None = None

    # ── Public API ─────────────────────────────────────────────────────────

    def setup(
        self,
        library_name: str,
        datastore_name: str,
        templates: list[LibraryTemplate],
    ) -> bool:
        """Create the library and upload each template. Returns True on success."""
        try:
            self._connect()
        except Exception as e:
            logger.error(f"Cannot connect to vCenter: {e}")
            return False

        try:
            library_id = self._ensure_library(library_name, datastore_name)
            if library_id is None:
                return False

            all_ok = True
            for tmpl in templates:
                local = Path(tmpl.local_path)
                if not local.exists():
                    logger.warning(
                        f"Template file not found: {tmpl.local_path} — skipping"
                    )
                    continue
                ok = self._upload_template(library_id, tmpl)
                if not ok:
                    all_ok = False

            return all_ok
        except Exception as e:
            logger.exception(f"Content library setup failed: {e}")
            return False
        finally:
            self._disconnect()

    def list_items(self, library_name: str) -> list[dict]:
        """Return the items in the named content library."""
        try:
            self._connect()
            library_id = self._find_library_by_name(library_name)
            if library_id is None:
                return []
            return self._list_library_items(library_id)
        except Exception as e:
            logger.error(f"Failed to list library items: {e}")
            return []
        finally:
            self._disconnect()

    # ── Library management ─────────────────────────────────────────────────

    def _ensure_library(
        self, name: str, datastore_name: str
    ) -> str | None:
        """Return the library ID, creating it on *datastore_name* if needed."""
        existing_id = self._find_library_by_name(name)
        if existing_id:
            logger.info(f"Content library '{name}' already exists (id={existing_id})")
            return existing_id

        datastore_id = self._find_datastore_id(datastore_name)
        if datastore_id is None:
            logger.error(f"Datastore '{datastore_name}' not found")
            return None

        logger.info(f"Creating content library '{name}' on datastore '{datastore_name}'")
        library_id = self._create_library(name, datastore_id)
        logger.info(f"Content library '{name}' created (id={library_id})")
        return library_id

    def _create_library(self, name: str, datastore_id: str) -> str:
        """Call the Content Library REST API to create a local library."""
        import requests as req  # type: ignore[import-not-found]

        url = f"https://{self.vcenter_host}/api/content/local-library"
        body = {
            "create_spec": {
                "name": name,
                "type": "LOCAL",
                "storage_backings": [
                    {
                        "type": "DATASTORE",
                        "datastore_id": datastore_id,
                    }
                ],
            },
            "client_token": str(uuid.uuid4()),
        }
        resp = req.post(
            url,
            json=body,
            headers={"vmware-api-session-id": self._session_id},
            verify=False,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def _find_library_by_name(self, name: str) -> str | None:
        """Return the library ID for *name* or None."""
        import requests as req  # type: ignore[import-not-found]

        url = f"https://{self.vcenter_host}/api/content/library?action=find"
        body = {"spec": {"name": name, "type": "LOCAL"}}
        resp = req.post(
            url,
            json=body,
            headers={"vmware-api-session-id": self._session_id},
            verify=False,
            timeout=30,
        )
        if resp.status_code == 200:
            ids = resp.json()
            return ids[0] if ids else None
        return None

    def _list_library_items(self, library_id: str) -> list[dict]:
        import requests as req  # type: ignore[import-not-found]

        url = f"https://{self.vcenter_host}/api/content/library/item?library_id={library_id}"
        resp = req.get(
            url,
            headers={"vmware-api-session-id": self._session_id},
            verify=False,
            timeout=30,
        )
        if resp.status_code != 200:
            return []
        item_ids: list[str] = resp.json()
        items = []
        for item_id in item_ids:
            item_url = f"https://{self.vcenter_host}/api/content/library/item/{item_id}"
            r = req.get(
                item_url,
                headers={"vmware-api-session-id": self._session_id},
                verify=False,
                timeout=30,
            )
            if r.status_code == 200:
                items.append(r.json())
        return items

    # ── Template upload ────────────────────────────────────────────────────

    def _upload_template(self, library_id: str, tmpl: LibraryTemplate) -> bool:
        """Create a library item and upload the OVA/OVF file."""
        import requests as req  # type: ignore[import-not-found]

        logger.info(f"Uploading template '{tmpl.name}' to content library")

        # Create library item
        item_url = f"https://{self.vcenter_host}/api/content/library/item"
        item_body = {
            "create_spec": {
                "library_id": library_id,
                "name": tmpl.name,
                "description": tmpl.description,
                "type": tmpl.item_type,
            },
            "client_token": str(uuid.uuid4()),
        }
        resp = req.post(
            item_url,
            json=item_body,
            headers={"vmware-api-session-id": self._session_id},
            verify=False,
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            logger.error(
                f"Failed to create library item '{tmpl.name}': {resp.status_code}"
            )
            return False

        item_id = resp.json()

        # Create an update session
        session_url = f"https://{self.vcenter_host}/api/content/library/item/update-session"
        session_body = {
            "create_spec": {"library_item_id": item_id},
            "client_token": str(uuid.uuid4()),
        }
        resp = req.post(
            session_url,
            json=session_body,
            headers={"vmware-api-session-id": self._session_id},
            verify=False,
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            logger.error(f"Failed to create update session for '{tmpl.name}'")
            return False

        update_session_id = resp.json()

        # Add a file to the update session
        file_name = Path(tmpl.local_path).name
        add_file_url = (
            f"https://{self.vcenter_host}/api/content/library/item"
            f"/update-session/{update_session_id}/file?action=add"
        )
        add_body = {
            "file_spec": {
                "name": file_name,
                "source_type": "PUSH",
            }
        }
        resp = req.post(
            add_file_url,
            json=add_body,
            headers={"vmware-api-session-id": self._session_id},
            verify=False,
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            logger.error(f"Failed to add file spec for '{tmpl.name}'")
            return False

        upload_endpoint = resp.json().get("upload_endpoint", {}).get("uri", "")
        if not upload_endpoint:
            logger.error(f"No upload URI returned for '{tmpl.name}'")
            return False

        # Stream upload the file
        logger.info(f"Uploading {file_name} ({Path(tmpl.local_path).stat().st_size} bytes)")
        with open(tmpl.local_path, "rb") as fh:
            put_resp = req.put(
                upload_endpoint,
                data=fh,
                headers={
                    "vmware-api-session-id": self._session_id,
                    "Content-Type": "application/octet-stream",
                },
                verify=False,
                timeout=3600,
            )
        if put_resp.status_code not in (200, 201):
            logger.error(
                f"File upload failed for '{tmpl.name}': {put_resp.status_code}"
            )
            return False

        # Complete the session
        complete_url = (
            f"https://{self.vcenter_host}/api/content/library/item"
            f"/update-session/{update_session_id}?action=complete"
        )
        req.post(
            complete_url,
            headers={"vmware-api-session-id": self._session_id},
            verify=False,
            timeout=30,
        )

        logger.info(f"Template '{tmpl.name}' uploaded successfully")
        return True

    # ── Datastore lookup ───────────────────────────────────────────────────

    def _find_datastore_id(self, name: str) -> str | None:
        """Return the managed object ID of the named datastore."""
        import requests as req  # type: ignore[import-not-found]

        url = f"https://{self.vcenter_host}/api/vcenter/datastore?names={name}"
        resp = req.get(
            url,
            headers={"vmware-api-session-id": self._session_id},
            verify=False,
            timeout=30,
        )
        if resp.status_code == 200:
            stores = resp.json()
            if stores:
                return stores[0]["datastore"]
        return None

    # ── Session management ─────────────────────────────────────────────────

    def _connect(self) -> None:
        if self._session_id is not None:
            return
        import requests as req  # type: ignore[import-not-found]

        url = f"https://{self.vcenter_host}/api/session"
        resp = req.post(
            url,
            auth=(self.username, self.password),
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        self._session_id = resp.json()
        logger.info(f"vCenter REST session established on {self.vcenter_host}")

    def _disconnect(self) -> None:
        if self._session_id is None:
            return
        try:
            import requests as req  # type: ignore[import-not-found]

            req.delete(
                f"https://{self.vcenter_host}/api/session",
                headers={"vmware-api-session-id": self._session_id},
                verify=False,
                timeout=10,
            )
        except Exception:
            pass
        finally:
            self._session_id = None
