# SPDX-License-Identifier: Apache-2.0
"""Verified GitHub release assets with atomic caching and explicit offline use."""

import fcntl
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

NETWORK_TIMEOUT = 30


def _release_asset(metadata, repository, name):
    if not isinstance(metadata, dict):
        raise RuntimeError("Expected a GitHub release metadata object")
    tag = metadata.get("tag_name")
    assets = metadata.get("assets")
    if not isinstance(tag, str) or not tag or not isinstance(assets, list):
        raise RuntimeError("GitHub release metadata is missing its tag or assets")
    matches = [asset for asset in assets
               if isinstance(asset, dict) and asset.get("name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"{repository} release {tag}: expected one asset named {name}")
    asset = matches[0]
    digest = asset.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise RuntimeError(f"{repository} release {tag}: {name} has no published SHA256")
    url = asset.get("browser_download_url")
    if not isinstance(url, str):
        raise RuntimeError(f"{repository} release {tag}: {name} has no download URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "github.com"
            or not parsed.path.startswith(f"/{repository}/releases/download/")
            or not parsed.path.endswith("/" + quote(name, safe=""))
            or parsed.query or parsed.fragment):
        raise RuntimeError(f"{repository} release {tag}: invalid download URL for {name}")
    return url, digest.removeprefix("sha256:").lower()


def _verify(path, expected):
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise RuntimeError(f"SHA256 mismatch for {path}: expected {expected}, actual {actual}")


def _request(url):
    return urlopen(Request(url, headers={
        "User-Agent": "virtio-villain",
        "Accept": "application/vnd.github+json" if url.startswith(
            "https://api.github.com/") else "application/octet-stream",
    }), timeout=NETWORK_TIMEOUT)


def github_release_asset(repository, name, cache, *, offline=False):
    """Return a verified latest asset, or the last verified release when offline.

    An online call resolves the latest release even when an older release is
    cached. Failures never silently select an older release. Offline calls
    neither access the network nor write to the cache.
    """
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            or any(part in (".", "..") for part in repository.split("/"))):
        raise ValueError(f"Invalid GitHub repository: {repository}")
    if not name or name in (".", "..") or Path(name).name != name or "\\" in name:
        raise ValueError(f"Invalid release asset name: {name}")
    directory = Path(cache) / repository / name
    manifest = directory / "latest.json"
    if offline:
        try:
            metadata = json.loads(manifest.read_text())
        except FileNotFoundError as error:
            raise RuntimeError(f"Offline release metadata not cached: {manifest}") from error
        except (ValueError, UnicodeError) as error:
            raise RuntimeError(f"Invalid cached release metadata: {manifest}: {error}") from error
        _, digest = _release_asset(metadata, repository, name)
        path = directory / digest / name
        try:
            _verify(path, digest)
        except FileNotFoundError as error:
            raise RuntimeError(f"Offline release asset not cached: {path}") from error
        return path

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        url = f"https://api.github.com/repos/{repository}/releases/latest"
        try:
            with _request(url) as response:
                metadata = json.load(response)
        except (OSError, HTTPException, ValueError, UnicodeError) as error:
            raise RuntimeError(f"Cannot resolve latest {repository} release: {error}") from error
        url, digest = _release_asset(metadata, repository, name)
        path = directory / digest / name
        try:
            _verify(path, digest)
        except FileNotFoundError:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".download-", dir=directory) as temporary:
                partial = Path(temporary) / name
                try:
                    with _request(url) as response, partial.open("wb") as stream:
                        shutil.copyfileobj(response, stream)
                except (OSError, HTTPException) as error:
                    raise RuntimeError(f"Cannot download {url}: {error}") from error
                _verify(partial, digest)
                partial.replace(path)
        with tempfile.TemporaryDirectory(prefix=".metadata-", dir=directory) as temporary:
            staged = Path(temporary) / "latest.json"
            staged.write_text(json.dumps({
                "tag_name": metadata["tag_name"],
                "assets": [{"name": name, "digest": f"sha256:{digest}",
                            "browser_download_url": url}],
            }, indent=2) + "\n")
            staged.replace(manifest)
    return path
