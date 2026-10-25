"""Bounded binary expansion controls without provider calls or member execution."""

import gzip
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

from scripts import repository_corpus_binary as binary


class Sink:
    def __init__(self):
        self.expanded_bytes = 0
        self.sources = []
        self.gaps = []

    def add(self, kind, source_id, url, version, raw, metadata, *, _depth):
        self.sources.append(
            dict(
                kind=kind,
                source_id=source_id,
                url=url,
                version=version,
                raw=raw,
                metadata=metadata,
                depth=_depth,
            )
        )

    def gap(self, kind, source_id, reason, metadata=None):
        self.gaps.append(dict(kind=kind, source_id=source_id, reason=reason, metadata=metadata))


def tar(members):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content, member_type in members:
            member = tarfile.TarInfo(name)
            member.type = member_type
            if member_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = "ordinary.txt"
            if member.isfile():
                member.size = len(content)
            archive.addfile(member, io.BytesIO(content) if member.isfile() else None)
    return output.getvalue()


def extract(sink, raw, depth=0):
    return binary.extract_binary(
        sink, raw, "git_blob", "git:blob:oid", "https://example.test/source", "version-sha", depth
    )


def test_tar_gzip_preserves_bytes_provenance_and_does_not_execute(tmp_path):
    sentinel = tmp_path / "must-not-exist"
    script = f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n".encode()
    raw = gzip.compress(
        tar(
            [
                ("app.py", script, tarfile.REGTYPE),
                ("docs.txt", "Описание документа".encode(), tarfile.REGTYPE),
            ]
        ),
        mtime=0,
    )
    sink = Sink()
    assert extract(sink, raw)
    assert not sentinel.exists()
    assert not sink.gaps
    assert [source["raw"] for source in sink.sources] == [script, "Описание документа".encode()]
    assert [source["metadata"]["member"] for source in sink.sources] == ["app.py", "docs.txt"]
    for source in sink.sources:
        assert source["metadata"]["container_sha256"] == hashlib.sha256(raw).hexdigest()
        assert source["metadata"]["extractor"] == "tarfile-v1"
        assert source["version"] == "version-sha"
        assert source["depth"] == 1


def test_tar_does_not_read_unsafe_sensitive_link_or_device_members(monkeypatch):
    raw = tar(
        [
            (name, b"DO_NOT_READ", kind)
            for name, kind in [
                ("../escape.txt", tarfile.REGTYPE),
                ("/absolute.txt", tarfile.REGTYPE),
                (".env", tarfile.REGTYPE),
                ("link", tarfile.SYMTYPE),
                ("hard", tarfile.LNKTYPE),
                ("device", tarfile.CHRTYPE),
            ]
        ]
        + [("ordinary.txt", b"safe", tarfile.REGTYPE)]
    )
    original = tarfile.TarFile.extractfile

    def guarded(archive, member):
        assert member.name == "ordinary.txt"
        return original(archive, member)

    monkeypatch.setattr(tarfile.TarFile, "extractfile", guarded)
    sink = Sink()
    assert extract(sink, raw)
    assert [source["raw"] for source in sink.sources] == [b"safe"]
    assert len(sink.gaps) == 6
    assert {gap["reason"] for gap in sink.gaps} == {
        "sensitive_or_unsafe_archive_member",
        "archive_link_or_special_member",
    }


def test_plain_gzip_has_stable_member_identity_and_container_hash():
    raw = gzip.compress(b"payload\n", mtime=0)
    sink = Sink()
    assert extract(sink, raw)
    source = sink.sources[0]
    assert source["source_id"] == "git:blob:oid!/gzip-payload"
    assert source["raw"] == b"payload\n"
    assert source["metadata"]["container_sha256"] == hashlib.sha256(raw).hexdigest()
    assert sink.expanded_bytes == len(b"payload\n")


@pytest.mark.parametrize("limit", ["MAX_MEMBER_BYTES", "MAX_EXPANDED_BYTES"])
def test_gzip_expansion_is_bounded_before_member_parsing(monkeypatch, limit):
    monkeypatch.setattr(binary, limit, 32)
    sink = Sink()
    assert extract(sink, gzip.compress(b"a" * 10000, mtime=0))
    assert not sink.sources
    assert [gap["reason"] for gap in sink.gaps] == ["archive_expansion_limit"]
    assert sink.expanded_bytes == 0


def test_tar_member_and_count_limits_are_reported(monkeypatch):
    raw = tar([("one", b"1", tarfile.REGTYPE), ("two", b"22", tarfile.REGTYPE)])
    monkeypatch.setattr(binary, "MAX_MEMBERS", 1)
    sink = Sink()
    assert extract(sink, raw)
    assert len(sink.sources) == 1
    assert [gap["reason"] for gap in sink.gaps] == ["archive_member_count_limit"]
    monkeypatch.setattr(binary, "MAX_MEMBERS", 10)
    monkeypatch.setattr(binary, "MAX_MEMBER_BYTES", 1)
    sink = Sink()
    assert extract(sink, raw)
    assert len(sink.sources) == 1
    assert [gap["reason"] for gap in sink.gaps] == ["archive_expansion_limit"]


