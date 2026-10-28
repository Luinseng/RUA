"""Reads and writes Unity WebGL data packages ("UnityWebData1.0").

At startup the WebGL loader unpacks every entry of the .data file into the in-memory file system at
"/<name>". The harness uses that to make StreamingAssets readable with plain synchronous System.IO,
exactly like on desktop: entries are appended under u2wdata/StreamingAssets/ and the patched game
code is pointed there (see PathShim). The same reader unpacks existing WebGL builds for inspection.

Layout (little endian): "UnityWebData1.0\\0", u32 header_size, then per entry u32 offset, u32 size,
u32 name_length, name bytes; file contents follow the header.
"""

from __future__ import annotations

import gzip
import io
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path

from .common import HarnessError, human_size, info, ok, step, warn

SIGNATURE = b"UnityWebData1.0\0"
PACK_PREFIX = "u2wdata/StreamingAssets/"
PACKED_MARKER = PACK_PREFIX + ".u2w-packed"


@dataclass
class Entry:
    name: str
    offset: int
    size: int


def _open_plain(path: Path) -> io.BufferedIOBase:
    """Opens a data file, transparently decompressing gzip/brotli builds into memory."""
    with open(path, "rb") as fh:
        head = fh.read(2)
    if head == b"\x1f\x8b":
        return io.BytesIO(gzip.decompress(path.read_bytes()))
    if path.name.endswith(".br"):
        try:
            import brotli  # type: ignore
        except ImportError as exc:
            raise HarnessError("brotli-compressed build: pip install brotli") from exc
        return io.BytesIO(brotli.decompress(path.read_bytes()))
    return open(path, "rb")


def read_entries(fh: io.BufferedIOBase) -> list[Entry]:
    fh.seek(0)
    if fh.read(len(SIGNATURE)) != SIGNATURE:
        raise HarnessError("not a UnityWebData1.0 file")
    (header_size,) = struct.unpack("<I", fh.read(4))
    entries = []
    while fh.tell() < header_size:
        offset, size, name_len = struct.unpack("<III", fh.read(12))
        name = fh.read(name_len).decode("utf-8")
        entries.append(Entry(name, offset, size))
    return entries


