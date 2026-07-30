"""Render one process graph (one ledger entry) as a compact text document.

The graph exists to assemble context, not to be walked by a model - see the
T5 task brief. This module is where that assembly turns into something a
model reads in a single call: text, not nested JSON, because text costs fewer
tokens and a model reasons over a flat chronological account more reliably
than over deep JSON. ``render_entry_brief`` produces the full document;
``render_entry_summary`` produces a much smaller companion meant for a cheap
gate to decide whether an entry is complete enough to be judged at all,
before paying for the full brief.

No fraud scenario is encoded here, matching ``profile.py``. Every section
states a record field or a count computed in ``profile.py`` - nothing here
asserts what a fact would imply. In particular the "Not present" section
(and its summary equivalent) reports two different kinds of absence and
states each as a plain, counted observation, never as a requirement, a
violation, or a concern:

- an identity dimension (date, amount, counterparty, document reference)
  that no record in the entry supplies at all;
- a companion record type or edge type that peer entries of the same shape
  carry and this entry does not.

Deciding whether either kind of absence matters is the next stage's job
(a data-quality gate, then a model), not this module's - this module never
returns anything shaped like a verdict, a requirement, or a concern score.

Every section - and the summary as a whole - has a hard character budget.
Hitting it truncates with an explicit marker rather than silently presenting
a partial entry as complete.

``render_entry_brief``/``render_entry_summary`` each call ``graph.subgraph()``
exactly once, for the one entry being rendered - unlike the mistake
``app/graph/subgraphs.py``'s docstring describes (calling it once per process
graph while enumerating *all* of them), this is a single on-demand call for a
single entry, the same pattern ``app/graph/tools.py``'s ``get_subgraph``
already uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx

from app.analysis.profile import COMPLETENESS_DIMENSIONS, DossierProfile, ShapeProfile
from app.evidence import EvidenceRecordStore
from app.graph.schema import record_node_id
from app.graph.store import load_graph, load_process_graphs
from app.graph.subgraphs import ProcessGraph

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The record-node id prefix, derived from the real schema function rather
# than duplicated as a literal - see _endpoint_record_id below.
_RECORD_NODE_PREFIX = record_node_id("")

_IDENTITY_DIMENSION_LABELS: dict[str, str] = {
    "date": "a date",
    "amount": "an amount",
    "counterparty": "a named counterparty",
    "document_reference": "a source-document reference",
}

# Per-section hard character budgets for the full brief. Deliberately exposed
# as module constants (rather than inlined) so a test can drive one down and
# assert the truncation marker fires - see test_entry_brief.py.
#
# Each budget is set with headroom over the real worst case measured across
# every entry in the sample dossier (4,902 process graphs), not fitted to
# today's numbers exactly - measured maxima: Entry 485, Timeline 2,492,
# Records 8,563, Parties 6,672, Relationships 4,614, Not-present 525,
# Conventions 272 chars. None of these budgets pads a smaller entry's
# output - a section only ever costs what its own real content needs - so
# their sum (~31,100 chars, a safety-valve ceiling, not a target) is not the
# same thing as any real entry's actual size. The real worst entry in the
# dossier (12 records, 8 parties) renders at ~21,700 chars (~5,425 tokens at
# the ~4 chars/token approximation this module documents and tests against),
# comfortably under the ~6k-token target - see
# test_full_brief_for_the_largest_real_entry_stays_under_6k_tokens and
# test_no_real_entry_in_the_sample_dossier_is_truncated_or_incomplete.
# Parties and Relationships carry the most headroom above their measured max
# because a busy entity's dossier-wide profile and the entry's internal edge
# list are the sections most likely to grow on a dossier with busier hubs
# than this sample one - see the task brief's "trap" about entity
# connectivity being expensive to enumerate in full. Conventions needs
# almost no headroom: since the column glossary was withdrawn (see
# _render_conventions_section's docstring), its content is two fixed
# sentences that do not vary by entry at all.
ENTRY_SECTION_BUDGET = 800
TIMELINE_SECTION_BUDGET = 3500
RECORDS_SECTION_BUDGET = 11000
PARTIES_SECTION_BUDGET = 8500
RELATIONSHIPS_SECTION_BUDGET = 6000
NOT_PRESENT_SECTION_BUDGET = 900
CONVENTIONS_SECTION_BUDGET = 400

# Overall budget for the compact summary - about 400 tokens at the ~4
# chars/token approximation this module documents and tests against.
SUMMARY_BUDGET = 1600

_TRUNCATION_MARKER = "\n[TRUNCATED: section exceeded its {budget}-char budget]"


def _apply_budget(text: str, budget: int) -> str:
    """Truncate ``text`` to ``budget`` chars, replacing whatever was cut with
    an explicit marker - never silently. Guarantees the result never exceeds
    ``budget``, even in the degenerate case where the marker itself is longer
    than the budget (the marker is truncated too, rather than overrunning)."""
    if len(text) <= budget:
        return text
    marker = _TRUNCATION_MARKER.format(budget=budget)
    if len(marker) >= budget:
        return marker[:budget]
    return text[: budget - len(marker)] + marker


def _fmt_amount(amount: float | None) -> str:
    if amount is None:
        return "n/a"
    return f"{amount:.2f}"


_EMPTY_FIELD_MARKER = "(empty)"


def _render_field_value(value: Any) -> str:
    """Render one record field's raw value for the Records section.

    ``None`` (an absent field, from the parsers) renders as the marker
    ``(empty)`` rather than Python's ``str(None)`` == "None", which would
    read as if the field's real value were the four-letter word "None". A
    real field value that happens to equal the marker text itself is quoted
    instead, so that one case can never be mistaken for the marker."""
    if value is None:
        return _EMPTY_FIELD_MARKER
    if value == _EMPTY_FIELD_MARKER:
        return f'"{value}"'
    return str(value)


@dataclass
class _EntryContext:
    process_graph: ProcessGraph
    records: list[dict[str, Any]]
    internal_edges: list[tuple[str, str, dict[str, Any]]]
    roles_by_entity: dict[str, list[tuple[str, str, str]]]


def _resolve_process_graph(graph_id: str, process_graphs: list[ProcessGraph]) -> ProcessGraph:
    target = next((pg for pg in process_graphs if pg.graph_id == graph_id), None)
    if target is None:
        raise ValueError(f"process graph {graph_id!r} not found")
    return target


def _build_entry_context(
    dossier_id: str,
    db_path: Path,
    process_graph: ProcessGraph,
    graph: nx.MultiDiGraph,
) -> _EntryContext:
    store = EvidenceRecordStore(dossier_id, db_path)
    raw_records = store.resolve(list(process_graph.record_ids))
    records = [EvidenceRecordStore.evidence_context(record) for record in raw_records]
    records_by_id = {record["record_id"]: record for record in records}
    ordered_records = [
        records_by_id[rid] for rid in process_graph.record_ids if rid in records_by_id
    ]

    entity_ids = set(process_graph.entity_node_ids)
    induced_nodes = {record_node_id(rid) for rid in process_graph.record_ids} | entity_ids
    internal_edges = list(graph.subgraph(induced_nodes).edges(data=True))

    roles_by_entity: dict[str, list[tuple[str, str, str]]] = {}
    for source, dst, data in internal_edges:
        edge_type = data.get("edge_type", "")
        if source in entity_ids:
            roles_by_entity.setdefault(source, []).append((edge_type, dst, "out"))
        if dst in entity_ids:
            roles_by_entity.setdefault(dst, []).append((edge_type, source, "in"))

    return _EntryContext(
        process_graph=process_graph,
        records=ordered_records,
        internal_edges=internal_edges,
        roles_by_entity=roles_by_entity,
    )


def _entry_shape(profile: DossierProfile, graph_id: str) -> tuple[str, ...]:
    return profile.entry_shape.get(graph_id, ())


def _shape_profile(profile: DossierProfile, graph_id: str) -> ShapeProfile | None:
    return profile.shapes.get(_entry_shape(profile, graph_id))


def _nearest_superset_shape(
    shapes: dict[tuple[str, ...], ShapeProfile], this_shape: tuple[str, ...]
) -> ShapeProfile | None:
    """The shape in the dossier whose record-type set is a strict superset of
    this entry's, adding the fewest extra record types. Deterministic:
    ties broken by the sorted extra types, then the shape tuple itself."""
    this_set = set(this_shape)
    candidates: list[tuple[int, tuple[str, ...], tuple[str, ...]]] = []
    for shape, shape_profile in shapes.items():
        other_set = set(shape_profile.record_types)
        if other_set > this_set:
            extra = tuple(sorted(other_set - this_set))
            candidates.append((len(extra), extra, shape))
    if not candidates:
        return None
    candidates.sort()
    _, _, best_shape = candidates[0]
    return shapes[best_shape]


# ---------------------------------------------------------------------------
# Full-brief sections
# ---------------------------------------------------------------------------


def _render_entry_section(profile: DossierProfile, context: _EntryContext) -> str:
    pg = context.process_graph
    record_types = sorted({record["record_type"] for record in context.records})
    dates = sorted(record["date"] for record in context.records if record.get("date"))

    # Subtotalled by record type, not just currency/sign: an invoice and the
    # journal postings it generated both carry the same money, so summing
    # every record in the entry into one figure counts that money twice - no
    # reading of the resulting number is meaningful, yet a model handed a
    # line labelled "Totals" will reason about it anyway. Each subtotal below
    # sums only records of one homogeneous type, so every figure describes
    # one real quantity.
    subtotals: dict[tuple[str, str, str], float] = {}
    for record in context.records:
        amount = record.get("amount")
        if amount is None:
            continue
        currency = record.get("currency") or "unknown"
        sign = "positive" if amount >= 0 else "negative"
        key = (record["record_type"], currency, sign)
        subtotals[key] = subtotals.get(key, 0.0) + amount
    subtotal_text = (
        ", ".join(
            f"{record_type} {currency} {sign}={_fmt_amount(value)}"
            for (record_type, currency, sign), value in sorted(subtotals.items())
        )
        or "none"
    )

    shape_profile = _shape_profile(profile, pg.graph_id)
    shape_count = shape_profile.entry_count if shape_profile else 0

    lines = [
        f"Entry {pg.graph_id}",
        f"Record types: {', '.join(record_types) if record_types else 'none'}",
        f"Subtotals by record type: {subtotal_text}",
        f"Date span: {dates[0] if dates else 'n/a'} to {dates[-1] if dates else 'n/a'}",
        f"Shape frequency: this combination of record types occurs {shape_count} "
        f"times of {profile.total_entries} entries in the dossier",
    ]
    return _apply_budget("\n".join(lines), ENTRY_SECTION_BUDGET)


def _render_timeline_section(context: _EntryContext) -> str:
    entries: list[tuple[str, str, str, str]] = []  # (date, column, record_id, record_type)
    for record in context.records:
        data = record.get("data") or {}
        for column, value in data.items():
            if isinstance(value, str) and _ISO_DATE_RE.match(value):
                entries.append((value, column, record["record_id"], record["record_type"]))
    entries.sort()

    lines = ["Timeline"]
    for date, column, record_id, record_type in entries:
        lines.append(f"{date}  {column}  {record_id} {record_type}")
    if len(entries) == 0:
        lines.append("No dated fields found on any record in this entry.")
    return _apply_budget("\n".join(lines), TIMELINE_SECTION_BUDGET)


def _render_records_section(context: _EntryContext) -> str:
    lines = ["Records"]
    for record in context.records:
        source = record.get("source") or {}
        location_bits = [source.get("relative_path", "unknown source")]
        if source.get("sheet"):
            location_bits.append(f"sheet {source['sheet']}")
        if source.get("row_number") is not None:
            row_bit = f"row {source['row_number']}"
            if source.get("row_end") is not None and source["row_end"] != source["row_number"]:
                row_bit += f"-{source['row_end']}"
            location_bits.append(row_bit)
        if source.get("page") is not None:
            location_bits.append(f"page {source['page']}")
        if source.get("paragraph") is not None:
            location_bits.append(f"paragraph {source['paragraph']}")

        lines.append(f"Record {record['record_id']} ({record['record_type']})")
        lines.append(f"  Source: {', '.join(location_bits)}")
        data = record.get("data") or {}
        for column in sorted(data):
            lines.append(f"  {column}: {_render_field_value(data[column])}")
        if record.get("text_content"):
            lines.append(f"  text_content: {record['text_content']}")
    return _apply_budget("\n".join(lines), RECORDS_SECTION_BUDGET)


def _render_edge_counts(edge_type_counts: dict[str, int]) -> str:
    """Render every edge type's count compactly: the non-zero ones spelled
    out, the zero ones named once as a single list rather than repeated as
    ``type=0`` - a zero count is still a measured fact worth keeping (e.g.
    "this vendor has 0 has_receipt edges"), it just does not need its own
    "=0" for each of the ~14 edge types that do not apply."""
    edge_items = sorted(edge_type_counts.items())
    present = [f"{edge_type}={count}" for edge_type, count in edge_items if count > 0]
    absent = [edge_type for edge_type, count in edge_items if count == 0]
    line = ", ".join(present) if present else "none"
    if absent:
        line += " | none: " + ", ".join(absent)
    return line


def _entity_profile_line(profile: DossierProfile, entity_node_id: str) -> str:
    entity_profile = profile.entities.get(entity_node_id)
    if entity_profile is None:
        return f"  Dossier-wide profile: no profile computed for {entity_node_id}"
    return (
        f"  Dossier-wide: {entity_profile.record_count} record(s), "
        f"{entity_profile.first_date or 'n/a'} to {entity_profile.last_date or 'n/a'}, "
        f"total {_fmt_amount(entity_profile.total_amount)} (mean {_fmt_amount(entity_profile.mean_amount)}), "
        f"master-data references: {entity_profile.master_data_reference_count}, "
        f"co-occurring entities: {entity_profile.co_occurring_entity_count}\n"
        f"  Edges: {_render_edge_counts(entity_profile.edge_type_counts)}"
    )


def _render_parties_section(profile: DossierProfile, context: _EntryContext) -> str:
    lines = ["Parties"]
    for entity_node_id in context.process_graph.entity_node_ids:
        roles = context.roles_by_entity.get(entity_node_id, [])
        role_text = (
            ", ".join(f"{edge_type} ({direction}, {other})" for edge_type, other, direction in sorted(roles))
            if roles
            else "no direct edge within this entry"
        )
        lines.append(f"Party {entity_node_id}")
        lines.append(f"  Roles in this entry: {role_text}")
        lines.append(_entity_profile_line(profile, entity_node_id))
    return _apply_budget("\n".join(lines), PARTIES_SECTION_BUDGET)


def _endpoint_record_id(node_id: str) -> str | None:
    """The bare record id if ``node_id`` is a record node, else ``None``."""
    if node_id.startswith(_RECORD_NODE_PREFIX):
        return node_id[len(_RECORD_NODE_PREFIX) :]
    return None


def _render_relationships_section(context: _EntryContext) -> str:
    lines = ["Relationships"]
    ordered_edges = sorted(
        context.internal_edges,
        key=lambda edge: (edge[2].get("edge_type", ""), edge[0], edge[1]),
    )
    for source, dst, data in ordered_edges:
        edge_type = data.get("edge_type", "")
        edge_record_ids = set(data.get("record_ids") or ())
        endpoint_record_ids = {
            rid for rid in (_endpoint_record_id(source), _endpoint_record_id(dst)) if rid is not None
        }
        # Only print the justifying record_ids when they carry a record
        # beyond the edge's own two endpoints - when the set is exactly
        # {source, dst}, both 36-char UUIDs are already on the line and
        # repeating them adds nothing. Do NOT simplify this to unconditional:
        # an edge between two entity nodes (no record endpoint at all) or a
        # document_join edge justified by a third record both need the
        # explicit list to stay traceable.
        if edge_record_ids and edge_record_ids != endpoint_record_ids:
            record_ids_text = ", ".join(sorted(edge_record_ids))
            lines.append(f"{source} -[{edge_type}]-> {dst} (record_ids: {record_ids_text})")
        else:
            lines.append(f"{source} -[{edge_type}]-> {dst}")
    if len(ordered_edges) == 0:
        lines.append("No internal relationships between this entry's own nodes.")
    return _apply_budget("\n".join(lines), RELATIONSHIPS_SECTION_BUDGET)


def _identity_absence_lines(profile: DossierProfile, graph_id: str) -> list[str]:
    completeness = profile.entry_completeness.get(graph_id)
    shape_profile = _shape_profile(profile, graph_id)
    if completeness is None or shape_profile is None:
        return []

    lines = []
    for dimension in COMPLETENESS_DIMENSIONS:
        if getattr(completeness, f"has_{dimension}"):
            continue
        peer_count = shape_profile.completeness_counts.get(dimension, 0)
        label = _IDENTITY_DIMENSION_LABELS[dimension]
        lines.append(
            f"No record in this entry supplies {label}; {peer_count} of "
            f"{shape_profile.entry_count} entries with this shape do."
        )
    return lines


def _companion_edge_lines(profile: DossierProfile, graph_id: str) -> list[str]:
    shape_profile = _shape_profile(profile, graph_id)
    if shape_profile is None:
        return []
    this_entry_edge_types = set(profile.entry_edge_types.get(graph_id, ()))

    lines = []
    for edge_type, count in sorted(shape_profile.edge_type_counts.items()):
        if count == 0 or edge_type in this_entry_edge_types:
            continue
        lines.append(
            f"{count} of {shape_profile.entry_count} entries with this shape carry "
            f"a {edge_type} edge; this entry does not."
        )
    return lines


def _companion_record_type_lines(profile: DossierProfile, graph_id: str) -> list[str]:
    shape_profile = _shape_profile(profile, graph_id)
    if shape_profile is None:
        return []
    nearest = _nearest_superset_shape(profile.shapes, shape_profile.record_types)
    if nearest is None:
        return []
    extra_types = sorted(set(nearest.record_types) - set(shape_profile.record_types))
    return [
        f"This entry's shape ({', '.join(shape_profile.record_types)}) occurs "
        f"{shape_profile.entry_count} time(s) in the dossier. A related shape adding "
        f"{', '.join(extra_types)} occurs {nearest.entry_count} time(s)."
    ]


def _render_not_present_section(profile: DossierProfile, graph_id: str) -> str:
    lines = ["Not present"]
    lines.extend(_identity_absence_lines(profile, graph_id))
    lines.extend(_companion_edge_lines(profile, graph_id))
    lines.extend(_companion_record_type_lines(profile, graph_id))
    if len(lines) == 1:
        lines.append("No absence found relative to this shape's peers.")
    return _apply_budget("\n".join(lines), NOT_PRESENT_SECTION_BUDGET)


def _render_conventions_section(context: _EntryContext) -> str:
    """States what was done to values between the source file and this brief
    - the two facts the model cannot know by inspection and that matter when
    it quotes an amount or a date back. Column names render verbatim as the
    export spells them elsewhere in the brief: a large model already knows
    what a German GDPdU/GoBD column name like FAKTURADATUM or LEISTUNGSDATUM
    denotes without being told, so an authored glossary would spend tokens
    teaching it something it knows, on every one of this dossier's 4,902
    briefs - see the T5 review brief's superseding note on Finding 2."""
    lines = [
        "Conventions",
        'Amounts on source documents use comma decimals (e.g. "9.780,00" is nine '
        "thousand seven hundred eighty) and are already converted to plain decimal "
        "numbers above.",
        "Dates on source documents are DD.MM.YYYY and are already normalized to ISO "
        "8601 (YYYY-MM-DD) above.",
    ]
    return _apply_budget("\n".join(lines), CONVENTIONS_SECTION_BUDGET)


