"""Bump release download URLs in README.md and docs/install.md.

Pure Python stdlib. Rewrites two narrowly-scoped patterns in the
documentation to point at a new version:

  1. The GitHub release download URL - both the tag in the path and
     the version embedded in the IPK filename.
  2. The "vX.Y.Z is the current build" sentence in README.md.

Other version mentions (historical measurement notes, CHANGELOG
entries) are left untouched.

Usage:
    python tools/bump_release_urls.py --to 0.3.4
    python tools/bump_release_urls.py --to 0.3.4 --dry-run
    python tools/bump_release_urls.py --check

The "from" version is derived from the docs themselves (the version
the existing release URLs point at). CONTROL/control and the Makefile
are never written to by this script. The two are deliberately
decoupled so the bump can be invoked at any point in the release
flow regardless of whether CONTROL/control has already been bumped.

`--check` exits 1 if the documentation contains a release URL or
current-build sentence that does not match the version recorded in
CONTROL/control (and Makefile, which must agree). Intended for use
in CI / pre-tag verification.
"""

import argparse
import os
import re
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

PACKAGE = "enigma2-plugin-extensions-fbc-channelspeedchange"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

DOC_FILES = [
    "README.md",
    os.path.join("docs", "install.md"),
]


def read_control_version():
    path = os.path.join(REPO_ROOT, "CONTROL", "control")
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^Version:\s*(\S+)\s*$", line)
            if m:
                return m.group(1)
    raise SystemExit("CONTROL/control: no Version: line found")


def read_makefile_version():
    path = os.path.join(REPO_ROOT, "Makefile")
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^VERSION\s*:=\s*(\S+)\s*$", line)
            if m:
                return m.group(1)
    raise SystemExit("Makefile: no VERSION := line found")


def current_version():
    """Return the version both authoritative files agree on, else fail."""
    ctrl = read_control_version()
    mk = read_makefile_version()
    if ctrl != mk:
        raise SystemExit(
            "CONTROL/control Version (%s) and Makefile VERSION (%s) "
            "disagree - reconcile them before bumping doc URLs."
            % (ctrl, mk)
        )
    if not VERSION_RE.match(ctrl):
        raise SystemExit("Unexpected version format: %r" % ctrl)
    return ctrl


def url_pattern(version):
    """Regex matching the GitHub release download URL for a version.

    The tag path segment and the filename's embedded version are
    required to be the same value, so an out-of-sync edit doesn't
    silently match.
    """
    v = re.escape(version)
    return re.compile(
        r"releases/download/v"
        + v
        + r"/"
        + re.escape(PACKAGE)
        + r"_"
        + v
        + r"_all\.ipk"
    )


def current_build_pattern(version):
    return re.compile(r"v" + re.escape(version) + r" is the current build")


def any_url_pattern():
    return re.compile(
        r"releases/download/v(\d+\.\d+\.\d+)/"
        + re.escape(PACKAGE)
        + r"_(\d+\.\d+\.\d+)_all\.ipk"
    )


def any_current_build_pattern():
    return re.compile(r"v(\d+\.\d+\.\d+) is the current build")


def render_url(version):
    return (
        "releases/download/v"
        + version
        + "/"
        + PACKAGE
        + "_"
        + version
        + "_all.ipk"
    )


def render_current_build(version):
    return "v" + version + " is the current build"


def bump_file(path, old, new, dry_run):
    """Apply the two substitutions to one file. Returns the diff lines."""
    full = os.path.join(REPO_ROOT, path)
    with open(full, "r", encoding="utf-8") as fh:
        text = fh.read()

    new_text, n_url = url_pattern(old).subn(render_url(new), text)
    new_text, n_cb = current_build_pattern(old).subn(
        render_current_build(new), new_text
    )

    total = n_url + n_cb
    if total == 0:
        return path, 0, 0, []

    if not dry_run:
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(new_text)

    return path, n_url, n_cb, []


