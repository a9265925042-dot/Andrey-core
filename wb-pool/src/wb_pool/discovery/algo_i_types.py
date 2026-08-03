"""Dataclasses for Algorithm I (independent layers pool builder).

Epic market-yadm. Philosophy: three independent добытчики (SERP main, SERP narrow,
MPStats 3-window) each apply ``feedbacks_threshold`` filter independently. Their
outputs are unioned and deduplicated; no composite score, no trim. Cabinet data
is NOT consulted.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AlgorithmIConfig:
    """Configuration for one run of Algorithm I on a subject.

    All defaults reflect "large category" (Кремы, Шампуни). For a smaller niche
    pass e.g. ``--serp-top 30 --mpstats-per-window 10 --feedbacks-threshold 100``.
    """

    # Required
    subject_id: int
    subject_name: str

    # Layer A: SERP main
    serp_top: int = 100

    # Layer B: SERP narrow (per-keyword) + narrow extraction
    serp_narrow_top: int = 100
    n_my_cards: int = 20

    # Layer C: MPStats (top-N in each of 3 windows). Default ≈ serp_top // 4
    # (auto-derived in the CLI if not explicitly set). MPStats is the cleanest
    # source (~3% trash), so we can afford to take more from it.
    mpstats_per_window: int = 25
    mpstats_parent_category: str = "Красота"

    # Universal filter applied independently per layer
    feedbacks_threshold: int = 300

    # My-card narrow keyword extraction window
    period_days: int = 30

    # Skip Phase 1 (my carto) — useful for new categories with no my cards yet
    manual_top_nm_ids: list[int] | None = None


@dataclass(frozen=True)
class AlgorithmIResult:
    """Output of one run of Algorithm I.

    pool_nm_ids is the union of all three layers after per-layer feedbacks filter
    and CardDetail dedup of deleted cards. Order is not meaningful — there is
    no ranking score.
    """

    pool_nm_ids: list[int]
    feedbacks_threshold_used: int
    layer_a_serp_main_count: int
    layer_b_serp_narrow_count: int
    layer_c_mpstats_count: int
    narrow_kws_used: list[str]
    layer_membership: dict[int, list[str]]
    dropped_low_feedbacks_per_layer: dict[str, int]
    dropped_deleted_on_wb: int
    duration_seconds: float
    notes: dict[str, str] = field(default_factory=dict)


__all__ = ["AlgorithmIConfig", "AlgorithmIResult"]
