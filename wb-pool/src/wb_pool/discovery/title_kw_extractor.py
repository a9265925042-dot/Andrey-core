"""Pure functions for narrow keyword extraction from card title.

Used by Algorithm H to derive a per-card "narrow keyword" — a focused query
that captures the product's main category (e.g. "крем воск от трещин"),
suitable for fetching niche competitors from WB SERP.

All functions here are pure (no IO, no DB). The DB-aware wrapper lives in
:mod:`wbap.competitor_pool.experiments.algo_h`.
"""

from __future__ import annotations

import re

# Stopwords: tokens dropped during title tokenisation. Universal across
# categories — gift/set/promo, sizing/units, prepositions, generic descriptors.
TITLE_STOPWORDS: frozenset[str] = frozenset(
    {
        # generic product / promotion
        "подарок", "подарки", "подарочный", "подарочная", "подарочное",
        "набор", "наборы", "наборчик",
        "комплект", "комплекты",
        "акция", "новинка", "распродажа", "скидка", "sale", "хит", "топ", "лучший",
        # quantity / sizing
        "мл", "г", "шт", "л", "литр", "литра", "литров", "грамм", "граммов",
        # prepositions / particles
        "для", "от", "и", "с", "на", "в", "по", "со", "из", "у", "к",
        "при", "над", "под", "за", "до", "же", "ли", "не", "или",
        # generic descriptor
        "большой", "малый", "новый", "качественный",
    }
)

# Narrow keyword blacklist: keywords that are too generic to use as
# narrow query (would return whole category). Applied AFTER extraction.
NARROW_KW_BLACKLIST: frozenset[str] = frozenset(
    {
        "крем", "шампунь", "маска", "лосьон", "бальзам", "тоник",
        "для женщин", "для мужчин", "женский", "мужской",
    }
)

_NUMBER_RE = re.compile(r"^\d+$")


def tokenise_title(title: str, *, brand_markers: set[str]) -> list[str]:
    """Tokenise card title into significant lowercased tokens.

    Splits on whitespace and commas. Drops:
    - Tokens in TITLE_STOPWORDS
    - Brand markers (case-insensitive)
    - Pure digits (e.g. "50", "2")
    - Tokens shorter than 3 characters

    Preserves order of first appearance.
    """
    if not title:
        return []
    brand_markers_lower = {b.lower() for b in brand_markers}
    raw_tokens = re.split(r"[\s,]+", title.lower())
    result: list[str] = []
    seen: set[str] = set()
    for tok in raw_tokens:
        tok = tok.strip()
        if not tok or len(tok) < 3:
            continue
        if _NUMBER_RE.match(tok):
            continue
        if tok in TITLE_STOPWORDS:
            continue
        if tok in brand_markers_lower:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        result.append(tok)
    return result


def extract_main_noun_phrase(
    title: str, *, brand_markers: set[str], max_tokens: int = 3
) -> str:
    """Extract main noun phrase from card title.

    Tokenises title (via :func:`tokenise_title`), takes first ``max_tokens``
    significant tokens and joins them with spaces. Returns empty string if
    no significant tokens survive filtering.

    Used as candidate "narrow keyword" — to be validated against
    mpstats_keywords (next layer in DB-aware wrapper).
    """
    tokens = tokenise_title(title, brand_markers=brand_markers)
    if not tokens:
        return ""
    return " ".join(tokens[:max_tokens])


def pick_narrow_keyword(
    *,
    title: str,
    candidates: list[tuple[str, float]],
    brand_markers: set[str],
    main_phrase_max_tokens: int = 3,
    min_title_token_match: int = 1,
) -> str | None:
    """Select narrow keyword for a card from mpstats keyword candidates.

    Strategy:
    1. Compute ``main_phrase`` from title (e.g. "крем воск трещин").
    2. If ``main_phrase`` (exact string) is among candidates → return it.
    3. Else: among candidates sorted by traffic DESC, pick the first
       keyword that:
       - Has at least ``min_title_token_match`` title tokens matching as
         WORDS (word-boundary regex, case-insensitive) — avoids false
         positive "крем" matching "крематорий".
       - Is NOT in NARROW_KW_BLACKLIST.
       - Does NOT contain any brand marker as a word (word-boundary).
    4. Returns None if no candidate survives.

    :param title: Card title (raw string from wb_cards.title).
    :param candidates: List of (keyword, avg_traffic) from mpstats_keywords
        for this nm, expected pre-sorted by traffic DESC (caller's job).
    :param brand_markers: Set of brand spellings to exclude.
    :param main_phrase_max_tokens: How many leading significant tokens
        constitute the main phrase. Default 3.
    :param min_title_token_match: Min number of title tokens that must
        match as words in candidate keyword. Default 1.
    """
    if not title:
        return None
    title_tokens = tokenise_title(title, brand_markers=brand_markers)
    if not title_tokens:
        return None
    main_phrase = " ".join(title_tokens[:main_phrase_max_tokens])

    # Step 1: exact main phrase match (preferred)
    for kw, _ in candidates:
        if (
            kw.lower().strip() == main_phrase
            and main_phrase not in NARROW_KW_BLACKLIST
        ):
            return main_phrase

    # Step 2: fallback — top-traffic candidate with title-token match.
    # Use word-boundary regex (\b) to avoid substring false positives
    # like "крем" matching "крематорий" or brand "or" tainting "корица".
    brand_markers_lower = {b.lower() for b in brand_markers}
    sorted_candidates = sorted(candidates, key=lambda kv: -kv[1])
    for kw, _ in sorted_candidates:
        kw_lower = kw.lower().strip()
        if kw_lower in NARROW_KW_BLACKLIST:
            continue
        if any(
            re.search(rf"\b{re.escape(marker)}\b", kw_lower)
            for marker in brand_markers_lower
        ):
            continue
        matches = sum(
            1 for tok in title_tokens
            if re.search(rf"\b{re.escape(tok)}\b", kw_lower)
        )
        if matches >= min_title_token_match:
            return kw_lower
    return None


__all__ = [
    "NARROW_KW_BLACKLIST",
    "TITLE_STOPWORDS",
    "extract_main_noun_phrase",
    "pick_narrow_keyword",
    "tokenise_title",
]
