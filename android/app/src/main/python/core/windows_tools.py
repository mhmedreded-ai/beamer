"""Decode Windows tool pipes after capture, outside subprocess's reader threads."""

import codecs
import os
import subprocess


def decode_output(data: bytes, encoding: str = "oem") -> str:
    # schtasks declares UTF-16 even when its redirected pipe contains OEM bytes.
    # Only a byte-order mark overrides the caller's knowledge of the tool.
    if data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    elif encoding in ("oem", "mbcs") and os.name != "nt":
        encoding = "utf-8"
    return data.decode(encoding, errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def run(args: list, *, encoding: str = "oem", timeout=None, startupinfo=None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args, capture_output=True, timeout=timeout, startupinfo=startupinfo,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    result.stdout = decode_output(result.stdout, encoding)
    result.stderr = decode_output(result.stderr, encoding)
    return result
