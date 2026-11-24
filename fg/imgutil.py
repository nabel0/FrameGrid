# FrameGrid — Copyright (C) 2026 Javier
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependency-free image utilities: file type detection by signature and PNG/JPEG dimensions."""
from __future__ import annotations

import os
import struct
import subprocess
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def kind(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


def size(path: Path) -> tuple[int, int] | None:
    """(width, height), reading only the file header."""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            k = kind(head)
            if k == "png" and head[12:16] == b"IHDR":
                return struct.unpack(">II", head[16:24])
            if k == "gif":
                return struct.unpack("<HH", head[6:10])
            if k == "jpg":
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff":
                        b = f.read(1)
                    while b == b"\xff":
                        b = f.read(1)
                    if not b:
                        return None
                    marker = b[0]
                    if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                        continue
                    seglen = struct.unpack(">H", f.read(2))[0]
                    if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        f.read(1)
                        h, w = struct.unpack(">HH", f.read(4))
                        return w, h
                    f.seek(seglen - 2, 1)
    except (OSError, struct.error):
        return None
    return None


def default_pictures_dir() -> Path:
    home = Path.home()
    try:
        out = subprocess.run(["xdg-user-dir", "PICTURES"], capture_output=True, text=True, timeout=3).stdout.strip()
        if out and Path(out).is_dir() and Path(out) != home:
            return Path(out)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for name in ("Imágenes", "Pictures", "Imagenes"):
        if (home / name).is_dir():
            return home / name
    return home


def safe_path(p: str | os.PathLike) -> Path:
    """Only paths inside the home directory are allowed."""
    home = Path.home().resolve()
    rp = Path(p).expanduser().resolve()
    if rp != home and home not in rp.parents:
        raise PermissionError("Only images in your home directory can be used.")
    return rp


def list_dir(p: str | None) -> dict:
    d = safe_path(p) if p else default_pictures_dir()
    if not d.is_dir():
        raise FileNotFoundError(f"Directory does not exist: {d}")
    dirs, files = [], []
    for e in sorted(d.iterdir(), key=lambda x: x.name.lower()):
        if e.name.startswith("."):
            continue
        try:
            if e.is_dir():
                dirs.append(e.name)
            elif e.suffix.lower() in IMAGE_EXTS and e.is_file():
                wh = size(e)
                files.append({"name": e.name, "path": str(e), "w": wh[0] if wh else None,
                              "h": wh[1] if wh else None, "ext": e.suffix.lower().lstrip(".")})
        except OSError:
            continue
    home = Path.home().resolve()
    return {"dir": str(d), "parent": str(d.parent) if d != home else None, "dirs": dirs, "files": files,
            "home": str(home)}
