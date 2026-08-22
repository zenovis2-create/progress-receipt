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


def unbatched_worktree_fingerprint(repo: Path) -> str:
    """Recompute the fingerprint one ``hash-object`` call at a time.

    The batched implementation must agree with this straightforward form; only
    the number of Git invocations differs.
    """
    status = collect_progress.run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    if not status:
        return "clean"
    digest = hashlib.sha256(status)
    staged = collect_progress.run_git(
        repo, "diff-index", "--cached", "--no-renames", "-z", "HEAD", "--", allow_failure=True
    )
    digest.update(staged.stdout if staged.returncode == 0 else b"<no-head>")
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

    def test_batch_fingerprint_matches_unbatched_and_is_content_stable(self) -> None:
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)
        (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
        (repo / "-leading & unicode 名.txt").write_text("untracked\n", encoding="utf-8")

        expected = unbatched_worktree_fingerprint(repo)
        actual = collect_progress.worktree_fingerprint(repo)
        self.assertEqual(expected, actual)
        self.assertEqual(actual, collect_progress.worktree_fingerprint(repo))

        (repo / "-leading & unicode 名.txt").write_text("changed content\n", encoding="utf-8")
        self.assertNotEqual(actual, collect_progress.worktree_fingerprint(repo))

    def test_staged_content_changes_the_fingerprint(self) -> None:
        temp, repo = self.make_repo()
        self.addCleanup(temp.cleanup)

        (repo / "tracked.txt").write_text("staged A\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        staged_a = collect_progress.worktree_fingerprint(repo)
        (repo / "tracked.txt").write_text("staged B\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")
        staged_b = collect_progress.worktree_fingerprint(repo)
        (repo / "tracked.txt").write_text("staged A\n", encoding="utf-8")
        git(repo, "add", "tracked.txt")

        self.assertNotEqual(staged_a, staged_b)
        self.assertEqual(staged_a, collect_progress.worktree_fingerprint(repo))

    def test_fingerprint_works_before_the_first_commit(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        git(repo, "init", "--quiet")
        (repo / "new.txt").write_text("unborn\n", encoding="utf-8")
        git(repo, "add", "new.txt")

        self.assertRegex(collect_progress.worktree_fingerprint(repo), r"^[0-9a-f]{64}$")

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

    def test_private_addresses_with_ports_are_redacted(self) -> None:
        self.assertEqual(
            "server <PRIVATE_ADDRESS>:8080 web http://<PRIVATE_ADDRESS>:3000/x db <PRIVATE_ADDRESS>:5432",
            collect_progress.sanitize_text(
                "server 10.0.0.1:8080 web http://192.168.1.5:3000/x db 127.0.0.1:5432"
            ),
        )

    def test_ipv4_mapped_ipv6_addresses_are_redacted(self) -> None:
        self.assertEqual("mapped <PRIVATE_ADDRESS> end", collect_progress.sanitize_text("mapped ::ffff:10.0.0.1 end"))

    def test_public_addresses_and_version_strings_survive(self) -> None:
        self.assertEqual(
            "dns 8.8.8.8 release 1.2.3.4 build 2.39.1.1 at 12:30",
            collect_progress.sanitize_text("dns 8.8.8.8 release 1.2.3.4 build 2.39.1.1 at 12:30"),
        )

    def test_quoted_secret_values_are_redacted_whole(self) -> None:
        self.assertEqual('password=<REDACTED> done', collect_progress.sanitize_text('password: "hunter 2" done'))

    def test_secret_assignment_does_not_span_lines(self) -> None:
        self.assertEqual("password:\nnext line", collect_progress.sanitize_text("password:\nnext line"))

    def test_words_that_merely_start_with_a_secret_keyword_survive(self) -> None:
        self.assertEqual(
            "tokenizer = tokenize(text) passwordless=true secretary:jane",
            collect_progress.sanitize_text("tokenizer = tokenize(text) passwordless=true secretary:jane"),
        )

    def test_bare_vendor_credentials_are_redacted(self) -> None:
        samples = {
            "sk-ant-api03-AAAAbbbbCCCCddddEEEEffff1234",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "github_pat_ABCDEFGHIJKLMNOPQRSTUV_wxyz0123456789",
            "AKIAIOSFODNN7EXAMPLE",
            "xoxb-123456789012-abcdefghijklmnop",
            "glpat-ABCDEFGHIJKLMNOPQRST",
            "AIzaSyA1234567890abcdefghijklmnopqrstuv",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I",
        }
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual("token <REDACTED> end", collect_progress.sanitize_text(f"token {sample} end"))

    def test_url_credentials_are_redacted(self) -> None:
        self.assertEqual(
            "clone https://user:<REDACTED>@example.com/repo.git",
            collect_progress.sanitize_text("clone https://user:s3cr3t-pw@example.com/repo.git"),
        )

    def test_basic_authorization_values_are_redacted(self) -> None:
        self.assertEqual(
            "Authorization: Basic <REDACTED>",
            collect_progress.sanitize_text("Authorization: Basic dXNlcjpwYXNzd29yZA=="),
        )

    def test_sanitization_is_idempotent(self) -> None:
        raw = (
            "api_key=abc123 Bearer xyz.123 10.0.0.1:8080 ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
            "https://user:pw@example.com -----BEGIN RSA PRIVATE KEY-----"
        )
        once = collect_progress.sanitize_text(raw)
        self.assertEqual(once, collect_progress.sanitize_text(once))

    def test_degenerate_home_values_are_not_substituted(self) -> None:
        with mock.patch.dict(collect_progress.os.environ, {"HOME": "/", "USERPROFILE": "C:\\"}, clear=False):
            with mock.patch.object(collect_progress.Path, "home", staticmethod(lambda: Path("/"))):
                self.assertEqual("a/b/c", collect_progress.sanitize_text("a/b/c"))

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
