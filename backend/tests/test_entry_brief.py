"""Tests for the entry-brief renderer (`app/analysis/entry_brief.py`).

Real-sample-dossier tests measure the actual brief size the orchestrator
needs to judge single-call feasibility (see the T5 task brief). Synthetic
tests (reusing the `_row`/`_build`/`_graph_id_for` helpers and the
`completeness_dossier` fixture from test_profile.py, rather than duplicating
them) pin down exact rendering behaviour: traceability, truncation, and the
"Not present" section's distinction between an entry's own missing identity
and a companion peer entries carry that this one lacks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.analysis import entry_brief
from app.analysis.entry_brief import render_entry_brief, render_entry_summary
from app.analysis.profile import build_profile
from tests.conftest import SAMPLE_DOSSIER_ID, requires_sample_zip
from tests.test_profile import _build, _graph_id_for, _row, completeness_dossier  # noqa: F401 - fixture import

DOSSIER_ID = SAMPLE_DOSSIER_ID

# Documented approximation used to translate the ~6k-token / ~400-token
# budgets in the task brief into a char assertion, per acceptance criterion 2.
_CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# Real sample dossier
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def largest_entry_graph_id(sample_process_graphs) -> str:
    return max(sample_process_graphs, key=lambda pg: (pg.record_count, pg.graph_id)).graph_id


@requires_sample_zip
def test_full_brief_for_the_largest_real_entry_stays_under_6k_tokens(
    sample_saved_db_path: Path, sample_graph, sample_process_graphs, sample_profile, largest_entry_graph_id: str
):
    brief = render_entry_brief(
        DOSSIER_ID,
        sample_saved_db_path,
        largest_entry_graph_id,
        sample_profile,
        graph=sample_graph,
        process_graphs=sample_process_graphs,
    )
    budget_chars = 6000 * _CHARS_PER_TOKEN
    assert len(brief) <= budget_chars, (
        f"brief for the largest real entry is {len(brief)} chars, over the "
        f"{budget_chars}-char (~6k token at {_CHARS_PER_TOKEN} chars/token) budget"
    )


@requires_sample_zip
def test_summary_for_the_largest_real_entry_stays_under_400_tokens_and_is_smaller_than_the_brief(
    sample_saved_db_path: Path, sample_graph, sample_process_graphs, sample_profile, largest_entry_graph_id: str
):
    summary = render_entry_summary(
        DOSSIER_ID,
        sample_saved_db_path,
        largest_entry_graph_id,
        sample_profile,
        graph=sample_graph,
        process_graphs=sample_process_graphs,
    )
    brief = render_entry_brief(
        DOSSIER_ID,
        sample_saved_db_path,
        largest_entry_graph_id,
        sample_profile,
        graph=sample_graph,
        process_graphs=sample_process_graphs,
    )
    budget_chars = 400 * _CHARS_PER_TOKEN
    assert len(summary) <= budget_chars
    assert len(summary) < len(brief)


@requires_sample_zip
def test_rendering_the_same_real_entry_twice_is_byte_identical(
    sample_saved_db_path: Path, sample_graph, sample_process_graphs, sample_profile, largest_entry_graph_id: str
):
    args = (DOSSIER_ID, sample_saved_db_path, largest_entry_graph_id, sample_profile)
    kwargs = dict(graph=sample_graph, process_graphs=sample_process_graphs)

    brief_a = render_entry_brief(*args, **kwargs)
    brief_b = render_entry_brief(*args, **kwargs)
    assert brief_a == brief_b

    summary_a = render_entry_summary(*args, **kwargs)
    summary_b = render_entry_summary(*args, **kwargs)
    assert summary_a == summary_b


@requires_sample_zip
def test_unknown_graph_id_raises(sample_saved_db_path: Path, sample_graph, sample_process_graphs, sample_profile):
    with pytest.raises(ValueError):
        render_entry_brief(
            DOSSIER_ID,
            sample_saved_db_path,
            "PG-does-not-exist",
            sample_profile,
            graph=sample_graph,
            process_graphs=sample_process_graphs,
        )


@requires_sample_zip
def test_truncation_marker_appears_when_a_section_budget_is_exceeded(
    monkeypatch, sample_saved_db_path: Path, sample_graph, sample_process_graphs, sample_profile, largest_entry_graph_id: str
):
    monkeypatch.setattr(entry_brief, "RECORDS_SECTION_BUDGET", 60)
    brief = render_entry_brief(
        DOSSIER_ID,
        sample_saved_db_path,
        largest_entry_graph_id,
        sample_profile,
        graph=sample_graph,
        process_graphs=sample_process_graphs,
    )
    assert "[TRUNCATED:" in brief
    assert "60-char budget" in brief


# ---------------------------------------------------------------------------
# Synthetic dossiers
# ---------------------------------------------------------------------------


def test_records_section_only_ever_echoes_real_planted_field_values(tmp_path: Path):
    """Drives the traceability acceptance criterion: every value the brief
    prints for a record's fields must be exactly the value that record
    carries - not fabricated, not borrowed from another field or record."""
    dossier_id = "sentinel-dossier"
    db_path = tmp_path / "registry.db"
    sentinels = {
        "SACHKONTONUMMER": "SENTINEL-ACCT-9f3e21",
        "BUCHUNGSTEXT": "SENTINEL-TEXT-7a1c44",
        "GEGENKONTO": "SENTINEL-CTR-2b6d88",
    }
    rows = [
        _row(
            "S1",
            dossier_id,
            "journal_entry",
            date="2024-05-01",
            amount=42.0,
            data=dict(sentinels),
            entities=[{"entity_type": "account", "entity_id": "900001"}],
        )
    ]
    graph, process_graphs = _build(dossier_id, db_path, rows)
    profile = build_profile(dossier_id, db_path, graph=graph, process_graphs=process_graphs)
    graph_id = process_graphs[0].graph_id

    brief = render_entry_brief(dossier_id, db_path, graph_id, profile, graph=graph, process_graphs=process_graphs)

    for column, value in sentinels.items():
        assert f"{column}: {value}" in brief, f"{column} was not echoed with its exact planted value"

    # Each sentinel was planted exactly once and nothing else in this entry
    # looks like it - if the brief fabricated or duplicated content, or bled a
    # value into the wrong column, this count would drift from 3.
    assert brief.count("SENTINEL-") == len(sentinels)


def test_timeline_only_lists_dates_actually_present_on_a_record(tmp_path: Path):
    dossier_id = "timeline-dossier"
    db_path = tmp_path / "registry.db"
    rows = [
        _row(
            "T1",
            dossier_id,
            "vendor_posting",
            date="2024-06-14",
            amount=980.0,
            data={"BELEGDATUM": "2024-06-14", "BUCHUNGSDATUM": "2024-06-15", "BUCHUNGSBETRAG": 980.0},
        )
    ]
    graph, process_graphs = _build(dossier_id, db_path, rows)
    profile = build_profile(dossier_id, db_path, graph=graph, process_graphs=process_graphs)
    graph_id = process_graphs[0].graph_id

    brief = render_entry_brief(dossier_id, db_path, graph_id, profile, graph=graph, process_graphs=process_graphs)

    assert "2024-06-14  BELEGDATUM (document date)  T1 vendor_posting" in brief
    assert "2024-06-15  BUCHUNGSDATUM (posting date)  T1 vendor_posting" in brief


def test_not_present_reports_missing_identity_dimensions_with_peer_counts(completeness_dossier):
    dossier_id, db_path, graph, process_graphs, profile = completeness_dossier
    m3_graph_id = _graph_id_for(process_graphs, "M3")

    brief = render_entry_brief(dossier_id, db_path, m3_graph_id, profile, graph=graph, process_graphs=process_graphs)

    assert "No record in this entry supplies a date; 2 of 3 entries with this shape do." in brief
    assert "No record in this entry supplies an amount; 2 of 3 entries with this shape do." in brief
    assert "No record in this entry supplies a named counterparty; 2 of 3 entries with this shape do." in brief
    assert (
        "No record in this entry supplies a source-document reference; 2 of 3 entries with this shape do."
        in brief
    )


def test_not_present_reports_a_missing_companion_edge_type_with_its_peer_count(completeness_dossier):
    dossier_id, db_path, graph, process_graphs, profile = completeness_dossier
    m3_graph_id = _graph_id_for(process_graphs, "M3")

    brief = render_entry_brief(dossier_id, db_path, m3_graph_id, profile, graph=graph, process_graphs=process_graphs)

    assert "1 of 3 entries with this shape carry a approved_by edge; this entry does not." in brief


def test_not_present_reports_a_missing_companion_record_type_via_the_nearest_shape(completeness_dossier):
    dossier_id, db_path, graph, process_graphs, profile = completeness_dossier
    m2_graph_id = _graph_id_for(process_graphs, "M2")

    brief = render_entry_brief(dossier_id, db_path, m2_graph_id, profile, graph=graph, process_graphs=process_graphs)

    assert "A related shape adding journal_entry occurs 1 time(s)." in brief


def test_entry_with_an_approver_is_not_flagged_for_the_edge_type_it_carries(completeness_dossier):
    """M1 has the approved_by edge itself - the companion-edge-type line must
    only ever be printed for entries that lack it."""
    dossier_id, db_path, graph, process_graphs, profile = completeness_dossier
    m1_graph_id = _graph_id_for(process_graphs, "M1")

    brief = render_entry_brief(dossier_id, db_path, m1_graph_id, profile, graph=graph, process_graphs=process_graphs)

    assert "approved_by edge; this entry does not" not in brief


def test_summary_carries_the_same_not_present_facts_as_the_full_brief(completeness_dossier):
    dossier_id, db_path, graph, process_graphs, profile = completeness_dossier
    m3_graph_id = _graph_id_for(process_graphs, "M3")

    summary = render_entry_summary(dossier_id, db_path, m3_graph_id, profile, graph=graph, process_graphs=process_graphs)

    assert "No record in this entry supplies a date; 2 of 3 entries with this shape do." in summary
    assert "1 of 3 entries with this shape carry a approved_by edge; this entry does not." in summary
    assert len(summary) <= entry_brief.SUMMARY_BUDGET


def test_summary_truncates_with_a_marker_when_the_overall_budget_is_exceeded(monkeypatch, completeness_dossier):
    dossier_id, db_path, graph, process_graphs, profile = completeness_dossier
    m3_graph_id = _graph_id_for(process_graphs, "M3")

    monkeypatch.setattr(entry_brief, "SUMMARY_BUDGET", 200)
    summary = render_entry_summary(dossier_id, db_path, m3_graph_id, profile, graph=graph, process_graphs=process_graphs)

    assert "[TRUNCATED:" in summary
    assert "200-char budget" in summary
    assert len(summary) <= 200
