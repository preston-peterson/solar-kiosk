"""install.sh's system check, run in bash with stand-in `uname`, `dpkg` and `getconf` commands."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


@pytest.fixture
def run(tmp_path):
    """run(snippet, machine=..., dpkg=..., bits=...) -> (exit code, stdout + stderr). A None leaves that command
    out entirely, as on a system without it. PATH holds only the stand-ins, so the real ones can't answer."""
    script = (ROOT / "install.sh").read_text()
    body, last = script.rstrip("\n").rsplit("\n", 1)
    assert last == 'main "$@"', "install.sh should end by calling main"
    lib = tmp_path / "install-lib.sh"
    lib.write_text(body + "\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    def stub(name: str, script: str) -> None:
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(0o755)

    def go(snippet: str, *, machine: str = "aarch64", dpkg: str | None = "arm64", bits: str | None = "64"):
        for p in bin_dir.iterdir():
            p.unlink()
        stub("uname", f'case "$1" in -s) echo Linux ;; -m) echo {machine} ;; esac\n')
        if dpkg is not None:
            stub("dpkg", f'[ "$1" = --print-architecture ] && echo {dpkg}\n')
        if bits is not None:
            stub("getconf", f'[ "$1" = LONG_BIT ] && echo {bits}\n')
        out = subprocess.run([BASH, "-c", f'source "{lib}"; {snippet}'], capture_output=True, text=True,
                             env={"PATH": str(bin_dir)}, timeout=20)
        return out.returncode, out.stdout + out.stderr

    return go


@pytest.mark.parametrize(("dpkg", "bits", "expected"), [
    ("arm64", "64", 1),      # 64-bit Raspberry Pi OS
    ("amd64", "64", 1),      # an ordinary 64-bit PC
    ("armhf", "64", 0),      # 32-bit Raspberry Pi OS: dpkg knows, whatever getconf says
    ("armel", None, 0),
    ("i386", "32", 0),
    (None, "32", 0),         # no dpkg: getconf decides
    (None, "64", 1),
    (None, None, 1),         # neither: assume 64-bit, as before
])
def test_is_32bit_system(run, dpkg, bits, expected):
    code, _ = run("is_32bit_system", dpkg=dpkg, bits=bits)
    assert code == expected


needs_systemd = pytest.mark.skipif(not os.path.isdir("/run/systemd/system"),
                                   reason="check_system first checks this machine uses systemd")


@needs_systemd
@pytest.mark.parametrize(("machine", "dpkg", "arch"), [("aarch64", "arm64", "aarch64"), ("x86_64", "amd64", "x86_64")])
def test_check_system_accepts_64bit(run, machine, dpkg, arch):
    code, out = run('check_system; echo "ARCH=$ARCH"', machine=machine, dpkg=dpkg)
    assert (code, out.strip()) == (0, f"ARCH={arch}")


@needs_systemd
@pytest.mark.parametrize(("machine", "dpkg", "bits"), [
    ("aarch64", "armhf", "32"),   # a Pi 4 or 5 on 32-bit Raspberry Pi OS: 64-bit kernel, 32-bit system
    ("aarch64", None, "32"),
    ("armv7l", "armhf", "32"),    # a 32-bit kernel too
])
def test_check_system_refuses_32bit(run, machine, dpkg, bits):
    code, out = run('check_system; echo "ARCH=$ARCH"', machine=machine, dpkg=dpkg, bits=bits)
    assert code == 1
    assert "This is a 32-bit system" in out and "Raspberry Pi OS (64-bit)" in out
    assert "ARCH=" not in out
