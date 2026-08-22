#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
# IPv6 is matched first so that IPv4-mapped forms such as ``::ffff:10.0.0.1`` are
# consumed whole; the IPv4 pattern deliberately permits a trailing ``:`` so that
# ``10.0.0.1:8080`` and ``http://192.168.1.5:3000`` are still redacted.
# Alphanumeric IPv6 boundaries avoid treating scope syntax such as ``foo::bar``
# as a truncated address.
IPV6 = re.compile(r"(?<![0-9A-Za-z_:])[0-9A-Fa-f]*:[0-9A-Fa-f:.]+(?![0-9A-Za-z_:])")
IPV4 = re.compile(r"(?<![0-9A-Fa-f.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9A-Za-z.])")
# The secret keyword must end a word component: ``password`` and ``api_keys`` are
# secrets, ``tokenizer`` and ``passwordless`` are not.
SECRET_WORD = r"(?:api[_-]?key|access[_-]?token|token|password|passwd|secret)s?(?![A-Za-z])"
SECRET_VALUE = r"(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)"
SECRET = re.compile(
    rf"(?i)\b(?P<secret_key>[A-Za-z0-9_-]*{SECRET_WORD})[ \t]*[:=][ \t]*(?P<secret_value>{SECRET_VALUE})"
)
SECRET_FLAG = re.compile(rf"(?i)(?P<secret_flag>--{SECRET_WORD}[ \t]+)(?P<secret_value>{SECRET_VALUE})")
BEARER = re.compile(r"(?i)\b(Bearer|Basic)[ \t]+[A-Za-z0-9._~+/-]+=*")
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
# Credentials that carry no key name and would otherwise survive as bare words.
CREDENTIAL = re.compile(
    r"\b("
    r"sk-ant-[A-Za-z0-9_-]{16,}"
    r"|sk-(?:proj-|live-|test-)?[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|glpat-[A-Za-z0-9_-]{16,}"
    r"|xox[abeoprs]-[A-Za-z0-9-]{10,}"
    r"|A(?:KIA|SIA)[0-9A-Z]{16}"
    r"|AIza[A-Za-z0-9_-]{35}"
    r"|npm_[A-Za-z0-9]{36}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")"
)
URL_CREDENTIAL = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)[^/\s@]+@")


class ProgressError(RuntimeError):
    pass


