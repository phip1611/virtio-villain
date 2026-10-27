#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Release resolution, checksum enforcement and cache failure handling."""

import contextlib
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib/python"))
from villain import artifacts, vmm  # noqa: E402

REPOSITORY = "example/project"
ASSET = "firmware.fd"


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name) / "cache"
        self.directory = self.cache / REPOSITORY / ASSET
        self.body = b"release asset fixture"
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.metadata = {
            "tag_name": "v1",
            "assets": [{
                "name": ASSET, "digest": f"sha256:{self.digest}",
                "browser_download_url": (
                    f"https://github.com/{REPOSITORY}/releases/download/v1/{ASSET}"),
            }],
        }

    def resolve(self, **kwargs):
        return artifacts.github_release_asset(REPOSITORY, ASSET, self.cache, **kwargs)

    def network(self, metadata=None, body=None):
        metadata = self.metadata if metadata is None else metadata
        body = self.body if body is None else body
        return patch.object(artifacts, "urlopen", side_effect=[
            io.BytesIO(json.dumps(metadata).encode()), io.BytesIO(body)])

    def populate(self):
        with self.network():
            return self.resolve()

    def assert_no_partial_files(self):
        for pattern in (".download-*", ".metadata-*"):
            self.assertEqual(list(self.directory.glob(pattern)), [])

    def test_download_verifies_published_digest_and_records_release(self):
        with self.network() as opened:
            path = self.resolve()
        self.assertEqual(path, self.directory / self.digest / ASSET)
        self.assertEqual(path.read_bytes(), self.body)
        self.assertEqual(json.loads((self.directory / "latest.json").read_text()),
                         self.metadata)
        self.assertEqual(opened.call_args_list[0].args[0].full_url,
                         f"https://api.github.com/repos/{REPOSITORY}/releases/latest")
        self.assertEqual(opened.call_args_list[1].args[0].full_url,
                         self.metadata["assets"][0]["browser_download_url"])
        for call in opened.call_args_list:
            self.assertEqual(call.kwargs["timeout"], artifacts.NETWORK_TIMEOUT)
        self.assert_no_partial_files()

    def test_online_rechecks_latest_but_reuses_verified_bytes(self):
        first = self.populate()
        with self.network() as opened:
            self.assertEqual(self.resolve(), first)
        self.assertEqual(opened.call_count, 1)

    def test_prefer_cached_downloads_once_then_reuses_without_network_or_writes(self):
        with self.network() as opened:
            path = self.resolve(prefer_cached=True)
        self.assertEqual(opened.call_count, 2)
        before = {item: item.stat().st_mtime_ns for item in self.cache.rglob("*")}
        with patch.object(artifacts, "urlopen",
                          side_effect=AssertionError("Network forbidden")), \
             patch.object(Path, "mkdir", side_effect=AssertionError("Cache is read only")), \
             patch.object(artifacts.fcntl, "flock",
                          side_effect=AssertionError("Write lock forbidden")):
            self.assertEqual(self.resolve(prefer_cached=True), path)
        self.assertEqual(before, {item: item.stat().st_mtime_ns
                                  for item in self.cache.rglob("*")})

    def test_prefer_cached_downloads_missing_bytes_from_the_recorded_release(self):
        path = self.populate()
        path.unlink()
        before = (self.directory / "latest.json").read_bytes()
        with patch.object(artifacts, "urlopen", return_value=io.BytesIO(self.body)) as opened:
            self.assertEqual(self.resolve(prefer_cached=True), path)
        opened.assert_called_once()
        self.assertEqual(opened.call_args.args[0].full_url,
                         self.metadata["assets"][0]["browser_download_url"])
        self.assertEqual(path.read_bytes(), self.body)
        self.assertEqual((self.directory / "latest.json").read_bytes(), before)
        self.assert_no_partial_files()

    def test_prefer_cached_rejects_corruption_without_network(self):
        self.populate().write_bytes(b"corrupt")
        with patch.object(artifacts, "urlopen") as opened, \
             self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
            self.resolve(prefer_cached=True)
        opened.assert_not_called()

    def test_prefer_cached_rejects_invalid_metadata_without_network(self):
        self.populate()
        (self.directory / "latest.json").write_text("{")
        with patch.object(artifacts, "urlopen") as opened, \
             self.assertRaisesRegex(RuntimeError, "Invalid cached release metadata"):
            self.resolve(prefer_cached=True)
        opened.assert_not_called()

    def test_offline_reuses_cache_without_network_or_writes(self):
        path = self.populate()
        before = {item: item.stat().st_mtime_ns
                  for item in self.cache.rglob("*")}
        with patch.object(artifacts, "urlopen",
                          side_effect=AssertionError("Network forbidden")):
            self.assertEqual(self.resolve(offline=True), path)
        self.assertEqual(before, {item: item.stat().st_mtime_ns
                                  for item in self.cache.rglob("*")})

    def test_offline_missing_metadata_does_not_create_cache(self):
        with patch.object(artifacts, "urlopen") as opened, \
             self.assertRaisesRegex(RuntimeError, "Offline release metadata not cached"):
            self.resolve(offline=True)
        opened.assert_not_called()
        self.assertFalse(self.cache.exists())

    def test_offline_missing_asset_is_not_downloaded(self):
        self.populate().unlink()
        with patch.object(artifacts, "urlopen") as opened, \
             self.assertRaisesRegex(RuntimeError, "Offline release asset not cached"):
            self.resolve(offline=True)
        opened.assert_not_called()

    def test_corrupted_cache_is_rejected_online_and_offline(self):
        self.populate().write_bytes(b"corrupt")
        for offline in (False, True):
            with self.subTest(offline=offline), self.network() as opened, \
                 self.assertRaisesRegex(RuntimeError, f"expected {self.digest}, actual"):
                self.resolve(offline=offline)
            self.assertEqual(opened.call_count, 0 if offline else 1)

    def test_download_checksum_mismatch_is_not_published(self):
        with self.network(body=b"incorrect"), \
             self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
            self.resolve()
        self.assertFalse((self.directory / "latest.json").exists())
        self.assertFalse((self.directory / self.digest / ASSET).exists())
        self.assert_no_partial_files()

    def test_published_digest_is_required_before_download(self):
        for digest in (None, "", "sha256:abc", "md5:" + "a" * 64, 123):
            self.metadata["assets"][0]["digest"] = digest
            with self.subTest(digest=digest), self.network() as opened, \
                 self.assertRaisesRegex(RuntimeError, "no published SHA256"):
                self.resolve()
            self.assertEqual(opened.call_count, 1)

    def test_missing_or_ambiguous_asset_is_rejected(self):
        original = self.metadata["assets"][0]
        for assets in ([], [original, original]):
            self.metadata["assets"] = assets
            with self.subTest(assets=assets), self.network() as opened, \
                 self.assertRaisesRegex(RuntimeError, "expected one asset named"):
                self.resolve()
            self.assertEqual(opened.call_count, 1)

    def test_malformed_metadata_is_rejected(self):
        for metadata in ([], {}, {"tag_name": "v1"}, {"tag_name": "", "assets": []}):
            with self.subTest(metadata=metadata), self.network(metadata) as opened, \
                 self.assertRaises(RuntimeError):
                self.resolve()
            self.assertEqual(opened.call_count, 1)

    def test_invalid_asset_urls_are_not_downloaded(self):
        for url in (
                "http://github.com/example/project/releases/download/v1/firmware.fd",
                "https://other.example/example/project/releases/download/v1/firmware.fd",
                "https://github.com/other/project/releases/download/v1/firmware.fd",
                "https://github.com/example/project/releases/download/v1/other.fd",
                "https://github.com/example/project/releases/download/v1/firmware.fd?x=1"):
            self.metadata["assets"][0]["browser_download_url"] = url
            with self.subTest(url=url), self.network() as opened, \
                 self.assertRaisesRegex(RuntimeError, "invalid download URL"):
                self.resolve()
            self.assertEqual(opened.call_count, 1)

    def test_resolution_failure_does_not_silently_use_old_release(self):
        path = self.populate()
        with patch.object(artifacts, "urlopen", side_effect=URLError("network unavailable")), \
             self.assertRaisesRegex(RuntimeError, "Cannot resolve latest"):
            self.resolve()
        with patch.object(artifacts, "urlopen") as opened:
            self.assertEqual(self.resolve(offline=True), path)
        opened.assert_not_called()

    def test_interrupted_download_removes_partial_files(self):
        class Interrupted(io.BytesIO):
            def read(self, size=-1):
                raise ConnectionResetError("connection interrupted")

        with patch.object(artifacts, "urlopen", side_effect=[
                io.BytesIO(json.dumps(self.metadata).encode()), Interrupted()]), \
             self.assertRaisesRegex(RuntimeError, "Cannot download.*connection interrupted"):
            self.resolve()
        self.assertFalse((self.directory / "latest.json").exists())
        self.assert_no_partial_files()

    def test_new_release_is_selected_and_previous_release_is_preserved(self):
        old = self.populate()
        self.body = b"new release"
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.metadata["tag_name"] = "v2"
        asset = self.metadata["assets"][0]
        asset["digest"] = f"sha256:{self.digest}"
        asset["browser_download_url"] = asset["browser_download_url"].replace("/v1/", "/v2/")
        with self.network():
            new = self.resolve()
        self.assertNotEqual(old, new)
        self.assertEqual(old.read_bytes(), b"release asset fixture")
        self.assertEqual(new.read_bytes(), self.body)
        self.assertEqual(self.resolve(offline=True), new)

    def test_failed_new_release_preserves_last_verified_offline_release(self):
        old = self.populate()
        self.metadata["assets"][0]["digest"] = "sha256:" + "0" * 64
        with self.network(), self.assertRaisesRegex(RuntimeError, "SHA256 mismatch"):
            self.resolve()
        self.assertEqual(self.resolve(offline=True), old)
        self.assert_no_partial_files()

    def test_invalid_cached_metadata_is_reported_without_network(self):
        self.populate()
        for data in (b"\xff", b"{"):
            (self.directory / "latest.json").write_bytes(data)
            with self.subTest(data=data), patch.object(artifacts, "urlopen") as opened, \
                 self.assertRaisesRegex(RuntimeError, "Invalid cached release metadata"):
                self.resolve(offline=True)
            opened.assert_not_called()

    def test_concurrent_requests_publish_one_complete_download(self):
        def response(request, **kwargs):
            data = (json.dumps(self.metadata).encode()
                    if request.full_url.startswith("https://api.github.com/") else self.body)
            return io.BytesIO(data)

        with patch.object(artifacts, "urlopen", side_effect=response) as opened, \
             ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self.resolve) for _ in range(3)]
            paths = [future.result(timeout=10) for future in futures]
        self.assertEqual(paths, [paths[0]] * 3)
        self.assertEqual(paths[0].read_bytes(), self.body)
        self.assertEqual(opened.call_count, 4)
        self.assert_no_partial_files()

    def test_concurrent_prefer_cached_requests_resolve_and_download_once(self):
        def response(request, **kwargs):
            data = (json.dumps(self.metadata).encode()
                    if request.full_url.startswith("https://api.github.com/") else self.body)
            return io.BytesIO(data)

        with patch.object(artifacts, "urlopen", side_effect=response) as opened, \
             ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self.resolve, prefer_cached=True) for _ in range(3)]
            paths = [future.result(timeout=10) for future in futures]
        self.assertEqual(paths, [paths[0]] * 3)
        self.assertEqual(paths[0].read_bytes(), self.body)
        self.assertEqual(opened.call_count, 2)
        self.assert_no_partial_files()

    def test_guest_kernel_uses_verified_cache_and_rejects_changed_bytes(self):
        name = "vmlinux-x86_64"
        repository = vmm.KERNEL_REPOSITORY
        self.metadata["assets"][0].update(
            name=name, browser_download_url=(
                f"https://github.com/{repository}/releases/download/v1/{name}"))
        with patch.object(vmm, "_SCRIPT_DIR", str(self.cache.parent)), \
             patch.object(vmm.platform, "machine", return_value="x86_64"):
            with self.network() as opened:
                path = Path(vmm.fetch_kernel())
            self.assertEqual(opened.call_count, 2)
            self.assertEqual(path, self.cache.parent / "target/kernel-cache" /
                             repository / name / self.digest / name)
            self.assertEqual(path.read_bytes(), self.body)
            with patch.object(artifacts, "urlopen") as opened:
                self.assertEqual(vmm.fetch_kernel(), str(path))
            opened.assert_not_called()
            path.write_bytes(b"corrupt")
            with patch.object(artifacts, "urlopen") as opened, \
                 self.assertRaisesRegex(
                     RuntimeError, "Cannot acquire guest kernel.*SHA256 mismatch"):
                vmm.fetch_kernel()
            opened.assert_not_called()

    def test_guest_kernel_keeps_existing_local_images_without_modifying_them(self):
        root = self.cache.parent
        local = root / "target/vmlinux-x86_64"
        local.parent.mkdir()
        local.write_bytes(b"local kernel")
        before = local.stat().st_mtime_ns
        with patch.object(vmm, "_SCRIPT_DIR", str(root)), \
             patch.object(vmm.platform, "machine", return_value="x86_64"), \
             patch.object(artifacts, "urlopen") as opened, \
             contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(vmm.fetch_kernel(), str(local))
        opened.assert_not_called()
        self.assertIn("without a recorded release checksum", errors.getvalue())
        self.assertEqual(local.read_bytes(), b"local kernel")
        self.assertEqual(local.stat().st_mtime_ns, before)
        self.assertFalse((root / "target/kernel-cache").exists())

    def test_guest_kernel_selection_preserves_architecture_and_mmio_defaults(self):
        for arch, mmio, name in (
                ("x86_64", False, "vmlinux-x86_64"),
                ("x86_64", True, "bzImage-x86_64"),
                ("aarch64", False, "Image-arm64"),
                ("aarch64", True, "Image-arm64")):
            with self.subTest(arch=arch, mmio=mmio), \
                 patch.object(vmm, "_SCRIPT_DIR", str(self.cache.parent)), \
                 patch.object(vmm.platform, "machine", return_value=arch), \
                 patch.object(artifacts, "github_release_asset",
                              return_value=Path("/cache") / name) as resolve:
                self.assertEqual(vmm.fetch_kernel(mmio), "/cache/" + name)
            resolve.assert_called_once_with(
                vmm.KERNEL_REPOSITORY, name, self.cache.parent / "target/kernel-cache",
                prefer_cached=True)

    def test_guest_kernel_reports_unsupported_architecture_and_cache_errors(self):
        with patch.object(vmm.platform, "machine", return_value="unknown"), \
             patch.object(artifacts, "github_release_asset") as resolve, \
             self.assertRaisesRegex(RuntimeError, "Use --kernel"):
            vmm.fetch_kernel()
        resolve.assert_not_called()
        with patch.object(vmm, "_SCRIPT_DIR", str(self.cache.parent)), \
             patch.object(vmm.platform, "machine", return_value="x86_64"), \
             patch.object(artifacts, "github_release_asset",
                          side_effect=PermissionError("Cache is not writable")), \
             self.assertRaisesRegex(RuntimeError, "Cannot acquire guest kernel.*not writable"):
            vmm.fetch_kernel()

    def test_invalid_cache_identifiers_are_rejected_before_io(self):
        for repository, asset in (
                ("../project", ASSET), ("example/..", ASSET), ("bad", ASSET),
                (REPOSITORY, "../asset"), (REPOSITORY, "/asset"),
                (REPOSITORY, "."), (REPOSITORY, "a\\b")):
            with self.subTest(repository=repository, asset=asset), \
                 patch.object(artifacts, "urlopen") as opened, \
                 self.assertRaises(ValueError):
                artifacts.github_release_asset(repository, asset, self.cache)
            opened.assert_not_called()
        self.assertFalse(self.cache.exists())