def doc_release_version():
    """Pick the version the documentation's release URLs currently point at.

    Looks at every doc file and returns the first release URL's version.
    If multiple URLs disagree, raises - the docs are inconsistent and the
    operator should reconcile them before bumping.

    The docs are the source of truth for "what version do install
    instructions ship right now"; CONTROL/control is the source of truth
    for "what version is being built". These are deliberately decoupled
    so this script can run at any point in the release flow.
    """
    seen = set()
    for path in DOC_FILES:
        full = os.path.join(REPO_ROOT, path)
        with open(full, "r", encoding="utf-8") as fh:
            for m in any_url_pattern().finditer(fh.read()):
                tag_v, file_v = m.group(1), m.group(2)
                if tag_v != file_v:
                    raise SystemExit(
                        "%s: release URL is internally inconsistent "
                        "(tag v%s, filename %s)" % (path, tag_v, file_v)
                    )
                seen.add(tag_v)
    if not seen:
        raise SystemExit(
            "No release URL found in any doc file. Cannot derive the "
            "current version automatically; check README.md and "
            "docs/install.md."
        )
    if len(seen) > 1:
        raise SystemExit(
            "Doc release URLs disagree on the version: %s. Reconcile "
            "them manually before bumping." % ", ".join(sorted(seen))
        )
    return seen.pop()


def cmd_bump(args):
    new = args.to
    if not VERSION_RE.match(new):
        raise SystemExit("--to: not a valid X.Y.Z version: %r" % new)
    old = doc_release_version()
    if old == new:
        print("Documentation already points at v%s - nothing to do." % new)
        return 0

    total = 0
    for path in DOC_FILES:
        _, n_url, n_cb, _ = bump_file(path, old, new, args.dry_run)
        if n_url or n_cb:
            print(
                "%s%s: %d URL%s, %d current-build sentence%s"
                % (
                    "would update " if args.dry_run else "updated ",
                    path,
                    n_url,
                    "" if n_url == 1 else "s",
                    n_cb,
                    "" if n_cb == 1 else "s",
                )
            )
            total += n_url + n_cb
        else:
            print("%s: no matches for v%s" % (path, old))

    if total == 0:
        print(
            "No URL or current-build matches for v%s found." % old
        )
        return 0
    return 0


def cmd_check(args):
    """Exit 1 if any release URL or current-build sentence in the docs
    refers to a version other than the current CONTROL/control version.
    """
    expected = current_version()
    stale = []
    for path in DOC_FILES:
        full = os.path.join(REPO_ROOT, path)
        with open(full, "r", encoding="utf-8") as fh:
            text = fh.read()
        for m in any_url_pattern().finditer(text):
            tag_v, file_v = m.group(1), m.group(2)
            if tag_v != expected or file_v != expected:
                stale.append(
                    "%s: release URL points at v%s / file v%s (expected %s)"
                    % (path, tag_v, file_v, expected)
                )
        for m in any_current_build_pattern().finditer(text):
            v = m.group(1)
            if v != expected:
                stale.append(
                    "%s: 'current build' sentence reads v%s (expected %s)"
                    % (path, v, expected)
                )

    if stale:
        for line in stale:
            print(line, file=sys.stderr)
        return 1
    print("All release URLs and current-build sentences point at v%s." % expected)
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        description="Bump release download URLs in plugin documentation."
    )
    sub = p.add_subparsers(dest="cmd")

    # Default subcommand: bump (mirrors a simple top-level invocation).
    p.add_argument(
        "--to",
        metavar="VERSION",
        help="New version (X.Y.Z) to point release URLs at.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing files.",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any URL or current-build sentence is stale.",
    )
    return p


def main(argv):
    args = build_parser().parse_args(argv[1:])
    if args.check:
        if args.to or args.dry_run:
            print("--check is mutually exclusive with --to/--dry-run",
                  file=sys.stderr)
            return 2
        return cmd_check(args)
    if not args.to:
        print("--to VERSION is required (or pass --check)", file=sys.stderr)
        return 2
    return cmd_bump(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
