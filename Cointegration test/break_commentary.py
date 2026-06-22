import os
import xml.etree.ElementTree as ET

import requests
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()


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
            # Google News appends " - Source Name" to titles
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


def generate_break_commentary(
    sym_a: str,
    sym_b: str,
    break_start,
    break_end,
    break_days: int,
    za_break_date=None,
) -> str:
    """Generate ~200-word break period commentary grounded strictly in web search results.

    Uses Anthropic web_search tool (server-side) with Google News RSS as the client-side
    fallback execution. Claude is prohibited from using background knowledge — every claim
    must cite a specific article returned by the search.
    """
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
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=700,
            system=system,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )

        # Collect any text blocks in this turn
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
            # No tool use found but stop_reason wasn't end_turn — return what we have
            return "\n".join(text_blocks).strip() or "Commentary could not be generated."

        messages.append({"role": "user", "content": tool_results})

    return "Commentary generation timed out after maximum search iterations."
