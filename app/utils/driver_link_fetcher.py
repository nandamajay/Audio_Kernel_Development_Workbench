"""Fetch driver source from common internal and public links."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup
from requests.auth import HTTPBasicAuth

from app.services.env_service import resolve_ssl_verify


@dataclass
class FetchResult:
    success: bool
    link_type: str
    source_code: str = ""
    filename: str = ""
    metadata: Dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "link_type": self.link_type,
            "source_code": self.source_code,
            "filename": self.filename,
            "metadata": self.metadata or {},
            "error": self.error,
        }


class DriverLinkFetcher:
    def __init__(self, ssl_verify: str | bool | None = True, ca_bundle: str | None = None, timeout: int = 15):
        self.verify = resolve_ssl_verify(ssl_verify_raw=ssl_verify, ca_bundle=ca_bundle)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "AKDW-DriverLinkFetcher/2.0"})

    def detect_link_type(self, url: str) -> str:
        low = (url or "").lower()
        if "gerrit.qualcomm.com" in low:
            return "gerrit"
        if low.startswith("http://go/") or low.startswith("https://go/") or "go.qualcomm.com" in low:
            return "go_link"
        if "grok.qualcomm.com" in low or "codesearch.qualcomm.com" in low:
            return "grok"
        if "lore.kernel.org" in low:
            return "lore"
        if "github.com" in low or "raw.githubusercontent.com" in low:
            return "github"
        if re.search(r"\.(c|h|patch|diff|dts|dtsi)(\?|$)", low):
            return "raw"
        return "unknown"

    def fetch(self, url: str, auth: dict | None = None) -> Dict[str, Any]:
        link_type = self.detect_link_type(url)
        handlers = {
            "gerrit": self._fetch_gerrit,
            "go_link": self._fetch_go_link,
            "grok": self._fetch_grok,
            "lore": self._fetch_lore,
            "github": self._fetch_github,
            "raw": self._fetch_raw,
        }
        handler = handlers.get(link_type)
        if not handler:
            return FetchResult(
                success=False,
                link_type=link_type,
                error="Unsupported or unknown link type.",
            ).to_dict()
        return handler(url, auth or {}).to_dict()

    def _get(self, url: str, *, auth: Optional[HTTPBasicAuth] = None, allow_redirects: bool = True) -> requests.Response:
        return self.session.get(
            url,
            timeout=self.timeout,
            verify=self.verify,
            allow_redirects=allow_redirects,
            auth=auth,
        )

    def _strip_gerrit_prefix(self, text: str) -> str:
        if text.startswith(")]}'"):
            return "\n".join(text.splitlines()[1:])
        return text

    def _fetch_gerrit(self, url: str, auth: dict) -> FetchResult:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        cl_match = re.search(r"/c/.+?/\+/([0-9]+)", parsed.path)
        if not cl_match:
            cl_match = re.search(r"/c/([0-9]+)", parsed.path)
        if not cl_match:
            cl_match = re.search(r"/#/c/([0-9]+)", url)
        if not cl_match:
            return FetchResult(False, "gerrit", error="Unable to parse Gerrit CL number from URL.")
        cl_number = cl_match.group(1)

        auth_obj = None
        if auth.get("gerrit_username") and auth.get("gerrit_password"):
            auth_obj = HTTPBasicAuth(auth.get("gerrit_username"), auth.get("gerrit_password"))

        change_url = (
            f"{base}/a/changes/{cl_number}?o=CURRENT_REVISION"
            f"&o=CURRENT_FILES&o=DETAILED_ACCOUNTS"
        )

        resp = self._get(change_url, auth=auth_obj, allow_redirects=True)
        if resp.status_code == 401 and auth_obj is not None:
            resp = self._get(change_url, auth=None, allow_redirects=True)

        if resp.status_code >= 400:
            return FetchResult(False, "gerrit", error=f"Gerrit API error: {resp.status_code}")

        try:
            payload = json.loads(self._strip_gerrit_prefix(resp.text))
        except Exception as exc:
            return FetchResult(False, "gerrit", error=f"Failed to parse Gerrit response: {exc}")

        revision = payload.get("current_revision")
        revisions = payload.get("revisions", {})
        files = revisions.get(revision, {}).get("files", {}) if revision else {}
        file_candidates = [
            path
            for path in files.keys()
            if path not in {"/COMMIT_MSG"}
        ]
        driver_files = [
            path
            for path in file_candidates
            if path.startswith("sound/soc/") and path.endswith((".c", ".h"))
        ]
        if not driver_files:
            driver_files = [path for path in file_candidates if path.endswith((".c", ".h"))]
        if not driver_files:
            return FetchResult(False, "gerrit", error="No .c/.h files found in Gerrit change.")

        file_path = driver_files[0]
        content_url = (
            f"{base}/a/changes/{cl_number}/revisions/{revision}"
            f"/files/{quote(file_path, safe='')}/content"
        )
        content_resp = self._get(content_url, auth=auth_obj, allow_redirects=True)
        if content_resp.status_code == 401 and auth_obj is not None:
            content_resp = self._get(content_url, auth=None, allow_redirects=True)
        if content_resp.status_code >= 400:
            return FetchResult(False, "gerrit", error=f"Unable to fetch file content: {content_resp.status_code}")

        raw_payload = self._strip_gerrit_prefix(content_resp.text).strip()
        try:
            decoded = base64.b64decode(raw_payload).decode("utf-8", errors="replace")
        except Exception as exc:
            return FetchResult(False, "gerrit", error=f"Failed to decode Gerrit content: {exc}")

        metadata = {
            "cl_number": cl_number,
            "subject": payload.get("subject", ""),
            "author": (payload.get("owner") or {}).get("name", ""),
            "description": payload.get("message", ""),
            "file_path": file_path,
            "repo": payload.get("project", ""),
            "change_id": payload.get("change_id", ""),
        }
        filename = file_path.split("/")[-1]
        return FetchResult(True, "gerrit", decoded, filename, metadata)

    def _fetch_go_link(self, url: str, auth: dict) -> FetchResult:
        try:
            resp = self._get(url, allow_redirects=True)
        except Exception as exc:
            return FetchResult(False, "go_link", error=f"Failed to resolve go link: {exc}")

        final_url = resp.url
        link_type = self.detect_link_type(final_url)
        if link_type == "go_link":
            return FetchResult(False, "go_link", error="Go link did not redirect to a supported source.")

        result = self.fetch(final_url, auth=auth)
        metadata = result.get("metadata") or {}
        metadata["go_link_original"] = url
        result["metadata"] = metadata
        result["link_type"] = link_type
        return FetchResult(**result)

    def _fetch_grok(self, url: str, auth: dict) -> FetchResult:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path
        match = re.search(r"/xref/([^/]+)/\+/(.+)", path)
        if not match:
            match = re.search(r"/source/([^/]+)/tree/(.+)", path)
        if not match:
            return FetchResult(False, "grok", error="Unable to parse Grok path.")
        repo, file_path = match.group(1), match.group(2)
        raw_url = f"{base}/xref/{repo}/+raw/{file_path}"

        resp = self._get(raw_url, allow_redirects=True)
        if resp.status_code == 200 and resp.text.strip():
            filename = file_path.split("/")[-1]
            metadata = {"repo": repo, "file_path": file_path}
            return FetchResult(True, "grok", resp.text, filename, metadata)

        page_resp = self._get(url, allow_redirects=True)
        if page_resp.status_code >= 400:
            return FetchResult(False, "grok", error=f"Grok fetch failed: {page_resp.status_code}")

        soup = BeautifulSoup(page_resp.text, "html.parser")
        file_block = soup.find(id="file")
        text = ""
        if file_block:
            text = file_block.get_text("\n")
        if not text:
            pre = soup.find("pre")
            if pre:
                text = pre.get_text("\n")
        if not text:
            return FetchResult(False, "grok", error="Unable to extract source from Grok HTML.")

        filename = file_path.split("/")[-1]
        metadata = {"repo": repo, "file_path": file_path}
        return FetchResult(True, "grok", text, filename, metadata)

    def _fetch_lore(self, url: str, auth: dict) -> FetchResult:
        raw_url = url
        if "mbox" not in url and not url.endswith("/raw"):
            raw_url = url.rstrip("/") + "/raw"
        resp = self._get(raw_url, allow_redirects=True)
        if resp.status_code >= 400:
            return FetchResult(False, "lore", error=f"Lore fetch failed: {resp.status_code}")
        text = resp.text or ""

        metadata = {"cl_number": "", "subject": "", "author": "", "description": "", "file_path": "", "repo": "", "change_id": ""}
        header_block = []
        for line in text.splitlines():
            if not line.strip():
                break
            header_block.append(line)
        for line in header_block:
            if line.lower().startswith("from:"):
                metadata["author"] = line.split(":", 1)[1].strip()
            if line.lower().startswith("subject:"):
                metadata["subject"] = line.split(":", 1)[1].strip()
            if line.lower().startswith("date:"):
                metadata["description"] = line.strip()
        return FetchResult(True, "lore", text, "patch.mbox", metadata)

    def _fetch_github(self, url: str, auth: dict) -> FetchResult:
        raw_url = url
        if "raw.githubusercontent.com" not in url:
            parsed = urlparse(url)
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 5 and parts[2] == "blob":
                user, repo, _, branch = parts[:4]
                path = "/".join(parts[4:])
                raw_url = f"https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}"
        resp = self._get(raw_url, allow_redirects=True)
        if resp.status_code >= 400:
            return FetchResult(False, "github", error=f"GitHub fetch failed: {resp.status_code}")
        filename = urlparse(raw_url).path.split("/")[-1]
        return FetchResult(True, "github", resp.text, filename, {"repo": ""})

    def _fetch_raw(self, url: str, auth: dict) -> FetchResult:
        resp = self._get(url, allow_redirects=True)
        if resp.status_code >= 400:
            return FetchResult(False, "raw", error=f"Raw fetch failed: {resp.status_code}")
        filename = urlparse(url).path.split("/")[-1]
        return FetchResult(True, "raw", resp.text, filename, {})
