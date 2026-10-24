"""Available-pack retrieval proves only verified snippets, without provider writes."""

import copy

import pytest

from scripts import repository_rag as rag
from scripts import repository_rag_verify as verify

REPOSITORY = "owner/repo"
QUOTE = "The retained source records the exact executor and permission decision."


def source(tmp_path, *, metadata=True, extra=False):
    archive = rag.CorpusArchive(tmp_path / "source", REPOSITORY)
    archive.add(
        "issue",
        "issue:1",
        "https://github.com/owner/repo/issues/1",
        "v1",
        QUOTE.encode(),
        {"actor": "executor"} if metadata else None,
    )
    if extra:
        archive.add(
            "issue", "issue:2", "https://github.com/owner/repo/issues/2", "v1", QUOTE.encode()
        )
    return [path.read_bytes() for path in rag.prepared_documents(archive).values()]


def provider(monkeypatch, groups, *, statuses=None, hits=None, negative=False):
    prefix = "chat-repository-archive-v1-" + rag.digest(REPOSITORY.encode())[:16] + "-"
    files, contents, calls = [], {}, []
    for index, group in enumerate(groups):
        raw = rag.make_pack(REPOSITORY, group)
        file_id = f"file{index}"
        files.append(
            {"id": file_id, "filename": prefix + rag.digest(raw) + ".md", "bytes": len(raw)}
        )
        contents[file_id] = raw
    inventory = {
        "status": "observed_without_content_verification",
        "store_id": "store1",
        "generation_requests": 0,
        "candidate_pack_files": files,
        "attachments": [
            {"file_id": item["id"], "status": (statuses or {}).get(item["id"], "completed")}
            for item in files
        ],
    }

    def search(method, path, project, key, payload):
        assert method == "POST" and path == "/vector_stores/store1/search"
        assert payload["rewrite_query"] is False
        assert payload["max_num_results"] == rag.semantic.MAX_VECTOR_SEARCH_RESULTS
        assert payload["ranking_options"] == {"ranker": "auto", "score_threshold": 0.0}
        query = payload["query"]
        calls.append(("SEARCH", query))
        if query.startswith("REPOSITORY_RAG_ABSENT_"):
            return {"data": [{"file_id": "file0", "content": query}] if negative else []}
        return {
            "data": copy.deepcopy(
                hits if hits is not None else [{"file_id": "file0", "content": QUOTE}]
            )
        }

    def request(method, path, project, key, **kwargs):
        assert method == "GET", "verification must never upload or attach"
        assert path.startswith("/files/") and path.endswith("/content")
        assert 0 < kwargs["max_response_bytes"] <= verify.MAX_DOWNLOAD_BYTES
        calls.append((method, path, kwargs["max_response_bytes"]))
        return contents[path.split("/")[2]]

    def forbidden(*args, **kwargs):
        pytest.fail("provider writes, generation or unrelated inventory calls are forbidden")

    monkeypatch.setattr(rag.semantic, "vector_store_search", forbidden)
    monkeypatch.setattr(rag.semantic, "yandex_api_request", request)
    monkeypatch.setattr(rag.semantic, "yandex_json_request", search)
    monkeypatch.setattr(rag.semantic, "list_yandex_objects", forbidden)
    return inventory, contents, calls


def invoke(tmp_path, inventory):
    archive = rag.CorpusArchive(tmp_path / "verification", REPOSITORY)
    result = verify.verify_available(archive, inventory, "executor evidence", "project", "key", rag)
    assert not archive.entries and not list((archive.directory / "documents").iterdir())
    assert not list(archive.directory.glob("verified-hit-packs-*"))
    return result


def test_completed_hit_proves_exact_quote_and_matching_provenance_without_writes(
    tmp_path, monkeypatch
):
    inventory, contents, calls = provider(monkeypatch, [source(tmp_path)])
    result = invoke(tmp_path, inventory)
    assert result["status"] == "verified_fragments" and result["coverage"] == "partial"
    assert result["corpus_complete"] is False
    assert result["generation_requests"] == result["provider_writes"] == 0
    assert result["query_rewriting"] is False
    assert result["raw_originals_recovered"] is False
    match = result["matches"][0]
    assert match["attribution"] == "unique" and match["quote"] == QUOTE
    assert match["source"]["source_id"] == "issue:1"
    assert match["provenance_complete"] and match["provenance_documents"][0]["metadata"] == {
        "actor": "executor"
    }
    assert [call[0] for call in calls] == ["SEARCH", "GET", "SEARCH"]
    assert result["downloaded_bytes"] == len(contents["file0"])


