import io
import tarfile

import pytest

from kalinka_plugin_roon.install import platform_archive, unpack


@pytest.mark.parametrize(
    "machine,bits,target",
    [
        ("x86_64", 64, "x64"),
        ("aarch64", 64, "armv8"),
        ("armv7l", 32, "armv7hf"),
        ("aarch64", 32, "armv7hf"),
        ("armv8l", 32, "armv7hf"),
    ],
)
def test_platform_selects_userspace_architecture(machine, bits, target):
    assert (
        platform_archive("Linux", machine, bits) == f"RoonBridge_linux{target}.tar.bz2"
    )


@pytest.mark.parametrize(
    "system,machine,bits",
    [
        ("Darwin", "aarch64", 64),
        ("Linux", "i686", 32),
        ("Linux", "armv6l", 32),
        ("Linux", "x86_64", 32),
    ],
)
def test_unsupported_platform_fails_explicitly(system, machine, bits):
    with pytest.raises(RuntimeError):
        platform_archive(system, machine, bits)


def archive(tmp_path, extra=None):
    path = tmp_path / "bridge.tar.bz2"
    with tarfile.open(path, "w:bz2") as bundle:
        for name in ["RoonBridge/start.sh", "RoonBridge/check.sh"]:
            member = tarfile.TarInfo(name)
            member.size, member.mode = 5, 0o755
            bundle.addfile(member, io.BytesIO(b"hello"))
        if extra:
            bundle.addfile(extra)
    return path


def test_extracts_executable_scripts(tmp_path):
    path = archive(tmp_path)
    root = unpack(path, tmp_path / "extracted")
    assert (root / "start.sh").stat().st_mode & 0o111


@pytest.mark.parametrize(
    "name", ["/tmp/evil", "RoonBridge/../../escape", "other/start.sh"]
)
def test_rejects_archive_traversal(tmp_path, name):
    path = archive(tmp_path, tarfile.TarInfo(name))
    with pytest.raises(RuntimeError, match="Unsafe"):
        unpack(path, tmp_path / "extracted")


def test_rejects_symlink_escape(tmp_path):
    member = tarfile.TarInfo("RoonBridge/escape")
    member.type, member.linkname = tarfile.SYMTYPE, "../../outside"
    path = archive(tmp_path, member)
    with pytest.raises(tarfile.FilterError):
        unpack(path, tmp_path / "extracted")


def test_allows_official_relative_symlink(tmp_path):
    member = tarfile.TarInfo("RoonBridge/sub/start")
    member.type, member.linkname = tarfile.SYMTYPE, "../start.sh"
    root = unpack(archive(tmp_path, member), tmp_path / "extracted")
    assert (root / "sub/start").read_bytes() == b"hello"