def run_git(
    repo: Path,
    *args: str,
    allow_failure: bool = False,
    input_data: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    command = ["git", "-c", "core.quotepath=false", *args]
    try:
        result = subprocess.run(
            command,
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            input=input_data,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProgressError(f"Git command could not run: {args[0]}") from exc
    if result.returncode and not allow_failure:
        raise ProgressError(f"Git command failed: {args[0]}")
    return result


def git_text(repo: Path, *args: str) -> str:
    return run_git(repo, *args).stdout.decode("utf-8", "replace").strip()


def resolve_ref(repo: Path, value: str) -> str:
    resolved = git_text(repo, "rev-parse", "--verify", f"{value}^{{commit}}")
    if not FULL_SHA.fullmatch(resolved):
        raise ProgressError(f"Reference did not resolve to a full commit: {value}")
    return resolved


def repository_id(repo: Path, head: str = "HEAD") -> str:
    roots = [line for line in git_text(repo, "rev-list", "--max-parents=0", head).splitlines() if line]
    if not roots:
        raise ProgressError("Repository has no root commit")
    return hashlib.sha256("\n".join(sorted(roots)).encode("ascii")).hexdigest()


def branch_name(repo: Path, branch_key: str | None = None) -> tuple[str, bool]:
    result = run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allow_failure=True)
    if result.returncode == 0:
        return sanitize_text(result.stdout.decode("utf-8", "replace").strip()), False
    if not branch_key:
        raise ProgressError("Detached HEAD requires --branch-key")
    return sanitize_text(branch_key), True


def home_paths() -> list[str]:
    """Return home directories worth redacting, longest first.

    Very short values such as ``/`` or ``C:\\`` are ignored: substituting them
    would corrupt every path in the report without hiding anything private.
    """
    try:
        candidates = {str(Path.home())}
    except (OSError, RuntimeError):
        candidates = set()
    candidates.update({os.environ.get("USERPROFILE", ""), os.environ.get("HOME", "")})
    usable = {item.rstrip("\\/") for item in candidates if item}
    return sorted((item for item in usable if len(item) >= 4), key=len, reverse=True)


def unmatched_trailing_delimiters(value: str) -> str:
    """Keep code delimiters that surround an unquoted assignment value."""
    if value[:1] in {'"', "'"}:
        return ""
    pairs = {")": "(", "]": "[", "}": "{"}
    remaining = value
    trailing = ""
    while remaining and remaining[-1] in pairs:
        closer = remaining[-1]
        if remaining.count(closer) <= remaining.count(pairs[closer]):
            break
        trailing = closer + trailing
        remaining = remaining[:-1]
    return trailing


def sanitize_text(value: Any) -> str:
    text = str(value).replace("\x00", "�")
    text = "".join(character if character in "\n\t" or ord(character) >= 32 else "�" for character in text)
    for home in home_paths():
        text = re.sub(re.escape(home), "<HOME>", text, flags=re.IGNORECASE)
        text = re.sub(re.escape(home.replace("\\", "/")), "<HOME>", text, flags=re.IGNORECASE)
    text = PRIVATE_KEY.sub("<PRIVATE_KEY_REDACTED>", text)
    text = URL_CREDENTIAL.sub(lambda match: f"{match.group(1)}<REDACTED>@", text)
    text = SECRET.sub(
        lambda match: f"{match.group('secret_key')}=<REDACTED>"
        f"{unmatched_trailing_delimiters(match.group('secret_value'))}",
        text,
    )
    text = SECRET_FLAG.sub(
        lambda match: f"{match.group('secret_flag')}<REDACTED>"
        f"{unmatched_trailing_delimiters(match.group('secret_value'))}",
        text,
    )
    text = BEARER.sub(lambda match: f"{match.group(1)} <REDACTED>", text)
    text = CREDENTIAL.sub("<REDACTED>", text)

    def replace_address(match: re.Match[str]) -> str:
        candidate = match.group(0)
        if candidate == "::":
            return candidate
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            return candidate
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return "<PRIVATE_ADDRESS>"
        if isinstance(address, ipaddress.IPv4Address) and address in ipaddress.ip_network("100.64.0.0/10"):
            return "<PRIVATE_ADDRESS>"
        return candidate

    return IPV4.sub(replace_address, IPV6.sub(replace_address, text))


def load_state(path: Path, repo_id: str, branch: str) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schemaVersion") != 1 or not isinstance(payload.get("branches"), dict):
            raise ProgressError("Accepted baseline state is invalid")
        ref = payload["branches"][f"{repo_id}:{branch}"]["toRef"]
    except ProgressError:
        raise
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProgressError("No accepted baseline exists for this repository and branch") from exc
    if not isinstance(ref, str) or not FULL_SHA.fullmatch(ref):
        raise ProgressError("Accepted baseline is invalid")
    return ref


def require_ancestor(repo: Path, base: str, head: str) -> None:
    reachable = run_git(repo, "cat-file", "-e", f"{base}^{{commit}}", allow_failure=True)
    if reachable.returncode:
        raise ProgressError("Baseline commit is no longer reachable; provide an explicit --base")
    ancestor = run_git(repo, "merge-base", "--is-ancestor", base, head, allow_failure=True)
    if ancestor.returncode:
        raise ProgressError("Baseline is not an ancestor of head; provide an explicit valid --base")


def worktree_fingerprint(repo: Path) -> str:
    status = run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    if not status:
        return "clean"
    digest = hashlib.sha256(status)
    # Porcelain status reports only that a path is staged, not which bytes were
    # staged, so two different staged contents would otherwise share a
    # fingerprint. diff-index contributes the staged blob IDs directly.
    staged = run_git(repo, "diff-index", "--cached", "--no-renames", "-z", "HEAD", "--", allow_failure=True)
    digest.update(staged.stdout if staged.returncode == 0 else b"<no-head>")
    paths = run_git(repo, "ls-files", "-m", "-o", "--exclude-standard", "-z").stdout.decode("utf-8", "replace").split("\0")
    changed_paths = sorted(item for item in paths if item)
    content_hashes = hash_worktree_paths(repo, changed_paths)
    for path, content in zip(changed_paths, content_hashes):
        digest.update(path.encode("utf-8", "replace"))
        digest.update(content)
    return digest.hexdigest()


def hash_worktree_paths(repo: Path, paths: list[str]) -> list[bytes]:
    if not paths:
        return []
    if any("\n" in path or "\r" in path for path in paths):
        return hash_worktree_paths_individually(repo, paths)

    batch_input = ("\n".join(paths) + "\n").encode("utf-8", "replace")
    result = run_git(
        repo,
        "hash-object",
        "--no-filters",
        "--stdin-paths",
        allow_failure=True,
        input_data=batch_input,
    )
    hashes = result.stdout.splitlines()
    if result.returncode == 0 and len(hashes) == len(paths):
        return hashes
    return hash_worktree_paths_individually(repo, paths)


def hash_worktree_paths_individually(repo: Path, paths: list[str]) -> list[bytes]:
    hashes: list[bytes] = []
    for path in paths:
        content = run_git(repo, "hash-object", "--no-filters", "--", path, allow_failure=True)
        hashes.append(content.stdout.strip() if content.returncode == 0 else b"<unreadable>")
    return hashes


def parse_name_status(data: bytes) -> list[tuple[str, str]]:
    tokens = data.decode("utf-8", "replace").split("\0")
    result: list[tuple[str, str]] = []
    index = 0
    while index + 1 < len(tokens) and tokens[index]:
        status = tokens[index]
        path = tokens[index + 1]
        result.append((status, path))
        index += 2
    return result


def parse_numstat(data: bytes) -> dict[str, tuple[int | None, int | None]]:
    result: dict[str, tuple[int | None, int | None]] = {}
    for record in data.decode("utf-8", "replace").split("\0"):
        if not record:
            continue
        parts = record.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        result[path] = (
            None if added == "-" else int(added),
            None if deleted == "-" else int(deleted),
        )
    return result


def collect_inventory(repo: Path, base: str, head: str, max_files: int, max_commits: int) -> dict[str, Any]:
    statuses = parse_name_status(run_git(repo, "diff", "--no-renames", "--name-status", "-z", base, head).stdout)
    deltas = parse_numstat(run_git(repo, "diff", "--no-renames", "--numstat", "-z", base, head).stdout)
    files = []
    for status, raw_path in statuses[:max_files]:
        added, deleted = deltas.get(raw_path, (0, 0))
        files.append(
            {
                "status": sanitize_text(status),
                "path": sanitize_text(raw_path),
                "added": added,
                "deleted": deleted,
                "binary": added is None or deleted is None,
                "source": "git",
            }
        )
    total_commits = int(git_text(repo, "rev-list", "--count", f"{base}..{head}"))
    log = run_git(repo, "log", "-z", f"--max-count={max_commits}", "--format=%H%x1f%s%x1f%aI", f"{base}..{head}").stdout
    parsed_commits = []
    for record in log.decode("utf-8", "replace").split("\0"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f", 2)
        if len(parts) == 3:
            parsed_commits.append({"sha": parts[0], "subject": sanitize_text(parts[1]), "authoredAt": parts[2], "source": "git"})
    return {
        "files": files,
        "totalFiles": len(statuses),
        "omittedFiles": max(0, len(statuses) - len(files)),
        "filesTruncated": len(statuses) > len(files),
        "commits": parsed_commits,
        "totalCommits": total_commits,
        "omittedCommits": max(0, total_commits - len(parsed_commits)),
        "commitsTruncated": total_commits > len(parsed_commits),
        "linesAdded": sum(added or 0 for added, _ in deltas.values()),
        "linesDeleted": sum(deleted or 0 for _, deleted in deltas.values()),
    }


def atomic_json(path: Path, payload: dict[str, Any], overwrite: bool = False) -> None:
    path = path.resolve()
    if path.exists() and not overwrite:
        raise ProgressError(f"Output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo).resolve()
    head = resolve_ref(repo, args.head)
    repo_id = repository_id(repo, head)
    branch, detached = branch_name(repo, args.branch_key)
    if args.base:
        base = resolve_ref(repo, args.base)
    elif args.state:
        base = load_state(Path(args.state), repo_id, branch)
    else:
        raise ProgressError("Use --base or provide --state with an accepted baseline")
    require_ancestor(repo, base, head)
    inventory = collect_inventory(repo, base, head, args.max_files, args.max_commits)
    fingerprint = worktree_fingerprint(repo)
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return {
        "schemaVersion": 1,
        "generatedAt": now,
        "repository": {"id": repo_id, "label": sanitize_text(repo.name), "branch": branch, "detached": detached, "worktreeFingerprint": fingerprint, "source": "git"},
        "range": {"fromRef": base, "toRef": head, "source": "git"},
        "report": {
            "title": "Project progress",
            "lang": "en",
            "status": "incomplete",
            "outcome": {"text": "Describe the observable outcome.", "source": "agent"},
            "before": {"text": "Describe the accepted baseline.", "source": "agent"},
            "after": {"text": "Describe the current verified state.", "source": "agent"},
        },
        "metrics": [
            {"label": "Changed files", "value": inventory["totalFiles"], "source": "git"},
            {"label": "Commits", "value": inventory["totalCommits"], "source": "git"},
            {"label": "Lines added", "value": inventory["linesAdded"], "source": "git"},
            {"label": "Lines removed", "value": inventory["linesDeleted"], "source": "git"},
        ],
        "claims": [],
        "evidence": [],
        "inventory": inventory,
        "qualityGate": {"status": "incomplete", "browserQa": {"completed": False, "source": "agent", "viewports": []}},
        "privacy": {"textSanitized": True, "capturesReviewed": False},
        "collection": {"source": "tool", "maxFiles": args.max_files, "maxCommits": args.max_commits},
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Collect a bounded, sanitized Git progress manifest.")
    result.add_argument("--repo", default=".")
    result.add_argument("--base")
    result.add_argument("--head", default="HEAD")
    result.add_argument("--state")
    result.add_argument("--branch-key")
    result.add_argument("--max-files", type=int, default=200)
    result.add_argument("--max-commits", type=int, default=50)
    result.add_argument("--output", required=True)
    result.add_argument("--overwrite", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.max_files < 1 or args.max_commits < 1:
        raise SystemExit("Bounds must be positive")
    try:
        atomic_json(Path(args.output), build_manifest(args), args.overwrite)
    except ProgressError as exc:
        raise SystemExit(f"progress collection failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
