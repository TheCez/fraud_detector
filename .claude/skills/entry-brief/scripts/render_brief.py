"""Render a real entry brief from the sample dossier, for inspection.

The analyst sees exactly this text and nothing else, so this is the first place to look when it
misses something: was the fact in the brief at all?

Usage, from `backend/`:

    python <this>/render_brief.py                          # size stats over a sample of entries
    python <this>/render_brief.py --entity vendor:209101   # every entry touching that entity
    python <this>/render_brief.py --graph PG-8134acaac5b855c4
    python <this>/render_brief.py --largest --section Parties

Builds the dossier from the real ZIP on first run and caches it under the scratch dir, which takes
about 20s; later runs reuse it. Pass --rebuild after changing a parser or the graph builder.
"""

from __future__ import annotations

import argparse
import shutil
import statistics
import sys
import zipfile
from pathlib import Path

# <repo>/.claude/skills/entry-brief/scripts/render_brief.py -> four levels up is the repo root.
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "backend"))

from app.analysis.entry_brief import render_entry_brief, render_entry_summary  # noqa: E402
from app.analysis.profile import build_profile  # noqa: E402
from app.graph.builder import build_graph  # noqa: E402
from app.graph.subgraphs import build_process_graphs  # noqa: E402
from app.ingestion.manifest import build_manifest  # noqa: E402
from app.normalization.orchestrator import normalize_dossier  # noqa: E402

DOSSIER = "sample-dossier"
ZIP = REPO / "sample_data" / "Uebungsdaten_Muster_Verpackungen.zip"
CACHE = Path(__file__).resolve().parent / ".cache"


def prepare(rebuild: bool) -> Path:
    if rebuild and CACHE.exists():
        shutil.rmtree(CACHE, ignore_errors=True)
    db_path = CACHE / "ws" / "registry.db"
    if db_path.exists():
        return db_path
    extract = CACHE / "extract"
    extract.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP) as zf:
        zf.extractall(extract)
    (root,) = [p for p in extract.iterdir() if p.is_dir()]
    workspace = CACHE / "ws" / "dossiers" / DOSSIER
    workspace.mkdir(parents=True, exist_ok=True)
    normalize_dossier(root, workspace, build_manifest(root, DOSSIER), DOSSIER)
    return db_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entity", help="entity node id, e.g. vendor:209101")
    ap.add_argument("--graph", help="a specific graph_id")
    ap.add_argument("--largest", action="store_true", help="the entry with the most records")
    ap.add_argument("--section", help="print only this section, e.g. Parties or 'Not present'")
    ap.add_argument("--summary", action="store_true", help="print the gate summary instead")
    ap.add_argument("--rebuild", action="store_true", help="re-normalize from the ZIP")
    args = ap.parse_args()

    db_path = prepare(args.rebuild)
    graph = build_graph(DOSSIER, db_path)
    pgs = build_process_graphs(DOSSIER, graph)
    profile = build_profile(DOSSIER, db_path, graph=graph, process_graphs=pgs)

    if args.entity:
        targets = [pg for pg in pgs if args.entity in pg.entity_node_ids]
    elif args.graph:
        targets = [pg for pg in pgs if pg.graph_id == args.graph]
    elif args.largest:
        targets = [max(pgs, key=lambda pg: (pg.record_count, pg.graph_id))]
    else:
        ordered = sorted(pgs, key=lambda pg: (pg.record_count, pg.graph_id))
        lens = [
            len(render_entry_brief(DOSSIER, db_path, pg.graph_id, profile, graph=graph, process_graphs=pgs))
            for pg in ordered[::200]
        ]
        print(f"entries={len(pgs)}  sampled={len(lens)}")
        print(f"brief chars: min={min(lens)} median={statistics.median(lens):.0f} max={max(lens)}")
        print(f"~tokens at 4 chars/token: median={statistics.median(lens)/4:.0f} max={max(lens)/4:.0f}")
        print(f"projected whole dossier: {statistics.mean(lens)*len(pgs)/4/1e6:.1f}M tokens")
        return

    if not targets:
        print("no entry matched")
        return

    for pg in targets:
        render = render_entry_summary if args.summary else render_entry_brief
        text = render(DOSSIER, db_path, pg.graph_id, profile, graph=graph, process_graphs=pgs)
        print(f"\n{'=' * 100}\n{pg.graph_id}: {pg.record_count} records, {len(pg.entity_node_ids)} entities, "
              f"{len(text)} chars, truncation markers: {text.count('[TRUNCATED')}\n{'=' * 100}")
        if args.section:
            blocks = [b for b in text.split("\n\n") if b.startswith(args.section)]
            print("\n\n".join(blocks) if blocks else f"(no section named {args.section!r})")
        else:
            print(text)


if __name__ == "__main__":
    main()
