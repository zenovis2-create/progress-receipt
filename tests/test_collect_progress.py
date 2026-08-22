from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from progress_receipt import collect as packaged_collect


collect_progress = packaged_collect._IMPLEMENTATION


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def legacy_worktree_fingerprint(repo: Path) -> str:
    status = collect_progress.run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    if not status:
        return "clean"
    digest = hashlib.sha256(status)
    paths = collect_progress.run_git(repo, "ls-files", "-m", "-o", "--exclude-standard", "-z").stdout.decode("utf-8", "replace").split("\0")
    for path in sorted(item for item in paths if item):
        digest.update(path.encode("utf-8", "replace"))
        content = collect_progress.run_git(repo, "hash-object", "--no-filters", "--", path, allow_failure=True)
        digest.update(content.stdout.strip() if content.returncode == 0 else b"<unreadable>")
    return digest.hexdigest()


class WorktreeFingerprintTests(unittest.TestCase):
    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        repo = Path(temp.name)
        git(repo, "init", "--quiet")
        git(repo, "config", "user.name", "Progress Test")
        git(repo, "config", "user.email", "progress@example.invalid")
        (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "--quiet", "-m", "initial")
        return temp, repo

    def test_batch_fingerprint_matches_legacy_and_is_content_stable(self) -> None:
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)
        (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
        (repo / "-leading & unicode 名.txt").write_text("untracked\n", encoding="utf-8")

        expected = legacy_worktree_fingerprint(repo)
        actual = collect_progress.worktree_fingerprint(repo)
        self.assertEqual(expected, actual)
        self.assertEqual(actual, collect_progress.worktree_fingerprint(repo))

        (repo / "-leading & unicode 名.txt").write_text("changed content\n", encoding="utf-8")
        self.assertNotEqual(actual, collect_progress.worktree_fingerprint(repo))

    def test_many_changed_paths_use_one_hash_object_batch(self) -> None:
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)
        (repo / "tracked.txt").write_text("modified\n", encoding="utf-8")
        for index in range(256):
            (repo / f"untracked-{index:03d}.txt").write_text(f"{index}\n", encoding="utf-8")

        original_run_git = collect_progress.run_git
        with mock.patch.object(collect_progress, "run_git", wraps=original_run_git) as run_git:
            fingerprint = collect_progress.worktree_fingerprint(repo)

        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        hash_object_calls = [call for call in run_git.call_args_list if call.args[1] == "hash-object"]
        self.assertEqual(1, len(hash_object_calls))
        self.assertIn("--stdin-paths", hash_object_calls[0].args)


class SanitizerTests(unittest.TestCase):
    def test_assignment_secrets_are_redacted(self) -> None:
        sanitized = collect_progress.sanitize_text("api_key=abc123 password:open-sesame")
        self.assertEqual("api_key=<REDACTED> password=<REDACTED>", sanitized)

    def test_flag_secrets_are_redacted(self) -> None:
        sanitized = collect_progress.sanitize_text("tool --token secret-token --password 'hunter two'")
        self.assertEqual("tool --token <REDACTED> --password <REDACTED>", sanitized)

    def test_bearer_tokens_are_redacted(self) -> None:
        sanitized = collect_progress.sanitize_text("Authorization: Bearer abc.def-123_456")
        self.assertEqual("Authorization: Bearer <REDACTED>", sanitized)

    def test_private_key_marker_is_redacted(self) -> None:
        value = "-----BEGIN RSA PRIVATE KEY-----\nprivate-body\n-----END RSA PRIVATE KEY-----"
        sanitized = collect_progress.sanitize_text(value)
        self.assertNotIn("-----BEGIN RSA PRIVATE KEY-----", sanitized)
        self.assertIn("<PRIVATE_KEY_REDACTED>", sanitized)

    def test_private_ipv4_addresses_are_redacted(self) -> None:
        sanitized = collect_progress.sanitize_text("private 10.0.0.7 loopback 127.0.0.1 public 8.8.8.8")
        self.assertEqual("private <PRIVATE_ADDRESS> loopback <PRIVATE_ADDRESS> public 8.8.8.8", sanitized)

    def test_private_ipv6_addresses_are_redacted(self) -> None:
        sanitized = collect_progress.sanitize_text("private fc00::1 loopback ::1 public 2606:4700:4700::1111")
        self.assertEqual(
            "private <PRIVATE_ADDRESS> loopback <PRIVATE_ADDRESS> public 2606:4700:4700::1111",
            sanitized,
        )

    def test_home_paths_are_redacted_in_native_and_slash_forms(self) -> None:
        home = str(Path.home())
        sanitized = collect_progress.sanitize_text(f"native {home} slash {home.replace(chr(92), '/')}")
        self.assertEqual("native <HOME> slash <HOME>", sanitized)


class InventoryBoundsTests(unittest.TestCase):
    def test_collection_reports_bounded_inventory_truncation(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        git(repo, "init", "--quiet")
        git(repo, "config", "user.name", "Progress Test")
        git(repo, "config", "user.email", "progress@example.invalid")
        (repo / "one.txt").write_text("before\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "baseline")
        base = collect_progress.resolve_ref(repo, "HEAD")
        (repo / "one.txt").write_text("after\n", encoding="utf-8")
        (repo / "two.txt").write_text("two\n", encoding="utf-8")
        (repo / "three.txt").write_text("three\n", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "--quiet", "-m", "three changed files")
        head = collect_progress.resolve_ref(repo, "HEAD")

        inventory = collect_progress.collect_inventory(repo, base, head, max_files=1, max_commits=1)

        self.assertEqual(3, inventory["totalFiles"])
        self.assertEqual(1, len(inventory["files"]))
        self.assertEqual(2, inventory["omittedFiles"])
        self.assertTrue(inventory["filesTruncated"])


if __name__ == "__main__":
    unittest.main()
