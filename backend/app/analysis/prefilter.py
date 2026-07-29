"""Cheap, deterministic pre-filter over process graphs.

The graph engine (T1) produces thousands of process graphs per dossier - one
model call per graph is the same cost wall that killed the Cognee integration.
This module decides which graphs are worth a model call at all; it never
decides whether something is fraud.

Design rule, non-negotiable: this filter must be recall-oriented and generous.
A false positive here costs one bounded model call. A false negative loses a
finding permanently, because nothing else ever looks at that graph again. If a
future edit narrows these signals until only genuinely fraudulent graphs pass,
it has quietly turned this filter into the rule engine the project owner
explicitly rejected in favor of LLM traversal - judgement, explanations, and
rejecting innocent-but-suspicious cases belong to the model, not here.

None of the signals below encode a specific vendor, account, or date range -
that was the mistake in the deleted cloud-ingestion payload filter
(``9000 <= amount < 10000``, accounts ``040``/``060``/``670``, dates
``2025-12``/``2026-01``), which was quietly tuned to the known findings.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.graph.schema import EdgeType
from app.graph.store import load_graph, load_process_graphs
from app.graph.subgraphs import ProcessGraph
from app.persistence.database import iter_records_by_dossier

logger = logging.getLogger(__name__)

# Repair/maintenance wording seen on asset descriptions (ANLAGENBEZEICHNUNG).
# Repairs and maintenance should be expensed, not capitalized - see the
# red-flag briefing in graph_analyzer.py.
_REPAIR_KEYWORDS = (
    "reparatur",
    "instandsetzung",
    "austausch",
    "generalüberholung",
    "generaluberholung",
    "wartung",
)

# Record types carrying a master-data change with separate changer/approver
# columns (GEAENDERT_VON / GENEHMIGT_VON).
_MASTER_DATA_TYPES = ("master_data", "master_change")

# Generic round-number thresholds an approval limit is plausibly set at. Not
# tuned to this dossier's actual threshold - deliberately broad so the filter
# doesn't have to know the real approval policy to be useful.
_ROUND_THRESHOLDS = (1_000.0, 5_000.0, 10_000.0, 25_000.0, 50_000.0, 100_000.0)
_ROUND_THRESHOLD_MARGIN = 0.10  # "just below" = within 10% under the threshold

# Booking-side vs. service-side date columns across the parsers this project
# has (gdpdu_txt and csv_parser). Both sides are normalized to ISO 8601 by the
# parser before they reach this module, so comparison is a plain string slice.
_BOOKING_DATE_FIELDS = ("BUCHUNGSDATUM", "FAKTURADATUM", "RECHNUNGSDATUM")
_SERVICE_DATE_FIELDS = ("BELEGDATUM", "LEISTUNGSDATUM")

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# Signal kinds, weighted by how specific and how rare they are. These weights
# order candidates so that a per-run model-call cap truncates the *weakest*
# candidates rather than an arbitrary slice.
#
# This ordering is load-bearing, not cosmetic. On the sample dossier the filter
# selects ~1,650 of ~4,900 graphs while the default cap is 500, so without a
# meaningful order the analyzer would examine an effectively random third of the
# candidates - process graphs arrive sorted by a uuid5-derived id - and a strong
# signal like "this vendor was paid but no goods receipt exists anywhere" could
# be dropped in favour of a graph whose only distinction is a round number.
#
# Weights express specificity, NOT a fraud probability, and this remains a
# decision about where to *look* first. A low-weight graph is still analyzed
# whenever the cap allows.
_SIGNAL_MISSING_RECEIPT = "missing_receipt"
_SIGNAL_SELF_APPROVAL = "self_approval"
_SIGNAL_REPAIR_ASSET = "repair_asset"
_SIGNAL_PERIOD_MISMATCH = "period_mismatch"
_SIGNAL_PAYMENT_SPLITTING = "payment_splitting"
_SIGNAL_ROUND_AMOUNT = "round_amount"

_SIGNAL_WEIGHTS: dict[str, int] = {
    # A payment with no corresponding goods receipt, and one person both making
    # and approving a master-data change, are narrow and hard to explain away.
    _SIGNAL_MISSING_RECEIPT: 100,
    _SIGNAL_SELF_APPROVAL: 100,
    # Wording and date mismatches are suggestive but have innocent explanations.
    _SIGNAL_REPAIR_ASSET: 80,
    _SIGNAL_PERIOD_MISMATCH: 70,
    # Several payments under one threshold is interesting; two is often routine.
    _SIGNAL_PAYMENT_SPLITTING: 60,
    # Round amounts are extremely common in real ledgers - by far the weakest
    # signal here, and the one a cap should discard first.
    _SIGNAL_ROUND_AMOUNT: 10,
}


@dataclass(frozen=True)
class Candidate:
    """A process graph that earned a model call, and why."""

    graph: ProcessGraph
    reasons: tuple[str, ...]
    signals: tuple[str, ...]

    @property
    def priority(self) -> int:
        """Weight of this graph's strongest signal - see ``_SIGNAL_WEIGHTS``."""
        return max((_SIGNAL_WEIGHTS.get(signal, 0) for signal in self.signals), default=0)


