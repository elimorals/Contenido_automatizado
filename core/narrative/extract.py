"""Article URL → Essence (one harness call: fetch + distill)."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import aiohttp
from readability import Document

from shared.schemas import Essence

from core.narrative.runtime import reel

# Hard caps so a hostile URL can't blow up the pipeline.
FETCH_TIMEOUT_S = 30.0
MAX_BODY_CHARS = 50_000
PROMPT_BODY_CHARS = 14_000
USER_AGENT = "contenido/0.1 (+https://github.com/Agent-Field/agentfield)"


_SYSTEM = """You are reading an article and extracting ITS ESSENCE for a short-form vertical video reel. ONE shot. The hook is the single most surprising or counter-intuitive thing in the piece — NOT the article's overall topic, NOT a tidy summary. The buzz-worthy claim that makes a thumb stop scrolling.

Rules:
  - core_claim: the ONE most surprising/counter-intuitive sentence the author would recognize. ≤25 words. This is the hook's raw material.
  - mechanism: 1-2 sentences explaining WHY the claim is true / HOW it works. The payoff to the hook.
  - evidence: 1-3 concrete grounding items — numbers, named entities, specific examples — verbatim or near-verbatim from the article. NOT paraphrases. NOT your own analysis.
  - content_mode: "scientific" ONLY if the source is a research paper / preprint / technical write-up with method + result + baseline shape (a Medium post explaining a paper still counts). Otherwise "general".
  - domain: one word for the subject area (e.g. "technology", "biology", "finance", "philosophy", "health", "design").

Stay faithful. Don't invent examples. Don't soften surprising claims. Pick the one thing in this article that, stated as a thumbnail, would make a stranger tap."""


async def _fetch(url: str) -> tuple[str, str]:
    """Fetch URL, return (raw_html, final_url)."""
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url, allow_redirects=True, max_redirects=5) as resp:
            resp.raise_for_status()
            text = await resp.text(errors="replace")
            return text, str(resp.url)


def _clean(html: str) -> tuple[str, str]:
    """Run readability for clean title + body. Pure CPU."""
    doc = Document(html)
    title = (doc.short_title() or doc.title() or "").strip()
    content_html = doc.summary(html_partial=True)
    text = re.sub(r"<[^>]+>", " ", content_html)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS]
    return title, text


@reel.reasoner("extract_essence")
async def extract_essence(app: Any, url: str) -> Essence:
    """Single harness: fetch the article, extract the most surprising
    claim + mechanism + evidence + content_mode + domain."""
    html, final_url = await _fetch(url)
    title, body = await asyncio.get_event_loop().run_in_executor(None, _clean, html)

    if not body:
        raise RuntimeError(f"extract_essence: could not extract readable text from {url}")

    user = (
        f"ARTICLE\n"
        f"  url   : {final_url}\n"
        f"  title : {title or '(no title)'}\n\n"
        f"FULL BODY (cleaned, truncated to fit context):\n{body[:PROMPT_BODY_CHARS]}"
    )

    return await app.ai(system=_SYSTEM, user=user, schema=Essence)
