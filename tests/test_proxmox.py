"""proxmox/install.sh against stand-ins for the Proxmox VE tools, which record each call."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "proxmox" / "install.sh"

STUBS = {
    "id": 'echo "${FAKE_UID:-0}"',
    "pvesh": "echo 105",
    "pvesm": 'printf "Name Type Status\\nlocal-lvm lvmthin active\\n"',
    "pveam": """case "$1" in
  available) printf "system debian-12-standard_12.12-1_amd64.tar.zst\\nsystem debian-13-standard_13.1-2_amd64.tar.zst\\nsystem debian-13-standard_13.1-1_amd64.tar.zst\\n" ;;
  list) echo "NAME SIZE" ;;
esac""",
    "pct": """case "$1" in
  status) exit 2 ;;  # no such container
  exec) shift 3
    [ "$1" = "${FAKE_FAIL:-}" ] && exit 1
    case "$1" in
      curl) printf 401 ;;
      hostname) echo "192.168.1.50 fd00::50" ;;
      sh) case "$3" in *"cat /data/password"*) echo s3cret-made-on-first-start ;; esac ;;
    esac ;;
esac""",
}


def run(tmp_path, **env):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        stub = bin_ / name
        stub.write_text(f'#!/bin/bash\necho "{name} $*" >>"$FAKE_DIR/calls"\n{body}\n')
        stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "FAKE_DIR": str(tmp_path), **env}
    return subprocess.run(
        ["bash", SCRIPT], check=False, env=env, capture_output=True, text=True, timeout=60
    )


def test_one_command_makes_a_container_running_both_apps(tmp_path):
    done = run(tmp_path)
    assert done.returncode == 0, done.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    assert "pveam download local debian-13-standard_13.1-2_amd64.tar.zst" in calls  # the newest
    create = next(c for c in calls if c.startswith("pct create"))
    for part in (
        "105 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst",
        "--unprivileged 1 --features nesting=1,keyctl=1",
        "--rootfs local-lvm:16",
        "--net0 name=eth0,bridge=vmbr0,ip=dhcp",
        "--onboot 1 --start 1",
    ):
        assert part in create
    order = [c.split()[4] if c.startswith("pct exec") else c.split()[1] for c in calls]
    assert order.index("create") < order.index("git") < order.index("curl")
    assert any("docker compose up -d --build" in c for c in calls)
    clone = next(c for c in calls if " git clone " in c)
    assert "--recurse-submodules" not in clone  # a plain clone: nothing to build by hand
    assert "http://192.168.1.50:8000/tracker/" in done.stdout
    assert "s3cret-made-on-first-start" in done.stdout  # the container's own password
    assert "pct destroy" not in done.stderr


def test_it_refuses_to_run_off_the_host_or_over_a_container(tmp_path):
    refused = run(tmp_path, FAKE_UID="1000").stderr
    assert "as root" in refused and "pct destroy" not in refused
    small = run(tmp_path, LXC_MEMORY="2048")
    assert small.returncode == 1 and "at least 3072" in small.stderr  # the build needs ~2.5 GB
    assert "pct destroy" not in small.stderr
    STUBS["pct"] = STUBS["pct"].replace("status) exit 2", "status) exit 0")
    try:
        taken = run(tmp_path, LXC_ID="200")
    finally:
        STUBS["pct"] = STUBS["pct"].replace("status) exit 0", "status) exit 2")
    assert taken.returncode == 1 and "container 200 exists already" in taken.stderr
    assert "pct destroy" not in taken.stderr  # not ours to remove
    assert not any(
        c.startswith("pct create") for c in (tmp_path / "calls").read_text().splitlines()
    )


def test_a_failure_after_the_container_exists_says_how_to_remove_it(tmp_path):
    failed = run(tmp_path, FAKE_FAIL="git")  # the clone fails, under set -e
    assert failed.returncode != 0
    assert "pct destroy 105 --force" in failed.stderr