def unpack(data_file: Path, output: Path) -> list[Entry]:
    with _open_plain(data_file) as fh:
        entries = read_entries(fh)
        root = output.resolve()
        for entry in entries:
            target = (output / entry.name).resolve()
            if root not in target.parents:
                raise HarnessError(f"refusing entry outside output: {entry.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            fh.seek(entry.offset)
            with open(target, "wb") as out:
                _copy_range(fh, out, entry.size)
    return entries


def _copy_range(src: io.BufferedIOBase, dst, size: int) -> None:
    remaining = size
    while remaining:
        chunk = src.read(min(remaining, 1 << 20))
        if not chunk:
            raise HarnessError("data file truncated")
        dst.write(chunk)
        remaining -= len(chunk)


def write(entries: list[tuple[str, object]], output: Path) -> None:
    """entries: (name, source) where source is a Path or (file object, offset, size) or bytes."""
    names = [name.encode("utf-8") for name, _ in entries]
    header_size = len(SIGNATURE) + 4 + sum(12 + len(n) for n in names)
    sizes = []
    for _, source in entries:
        if isinstance(source, Path):
            sizes.append(source.stat().st_size)
        elif isinstance(source, bytes):
            sizes.append(len(source))
        else:
            sizes.append(source[2])
    temp = output.with_name(output.name + ".u2wtmp")
    with open(temp, "wb") as out:
        out.write(SIGNATURE)
        out.write(struct.pack("<I", header_size))
        offset = header_size
        for name, size in zip(names, sizes):
            out.write(struct.pack("<III", offset, size, len(name)))
            out.write(name)
            offset += size
        for (_, source), size in zip(entries, sizes):
            if isinstance(source, Path):
                with open(source, "rb") as fh:
                    shutil.copyfileobj(fh, out, 1 << 20)
            elif isinstance(source, bytes):
                out.write(source)
            else:
                fh, start, length = source
                fh.seek(start)
                _copy_range(fh, out, length)
    temp.replace(output)


def find_data_file(build: Path) -> Path:
    folder = build / "Build" if (build / "Build").is_dir() else build
    candidates = [p for p in folder.iterdir() if p.is_file() and (p.name.endswith(".data") or p.name.endswith(".data.unityweb")
                                                                 or p.name.endswith(".data.gz") or p.name.endswith(".data.br"))]
    if len(candidates) != 1:
        raise HarnessError(f"expected exactly one .data file in {folder}, found {[c.name for c in candidates]}")
    return candidates[0]


def _shipped(path: Path) -> bool:
    """Files Unity would copy into a player build (no .meta, hidden or temporary files)."""
    return not (path.name.startswith(".") or path.name.endswith((".meta", "~")))


def pack_streaming_assets(build: Path, streaming_assets: Path, limit_mb: int = 1024) -> int:
    """Appends StreamingAssets into the build's .data file. Returns the number of files packed, or -1 when the
    folder is too big to preload (the game then reads StreamingAssets over HTTP, as Unity WebGL normally does)."""
    data_file = find_data_file(build)
    if data_file.name.endswith((".gz", ".br")):
        raise HarnessError("pack before compressing: the harness builds with compression disabled for this reason")
    files = sorted(p for p in streaming_assets.rglob("*") if p.is_file() and _shipped(p)) if streaming_assets.is_dir() else []
    total = sum(f.stat().st_size for f in files)
    if total > limit_mb << 20:
        warn(f"StreamingAssets is {human_size(total)} (> {limit_mb} MB): not packed. The whole .data file is loaded into "
             "memory at startup; raise --pack-limit-mb if the game really needs synchronous reads of all of it.")
        return -1
    step(f"packing {len(files)} StreamingAssets files ({human_size(total)}) into {data_file.name}")

    with _open_plain(data_file) as fh:
        existing = read_entries(fh)
        kept = [e for e in existing if not e.name.startswith(PACK_PREFIX)]
        entries: list[tuple[str, object]] = [(e.name, (fh, e.offset, e.size)) for e in kept]
        entries += [(PACK_PREFIX + f.relative_to(streaming_assets).as_posix(), f) for f in files]
        entries.append((PACKED_MARKER, b"unity2web\n"))
        if sum(e.size for e in kept) + total > 0xFFFFFFFF:
            raise HarnessError("UnityWebData offsets are 32-bit: engine data plus StreamingAssets exceed 4 GB")
        write(entries, data_file.with_name(data_file.name + ".new"))
    # Older builds may have gzip-compressed the .unityweb; the loader accepts it uncompressed too.
    data_file.with_name(data_file.name + ".new").replace(data_file)
    ok(f"{data_file.name}: {len(kept)} engine entries + {len(files)} StreamingAssets files ({human_size(data_file.stat().st_size)})")
    return len(files)


def compress(build: Path, *, brotli_too: bool = True) -> None:
    """Writes .gz (and .br when the brotli module is installed) next to each Build file for content negotiation."""
    folder = build / "Build" if (build / "Build").is_dir() else build
    try:
        import brotli  # type: ignore
    except ImportError:
        brotli = None
        if brotli_too:
            warn("python 'brotli' not installed: writing gzip only (pip install brotli for smaller downloads)")
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix in (".gz", ".br") or path.stat().st_size < 1024:
            continue
        raw = path.read_bytes()
        gz = path.with_name(path.name + ".gz")
        gz.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
        line = f"{path.name}: {human_size(len(raw))} -> gzip {human_size(gz.stat().st_size)}"
        if brotli and brotli_too:
            br = path.with_name(path.name + ".br")
            br.write_bytes(brotli.compress(raw, quality=9))
            line += f", brotli {human_size(br.stat().st_size)}"
        info(line)


def describe(data_file: Path) -> list[str]:
    with _open_plain(data_file) as fh:
        entries = read_entries(fh)
    return [f"{human_size(e.size):>10}  {e.name}" for e in entries]
