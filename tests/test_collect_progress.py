from __future__ import annotations

import hashlib
import random
from pathlib import Path
import string
import subprocess
import tempfile
import unittest
from unittest import mock

from progress_receipt import collect as packaged_collect


collect_progress = packaged_collect._IMPLEMENTATION

SANITIZER_FUZZ_SEED = 0xC0DEC0DE
SANITIZER_FUZZ_LINES = 600
SANITIZER_SECRET_KINDS = (
    "anthropic",
    "openai",
    "github",
    "github_pat",
    "gitlab",
    "slack",
    "aws",
    "google",
    "npm",
    "jwt",
    "private_ipv4",
    "private_ipv6",
    "mapped_ipv6",
    "quoted_assignment",
    "plain_assignment",
    "quoted_flag",
    "bearer",
    "basic",
    "url_userinfo",
    "private_key_marker",
)


def random_characters(randomizer: random.Random, alphabet: str, length: int) -> str:
    return "".join(randomizer.choice(alphabet) for _ in range(length))


def sanitizer_secret_case(randomizer: random.Random, kind: str, index: int) -> tuple[str, str, str]:
    alphanumeric = string.ascii_letters + string.digits
    alphanumeric_dash = alphanumeric + "_-"
    uppercase = string.ascii_uppercase + string.digits
    unique = f"{index:04d}{random_characters(randomizer, alphanumeric, 20)}"

    if kind == "anthropic":
        value = "sk-ant-" + random_characters(randomizer, alphanumeric_dash, 28)
        return value, "<REDACTED>", value
    if kind == "openai":
        value = randomizer.choice(("sk-", "sk-proj-", "sk-live-", "sk-test-")) + random_characters(
            randomizer, alphanumeric, 28
        )
        return value, "<REDACTED>", value
    if kind == "github":
        value = randomizer.choice(("ghp_", "gho_", "ghu_", "ghs_", "ghr_")) + random_characters(
            randomizer, alphanumeric, 32
        )
        return value, "<REDACTED>", value
    if kind == "github_pat":
        value = "github_pat_" + random_characters(randomizer, alphanumeric + "_", 32)
        return value, "<REDACTED>", value
    if kind == "gitlab":
        value = "glpat-" + random_characters(randomizer, alphanumeric_dash, 24)
        return value, "<REDACTED>", value
    if kind == "slack":
        value = "xox" + randomizer.choice("abeoprs") + "-" + random_characters(randomizer, alphanumeric + "-", 24)
        return value, "<REDACTED>", value
    if kind == "aws":
        value = randomizer.choice(("AKIA", "ASIA")) + random_characters(randomizer, uppercase, 16)
        return value, "<REDACTED>", value
    if kind == "google":
        value = "AIza" + random_characters(randomizer, alphanumeric_dash, 35)
        return value, "<REDACTED>", value
    if kind == "npm":
        value = "npm_" + random_characters(randomizer, alphanumeric, 36)
        return value, "<REDACTED>", value
    if kind == "jwt":
        value = ".".join(
            (
                "eyJ" + random_characters(randomizer, alphanumeric_dash, 14),
                random_characters(randomizer, alphanumeric_dash, 18),
                random_characters(randomizer, alphanumeric_dash, 22),
            )
        )
        return value, "<REDACTED>", value
    if kind == "private_ipv4":
        address = randomizer.choice(
            (
                f"10.{randomizer.randrange(256)}.{randomizer.randrange(256)}.{randomizer.randrange(1, 255)}",
                f"172.{randomizer.randrange(16, 32)}.{randomizer.randrange(256)}.{randomizer.randrange(1, 255)}",
                f"192.168.{randomizer.randrange(256)}.{randomizer.randrange(1, 255)}",
                f"100.{randomizer.randrange(64, 128)}.{randomizer.randrange(256)}.{randomizer.randrange(1, 255)}",
            )
        )
        port = randomizer.randrange(1024, 65536)
        return f"http://{address}:{port}/health", f"http://<PRIVATE_ADDRESS>:{port}/health", address
    if kind == "private_ipv6":
        address = randomizer.choice((f"fd12::{index:x}", f"fc00::{index:x}", f"fe80::{index:x}", "::1"))
        return address, "<PRIVATE_ADDRESS>", address
    if kind == "mapped_ipv6":
        address = f"::ffff:10.{index % 256}.{randomizer.randrange(256)}.{randomizer.randrange(1, 255)}"
        return address, "<PRIVATE_ADDRESS>", address
    if kind == "quoted_assignment":
        value = f"phrase {unique} with spaces"
        key = randomizer.choice(("password", "api_key", "client_secret", "access_token", "tokens"))
        return f'{key}: "{value}"', f"{key}=<REDACTED>", value
    if kind == "plain_assignment":
        value = "value-" + unique
        key = randomizer.choice(("password", "passwd", "api-key", "secret", "token"))
        return f"{key} = {value}", f"{key}=<REDACTED>", value
    if kind == "quoted_flag":
        value = f"flag {unique} value"
        flag = randomizer.choice(("--password", "--token", "--api-key", "--access-token"))
        spacing = randomizer.choice((" ", "  ", "\t"))
        return f"{flag}{spacing}'{value}'", f"{flag}{spacing}<REDACTED>", value
    if kind == "bearer":
        value = random_characters(randomizer, alphanumeric_dash + ".", 42)
        return f"Bearer {value}", "Bearer <REDACTED>", value
    if kind == "basic":
        value = random_characters(randomizer, alphanumeric + "+/", 36) + "=="
        return f"Basic {value}", "Basic <REDACTED>", value
    if kind == "url_userinfo":
        value = "pw-" + unique
        return (
            f"https://build:{value}@git.example.test/team/repo.git",
            "https://build:<REDACTED>@git.example.test/team/repo.git",
            value,
        )
    if kind == "private_key_marker":
        value = "-----BEGIN OPENSSH PRIVATE KEY-----"
        return value, "<PRIVATE_KEY_REDACTED>", value
    raise AssertionError(f"unknown sanitizer fuzz kind: {kind}")


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
    def test_seeded_fuzz_corpus_redacts_secrets_without_mangling_safe_text(self) -> None:
        randomizer = random.Random(SANITIZER_FUZZ_SEED)
        raw_lines = ["Authorization: Bearer", "safeFollowerAlpha completed without credentials"]
        expected_lines = list(raw_lines)
        injected: list[tuple[str, str]] = []
        covered: set[str] = set()
        kinds: list[str] = []
        safe_controls = (
            "tokenizer = tokenize(safeValueAlpha)",
            "passwordless=true secretary:safeValueBeta",
            r"C:\workspace\safePathGamma\src\worker.py",
            "GET https://example.test/safeRouteDelta?trace=safeTraceEpsilon",
            "release 1.2.3.4 uses public DNS 8.8.8.8",
            "secretariat reviewed token_bucket and password_policy",
        )
        aggressive_controls = (
            ("tokens = load_feature()", "tokens=<REDACTED>"),
            ("client_tokens: safeFixtureValue", "client_tokens=<REDACTED>"),
        )
        templates = (
            "src/{safe_a}/client.py: configure({secret})  # {safe_b}",
            r"C:\workspace\{safe_a}\config.toml :: {secret} :: {safe_b}",
            "GET https://example.test/{safe_a}?trace={safe_b} -> {secret}",
            'log.info("{safe_a} finished {safe_b}")  # {secret}',
            'config["{safe_a}"] = "{safe_b}"  # {secret}',
            "Reviewed {safe_a} for release {safe_b}: {secret}",
        )

        while len(raw_lines) < SANITIZER_FUZZ_LINES:
            line_index = len(raw_lines)
            if line_index % 7 == 0:
                if randomizer.randrange(3) == 0:
                    raw, expected = randomizer.choice(aggressive_controls)
                else:
                    raw = expected = randomizer.choice(safe_controls)
                raw_lines.append(raw)
                expected_lines.append(expected)
                continue

            if not kinds:
                kinds = list(SANITIZER_SECRET_KINDS)
                randomizer.shuffle(kinds)
            kind = kinds.pop()
            covered.add(kind)
            raw_secret, redacted_secret, secret_value = sanitizer_secret_case(randomizer, kind, line_index)
            safe_a = f"safeCase{line_index:04d}{random_characters(randomizer, string.ascii_letters, 8)}"
            safe_b = f"traceMark{line_index:04d}{random_characters(randomizer, string.ascii_letters, 8)}"
            template = randomizer.choice(templates)
            raw_lines.append(template.format(safe_a=safe_a, safe_b=safe_b, secret=raw_secret))
            expected_lines.append(template.format(safe_a=safe_a, safe_b=safe_b, secret=redacted_secret))
            injected.append((kind, secret_value))

        raw_corpus = "\n".join(raw_lines)
        expected_corpus = "\n".join(expected_lines)
        sanitized = collect_progress.sanitize_text(raw_corpus)
        if sanitized != expected_corpus:
            actual_lines = sanitized.split("\n")
            mismatch = next(
                index
                for index in range(max(len(expected_lines), len(actual_lines)))
                if index >= len(expected_lines)
                or index >= len(actual_lines)
                or expected_lines[index] != actual_lines[index]
            )
            raw = raw_lines[mismatch] if mismatch < len(raw_lines) else "<missing>"
            expected = expected_lines[mismatch] if mismatch < len(expected_lines) else "<missing>"
            actual = actual_lines[mismatch] if mismatch < len(actual_lines) else "<missing>"
            self.fail(
                f"seed {SANITIZER_FUZZ_SEED:#x}, line {mismatch}:\n"
                f"raw:      {raw!r}\nexpected: {expected!r}\nactual:   {actual!r}"
            )

        self.assertEqual(set(SANITIZER_SECRET_KINDS), covered)
        for kind, secret_value in injected:
            with self.subTest(kind=kind, secret=secret_value):
                self.assertNotIn(secret_value, sanitized)
        self.assertEqual(sanitized, collect_progress.sanitize_text(sanitized))

    def test_assignment_secrets_are_redacted(self) -> None:
        sanitized = collect_progress.sanitize_text("api_key=abc123 password:open-sesame")
        self.assertEqual("api_key=<REDACTED> password=<REDACTED>", sanitized)

    def test_flag_secrets_are_redacted(self) -> None:
        sanitized = collect_progress.sanitize_text("tool --token secret-token --password 'hunter two'")
        self.assertEqual("tool --token <REDACTED> --password <REDACTED>", sanitized)

    def test_bearer_tokens_are_redacted(self) -> None:
        sanitized = collect_progress.sanitize_text("Authorization: Bearer abc.def-123_456")
        self.assertEqual("Authorization: Bearer <REDACTED>", sanitized)

    def test_authorization_value_does_not_span_lines(self) -> None:
        raw = "Authorization: Bearer\nsafeFollowerAlpha\nAuthorization: Basic\nsafeFollowerBeta"
        self.assertEqual(raw, collect_progress.sanitize_text(raw))

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

    def test_ipv6_sanitizer_preserves_scope_and_separator_syntax(self) -> None:
        raw = "C++ foo::bar std::vector separator :: end"
        self.assertEqual(raw, collect_progress.sanitize_text(raw))

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

    def test_secret_assignments_preserve_surrounding_code_delimiters(self) -> None:
        self.assertEqual(
            "configure(api_key=<REDACTED>) items[password=<REDACTED>] "
            "mapping{secret=<REDACTED>} tokens=<REDACTED> "
            "parenthesized password=<REDACTED> invoke(--token <REDACTED>)",
            collect_progress.sanitize_text(
                "configure(api_key=alpha) items[password=bravo] "
                "mapping{secret=charlie} tokens = load() "
                "parenthesized password=(delta) invoke(--token echo)"
            ),
        )

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
