"""Install the unmodified official Bridge archive in plugin-owned storage."""

import asyncio
import json
import platform
import shutil
import struct
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import httpx

DOWNLOAD_ROOT = "https://download.roonlabs.net/builds"
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_UNPACKED = 1024 * 1024 * 1024


def platform_archive(system=None, machine=None, bits=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    bits = bits or struct.calcsize("P") * 8
    if system != "Linux":
        raise RuntimeError("Roon Bridge plugin currently supports Linux only")
    if machine in ("x86_64", "amd64") and bits == 64:
        target = "x64"
    elif machine in ("aarch64", "arm64") and bits == 64:
        target = "armv8"
    elif machine in ("armv7l", "armv8l", "aarch64", "arm64") and bits == 32:
        # check.sh verifies the hard-float ABI and library dependencies.
        target = "armv7hf"
    else:
        raise RuntimeError(f"No official Roon Bridge binary for {machine}/{bits}-bit")
    return f"RoonBridge_linux{target}.tar.bz2"


def unpack(archive: Path, destination: Path):
    with tarfile.open(archive, "r:bz2") as bundle:
        members = bundle.getmembers()
        if len(members) > 10000 or sum(m.size for m in members) > MAX_UNPACKED:
            raise RuntimeError("Roon Bridge archive exceeds extraction limits")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.parts[0] != "RoonBridge"
            ):
                raise RuntimeError("Unsafe path in Roon Bridge archive")
            # data_filter also checks symlinks, hardlinks, devices and modes.
            tarfile.data_filter(member, destination)
        bundle.extractall(destination, members=members, filter="data")
    bridge = destination / "RoonBridge"
    if not all((bridge / name).is_file() for name in ("start.sh", "check.sh")):
        raise RuntimeError("Incomplete Roon Bridge archive")
    # The data filter preserves executable bits on regular files.
    return bridge


async def install_bridge(root: Path):
    archive_name = platform_archive()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = root / "RoonBridge"
    marker = root / "platform.json"
    if destination.exists():
        if not marker.exists() or json.loads(marker.read_text()) != archive_name:
            raise RuntimeError(
                "Bridge install belongs to a different platform; remove it while disabled"
            )
        if not (destination / "start.sh").is_file():
            raise RuntimeError("Incomplete Bridge install; remove it while disabled")
        return destination
    # Same filesystem: only a complete archive becomes the live installation.
    with tempfile.TemporaryDirectory(prefix="download-", dir=root) as temp:
        staging = Path(temp)
        archive = staging / archive_name
        async with httpx.AsyncClient(follow_redirects=False, timeout=60) as client:
            async with client.stream(
                "GET", f"{DOWNLOAD_ROOT}/{archive_name}"
            ) as response:
                response.raise_for_status()
                size = 0
                with archive.open("wb") as out:
                    async for chunk in response.aiter_bytes(256 * 1024):
                        size += len(chunk)
                        if size > MAX_DOWNLOAD:
                            raise RuntimeError(
                                "Roon Bridge download exceeds size limit"
                            )
                        out.write(chunk)
        # Ensure cancellation waits for extraction before removing its directory.
        extraction = asyncio.create_task(asyncio.to_thread(unpack, archive, staging))
        try:
            bridge = await asyncio.shield(extraction)
        except asyncio.CancelledError:
            await extraction
            raise
        marker.write_text(json.dumps(archive_name))
        bridge.rename(destination)
    return destination


async def install_extension(root: Path):
    """Native packages ship dependencies; source/pip installs provision once."""
    source = Path(__file__).with_name("extension")
    bundled = Path("/usr/libexec/kalinka-plugin-roon/extension")
    if (bundled / "node_modules").is_dir() and all(
        (bundled / name).is_file()
        and (bundled / name).read_bytes() == (source / name).read_bytes()
        for name in ("main.js", "zones.js", "package-lock.json")
    ):
        return bundled
    import hashlib

    # Include helper source, so a plugin update does not reuse old JS simply
    # because its third-party dependencies stayed the same.
    lock = hashlib.sha256(
        b"".join(
            p.read_bytes()
            for p in sorted(source.iterdir())
            if p.suffix in (".js", ".json")
        )
    ).digest()
    destination = root / "extension"
    marker = destination / ".installed-lock"
    if marker.is_file() and (destination / "node_modules/node-roon-api").is_dir():
        if (destination / ".installed-lock").read_bytes() == lock:
            return destination
    if not shutil.which("npm"):
        raise RuntimeError(
            "Install Node.js 18+ and npm, or use the Debian plugin package"
        )
    destination.mkdir(parents=True, exist_ok=True)
    # A failed upgrade must never leave an old success marker beside a
    # partially replaced dependency tree (including after a later rollback).
    marker.unlink(missing_ok=True)
    for item in source.iterdir():
        if item.suffix in (".json", ".js"):
            shutil.copyfile(item, destination / item.name)
    from .process import run_checked

    await run_checked(
        ["npm", "ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=destination,
        timeout=180,
    )
    marker.write_bytes(lock)
    return destination
