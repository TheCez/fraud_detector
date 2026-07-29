import json
import sqlite3
from pathlib import Path


_CREATE_DOSSIERS_TABLE = """
CREATE TABLE IF NOT EXISTS dossiers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploading',
    file_count INTEGER DEFAULT 0,
    record_count INTEGER DEFAULT 0,
    finding_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
)
"""

_CREATE_NORMALIZED_RECORDS_TABLE = """
CREATE TABLE IF NOT EXISTS normalized_records (
    record_id TEXT PRIMARY KEY,
    dossier_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    record_type TEXT NOT NULL,
    date TEXT,
    amount REAL,
    currency TEXT,
    data_json TEXT,
    FOREIGN KEY (dossier_id) REFERENCES dossiers(id)
)
"""

_CREATE_NORMALIZED_RECORDS_IDX_DOSSIER = """
CREATE INDEX IF NOT EXISTS idx_normalized_records_dossier
ON normalized_records(dossier_id)
"""

_CREATE_NORMALIZED_RECORDS_IDX_FILE = """
CREATE INDEX IF NOT EXISTS idx_normalized_records_file
ON normalized_records(dossier_id, file_id)
"""

_CREATE_NORMALIZED_RECORDS_IDX_TYPE = """
CREATE INDEX IF NOT EXISTS idx_normalized_records_type
ON normalized_records(dossier_id, record_type)
"""

_BATCH_SIZE = 1000


def init_registry(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute(_CREATE_DOSSIERS_TABLE)
        con.commit()
    finally:
        con.close()


def insert_dossier(db_path: Path, dossier: dict) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO dossiers (id, name, status, file_count, record_count, finding_count, created_at) "
            "VALUES (:id, :name, :status, :file_count, :record_count, :finding_count, :created_at)",
            dossier,
        )
        con.commit()
    finally:
        con.close()


def update_dossier_status(db_path: Path, dossier_id: str, status: str, **kwargs) -> None:
    fields = ["status = ?"]
    values: list = [status]

    for key, value in kwargs.items():
        fields.append(f"{key} = ?")
        values.append(value)

    values.append(dossier_id)

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            f"UPDATE dossiers SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        con.commit()
    finally:
        con.close()


