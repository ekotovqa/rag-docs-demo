"""Postgres as the one store: pgvector for the dense side, tsvector for the lexical side, fused in SQL.

Lexical scoring is not ts_rank: a query term is worth what the retriever says it is worth
(a document number more than the word "invoice"), and a chunk scores the sum of the terms
it matches. Ranks from both sides are fused with reciprocal rank fusion.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from .ingest.chunk import Chunk

RRF_K = 60
# A chunk that matches a term of at least this weight (a document number, a SKU) names the document
# the question is about; that beats any semantic neighbour. The boost exceeds the largest fused score
# two first ranks can produce (2 / (RRF_K + 1) = 0.033), so such chunks always come first; among
# themselves they keep the RRF order.
ID_WEIGHT = 3.0
ID_BOOST = 0.05  # per identifier matched; one match is above the RRF ceiling of 2/61, two leave no doubt


@dataclass
class Hit:
    id: int
    file: str
    page: int
    idx: int
    text: str
    source: str
    score: float
    vec_rank: int | None
    vec_dist: float | None
    lex_rank: int | None
    lex_score: float | None
    has_id: bool = False  # matched a document number or SKU named in the question
    id_matches: int = 0  # how many of them: a chunk naming both the order and the SKU beats one naming either


SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS documents (
    id          serial PRIMARY KEY,
    file        text UNIQUE NOT NULL,
    sha256      text NOT NULL,
    pages       int  NOT NULL,
    ocr_pages   int  NOT NULL DEFAULT 0,
    ingested_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS chunks (
    id          serial PRIMARY KEY,
    document_id int NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    file        text NOT NULL,
    page        int  NOT NULL,
    idx         int  NOT NULL,
    text        text NOT NULL,
    source      text NOT NULL,
    embedding   vector({dim}) NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED
);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_file_page_idx ON chunks (file, page, idx);
CREATE TABLE IF NOT EXISTS spend (
    day       date PRIMARY KEY,
    usd       numeric(12, 6) NOT NULL DEFAULT 0,
    questions int NOT NULL DEFAULT 0
);
"""

SEARCH_SQL = """
WITH terms AS (
    SELECT * FROM unnest(%(terms)s::text[], %(weights)s::float8[]) AS t(term, w)
),
lex AS (
    SELECT c.id, SUM(t.w) AS lex_score,
           bool_or(t.w >= %(id_weight)s) AS has_id,
           SUM(CASE WHEN t.w >= %(id_weight)s THEN 1 ELSE 0 END) AS id_matches,
           row_number() OVER (ORDER BY SUM(t.w) DESC, c.id) AS r
    FROM chunks c
    JOIN terms t ON c.tsv @@ plainto_tsquery('simple', t.term)
    GROUP BY c.id
    ORDER BY SUM(t.w) DESC, c.id
    LIMIT %(n)s
),
vec AS (
    SELECT id, embedding <=> %(qvec)s::vector AS dist,
           row_number() OVER (ORDER BY embedding <=> %(qvec)s::vector, id) AS r
    FROM chunks
    ORDER BY dist, id
    LIMIT %(n)s
)
SELECT c.id, c.file, c.page, c.idx, c.text, c.source,
       vec.r AS vec_rank, vec.dist AS vec_dist, lex.r AS lex_rank, lex.lex_score, COALESCE(lex.has_id, false) AS has_id,
       COALESCE(lex.id_matches, 0) AS id_matches,
       COALESCE(1.0 / (%(rrf)s + vec.r), 0) + COALESCE(1.0 / (%(rrf)s + lex.r), 0)
       + %(id_boost)s * COALESCE(lex.id_matches, 0) AS score
FROM chunks c
LEFT JOIN vec ON vec.id = c.id
LEFT JOIN lex ON lex.id = c.id
WHERE vec.id IS NOT NULL OR lex.id IS NOT NULL
ORDER BY score DESC, vec.dist ASC NULLS LAST, c.id
LIMIT %(k)s;
"""


