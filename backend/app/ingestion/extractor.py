from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel


class ExtractionConfig(BaseModel):
    max_compressed_size_mb: float = 100
    max_uncompressed_size_mb: float = 500
    max_entry_count: int = 1000
    max_compression_ratio: float = 20.0


class ExtractionResult(BaseModel):
    success: bool
    extracted_count: int = 0
    total_size_bytes: int = 0
    entries: list[str] = []
    error: str | None = None


def _validate_entry_name(name: str) -> str | None:
    """Return error message if name is unsafe, else None."""
    posix = PurePosixPath(name)
    if posix.is_absolute():
        return f"Absolute path in archive: {name}"
    for part in posix.parts:
        if part == "..":
            return f"Path traversal in archive entry: {name}"
    return None


async def extract_zip(
    zip_path: Path,
    target_dir: Path,
    config: ExtractionConfig | None = None,
) -> ExtractionResult:
    cfg = config or ExtractionConfig()

    if not zip_path.exists():
        return ExtractionResult(success=False, error=f"File not found: {zip_path}")

    if not zipfile.is_zipfile(zip_path):
        return ExtractionResult(success=False, error="File is not a valid ZIP archive")

    compressed_size = zip_path.stat().st_size
    if compressed_size > cfg.max_compressed_size_mb * 1024 * 1024:
        return ExtractionResult(
            success=False,
            error=f"Compressed size {compressed_size / 1024 / 1024:.1f} MB exceeds limit of {cfg.max_compressed_size_mb} MB",
        )

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            infos = zf.infolist()

            if len(infos) > cfg.max_entry_count:
                return ExtractionResult(
                    success=False,
                    error=f"Archive contains {len(infos)} entries, exceeding limit of {cfg.max_entry_count}",
                )

            total_uncompressed: int = 0
            for info in infos:
                name_error = _validate_entry_name(info.filename)
                if name_error:
                    return ExtractionResult(success=False, error=name_error)

                if info.external_attr >> 28 == 0xA:
                    return ExtractionResult(
                        success=False,
                        error=f"Symlink detected in archive: {info.filename}",
                    )

                # Unix symlink bit in external_attr
                if info.create_system == 3:
                    unix_mode = (info.external_attr >> 16) & 0xFFFF
                    if (unix_mode & 0o170000) == 0o120000:
                        return ExtractionResult(
                            success=False,
                            error=f"Symlink detected in archive: {info.filename}",
                        )

                if info.file_size > 0 and info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > cfg.max_compression_ratio:
                        return ExtractionResult(
                            success=False,
                            error=(
                                f"Entry '{info.filename}' has compression ratio {ratio:.1f}, "
                                f"exceeding limit of {cfg.max_compression_ratio}"
                            ),
                        )

                total_uncompressed += info.file_size

            if total_uncompressed > cfg.max_uncompressed_size_mb * 1024 * 1024:
                return ExtractionResult(
                    success=False,
                    error=(
                        f"Total uncompressed size {total_uncompressed / 1024 / 1024:.1f} MB "
                        f"exceeds limit of {cfg.max_uncompressed_size_mb} MB"
                    ),
                )

            target_dir.mkdir(parents=True, exist_ok=True)
            extracted_entries: list[str] = []
            total_bytes: int = 0

            for info in infos:
                if info.is_dir():
                    (target_dir / info.filename).mkdir(parents=True, exist_ok=True)
                    continue

                dest = target_dir / info.filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as dst:
                    data = src.read()
                    dst.write(data)
                    total_bytes += len(data)

                extracted_entries.append(info.filename)

            return ExtractionResult(
                success=True,
                extracted_count=len(extracted_entries),
                total_size_bytes=total_bytes,
                entries=extracted_entries,
            )

    except zipfile.BadZipFile:
        return ExtractionResult(success=False, error="Corrupted or invalid ZIP archive")
    except Exception as exc:
        return ExtractionResult(success=False, error=f"Extraction failed: {exc}")
