"""The spend ledger in Postgres. Needs the local Postgres container (see README); skipped when it is not reachable."""
import os
import uuid

import psycopg
import pytest

from ragdemo.store import Store

DSN = os.environ.get("DATABASE_URL", "postgresql://ragdemo:ragdemo@localhost:54329/ragdemo")


@pytest.fixture
def store():
    try:
        psycopg.connect(DSN, connect_timeout=2).close()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no Postgres at {DSN}: {exc}")
    name = f"ragtest_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    s = Store(DSN.rsplit("/", 1)[0] + "/" + name, dim=8)
    s.init_schema()
    yield s
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE {name} WITH (FORCE)")


@pytest.mark.integration
def test_spend_is_summed_per_day_and_per_month(store):
    assert store.spend_load("2026-11-18") == (0.0, 0.0)
    store.spend_add("2026-11-18", 0.0144)
    store.spend_add("2026-11-18", 0.0172)
    store.spend_add("2026-11-03", 1.4856)   # same month, another day
    store.spend_add("2026-10-31", 9.0)      # last month: not counted
    today, month = store.spend_load("2026-11-18")
    assert today == pytest.approx(0.0316) and month == pytest.approx(1.5172)
    with store.connect() as conn:
        row = conn.execute("SELECT questions FROM spend WHERE day = '2026-11-18'").fetchone()
    assert row["questions"] == 2
