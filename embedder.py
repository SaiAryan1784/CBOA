"""
Chunks source files and stores embeddings in Neon pgvector.
Uses HuggingFace Inference API (free) for embeddings.
Model: sentence-transformers/all-MiniLM-L6-v2 → 384 dimensions
Endpoint: router.huggingface.co (new as of 2025)
"""
import asyncio
import os

import httpx

from db import save_chunk, similarity_search

HF_API_URL = "https://router.huggingface.co/hf-inference/models/sentence-transformers/all-MiniLM-L6-v2/pipeline/feature-extraction"
HF_TOKEN = os.environ.get("HF_TOKEN", "")  # free HF token
CHUNK_SIZE = 600   # chars per chunk
CHUNK_OVERLAP = 80


def chunk_file(path: str, content: str) -> list[str]:
    """Split a source file into overlapping chunks with file path context."""
    lines = content.splitlines()
    chunks = []
    current = [f"# File: {path}"]
    current_len = len(current[0])

    for line in lines:
        current.append(line)
        current_len += len(line) + 1
        if current_len >= CHUNK_SIZE:
            chunks.append("\n".join(current))
            # keep last few lines as overlap
            overlap_lines = current[-5:]
            current = [f"# File: {path} (continued)"] + overlap_lines
            current_len = sum(len(l) + 1 for l in current)

    if len(current) > 2:
        chunks.append("\n".join(current))

    return chunks


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Get embeddings from HuggingFace Inference API with retry on cold-start 503."""
    headers = {"Content-Type": "application/json"}
    if HF_TOKEN:
        headers["Authorization"] = f"Bearer {HF_TOKEN}"

    last_error: Exception | None = None
    for attempt in range(3):
        if attempt > 0:
            await asyncio.sleep(2 ** attempt)  # 2s, 4s backoff
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    HF_API_URL,
                    json={"inputs": texts, "options": {"wait_for_model": True}},
                    headers=headers,
                    timeout=40,
                )
            if resp.status_code == 503:
                # Model loading — wait_for_model should handle it, but retry anyway
                last_error = RuntimeError(f"HF 503 model loading (attempt {attempt + 1})")
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"HF embedding failed: {resp.status_code} {resp.text[:200]}")
            return resp.json()
        except httpx.TimeoutException as e:
            last_error = e
            continue

    raise last_error or RuntimeError("HF embedding failed after 3 attempts")


async def embed_and_store(repo: str, files: dict[str, str]) -> int:
    """
    Chunk all files and store embeddings in Neon pgvector.
    Returns total number of chunks stored.
    """
    all_chunks: list[tuple[str, int, str]] = []  # (filepath, idx, chunk_text)

    for path, content in files.items():
        chunks = chunk_file(path, content)
        for i, chunk in enumerate(chunks):
            all_chunks.append((path, i, chunk))

    # Embed in batches of 8 (HF free tier limit)
    BATCH = 8
    total_stored = 0

    for batch_start in range(0, len(all_chunks), BATCH):
        batch = all_chunks[batch_start : batch_start + BATCH]
        texts = [c[2] for c in batch]

        try:
            embeddings = await embed_texts(texts)
        except Exception as e:
            print(f"[embedder] Batch failed, skipping: {e}")
            continue

        for (path, idx, text), emb in zip(batch, embeddings):
            await save_chunk(repo, path, idx, text, emb)
            total_stored += 1

    return total_stored


async def retrieve_context(repo: str, question: str, top_k: int = 6) -> list[dict]:
    """Embed the question and find most relevant code chunks."""
    q_emb = await embed_texts([question])
    return await similarity_search(repo, q_emb[0], top_k=top_k)