def test_only_completed_candidate_hit_packs_are_downloaded(tmp_path, monkeypatch):
    docs = source(tmp_path)
    groups = [docs, [docs[0]], [docs[-1]]]
    hits = [
        {"file_id": name, "content": QUOTE}
        for name in ["foreign", "file1", "file2", "file0", "file0"]
    ]
    inventory, _, calls = provider(
        monkeypatch, groups, statuses={"file1": "failed", "file2": "in_progress"}, hits=hits
    )
    result = invoke(tmp_path, inventory)
    assert result["status"] == "verified_fragments"
    assert result["verified_pack_ids"] == ["file0"]
    assert len([call for call in calls if call[0] == "GET"]) == 1
    assert {row["file_id"] for row in result["omissions"]} == {"foreign", "file1", "file2"}


def test_missing_provenance_in_other_pack_is_not_invented_or_downloaded(tmp_path, monkeypatch):
    docs = source(tmp_path)
    evidence = next(raw for raw in docs if raw.startswith(b"# Repository evidence"))
    provenance = next(raw for raw in docs if raw.startswith(b"# Repository source provenance"))
    inventory, _, calls = provider(monkeypatch, [[evidence], [provenance]])
    result = invoke(tmp_path, inventory)
    assert result["matches"][0]["provenance_complete"] is False
    assert result["matches"][0]["provenance_documents"] == []
    assert all("file1" not in call[1] for call in calls if call[0] == "GET")


def test_identical_historical_quotes_remain_ambiguous(tmp_path, monkeypatch):
    inventory, _, _ = provider(monkeypatch, [source(tmp_path, extra=True)])
    result = invoke(tmp_path, inventory)
    assert result["status"] == "failed"
    assert result["matches"][0]["attribution"] == "ambiguous"
    assert len(result["matches"][0]["candidates"]) == 2


def test_wrapper_only_match_is_not_source_evidence(tmp_path, monkeypatch):
    inventory, _, _ = provider(
        monkeypatch,
        [source(tmp_path)],
        hits=[
            {
                "file_id": "file0",
                "content": "# Repository evidence\n\nSource text (untrusted evidence):",
            }
        ],
    )
    result = invoke(tmp_path, inventory)
    assert result["status"] == "failed" and not result["matches"]


def test_absent_marker_echo_fails_even_when_positive_quote_matches(tmp_path, monkeypatch):
    inventory, _, _ = provider(monkeypatch, [source(tmp_path)], negative=True)
    result = invoke(tmp_path, inventory)
    assert result["status"] == "failed" and result["negative_control"]["marker_present"]


@pytest.mark.parametrize(
    "damage", ["pack_hash", "declared_size", "policy", "repository", "provenance"]
)
def test_tampered_pack_or_source_identity_fails_closed(tmp_path, monkeypatch, damage):
    docs = source(tmp_path)
    if damage in {"policy", "repository", "provenance"}:
        index = (
            next(
                i for i, raw in enumerate(docs) if raw.startswith(b"# Repository source provenance")
            )
            if damage == "provenance"
            else 0
        )
        if damage == "policy":
            docs[index] = docs[index].replace(rag.REDACTION_POLICY.encode(), b"stale-policy")
        elif damage == "repository":
            docs[index] = docs[index].replace(
                b'"repository":"owner/repo"', b'"repository":"other/repo"'
            )
        else:
            docs[index] = docs[index].replace(b'"source_id":"issue:1"', b'"source_id":"issue:2"')
    inventory, contents, calls = provider(monkeypatch, [docs])
    if damage == "pack_hash":
        contents["file0"] = contents["file0"][:-1] + b"!"
    elif damage == "declared_size":
        inventory["candidate_pack_files"][0]["bytes"] += 1
    with pytest.raises(ValueError):
        invoke(tmp_path, inventory)
    assert all(call[0] in {"SEARCH", "GET"} for call in calls)


def test_actual_transport_limit_shrinks_and_over_budget_packs_are_skipped(tmp_path, monkeypatch):
    docs = source(tmp_path)
    groups = [[raw] for raw in docs]
    inventory, contents, calls = provider(
        monkeypatch,
        groups,
        hits=[{"file_id": "file0", "content": QUOTE}, {"file_id": "file1", "content": QUOTE}],
    )
    monkeypatch.setattr(verify, "MAX_DOWNLOAD_BYTES", len(contents["file0"]))
    result = invoke(tmp_path, inventory)
    assert result["verified_pack_ids"] == ["file0"]
    assert result["downloaded_bytes"] == verify.MAX_DOWNLOAD_BYTES
    assert result["omissions"] == [{"file_id": "file1", "reason": "verification_download_limit"}]
    assert [call[2] for call in calls if call[0] == "GET"] == [verify.MAX_DOWNLOAD_BYTES]