class Store:
    def __init__(self, dsn: str, dim: int = 384) -> None:
        self.dsn = dsn
        self.dim = dim

    def connect(self) -> psycopg.Connection[Any]:
        """A connection with the pgvector adapter registered; `init_schema` must have run once before."""
        conn = psycopg.connect(self.dsn, row_factory=dict_row)
        register_vector(conn)
        return conn

    def init_schema(self) -> None:
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn:  # no adapter yet: the type may not exist
            conn.execute(SCHEMA.format(dim=self.dim))
            conn.commit()

    # --- Spend ledger: the demo's money ceilings must survive a cold start ---

    def spend_load(self, day: str) -> tuple[float, float]:
        """(spent on `day`, spent in the month of `day`), USD. `day` is YYYY-MM-DD."""
        with self.connect() as conn:
            today = conn.execute("SELECT usd FROM spend WHERE day = %s::date", (day,)).fetchone()
            month = conn.execute(
                "SELECT COALESCE(SUM(usd), 0) AS usd FROM spend WHERE date_trunc('month', day) = date_trunc('month', %s::date)",
                (day,),
            ).fetchone()
        return (float(today["usd"]) if today else 0.0), float(month["usd"])

    def spend_add(self, day: str, usd: float) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO spend (day, usd, questions) VALUES (%s::date, %s, 1) "
                "ON CONFLICT (day) DO UPDATE SET usd = spend.usd + EXCLUDED.usd, questions = spend.questions + 1",
                (day, usd),
            )
            conn.commit()

    def document_state(self) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT file, sha256 FROM documents").fetchall()
        return {r["file"]: r["sha256"] for r in rows}

    def replace_document(self, file: str, sha: str, pages: int, ocr_pages: int,
                         chunks: list[Chunk], embeddings: list[list[float]]) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("one embedding per chunk")
        with self.connect() as conn:
            conn.execute("DELETE FROM documents WHERE file = %s", (file,))
            doc_id = conn.execute(
                "INSERT INTO documents (file, sha256, pages, ocr_pages) VALUES (%s, %s, %s, %s) RETURNING id",
                (file, sha, pages, ocr_pages),
            ).fetchone()["id"]
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO chunks (document_id, file, page, idx, text, source, embedding) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [(doc_id, c.file, c.page, c.idx, c.text, c.source, e) for c, e in zip(chunks, embeddings)],
                )
            conn.commit()
        return doc_id

    def delete_missing(self, keep_files: set[str]) -> int:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM documents WHERE NOT (file = ANY(%s))", (sorted(keep_files),))
            conn.commit()
            return cur.rowcount

    def search(self, query_vector: list[float], terms: list[tuple[str, float]], k: int = 6, n: int = 30) -> list[Hit]:
        params = {
            "terms": [t for t, _ in terms],
            "weights": [w for _, w in terms],
            "qvec": query_vector,
            "n": n,
            "k": k,
            "rrf": RRF_K,
            "id_weight": ID_WEIGHT,
            "id_boost": ID_BOOST,
        }
        with self.connect() as conn:
            rows = conn.execute(SEARCH_SQL, params).fetchall()
        return [Hit(r["id"], r["file"], r["page"], r["idx"], r["text"], r["source"], float(r["score"]),
                    r["vec_rank"], None if r["vec_dist"] is None else float(r["vec_dist"]),
                    r["lex_rank"], None if r["lex_score"] is None else float(r["lex_score"]), bool(r["has_id"]),
                    int(r["id_matches"])) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            docs = conn.execute("SELECT count(*) AS n, COALESCE(sum(pages),0) AS pages, COALESCE(sum(ocr_pages),0) AS ocr_pages FROM documents").fetchone()
            chunks = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()
        return {"documents": docs["n"], "pages": int(docs["pages"]), "ocr_pages": int(docs["ocr_pages"]), "chunks": chunks["n"]}

    def files(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return conn.execute("SELECT file, pages, ocr_pages, ingested_at FROM documents ORDER BY file").fetchall()
