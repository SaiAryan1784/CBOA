import os

from groq import AsyncGroq

from embedder import retrieve_context

client = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])


async def answer_question(repo: str, question: str) -> str:
    """
    RAG pipeline:
    1. Embed the question
    2. Retrieve top-k relevant code chunks from pgvector
    3. Feed chunks + question to Groq Llama for a grounded answer
    """
    chunks = await retrieve_context(repo, question, top_k=6)

    if not chunks:
        return "No code chunks found for this repo. Make sure analysis is complete."

    context_blocks = []
    for c in chunks:
        sim = round(c.get("similarity", 0) * 100, 1)
        context_blocks.append(
            f"### `{c['filepath']}` (relevance: {sim}%)\n```\n{c['content']}\n```"
        )
    context = "\n\n".join(context_blocks)

    system_prompt = f"""\
You are a senior engineer helping a new developer understand the codebase `{repo}`.

Answer their question using ONLY the code snippets provided below.
- Be specific: reference file names and line patterns when relevant.
- If the answer is not in the provided context, say so clearly — don't guess.
- Keep the answer concise (3-6 sentences max unless a list is needed).
- Use inline code formatting for symbols, functions, classes.

Retrieved code context:
{context}
"""

    resp = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
        temperature=0.1,
        max_tokens=800,
    )

    return resp.choices[0].message.content
