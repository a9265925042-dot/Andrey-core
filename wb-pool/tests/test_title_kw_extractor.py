"""Pure-function tests for narrow keyword extraction."""

from wb_pool.discovery.title_kw_extractor import (
    extract_main_noun_phrase,
    pick_narrow_keyword,
    tokenise_title,
)

BRAND = {"mybrand", "мойбренд"}


def test_tokenise_drops_stopwords_numbers_brand():
    tokens = tokenise_title(
        "MYBRAND Крем воск для ног от трещин 50 мл подарок", brand_markers=BRAND
    )
    assert tokens == ["крем", "воск", "ног", "трещин"]


def test_tokenise_empty_title():
    assert tokenise_title("", brand_markers=BRAND) == []


def test_extract_main_noun_phrase_max_tokens():
    phrase = extract_main_noun_phrase(
        "Крем воск для ног от трещин увлажняющий", brand_markers=BRAND, max_tokens=3
    )
    assert phrase == "крем воск ног"


def test_pick_narrow_keyword_exact_main_phrase_wins():
    kw = pick_narrow_keyword(
        title="Крем воск ног",
        candidates=[("крем воск ног", 10.0), ("супер популярный", 999.0)],
        brand_markers=BRAND,
    )
    assert kw == "крем воск ног"


def test_pick_narrow_keyword_fallback_by_traffic_with_word_match():
    kw = pick_narrow_keyword(
        title="Крем воск для ног от трещин",
        candidates=[
            ("шампунь для волос", 500.0),  # no title-token match
            ("крем", 400.0),  # blacklisted
            ("воск для пяток", 300.0),  # matches "воск"
        ],
        brand_markers=BRAND,
    )
    assert kw == "воск для пяток"


def test_pick_narrow_keyword_skips_brand_candidates():
    kw = pick_narrow_keyword(
        title="Крем воск для ног",
        candidates=[("mybrand воск крем", 900.0), ("воск кремовый", 100.0)],
        brand_markers=BRAND,
    )
    assert kw == "воск кремовый"


def test_pick_narrow_keyword_none_when_no_match():
    kw = pick_narrow_keyword(
        title="Крем",  # blacklisted as main phrase
        candidates=[("шампунь", 100.0)],
        brand_markers=BRAND,
    )
    assert kw is None


def test_word_boundary_no_substring_false_positive():
    # "крем" must NOT match inside "крематорий"
    kw = pick_narrow_keyword(
        title="Крем увлажняющий лица",
        candidates=[("крематорий услуги", 900.0)],
        brand_markers=BRAND,
    )
    assert kw is None
