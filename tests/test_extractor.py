from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import py7zr

from recursive_unzip_tool.extractor import (
    extract_archive,
    extract_recursive,
    find_archives,
)


def _make_zip(path: Path, filename: str = "payload.txt", content: str = "ok") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(filename, content)


def _make_tar_gz(path: Path, source_file: Path) -> None:
    with tarfile.open(path, "w:gz") as archive:
        archive.add(source_file, arcname=source_file.name)


def _make_7z(path: Path, source_file: Path) -> None:
    with py7zr.SevenZipFile(path, "w") as archive:
        archive.write(source_file, arcname=source_file.name)


def test_extract_recursive_extracts_zip_to_archive_directory(tmp_path: Path) -> None:
    nested = tmp_path / "level1" / "level2"
    nested.mkdir(parents=True)
    archive_path = nested / "bundle.zip"
    _make_zip(archive_path, "inside.txt", "zip content")

    results = extract_recursive(tmp_path, [".zip"])

    assert len(results) == 1
    assert results[0].status == "success"
    assert archive_path.exists()
    assert (nested / "inside.txt").read_text() == "zip content"


def test_extract_archive_deletes_source_only_when_enabled(tmp_path: Path) -> None:
    keep_archive = tmp_path / "keep.zip"
    delete_archive = tmp_path / "delete.zip"
    _make_zip(keep_archive)
    _make_zip(delete_archive)

    keep_result = extract_archive(keep_archive, delete_source=False)
    delete_result = extract_archive(delete_archive, delete_source=True)

    assert keep_result.status == "success"
    assert keep_archive.exists()
    assert delete_result.status == "success"
    assert delete_result.deleted_source is True
    assert not delete_archive.exists()


def test_failed_archive_is_not_deleted(tmp_path: Path) -> None:
    archive_path = tmp_path / "broken.zip"
    archive_path.write_text("not a zip file", encoding="utf-8")

    result = extract_archive(archive_path, delete_source=True)

    assert result.status == "failed"
    assert archive_path.exists()


def test_find_archives_respects_enabled_formats(tmp_path: Path) -> None:
    zip_path = tmp_path / "bundle.zip"
    tar_source = tmp_path / "payload.txt"
    tar_path = tmp_path / "bundle.tar.gz"
    _make_zip(zip_path)
    tar_source.write_text("tar content", encoding="utf-8")
    _make_tar_gz(tar_path, tar_source)

    found = find_archives(tmp_path, [".zip"])

    assert found == [zip_path]


def test_extract_recursive_extracts_tar_gz(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    source_file = tmp_path / "source.txt"
    source_file.write_text("tar content", encoding="utf-8")
    archive_path = archive_dir / "bundle.tar.gz"
    _make_tar_gz(archive_path, source_file)
    source_file.unlink()

    results = extract_recursive(tmp_path, [".tar*"])

    assert len(results) == 1
    assert results[0].status == "success"
    assert (archive_dir / "source.txt").read_text(encoding="utf-8") == "tar content"


def test_extract_recursive_extracts_7z(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    source_file = tmp_path / "source.txt"
    source_file.write_text("7z content", encoding="utf-8")
    archive_path = archive_dir / "bundle.7z"
    _make_7z(archive_path, source_file)
    source_file.unlink()

    results = extract_recursive(tmp_path, [".7z"])

    assert len(results) == 1
    assert results[0].status == "success"
    assert (archive_dir / "source.txt").read_text(encoding="utf-8") == "7z content"