def _get_ci(data: dict[str, Any], field_name: str) -> Any:
    """Case-insensitive dict lookup - column names come from the source export
    verbatim and casing is not guaranteed to match our constants' casing."""
    if field_name in data:
        return data[field_name]
    upper = field_name.upper()
    for key, value in data.items():
        if key.upper() == upper:
            return value
    return None


def _period_of(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    if _ISO_DATE_RE.match(text):
        return text[:7]
    return None


def _booking_service_period_mismatch(data: dict[str, Any]) -> bool:
    booking_period = None
    for field_name in _BOOKING_DATE_FIELDS:
        booking_period = _period_of(_get_ci(data, field_name))
        if booking_period:
            break

    service_period = None
    for field_name in _SERVICE_DATE_FIELDS:
        service_period = _period_of(_get_ci(data, field_name))
        if service_period:
            break

    return bool(booking_period) and bool(service_period) and booking_period != service_period


def _is_round_amount(amount: float | None) -> bool:
    """A whole multiple of 10 currency units - e.g. 53.550,00 EUR.

    Verified against the real sample dossier: only ~1% of vendor postings
    satisfy this, so it is a selective signal rather than one that fires on
    nearly everything.
    """
    if not amount:
        return False
    cents = round(abs(amount) * 100)
    return cents % 1000 == 0


def _round_threshold_just_below(amount: float | None) -> float | None:
    if not amount:
        return None
    value = abs(amount)
    for threshold in _ROUND_THRESHOLDS:
        if threshold * (1 - _ROUND_THRESHOLD_MARGIN) <= value < threshold:
            return threshold
    return None


def _record_view(row: dict[str, Any]) -> dict[str, Any]:
    parsed = json.loads(row["data_json"])
    return {
        "record_type": row["record_type"],
        "amount": row["amount"],
        "date": row["date"],
        "data": parsed.get("data") or {},
    }


def select_candidate_graphs(dossier_id: str, db_path: Path) -> list[Candidate]:
    """Select process graphs worth a model call, with the reason(s) why.

    Loads the whole persisted graph and streams every normalized record for
    the dossier exactly once - both are cheap local SQLite/in-memory
    operations, unlike the model calls this function exists to ration.
    """
    graph = load_graph(db_path, dossier_id)
    process_graphs = load_process_graphs(db_path, dossier_id)

    records_by_id = {
        row["record_id"]: _record_view(row) for row in iter_records_by_dossier(db_path, dossier_id)
    }

    vendors_with_receipt = {
        source
        for source, _target, data in graph.edges(data=True)
        if data.get("edge_type") == EdgeType.has_receipt.value
    }

    candidates: list[Candidate] = []

    for process_graph in process_graphs:
        reasons: list[str] = []
        signals: set[str] = set()

        for entity_node_id in process_graph.entity_node_ids:
            if entity_node_id.startswith("vendor:") and entity_node_id not in vendors_with_receipt:
                reasons.append(
                    f"vendor {entity_node_id} has postings in this graph but no has_receipt edge anywhere in the dossier"
                )
                signals.add(_SIGNAL_MISSING_RECEIPT)

        # (date, absolute amount, threshold) for each record sitting just under a
        # plausible approval threshold. Grouped by date below - see the splitting
        # check after this loop.
        near_threshold: list[tuple[str, float, float]] = []
        for record_id in process_graph.record_ids:
            record = records_by_id.get(record_id)
            if record is None:
                continue

            amount = record["amount"]
            data = record["data"]
            record_type = record["record_type"]

            if _is_round_amount(amount):
                reasons.append(f"record {record_id} has a round amount ({amount})")
                signals.add(_SIGNAL_ROUND_AMOUNT)

            threshold = _round_threshold_just_below(amount)
            if threshold is not None and record["date"]:
                near_threshold.append((str(record["date"]), abs(float(amount)), threshold))

            if record_type in _MASTER_DATA_TYPES:
                changer = _get_ci(data, "GEAENDERT_VON")
                approver = _get_ci(data, "GENEHMIGT_VON")
                if changer and approver and str(changer) == str(approver):
                    reasons.append(
                        f"record {record_id}: changer and approver are both {changer!r} (segregation of duties)"
                    )
                    signals.add(_SIGNAL_SELF_APPROVAL)

            if record_type in ("asset_record", "asset_posting"):
                description = str(_get_ci(data, "ANLAGENBEZEICHNUNG") or "")
                lowered = description.lower()
                if any(keyword in lowered for keyword in _REPAIR_KEYWORDS):
                    reasons.append(
                        f"record {record_id}: asset description reads like repair/maintenance ({description!r})"
                    )
                    signals.add(_SIGNAL_REPAIR_ASSET)

            if _booking_service_period_mismatch(data):
                reasons.append(f"record {record_id}: booking date and service date fall in different periods")
                signals.add(_SIGNAL_PERIOD_MISMATCH)

        # Splitting means several payments that individually stay under an approval
        # threshold but together cross it - so require same-day clustering AND a
        # combined total over the threshold, not merely two smallish amounts.
        #
        # Counting any two near-threshold records was far too loose: on the sample
        # dossier it fired on 1,167 of 4,902 graphs, flooding the ranked candidate
        # list and pushing genuine splitting behind hundreds of coincidences. A
        # process graph is already one document cluster, so grouping by date within
        # it is enough to identify a same-counterparty batch.
        by_date: dict[str, list[tuple[float, float]]] = {}
        for date, amount_abs, threshold in near_threshold:
            by_date.setdefault(date, []).append((amount_abs, threshold))

        for date, group in sorted(by_date.items()):
            if len(group) < 2:
                continue
            combined = sum(amount_abs for amount_abs, _ in group)
            threshold = max(threshold for _, threshold in group)
            if combined >= threshold:
                reasons.append(
                    f"{len(group)} payments on {date} each sit just below {threshold:.0f} "
                    f"but total {combined:.2f} - possible splitting to evade an approval limit"
                )
                signals.add(_SIGNAL_PAYMENT_SPLITTING)
                break

        if reasons:
            candidates.append(
                Candidate(
                    graph=process_graph,
                    reasons=tuple(reasons),
                    signals=tuple(sorted(signals)),
                )
            )

    # Strongest signal first, then most corroborating signals, then graph_id so
    # the order is fully deterministic. A per-run cap truncates this list, so
    # this sort decides what gets dropped when there is more to look at than
    # budget allows - see _SIGNAL_WEIGHTS.
    candidates.sort(key=lambda c: (-c.priority, -len(c.signals), c.graph.graph_id))

    logger.info(
        "dossier %s: pre-filter selected %d of %d process graphs for a model call "
        "(strongest signal first; highest priority %d)",
        dossier_id,
        len(candidates),
        len(process_graphs),
        candidates[0].priority if candidates else 0,
    )
    return candidates


__all__ = ["Candidate", "select_candidate_graphs"]
