from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest

from app.ingestion.extractor import ExtractionConfig, extract_zip


def _make_zip(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return zip_path


@pytest.fixture
def target_dir(tmp_path: Path) -> Path:
    return tmp_path / "output"


async def test_valid_zip_extracts(tmp_path: Path, target_dir: Path):
    zip_path = _make_zip(tmp_path, {
        "folder/hello.txt": b"Hello world",
        "data.csv": b"a,b,c\n1,2,3\n",
    })

    result = await extract_zip(zip_path, target_dir)

    assert result.success is True
    assert result.extracted_count == 2
    assert result.error is None
    assert "folder/hello.txt" in result.entries
    assert "data.csv" in result.entries
    assert (target_dir / "folder" / "hello.txt").read_bytes() == b"Hello world"
    assert (target_dir / "data.csv").read_bytes() == b"a,b,c\n1,2,3\n"


async def test_path_traversal_rejected(tmp_path: Path, target_dir: Path):
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../escape.txt", b"gotcha")

    result = await extract_zip(zip_path, target_dir)

    assert result.success is False
    assert "traversal" in result.error.lower()


async def test_absolute_path_rejected(tmp_path: Path, target_dir: Path):
    zip_path = tmp_path / "abs.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("/etc/passwd", b"root:x:0:0")
    buf.seek(0)
    zip_path.write_bytes(buf.getvalue())

    result = await extract_zip(zip_path, target_dir)

    assert result.success is False
    assert "absolute" in result.error.lower()


async def test_non_zip_rejected(tmp_path: Path, target_dir: Path):
    fake = tmp_path / "notazip.zip"
    fake.write_bytes(b"this is not a zip file at all")

    result = await extract_zip(fake, target_dir)

    assert result.success is False
    assert "not a valid zip" in result.error.lower()


async def test_entry_count_limit(tmp_path: Path, target_dir: Path):
    zip_path = tmp_path / "many.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for i in range(15):
            zf.writestr(f"file_{i}.txt", f"content {i}")

    config = ExtractionConfig(max_entry_count=10)
    result = await extract_zip(zip_path, target_dir, config)

    assert result.success is False
    assert "15 entries" in result.error
    assert "limit of 10" in result.error


async def test_compression_ratio_rejected(tmp_path: Path, target_dir: Path):
    zip_path = tmp_path / "bomb.zip"
    # Highly compressible data - zeros compress extremely well
    huge_data = b"\x00" * (1024 * 1024)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.bin", huge_data)

    config = ExtractionConfig(max_compression_ratio=5.0)
    result = await extract_zip(zip_path, target_dir, config)

    assert result.success is False
    assert "compression ratio" in result.error.lower()


async def test_uncompressed_size_limit(tmp_path: Path, target_dir: Path):
    zip_path = tmp_path / "big.zip"
    data = b"x" * (2 * 1024 * 1024)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("big.bin", data)

    config = ExtractionConfig(max_uncompressed_size_mb=1)
    result = await extract_zip(zip_path, target_dir, config)

    assert result.success is False
    assert "uncompressed size" in result.error.lower()


async def test_symlink_rejected(tmp_path: Path, target_dir: Path):
    # Craft a ZIP with a symlink entry using Unix external attributes
    zip_path = tmp_path / "symlink.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link.txt")
        info.create_system = 3  # Unix
        # Set symlink file type (0o120000) with permissions 0o777
        info.external_attr = (0o120777) << 16
        zf.writestr(info, "/etc/passwd")
    buf.seek(0)
    zip_path.write_bytes(buf.getvalue())

    result = await extract_zip(zip_path, target_dir)

    assert result.success is False
    assert "symlink" in result.error.lower()


async def test_file_not_found(tmp_path: Path, target_dir: Path):
    missing = tmp_path / "nonexistent.zip"
    result = await extract_zip(missing, target_dir)

    assert result.success is False
    assert "not found" in result.error.lower()


async def test_unicode_filenames(tmp_path: Path, target_dir: Path):
    zip_path = _make_zip(tmp_path, {
        "Uebersicht/Rechnungen_2024.csv": b"Betrag;Datum\n100,50;01.03.2024\n",
    })

    result = await extract_zip(zip_path, target_dir)

    assert result.success is True
    assert (target_dir / "Uebersicht" / "Rechnungen_2024.csv").exists()
