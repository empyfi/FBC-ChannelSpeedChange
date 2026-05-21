"""Compile .po files into the GNU gettext .mo binary format.

Pure Python, no external dependencies. Implements the documented .mo
file format
(https://www.gnu.org/software/gettext/manual/html_node/MO-Files.html)
well enough to produce files that enigma2's gettext loader accepts.

Usage as a module:
    from tools.compile_po import compile_po_to_mo
    compile_po_to_mo("po/de.po", "src/.../locale/de/LC_MESSAGES/FBCChannelSpeedChange.mo")

Usage from the shell:
    python tools/compile_po.py po/de.po out.mo
"""

import struct
import sys


def parse_po(path):
    """Parse a .po file into a dict of {source: translation}.

    Handles multi-line msgid / msgstr blocks (continuation strings
    starting with a quote). Skips comments and blank lines. Includes
    the metadata entry (msgid "") which gettext requires.
    """
    messages = {}
    msgid = None
    msgstr = None
    state = None
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n").rstrip("\r")
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                # blank or comment - flush pending entry, reset
                if msgid is not None and msgstr is not None:
                    messages[msgid] = msgstr
                    msgid = msgstr = None
                state = None
                continue
            if stripped.startswith("msgid "):
                # flush previous entry
                if msgid is not None and msgstr is not None:
                    messages[msgid] = msgstr
                msgid = _unquote(stripped[6:])
                msgstr = None
                state = "id"
            elif stripped.startswith("msgstr "):
                msgstr = _unquote(stripped[7:])
                state = "str"
            elif stripped.startswith('"'):
                content = _unquote(stripped)
                if state == "id":
                    msgid = (msgid or "") + content
                elif state == "str":
                    msgstr = (msgstr or "") + content
    # flush last entry at EOF
    if msgid is not None and msgstr is not None:
        messages[msgid] = msgstr
    return messages


def _unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    # Handle the common escapes used in .po files.
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "r":
                out.append("\r")
            elif nxt == '"':
                out.append('"')
            elif nxt == "\\":
                out.append("\\")
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def write_mo(messages, out_path):
    """Write the GNU gettext .mo binary at out_path.

    messages: dict {source_str: translated_str}. The empty-source
    entry (gettext metadata) is included if present.

    Entries with empty translations are dropped so gettext falls
    through to the source string. The empty-source metadata entry is
    NOT dropped - it carries Content-Type etc.
    """
    keys = sorted(
        k for k, v in messages.items() if v or k == ""
    )
    sources = [k.encode("utf-8") for k in keys]
    targets = [messages[k].encode("utf-8") for k in keys]

    n = len(keys)
    header_size = 28
    orig_table_offset = header_size
    trans_table_offset = orig_table_offset + 8 * n
    strings_offset = trans_table_offset + 8 * n

    orig_entries = []
    trans_entries = []
    strings_blob = b""

    cursor = strings_offset
    for s in sources:
        orig_entries.append((len(s), cursor))
        strings_blob += s + b"\x00"
        cursor += len(s) + 1
    for t in targets:
        trans_entries.append((len(t), cursor))
        strings_blob += t + b"\x00"
        cursor += len(t) + 1

    parts = []
    parts.append(struct.pack("<I", 0x950412DE))   # magic, little-endian
    parts.append(struct.pack("<I", 0))            # format version
    parts.append(struct.pack("<I", n))            # number of entries
    parts.append(struct.pack("<I", orig_table_offset))
    parts.append(struct.pack("<I", trans_table_offset))
    parts.append(struct.pack("<I", 0))            # hash table size
    parts.append(struct.pack("<I", 0))            # hash table offset
    for length, offset in orig_entries:
        parts.append(struct.pack("<II", length, offset))
    for length, offset in trans_entries:
        parts.append(struct.pack("<II", length, offset))
    parts.append(strings_blob)

    import os
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(b"".join(parts))


def compile_po_to_mo(po_path, mo_path):
    """Convenience: parse a .po, write a .mo."""
    messages = parse_po(po_path)
    write_mo(messages, mo_path)
    return len(messages)


def main(argv):
    if len(argv) != 3:
        print("usage: compile_po.py <input.po> <output.mo>", file=sys.stderr)
        return 2
    n = compile_po_to_mo(argv[1], argv[2])
    print("compiled %s -> %s (%d entries)" % (argv[1], argv[2], n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
