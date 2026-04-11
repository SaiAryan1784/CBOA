import json
import os

import asyncpg

_pool: asyncpg.Pool | None = None

CREATE_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS analyses (
    id              SERIAL PRIMARY KEY,
    repo            TEXT NOT NULL,
    branch          TEXT NOT NULL DEFAULT 'main',
    status          TEXT NOT NULL DEFAULT 'running',  -- running | complete | error
    wiki            TEXT,
    onboarding_plan TEXT,
    arch_diagram    TEXT,
    hotspots        JSONB,
    dep_vulnerabilities JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (repo, branch)
);

CREATE TABLE IF NOT EXISTS code_chunks (
    id          SERIAL PRIMARY KEY,
    repo        TEXT NOT NULL,
    filepath    TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(384),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (repo, filepath, chunk_index)
);

CREATE INDEX IF NOT EXISTS code_chunks_repo_idx ON code_chunks (repo);
CREATE INDEX IF NOT EXISTS code_chunks_embedding_idx
    ON code_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);
"""


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = os.environ["DATABASE_URL"]

        async def _init(conn):
            await conn.set_type_codec(
                "jsonb",
                encoder=json.dumps,
                decoder=json.loads,
                schema="pg_catalog",
            )

        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, init=_init)
    return _pool


async def init_schema():
    pool = await get_pool()
    await pool.execute(CREATE_SCHEMA)


async def save_report(
    repo: str,
    branch: str,
    wiki: str,
    onboarding_plan: str,
    arch_diagram: str,
    hotspots: list,
    dep_vulnerabilities: list,
):
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE analyses
        SET status = 'complete',
            wiki = $3,
            onboarding_plan = $4,
            arch_diagram = $5,
            hotspots = $6,
            dep_vulnerabilities = $7
        WHERE repo = $1 AND branch = $2
        """,
        repo, branch, wiki, onboarding_plan, arch_diagram,
        hotspots, dep_vulnerabilities,
    )


async def save_error(repo: str, branch: str, error: str):
    pool = await get_pool()
    await pool.execute(
        "UPDATE analyses SET status = 'error', error_message = $3 WHERE repo = $1 AND branch = $2",
        repo, branch, error,
    )


async def save_chunk(repo: str, filepath: str, chunk_index: int, content: str, embedding: list):
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO code_chunks (repo, filepath, chunk_index, content, embedding)
        VALUES ($1, $2, $3, $4, $5::vector)
        ON CONFLICT DO NOTHING
        """,
        repo, filepath, chunk_index, content, str(embedding),
    )


async def similarity_search(repo: str, query_embedding: list, top_k: int = 6) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT filepath, content,
               1 - (embedding <=> $2::vector) AS similarity
        FROM code_chunks
        WHERE repo = $1
        ORDER BY embedding <=> $2::vector
        LIMIT $3
        """,
        repo, str(query_embedding), top_k,
    )
    return [dict(r) for r in rows]