def render_entry_brief(
    dossier_id: str,
    db_path: Path,
    graph_id: str,
    profile: DossierProfile,
    *,
    graph: nx.MultiDiGraph | None = None,
    process_graphs: list[ProcessGraph] | None = None,
) -> str:
    """Render one process graph as a full text brief for a single-call model read."""
    if process_graphs is None:
        process_graphs = load_process_graphs(db_path, dossier_id)
    process_graph = _resolve_process_graph(graph_id, process_graphs)
    if graph is None:
        graph = load_graph(db_path, dossier_id)

    context = _build_entry_context(dossier_id, db_path, process_graph, graph)

    sections = [
        _render_entry_section(profile, context),
        _render_timeline_section(context),
        _render_records_section(context),
        _render_parties_section(profile, context),
        _render_relationships_section(context),
        _render_not_present_section(profile, graph_id),
        _render_conventions_section(context),
    ]
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Compact summary - for a cheap completeness gate, not for analysis.
# ---------------------------------------------------------------------------


def _summary_completeness_lines(profile: DossierProfile, graph_id: str) -> list[str]:
    completeness = profile.entry_completeness.get(graph_id)
    if completeness is None:
        return ["Completeness: unknown"]
    present = [
        dimension for dimension in COMPLETENESS_DIMENSIONS if getattr(completeness, f"has_{dimension}")
    ]
    absent = [
        dimension for dimension in COMPLETENESS_DIMENSIONS if not getattr(completeness, f"has_{dimension}")
    ]
    return [
        f"Completeness: present={','.join(present) or 'none'} absent={','.join(absent) or 'none'}",
    ]


