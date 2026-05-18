"""Pure-Python IPK builder.

Mirrors the Makefile but does not require `make` or `ar` - useful on
Windows dev hosts. Produces the same IPK layout opkg expects:

    !<arch>
    debian-binary    -> b"2.0\\n"
    control.tar.gz   -> tar of CONTROL/* (with control file)
    data.tar.gz      -> tar of src/usr/...

Usage:
    python build.py
"""

import io
import os
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(ROOT, "src")
CONTROL_DIR = os.path.join(ROOT, "CONTROL")
BUILD_DIR = os.path.join(ROOT, "build")

PACKAGE_NAME = "enigma2-plugin-extensions-fbc-channelspeedchange"


def _read_control_field(field):
    path = os.path.join(CONTROL_DIR, "control")
    with open(path) as fh:
        for line in fh:
            if line.startswith(field + ":"):
                return line.split(":", 1)[1].strip()
    raise SystemExit("control file missing field: %s" % field)


def _make_tar_gz(out_path, src_root, arcname_prefix=""):
    """Create a tar.gz at out_path with files from src_root.

    Forces owner/group to 0/root so the package installs cleanly on the
    target box regardless of who built it.
    """
    with tarfile.open(out_path, "w:gz", format=tarfile.USTAR_FORMAT) as tar:
        for dirpath, _dirnames, filenames in os.walk(src_root):
            # Always emit a directory entry so empty dirs survive.
            rel_dir = os.path.relpath(dirpath, src_root)
            if rel_dir == ".":
                rel_dir = ""
            if rel_dir:
                arc = os.path.join(arcname_prefix, rel_dir).replace("\\", "/")
                info = tarfile.TarInfo(name=arc)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = int(time.time())
                tar.addfile(info)
            for fname in sorted(filenames):
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, src_root)
                arc = os.path.join(arcname_prefix, rel).replace("\\", "/")
                info = tar.gettarinfo(full, arcname=arc)
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mode = 0o644
                with open(full, "rb") as fh:
                    tar.addfile(info, fh)


def _make_ar(out_path, members):
    """Write a System V / BSD compatible ar archive.

    members: list of (name, bytes). Names <= 16 chars (no special handling
    needed for the three IPK members: debian-binary, control.tar.gz,
    data.tar.gz).
    """
    now = int(time.time())
    with open(out_path, "wb") as fh:
        fh.write(b"!<arch>\n")
        for name, data in members:
            if len(name) > 16:
                raise ValueError("ar member name too long: %s" % name)
            header = (
                name.ljust(16).encode("ascii") +
                str(now).ljust(12).encode("ascii") +
                b"0     " +
                b"0     " +
                b"100644  " +
                str(len(data)).ljust(10).encode("ascii") +
                b"`\n"
            )
            assert len(header) == 60, len(header)
            fh.write(header)
            fh.write(data)
            if len(data) % 2 == 1:
                fh.write(b"\n")


def main():
    version = _read_control_field("Version")
    arch = _read_control_field("Architecture")
    ipk_name = "%s_%s_%s.ipk" % (PACKAGE_NAME, version, arch)
    ipk_path = os.path.join(ROOT, ipk_name)

    # Clean & recreate build directory.
    if os.path.isdir(BUILD_DIR):
        import shutil
        shutil.rmtree(BUILD_DIR)
    os.makedirs(BUILD_DIR)

    data_tar = os.path.join(BUILD_DIR, "data.tar.gz")
    control_tar = os.path.join(BUILD_DIR, "control.tar.gz")

    _make_tar_gz(data_tar, SRC_DIR)
    _make_tar_gz(control_tar, CONTROL_DIR)

    with open(data_tar, "rb") as fh:
        data_bytes = fh.read()
    with open(control_tar, "rb") as fh:
        control_bytes = fh.read()

    _make_ar(
        ipk_path,
        [
            ("debian-binary", b"2.0\n"),
            ("control.tar.gz", control_bytes),
            ("data.tar.gz", data_bytes),
        ],
    )
    size = os.path.getsize(ipk_path)
    print("Built %s (%d bytes)" % (os.path.basename(ipk_path), size))


if __name__ == "__main__":
    main()
