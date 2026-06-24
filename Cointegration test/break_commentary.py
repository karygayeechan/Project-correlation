import os
import time
import xml.etree.ElementTree as ET

import requests
from anthropic import Anthropic, APIStatusError
from dotenv import load_dotenv

load_dotenv()

_TOOL_DEF = {
    "name": "web_search",
    "description": (
        "Search Google News for recent articles about financial events, "
        "stock performance, earnings, and macroeconomic conditions."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The Google News search query string.",
            }
        },
        "required": ["query"],
    },
}


def _search_google_news(query: str, max_results: int = 6) -> str:
    """Fetch headlines from Google News RSS and return formatted text for Claude."""
    url = (
        "https://news.google.com/rss/search"
        f"?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.content)
        items = []
        for item in root.findall(".//item"):
            raw_title = item.findtext("title", "")
            if " - " in raw_title:
                title, source_guess = raw_title.rsplit(" - ", 1)
            else:
                title, source_guess = raw_title, ""
            src_el = item.find("source")
            source   = src_el.text if src_el is not None else source_guess
            pub_date = item.findtext("pubDate", "")[:22].strip()
            link     = item.findtext("link", "")
            snippet  = item.findtext("description", "")[:300]
            items.append(
                f"TITLE: {title}\nSOURCE: {source}\nDATE: {pub_date}\n"
                f"URL: {link}\nSNIPPET: {snippet}"
            )
            if len(items) >= max_results:
                break
        return ("\n---\n".join(items)) if items else "No articles found for this query."
    except Exception as exc:
        return f"Search failed: {exc}"


_MODEL_FALLBACKS = ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]


def _api_call_with_retry(client, **kwargs):
    """Try each model fallback with exponential backoff on 500/529 errors."""
    models = _MODEL_FALLBACKS[:]
    while models:
        model = models.pop(0)
        delays = [3, 8, 20]
        for delay in delays + [None]:
            try:
                return client.messages.create(**kwargs, model=model)
            except APIStatusError as e:
                if e.status_code in (500, 529):
                    if delay is not None:
                        time.sleep(delay)
                        continue
                    # exhausted retries for this model — try next
                    break
                raise
    raise RuntimeError("All models unavailable after retries.")


def generate_break_commentary(
    sym_a: str,
    sym_b: str,
    break_start,
    break_end,
    break_days: int,
    za_break_date=None,
) -> str:
    """Generate ~200-word break period commentary grounded strictly in web search results."""
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    start_str = break_start.strftime("%B %Y")
    end_str   = break_end.strftime("%B %Y")
    years     = f"{break_start.year}-{break_end.year}"

    system = (
        "You are a financial analyst. Write approximately 200 words.\n"
        "STRICT ANTI-HALLUCINATION RULES — NO EXCEPTIONS:\n"
        "1. You may ONLY state facts that are directly supported by an article "
        "in the search results you received. Do not use any background knowledge.\n"
        "2. After every sentence or claim, include a citation in this exact format: "
        "[\"Article Title\", Source Name, Date]\n"
        "3. If the search results do not contain enough evidence to explain the break, "
        "say so explicitly — do not speculate.\n"
        "4. Write in plain prose. No bullet points, no headers."
    )

    user = (
        f"I need to understand why the cointegration relationship between {sym_a} and {sym_b} "
        f"broke down during {start_str} to {end_str} ({break_days} calendar days).\n\n"
        f"Please search for news articles about:\n"
        f"1. {sym_a} {sym_b} stock performance {years}\n"
        f"2. {sym_a} {sym_b} earnings results balance sheet {years}\n"
        f"3. Federal Reserve interest rate hikes impact bank stocks {years}\n"
        f"4. Banking sector crisis stress {years}\n\n"
        f"Then write approximately 200 words explaining what caused this breakdown. "
        f"Cite every single claim with the exact article you found: "
        f"[\"Article Title\", Source Name, Date]. "
        f"Do not state anything not directly supported by the articles returned."
    )

    messages = [{"role": "user", "content": user}]

    for _ in range(10):  # max search iterations
        response = _api_call_with_retry(
            client,
            max_tokens=700,
            system=system,
            tools=[_TOOL_DEF],
            messages=messages,
        )

        text_blocks = [
            b.text for b in response.content
            if hasattr(b, "text") and b.type == "text"
        ]

        if response.stop_reason == "end_turn":
            return "\n".join(text_blocks).strip()

        # Handle tool use — execute the search and feed results back
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use" and block.name == "web_search":
                query   = block.input.get("query", "") if hasattr(block, "input") else ""
                content = _search_google_news(query)
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     content,
                })

        if not tool_results:
            return "\n".join(text_blocks).strip() or "Commentary could not be generated."

        messages.append({"role": "user", "content": tool_results})

    return "Commentary generation timed out after maximum search iterations."
