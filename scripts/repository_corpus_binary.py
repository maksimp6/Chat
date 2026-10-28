"""Bounded derived text from repository TAR/gzip/PDF originals.

Archive paths are identifiers only: no member is extracted onto the filesystem.
PDF processing uses a private temporary directory and a separate constrained
process; originals and derived output still pass through the caller's archive.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from scripts.semantic_pipeline import _repository_path_is_sensitive

MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024**3
MAX_MEMBERS = 10000
MAX_DEPTH = 2
MAX_PDF_TEXT_BYTES = 8 * 1024 * 1024
PDF_TIMEOUT_SECONDS = 20
PDF_MEMORY_BYTES = 512 * 1024 * 1024

# Run limits in a fresh isolated interpreter instead of preexec_fn, which is
# unsafe when the caller has other threads. The command and arguments are
# always constructed locally; source bytes never become command text.
_LIMITED_EXEC = """
import os, resource, sys
file_limit, memory_limit = int(sys.argv[1]), int(sys.argv[2])
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
os.execv(sys.argv[3], sys.argv[3:])
"""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\0" not in name
        and not _repository_path_is_sensitive(name)
    )


def _charge(sink, size: int, kind: str, source_id: str) -> bool:
    if (
        size < 0
        or size > MAX_MEMBER_BYTES
        or sink.expanded_bytes + size > MAX_EXPANDED_BYTES
    ):
        sink.gap(kind, source_id, "archive_expansion_limit", {"bytes": size})
        return False
    sink.expanded_bytes += size
    return True


def _tar_member(sink, archive, member, index, context) -> None:
    kind, source_id, url, version, depth, container_hash = context
    member_id = source_id + "!/" + member.name
    if member.isdir():
        return
    if not _safe_member(member.name):
        sink.gap(kind, member_id, "sensitive_or_unsafe_archive_member")
        return
    # Never ask extractfile to resolve a symlink/hardlink, device, sparse
    # extension or other special member, even if its target is in this archive.
    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
        sink.gap(kind, member_id, "archive_link_or_special_member")
        return
    if not _charge(sink, member.size, kind, member_id):
        return
    stream = archive.extractfile(member)
    if stream is None:
        sink.gap(kind, member_id, "archive_member_unavailable")
        return
    with stream:
        content = stream.read(member.size + 1)
    if len(content) != member.size:
        sink.gap(kind, member_id, "archive_member_size_mismatch")
        return
    sink.add(
        kind + "_member",
        member_id,
        url,
        version,
        content,
        {
            "container_sha256": container_hash,
            "member": member.name,
            "member_index": index,
            "extractor": "tarfile-v1",
        },
        _depth=depth + 1,
    )


def _tar(
    sink, raw: bytes, kind, source_id, url, version, depth, container_hash
) -> bool:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")
    except tarfile.ReadError:
        return False
    context = kind, source_id, url, version, depth, container_hash
    try:
        with archive:
            for index, member in enumerate(archive):
                if index >= MAX_MEMBERS:
                    sink.gap(kind, source_id, "archive_member_count_limit")
                    break
                _tar_member(sink, archive, member, index, context)
    except (OSError, ValueError, EOFError, tarfile.TarError):
        sink.gap(kind, source_id, "archive_read_failed")
    return True


def _gzip(sink, raw, kind, source_id, url, version, depth) -> None:
    remaining = max(0, min(MAX_MEMBER_BYTES, MAX_EXPANDED_BYTES - sink.expanded_bytes))
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
            expanded = compressed.read(remaining + 1)
    except (OSError, EOFError, ValueError):
        sink.gap(kind, source_id, "gzip_read_failed")
        return
    # Charge decompressed bytes, including tar headers, before parsing them.
    # Tar member copies also consume the shared expansion budget.
    if not _charge(sink, len(expanded), kind, source_id):
        return
    container_hash = _sha256(raw)
    if _tar(sink, expanded, kind, source_id, url, version, depth, container_hash):
        return
    sink.add(
        kind + "_member",
        source_id + "!/gzip-payload",
        url,
        version,
        expanded,
        {
            "container_sha256": container_hash,
            "member": "gzip-payload",
            "extractor": "gzip-v1",
        },
        _depth=depth + 1,
    )


def _run_pdf_command(
    executable: str, arguments: list[str], directory: Path, *, stderr=None
) -> None:
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _LIMITED_EXEC,
            str(MAX_PDF_TEXT_BYTES),
            str(PDF_MEMORY_BYTES),
            executable,
            *arguments,
        ],
        check=True,
        timeout=PDF_TIMEOUT_SECONDS,
        cwd=directory,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL if stderr is None else stderr,
        env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    )


def _pdf_text(raw: bytes, executable: str) -> tuple[bytes, str]:
    with tempfile.TemporaryDirectory(prefix="repository-pdf-") as temporary:
        directory = Path(temporary)
        input_path, output_path = directory / "source.pdf", directory / "text.txt"
        input_path.write_bytes(raw)
        input_path.chmod(0o600)
        version_path = directory / "version.txt"
        with version_path.open("wb") as stream:
            _run_pdf_command(executable, ["-v"], directory, stderr=stream)
        with version_path.open("rb") as stream:
            version = stream.read(4097)
        if len(version) > 4096 or not version.startswith(b"pdftotext version "):
            raise ValueError("pdf_extractor_version_invalid")
        _run_pdf_command(
            executable,
            ["-layout", "-enc", "UTF-8", str(input_path), str(output_path)],
            directory,
        )
        with output_path.open("rb") as stream:
            text = stream.read(MAX_PDF_TEXT_BYTES + 1)
        if len(text) >= MAX_PDF_TEXT_BYTES:
            raise ValueError("pdf_text_limit")
        text.decode("utf-8")
        return text, version.decode("utf-8", errors="replace").splitlines()[0]


def _pdf(sink, raw, kind, source_id, url, version, depth) -> None:
    executable = shutil.which("pdftotext")
    if executable is None:
        sink.gap(kind, source_id, "pdf_extractor_unavailable")
        return
    try:
        text, extractor_version = _pdf_text(raw, executable)
    except subprocess.TimeoutExpired:
        sink.gap(kind, source_id, "pdf_extraction_timeout")
        return
    except subprocess.CalledProcessError as exc:
        sink.gap(
            kind, source_id, "pdf_extractor_failed", {"returncode": exc.returncode}
        )
        return
    except ValueError as exc:
        reason = (
            str(exc)
            if str(exc) in {"pdf_text_limit", "pdf_extractor_version_invalid"}
            else "pdf_output_invalid"
        )
        sink.gap(kind, source_id, reason)
        return
    except OSError as exc:
        sink.gap(
            kind,
            source_id,
            "pdf_extractor_io_failed",
            {"error_type": type(exc).__name__},
        )
        return
    if not text.strip():
        sink.gap(kind, source_id, "pdf_text_layer_empty")
    elif _charge(sink, len(text), kind, source_id):
        sink.add(
            kind + "_text",
            source_id + "#pdf-text",
            url,
            version,
            text,
            {
                "container_sha256": _sha256(raw),
                "member": "pdf-text",
                "extractor": "pdftotext-v1",
                "extractor_version": extractor_version,
                "extraction_scope": "PDF text layer; images and geometry not interpreted",
            },
            _depth=depth + 1,
        )


def extract_binary(
    sink, raw: bytes, kind: str, source_id: str, url: str, version: str, depth: int
) -> bool:
    """Attempt bounded TAR/gzip/PDF extraction; false means unsupported format.

    The sink exposes ``expanded_bytes``, ``gap`` and the archive's ``add`` API.
    It remains responsible for redaction and preserving original/derived hashes.
    """
    is_pdf, is_gzip = raw.startswith(b"%PDF-"), raw.startswith(b"\x1f\x8b")
    if depth >= MAX_DEPTH:
        if is_pdf or is_gzip or raw[257:262] == b"ustar":
            sink.gap(kind, source_id, "archive_depth_limit")
            return True
        return False
    if is_pdf:
        _pdf(sink, raw, kind, source_id, url, version, depth)
        return True
    if is_gzip:
        _gzip(sink, raw, kind, source_id, url, version, depth)
        return True
    return _tar(sink, raw, kind, source_id, url, version, depth, _sha256(raw))
