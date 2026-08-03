"""Tests for purchase grouping primitives + mpstats merge helper."""

from datetime import date

from wb_pool.discovery.mpstats_layer import merge_pool_with_topup
from wb_pool.purchase.groups import (
    GroupPlan,
    chunk_with_padding,
    compute_comparison_id,
)


def test_chunk_exact_multiple_no_padding():
    groups = chunk_with_padding(list(range(1, 11)), padding_pool=[100, 101])
    assert [len(g) for g in groups] == [5, 5]
    assert groups[0] == [1, 2, 3, 4, 5]


def test_chunk_short_last_group_padded_from_pool():
    groups = chunk_with_padding([1, 2, 3, 4, 5, 6, 7], padding_pool=[100, 101, 102, 103])
    assert len(groups) == 2
    assert groups[1][:2] == [6, 7]
    assert len(groups[1]) == 5
    assert all(nm in {100, 101, 102, 103} for nm in groups[1][2:])


def test_chunk_padding_exhausted_leaves_short_group():
    groups = chunk_with_padding([1, 2, 3], padding_pool=[100])
    assert groups == [[1, 2, 3, 100]]


def test_chunk_deterministic_given_seed():
    a = chunk_with_padding([1, 2, 3], padding_pool=[10, 20, 30], rng_seed=42)
    b = chunk_with_padding([1, 2, 3], padding_pool=[10, 20, 30], rng_seed=42)
    assert a == b


def test_compute_comparison_id_deterministic_and_sorted():
    plan = GroupPlan(nm_ids=[555, 111, 333])
    cmp_id = compute_comparison_id(
        subject_id=357,
        plan=plan,
        period_start=date(2026, 2, 19),
        period_end=date(2026, 5, 20),
    )
    assert cmp_id == "sub357-g111-p20260219-20260520"


def test_merge_pool_with_topup_rounds_to_cabinet_quantum():
    # 10 pool + 7 unique = 17 → round UP to 20, но доступно только 17 → вниз до 15
    result = merge_pool_with_topup(list(range(1, 11)), list(range(20, 27)))
    assert len(result) == 15
    assert result[:10] == list(range(1, 11))


def test_merge_pool_with_topup_dedup():
    result = merge_pool_with_topup([1, 2, 3, 4, 5], [5, 4, 6, 7, 8, 9, 10])
    assert result == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