def test_shared_expansion_budget_and_archive_depth(monkeypatch):
    monkeypatch.setattr(binary, "MAX_EXPANDED_BYTES", 10)
    sink = Sink()
    sink.expanded_bytes = 8
    raw = tar([("file", b"123", tarfile.REGTYPE)])
    assert extract(sink, raw)
    assert not sink.sources and sink.expanded_bytes == 8
    for payload in (raw, gzip.compress(b"value"), b"%PDF-1.4\n"):
        bounded = Sink()
        assert extract(bounded, payload, depth=2)
        assert [gap["reason"] for gap in bounded.gaps] == ["archive_depth_limit"]


def test_corrupt_gzip_and_unsupported_binary_are_distinct():
    sink = Sink()
    assert extract(sink, b"\x1f\x8btruncated")
    assert [gap["reason"] for gap in sink.gaps] == ["gzip_read_failed"]
    unsupported = Sink()
    assert extract(unsupported, b"\x89PNG\r\n\x1a\n") is False
    assert not unsupported.sources and not unsupported.gaps


def test_duplicate_member_names_retain_distinct_archive_positions():
    sink = Sink()
    assert extract(
        sink, tar([("same", b"first", tarfile.REGTYPE), ("same", b"second", tarfile.REGTYPE)])
    )
    assert [source["raw"] for source in sink.sources] == [b"first", b"second"]
    assert [source["metadata"]["member_index"] for source in sink.sources] == [0, 1]


def test_pdf_missing_tool_is_an_explicit_gap(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    sink = Sink()
    assert extract(sink, b"%PDF-1.4\n")
    assert [gap["reason"] for gap in sink.gaps] == ["pdf_extractor_unavailable"]


def test_pdf_command_is_fixed_isolated_limited_and_excludes_credentials(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("YANDEX_API_KEY", "MUST_NOT_REACH_EXTRACTOR")
    binary._run_pdf_command("/usr/bin/pdftotext", ["-v"], tmp_path)
    command, arguments = calls[0]
    assert command[1:3] == ["-I", "-c"]
    assert "RLIMIT_FSIZE" in command[3] and "RLIMIT_CPU" in command[3] and "RLIMIT_AS" in command[3]
    assert command[-2:] == ["/usr/bin/pdftotext", "-v"]
    assert arguments["timeout"] == binary.PDF_TIMEOUT_SECONDS
    assert arguments["env"] == {"PATH": binary.os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    assert arguments["stdin"] == arguments["stdout"] == arguments["stderr"] == subprocess.DEVNULL


def test_pdf_success_preserves_text_and_extractor_provenance(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/pdftotext")
    monkeypatch.setattr(
        binary, "_pdf_text", lambda *args: (b"Exact text\n", "pdftotext version fixture")
    )
    sink = Sink()
    raw = b"%PDF-1.4\nfixture"
    assert extract(sink, raw)
    source = sink.sources[0]
    assert source["raw"] == b"Exact text\n"
    assert source["source_id"].endswith("#pdf-text")
    assert source["metadata"]["container_sha256"] == hashlib.sha256(raw).hexdigest()
    assert source["metadata"]["extractor_version"] == "pdftotext version fixture"
    assert "images and geometry not interpreted" in source["metadata"]["extraction_scope"]


@pytest.mark.parametrize(
    "outcome,reason",
    [
        (subprocess.TimeoutExpired("pdftotext", 20), "pdf_extraction_timeout"),
        (subprocess.CalledProcessError(1, "pdftotext"), "pdf_extractor_failed"),
        (ValueError("pdf_text_limit"), "pdf_text_limit"),
        (b" \n", "pdf_text_layer_empty"),
    ],
)
def test_failed_empty_or_limited_pdf_does_not_become_indexed_text(monkeypatch, outcome, reason):
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/pdftotext")

    def attempt(*args):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, "version"

    monkeypatch.setattr(binary, "_pdf_text", attempt)
    sink = Sink()
    assert extract(sink, b"%PDF-1.4\n")
    assert not sink.sources
    assert [gap["reason"] for gap in sink.gaps] == [reason]


def test_real_pdf_text_extraction_with_available_system_tool():
    executable = shutil.which("pdftotext")
    if executable is None:
        pytest.skip("optional system pdftotext is not installed")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 12 Tf 10 100 Td (Bounded PDF evidence) Tj ET\n"
    objects.append(
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream"
    )
    raw = b"%PDF-1.4\n"
    offsets = [0]
    for number, content in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f"{number} 0 obj\n".encode() + content + b"\nendobj\n"
    start = len(raw)
    raw += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    raw += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    raw += f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    text, version = binary._pdf_text(raw, executable)
    assert b"Bounded PDF evidence" in text
    assert version.startswith("pdftotext version ")


def test_pdf_child_really_enforces_output_file_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(binary, "MAX_PDF_TEXT_BYTES", 1024)
    output = tmp_path / "bounded-output"
    script = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'x' * 4096)"
    with pytest.raises(subprocess.CalledProcessError):
        binary._run_pdf_command(binary.sys.executable, ["-I", "-c", script, str(output)], tmp_path)
    assert output.stat().st_size <= 1024


def test_truncated_tar_member_reports_failure_without_invented_text():
    member = tarfile.TarInfo("truncated.txt")
    member.size = 1024
    raw = member.tobuf() + b"short"
    sink = Sink()
    assert extract(sink, raw)
    assert not sink.sources
    assert [gap["reason"] for gap in sink.gaps] == ["archive_read_failed"]