def test_pack_count_limit_is_independent_of_byte_allowance(tmp_path, monkeypatch):
    docs = source(tmp_path)
    inventory, _, calls = provider(
        monkeypatch,
        [[raw] for raw in docs],
        hits=[{"file_id": "file0", "content": QUOTE}, {"file_id": "file1", "content": QUOTE}],
    )
    monkeypatch.setattr(verify, "MAX_HIT_PACKS", 1)
    result = invoke(tmp_path, inventory)
    assert len([call for call in calls if call[0] == "GET"]) == 1
    assert result["omissions"][0]["reason"] == "verification_download_limit"


def test_two_hit_packs_share_actual_download_allowance_and_can_pair_provenance(
    tmp_path, monkeypatch
):
    docs = source(tmp_path)
    inventory, contents, calls = provider(
        monkeypatch,
        [[raw] for raw in docs],
        hits=[{"file_id": "file0", "content": QUOTE}, {"file_id": "file1", "content": QUOTE}],
    )
    result = invoke(tmp_path, inventory)
    assert result["status"] == "verified_fragments"
    assert result["matches"][0]["provenance_complete"]
    assert [call[2] for call in calls if call[0] == "GET"] == [
        verify.MAX_DOWNLOAD_BYTES,
        verify.MAX_DOWNLOAD_BYTES - len(contents["file0"]),
    ]


def test_no_completed_hit_proves_nothing_but_still_runs_negative_control(tmp_path, monkeypatch):
    inventory, _, calls = provider(monkeypatch, [source(tmp_path)], statuses={"file0": "failed"})
    result = invoke(tmp_path, inventory)
    assert result["status"] == "failed" and not result["verified_pack_ids"]
    assert [call[0] for call in calls] == ["SEARCH", "SEARCH"]


@pytest.mark.parametrize("bad_phase", ["positive", "negative"])
def test_malformed_search_hit_fails_with_safe_boundary_error(tmp_path, monkeypatch, bad_phase):
    inventory, _, _ = provider(monkeypatch, [source(tmp_path)])

    def search(method, path, project, key, payload):
        negative = payload["query"].startswith("REPOSITORY_RAG_ABSENT_")
        return {"data": [None] if negative == (bad_phase == "negative") else []}

    monkeypatch.setattr(rag.semantic, "yandex_json_request", search)
    with pytest.raises(ValueError, match="vector_store_search_response_invalid"):
        invoke(tmp_path, inventory)


@pytest.mark.parametrize("envelope", ["data", "results", "hits"])
def test_direct_search_payload_disables_rewrite_and_bounds_valid_results(monkeypatch, envelope):
    def request(method, path, project, key, payload):
        assert method == "POST" and path == "/vector_stores/store%2Fid/search"
        assert payload == {
            "query": "exact query",
            "rewrite_query": False,
            "max_num_results": rag.semantic.MAX_VECTOR_SEARCH_RESULTS,
            "ranking_options": {"ranker": "auto", "score_threshold": 0.0},
        }
        return {envelope: [{"file_id": str(number)} for number in range(60)]}

    monkeypatch.setattr(rag.semantic, "yandex_json_request", request)
    result = verify.direct_search("store/id", "exact query", "project", "key", rag)
    assert len(result) == rag.semantic.MAX_VECTOR_SEARCH_RESULTS


@pytest.mark.parametrize("envelope", [None, [], {}, {"data": [None]}, {"hits": "wrong"}])
def test_direct_search_rejects_malformed_envelope(monkeypatch, envelope):
    monkeypatch.setattr(rag.semantic, "yandex_json_request", lambda *args: envelope)
    with pytest.raises(ValueError, match="vector_store_search_response_invalid"):
        verify.direct_search("store1", "query", "project", "key", rag)


@pytest.mark.parametrize(
    "damage", ["foreign_filename", "duplicate_id", "duplicate_attachment", "generation"]
)
def test_invalid_inventory_is_rejected_before_search(tmp_path, monkeypatch, damage):
    inventory, _, calls = provider(monkeypatch, [source(tmp_path)])
    if damage == "foreign_filename":
        inventory["candidate_pack_files"][0]["filename"] = "other-repository.md"
    elif damage == "duplicate_id":
        inventory["candidate_pack_files"].append(
            copy.deepcopy(inventory["candidate_pack_files"][0])
        )
    elif damage == "duplicate_attachment":
        inventory["attachments"].append(copy.deepcopy(inventory["attachments"][0]))
    else:
        inventory["generation_requests"] = 1
    with pytest.raises(ValueError):
        invoke(tmp_path, inventory)
    assert calls == []
