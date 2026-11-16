"""Needs the local Postgres container (see README). Skipped when it is not reachable."""
import os
import uuid

import psycopg
import pytest

from ragdemo.ingest.chunk import Chunk
from ragdemo.store import Store

DSN = os.environ.get("DATABASE_URL", "postgresql://ragdemo:ragdemo@localhost:54329/ragdemo")
DIM = 8


@pytest.fixture(scope="module")
def store():
    try:
        psycopg.connect(DSN, connect_timeout=2).close()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no Postgres at {DSN}: {exc}")
    # a throwaway database so the integration test never touches the demo index
    name = f"ragtest_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    test_dsn = DSN.rsplit("/", 1)[0] + "/" + name
    s = Store(test_dsn, dim=DIM)
    s.init_schema()
    yield s
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE {name} WITH (FORCE)")


def vec(*hot: int) -> list[float]:
    v = [0.0] * DIM
    for h in hot:
        v[h] = 1.0
    return v


@pytest.mark.integration
def test_replace_search_and_fuse(store):
    chunks_a = [
        Chunk("INV-1.pdf", 1, 0, "INVOICE | Invoice no.: INV-2025-0001\nSteel sheet 10 61.20 612.00", "text", "h"),
        Chunk("INV-1.pdf", 2, 0, "Invoice INV-2025-0001 (continued)\nTOTAL DUE 1,862.00 EUR", "text", "h"),
    ]
    chunks_b = [Chunk("INV-2.pdf", 1, 0, "INVOICE | Invoice no.: INV-2025-0002\nNitrile gloves 5 8.95 44.75\nTOTAL DUE 44.75 USD", "ocr", "h")]
    store.replace_document("INV-1.pdf", "sha-a", 2, 0, chunks_a, [vec(0), vec(1)])
    store.replace_document("INV-2.pdf", "sha-b", 1, 1, chunks_b, [vec(2)])
    assert store.stats() == {"documents": 2, "pages": 3, "ocr_pages": 1, "chunks": 3}
    assert store.document_state() == {"INV-1.pdf": "sha-a", "INV-2.pdf": "sha-b"}

    # the question names a document: its chunks beat the vector neighbour even though the vector points elsewhere
    hits = store.search(vec(2), [("INV-2025-0001", 3.0), ("total", 0.4)], k=3)
    assert [h.file for h in hits] == ["INV-1.pdf", "INV-1.pdf", "INV-2.pdf"]
    assert hits[0].page == 2, "the chunk matching both the number and 'total' wins"
    assert hits[0].lex_score == 3.4 and hits[0].lex_rank == 1 and hits[0].has_id
    assert hits[2].vec_rank == 1 and not hits[2].has_id, "the vector neighbour is still in the list"
    assert hits[1].score > hits[2].score + 0.03, "a lexical-only document match outranks a vector-and-lexical neighbour"

    # without an identifier, the fusion is plain RRF and the vector side can win
    hits = store.search(vec(2), [("total", 0.4)], k=3)
    assert hits[0].file == "INV-2.pdf"

    # identifiers with slashes and dots go through the same parser on both sides
    store.replace_document("NC.pdf", "sha-c", 1, 0,
                           [Chunk("NC.pdf", 1, 0, "Invoice no.: NC/2025/0912 Terminal block AE-TB-2.5", "text", "h")], [vec(3)])
    assert store.search(vec(7), [("NC/2025/0912", 3.0)], k=1)[0].file == "NC.pdf"
    assert store.search(vec(7), [("AE-TB-2.5", 3.0)], k=1)[0].file == "NC.pdf"

    # re-ingesting a file replaces its chunks; removing files drops them
    store.replace_document("INV-2.pdf", "sha-b2", 1, 0, chunks_b[:1], [vec(4)])
    assert store.stats()["chunks"] == 4
    assert store.delete_missing({"INV-1.pdf"}) == 2
    assert store.stats()["documents"] == 1

    # no lexical terms at all: pure vector ranking, no error
    assert store.search(vec(0), [], k=2)[0].page == 1
