"""scripts/sync-am-i-exposed.sh against a made-up am-i-exposed and a copy of this layout."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "sync-am-i-exposed.sh"
# Throwaway repositories: no user or global git settings, hooks or identity of the machine.
ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.org",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.org",
}


def git(cwd, *args):
    out = subprocess.run(
        ["git", *args], check=False, cwd=cwd, env=ENV, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def write(root, files):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(text if isinstance(text, bytes) else text.encode())


def commit(root, files, message):
    write(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


NAV = "".join(f"line {i}\n" for i in range(1, 21))


@pytest.fixture
def repos(tmp_path):
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    up = tmp_path / "upstream"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    old = commit(
        up,
        {"src/nav.ts": NAV, "CLAUDE.md": "notes", "public/data/entity-index.bin": b"\x00one"},
        "v1",
    )
    me = tmp_path / "me"
    (me / "web").mkdir(parents=True)
    git(me, "init", "-q", "-b", "main")
    sha = hashlib.sha256(b"\x00one").hexdigest()
    commit(
        me,
        {
            # The snapshot, less what this repository leaves out, plus a change of its own.
            "web/src/nav.ts": NAV.replace("line 2\n", "line 2 + Wallets\n"),
            "web/UPSTREAM": f"{up.as_uri()} {old}\n",
            "web/public/data/entity-data.sha256": f"{sha}  entity-index.bin\n",
        },
        "snapshot",
    )
    return up, me, old


def sync(me, *args):
    return subprocess.run(
        ["bash", SCRIPT, *args],
        check=False,
        cwd=me,
        env=ENV,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_upstream_changes_merge_with_mine_and_the_left_out_stay_out(repos):
    up, me, old = repos
    new = commit(
        up,
        {
            "src/nav.ts": NAV.replace("line 19\n", "line 19 + Tutorial\n"),
            "src/new.ts": "export {};\n",
            "CLAUDE.md": "more notes",
            ".github/workflows/ci.yml": "on: push",
            "public/data/entity-index.bin": b"\x00two",
        },
        "v2",
    )
    done = sync(me)
    assert done.returncode == 0, done.stderr + done.stdout
    nav = (me / "web/src/nav.ts").read_text()
    assert "line 2 + Wallets\n" in nav and "line 19 + Tutorial\n" in nav
    assert (me / "web/src/new.ts").exists()
    assert not (me / "web/CLAUDE.md").exists() and not (me / "web/.github").exists()
    assert not (me / "web/public/data/entity-index.bin").exists()  # fetched at build time
    assert (me / "web/UPSTREAM").read_text() == f"{up.as_uri()} {new}\n"
    two = hashlib.sha256(b"\x00two").hexdigest()
    assert (me / "web/public/data/entity-data.sha256").read_text() == f"{two}  entity-index.bin\n"
    assert git(me, "log", "--format=%s") == "snapshot"  # nothing committed for me
    git(me, "commit", "-q", "-am", "merge am-i-exposed")
    assert "already" in sync(me, new).stdout and old != new


def test_a_conflict_is_left_in_the_file_and_said(repos):
    up, me, _ = repos
    commit(up, {"src/nav.ts": NAV.replace("line 2\n", "line 2 + Graph\n")}, "v2")
    done = sync(me)
    assert done.returncode != 0 and "conflict" in done.stdout
    assert "<<<<<<<" in (me / "web/src/nav.ts").read_text()


def test_it_refuses_to_run_over_uncommitted_work(repos):
    _, me, _ = repos
    (me / "web/src/nav.ts").write_text("half done")
    done = sync(me)
    assert done.returncode == 1 and "commit or set aside" in done.stderr
