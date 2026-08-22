from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import collect_progress


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


if __name__ == "__main__":
    unittest.main()