def get_dossier(db_path: Path, dossier_id: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT * FROM dossiers WHERE id = ?", (dossier_id,)).fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        con.close()


def get_all_dossiers(db_path: Path) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT * FROM dossiers ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# --- Normalized records ---


def init_normalized_table(db_path: Path) -> None:
    """Create normalized_records table if not exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute(_CREATE_NORMALIZED_RECORDS_TABLE)
        con.execute(_CREATE_NORMALIZED_RECORDS_IDX_DOSSIER)
        con.execute(_CREATE_NORMALIZED_RECORDS_IDX_FILE)
        con.execute(_CREATE_NORMALIZED_RECORDS_IDX_TYPE)
        con.commit()
    finally:
        con.close()


def bulk_insert_records(db_path: Path, records: list[dict]) -> int:
    """Insert records in batches. Returns count inserted."""
    if not records:
        return 0

    con = sqlite3.connect(db_path)
    inserted = 0
    try:
        for i in range(0, len(records), _BATCH_SIZE):
            batch = records[i : i + _BATCH_SIZE]
            con.executemany(
                "INSERT OR REPLACE INTO normalized_records "
                "(record_id, dossier_id, file_id, record_type, date, amount, currency, data_json) "
                "VALUES (:record_id, :dossier_id, :file_id, :record_type, :date, :amount, :currency, :data_json)",
                batch,
            )
            inserted += len(batch)
        con.commit()
    finally:
        con.close()
    return inserted


def get_records_by_file(
    db_path: Path, dossier_id: str, file_id: str, limit: int = 100, offset: int = 0
) -> list[dict]:
    """Get normalized records for a specific file."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM normalized_records WHERE dossier_id = ? AND file_id = ? "
            "LIMIT ? OFFSET ?",
            (dossier_id, file_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_records_by_type(
    db_path: Path, dossier_id: str, record_type: str
) -> list[dict]:
    """Get all records of a given type for a dossier."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM normalized_records WHERE dossier_id = ? AND record_type = ?",
            (dossier_id, record_type),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_record_count(db_path: Path, dossier_id: str) -> int:
    """Total record count for a dossier."""
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM normalized_records WHERE dossier_id = ?",
            (dossier_id,),
        ).fetchone()
        return row[0] if row else 0
    finally:
        con.close()


def get_record_by_id(db_path: Path, dossier_id: str, record_id: str) -> dict | None:
    """Return one record only when it belongs to the requested dossier."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM normalized_records WHERE dossier_id = ? AND record_id = ?",
            (dossier_id, record_id),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


# --- Findings ---

_CREATE_FINDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT NOT NULL,
    dossier_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (dossier_id, finding_id),
    FOREIGN KEY (dossier_id) REFERENCES dossiers(id)
)
"""

_CREATE_FINDINGS_IDX = """
CREATE INDEX IF NOT EXISTS idx_findings_dossier
ON findings(dossier_id)
"""


def init_findings_table(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        columns = con.execute("PRAGMA table_info(findings)").fetchall()
        if columns and {row[1] for row in columns if row[5]} == {"finding_id"}:
            con.execute("ALTER TABLE findings RENAME TO findings_legacy")
            con.execute(_CREATE_FINDINGS_TABLE)
            con.execute(
                "INSERT INTO findings (finding_id, dossier_id, data_json) "
                "SELECT finding_id, dossier_id, data_json FROM findings_legacy"
            )
            con.execute("DROP TABLE findings_legacy")
        con.execute(_CREATE_FINDINGS_TABLE)
        con.execute(_CREATE_FINDINGS_IDX)
        con.commit()
    finally:
        con.close()


# --- Analysis runs and graph ingestion ---

_CREATE_ANALYSIS_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    id TEXT PRIMARY KEY,
    dossier_id TEXT NOT NULL,
    requested_mode TEXT NOT NULL,
    mode TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (dossier_id) REFERENCES dossiers(id)
)
"""

_CREATE_GRAPH_INGESTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS graph_ingestions (
    dossier_id TEXT PRIMARY KEY,
    dataset_name TEXT NOT NULL,
    normalized_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    error TEXT,
    FOREIGN KEY (dossier_id) REFERENCES dossiers(id)
)
"""


def init_analysis_tables(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(_CREATE_ANALYSIS_RUNS_TABLE)
        con.execute(_CREATE_GRAPH_INGESTIONS_TABLE)
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_runs_dossier "
            "ON analysis_runs(dossier_id, created_at DESC)"
        )
        con.commit()
    finally:
        con.close()


def get_graph_ingestion(db_path: Path, dossier_id: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT * FROM graph_ingestions WHERE dossier_id = ?", (dossier_id,)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


def save_graph_ingestion(
    db_path: Path, dossier_id: str, dataset_name: str, normalized_sha256: str, status: str, error: str | None = None
) -> None:
    from datetime import datetime, timezone

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO graph_ingestions (dossier_id, dataset_name, normalized_sha256, status, updated_at, error) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(dossier_id) DO UPDATE SET dataset_name = excluded.dataset_name, "
            "normalized_sha256 = excluded.normalized_sha256, status = excluded.status, "
            "updated_at = excluded.updated_at, error = excluded.error",
            (dossier_id, dataset_name, normalized_sha256, status, datetime.now(timezone.utc).isoformat(), error),
        )
        con.commit()
    finally:
        con.close()


def create_analysis_run(db_path: Path, dossier_id: str, requested_mode: str) -> str:
    import uuid
    from datetime import datetime, timezone

    run_id = str(uuid.uuid4())
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO analysis_runs (id, dossier_id, requested_mode, status, created_at) "
            "VALUES (?, ?, ?, 'running', ?)",
            (run_id, dossier_id, requested_mode, datetime.now(timezone.utc).isoformat()),
        )
        con.commit()
        return run_id
    finally:
        con.close()


def complete_analysis_run(
    db_path: Path, run_id: str, status: str, mode: str | None = None, error: str | None = None
) -> None:
    from datetime import datetime, timezone

    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE analysis_runs SET status = ?, mode = ?, error = ?, completed_at = ? WHERE id = ?",
            (status, mode, error, datetime.now(timezone.utc).isoformat(), run_id),
        )
        con.commit()
    finally:
        con.close()


def insert_findings(db_path: Path, dossier_id: str, findings_json: list[dict]) -> None:
    """Insert findings as JSON blobs."""
    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            "INSERT OR REPLACE INTO findings (finding_id, dossier_id, data_json) VALUES (?, ?, ?)",
            [(f["finding_id"], dossier_id, json.dumps(f, ensure_ascii=False)) for f in findings_json],
        )
        con.commit()
    finally:
        con.close()


def get_findings(db_path: Path, dossier_id: str) -> list[dict]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT data_json FROM findings WHERE dossier_id = ? ORDER BY finding_id",
            (dossier_id,),
        ).fetchall()
        return [json.loads(r["data_json"]) for r in rows]
    finally:
        con.close()


def get_finding(db_path: Path, dossier_id: str, finding_id: str) -> dict | None:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT data_json FROM findings WHERE dossier_id = ? AND finding_id = ?",
            (dossier_id, finding_id),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["data_json"])
    finally:
        con.close()