def _summary_party_lines(profile: DossierProfile, context: _EntryContext) -> list[str]:
    lines = []
    for entity_node_id in context.process_graph.entity_node_ids:
        entity_profile = profile.entities.get(entity_node_id)
        roles = sorted({edge_type for edge_type, _other, _direction in context.roles_by_entity.get(entity_node_id, [])})
        role_text = "/".join(roles) if roles else "no direct edge"
        if entity_profile is None:
            lines.append(f"{entity_node_id}: {role_text}; no dossier-wide profile")
            continue
        lines.append(
            f"{entity_node_id}: {role_text}; {entity_profile.record_count} record(s) dossier-wide, "
            f"co-occurring entities={entity_profile.co_occurring_entity_count}"
        )
    return lines


def render_entry_summary(
    dossier_id: str,
    db_path: Path,
    graph_id: str,
    profile: DossierProfile,
    *,
    graph: nx.MultiDiGraph | None = None,
    process_graphs: list[ProcessGraph] | None = None,
) -> str:
    """Render a compact (~400-token) summary of one entry - the completeness
    facts and peer comparison a cheap gate needs, without the cost of the
    full brief. See the module docstring: the gate decides from these facts,
    it is not decided here."""
    if process_graphs is None:
        process_graphs = load_process_graphs(db_path, dossier_id)
    process_graph = _resolve_process_graph(graph_id, process_graphs)
    if graph is None:
        graph = load_graph(db_path, dossier_id)

    context = _build_entry_context(dossier_id, db_path, process_graph, graph)
    shape_profile = _shape_profile(profile, graph_id)

    lines = [
        f"Entry {graph_id}",
        f"Shape occurs {shape_profile.entry_count if shape_profile else 0} of "
        f"{profile.total_entries} time(s) in the dossier",
    ]
    lines.extend(_summary_completeness_lines(profile, graph_id))
    lines.append("Parties:")
    lines.extend(f"  {line}" for line in _summary_party_lines(profile, context))
    lines.append("Not present:")
    not_present_lines = (
        _identity_absence_lines(profile, graph_id)
        + _companion_edge_lines(profile, graph_id)
        + _companion_record_type_lines(profile, graph_id)
    )
    if not_present_lines:
        lines.extend(f"  {line}" for line in not_present_lines)
    else:
        lines.append("  No absence found relative to this shape's peers.")

    return _apply_budget("\n".join(lines), SUMMARY_BUDGET)


__all__ = [
    "CONVENTIONS_SECTION_BUDGET",
    "ENTRY_SECTION_BUDGET",
    "NOT_PRESENT_SECTION_BUDGET",
    "PARTIES_SECTION_BUDGET",
    "RECORDS_SECTION_BUDGET",
    "RELATIONSHIPS_SECTION_BUDGET",
    "SUMMARY_BUDGET",
    "TIMELINE_SECTION_BUDGET",
    "render_entry_brief",
    "render_entry_summary",
]
