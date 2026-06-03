from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import py7zr

from recursive_unzip_tool.extractor import (
    ArchiveItem,
    ExecutionOptions,
    ScanOptions,
    extract_archive,
    extract_recursive,
    extract_selected,
    find_archives,
    scan_archives,
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


def test_scan_archives_returns_archive_metadata(tmp_path: Path) -> None:
    nested = tmp_path / "level1" / "level2"
    nested.mkdir(parents=True)
    archive_path = nested / "bundle.zip"
    _make_zip(archive_path, "inside.txt", "zip content")

    result = scan_archives(tmp_path, ScanOptions(enabled_formats=(".zip",)))

    assert len(result.items) == 1
    item = result.items[0]
    assert item.path == archive_path
    assert item.relative_path == Path("level1/level2/bundle.zip")
    assert item.archive_format == ".zip"
    assert item.size > 0
    assert item.is_archive is True
    assert item.selected is True
    assert result.scanned_dirs == 3


def test_scan_archives_respects_max_depth(tmp_path: Path) -> None:
    shallow = tmp_path / "shallow.zip"
    deep_dir = tmp_path / "level1" / "level2"
    deep_dir.mkdir(parents=True)
    deep = deep_dir / "deep.zip"
    _make_zip(shallow)
    _make_zip(deep)

    result = scan_archives(
        tmp_path,
        ScanOptions(enabled_formats=(".zip",), max_depth=1),
    )

    assert [item.path for item in result.items] == [shallow]


def test_scan_archives_respects_enabled_formats(tmp_path: Path) -> None:
    zip_path = tmp_path / "bundle.zip"
    tar_source = tmp_path / "payload.txt"
    tar_path = tmp_path / "bundle.tar.gz"
    _make_zip(zip_path)
    tar_source.write_text("tar content", encoding="utf-8")
    _make_tar_gz(tar_path, tar_source)

    result = scan_archives(tmp_path, ScanOptions(enabled_formats=(".zip",)))

    assert [item.path for item in result.items] == [zip_path]


def test_scan_archives_show_all_files_marks_regular_files_not_executable(tmp_path: Path) -> None:
    archive_path = tmp_path / "bundle.zip"
    text_path = tmp_path / "notes.txt"
    _make_zip(archive_path)
    text_path.write_text("plain text", encoding="utf-8")

    result = scan_archives(
        tmp_path,
        ScanOptions(enabled_formats=(".zip",), show_all_files=True),
    )

    by_name = {item.path.name: item for item in result.items}
    assert by_name["bundle.zip"].is_archive is True
    assert by_name["bundle.zip"].selected is True
    assert by_name["notes.txt"].is_archive is False
    assert by_name["notes.txt"].selected is True


def test_extract_selected_only_processes_selected_archive_items(tmp_path: Path) -> None:
    selected_zip = tmp_path / "selected.zip"
    skipped_zip = tmp_path / "skipped.zip"
    _make_zip(selected_zip, "selected.txt", "selected")
    _make_zip(skipped_zip, "skipped.txt", "skipped")
    scan_result = scan_archives(tmp_path, ScanOptions(enabled_formats=(".zip",)))

    for item in scan_result.items:
        item.selected = item.path == selected_zip

    results = extract_selected(scan_result.items, ExecutionOptions(worker_count=2))

    assert len(results) == 1
    assert results[0].path == selected_zip
    assert (tmp_path / "selected.txt").read_text(encoding="utf-8") == "selected"
    assert not (tmp_path / "skipped.txt").exists()


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


def test_failed_archive_is_not_deleted_and_has_suggestion(tmp_path: Path) -> None:
    archive_path = tmp_path / "broken.zip"
    archive_path.write_text("not a zip file", encoding="utf-8")

    result = extract_archive(archive_path, delete_source=True)

    assert result.status == "failed"
    assert archive_path.exists()
    assert result.error_type == "corrupt"
    assert "CRC" in result.suggestion or "损坏" in result.suggestion


def test_retry_failed_flow_can_filter_failed_items(tmp_path: Path) -> None:
    good = tmp_path / "good.zip"
    broken = tmp_path / "broken.zip"
    _make_zip(good, "good.txt", "ok")
    broken.write_text("not a zip file", encoding="utf-8")
    scan_result = scan_archives(tmp_path, ScanOptions(enabled_formats=(".zip",)))

    first_results = extract_selected(scan_result.items, ExecutionOptions(worker_count=2))
    failed_paths = {result.path for result in first_results if result.status == "failed"}
    retry_items = [item for item in scan_result.items if item.path in failed_paths]

    retry_results = extract_selected(retry_items, ExecutionOptions(worker_count=1))

    assert failed_paths == {broken}
    assert [result.path for result in retry_results] == [broken]
    assert retry_results[0].status == "failed"


def test_extract_recursive_legacy_wrapper_extracts_tar_gz(tmp_path: Path) -> None:
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


def test_find_archives_legacy_wrapper(tmp_path: Path) -> None:
    archive_path = tmp_path / "bundle.7z"
    source_file = tmp_path / "source.txt"
    source_file.write_text("7z content", encoding="utf-8")
    _make_7z(archive_path, source_file)

    found = find_archives(tmp_path, [".7z"])

    assert found == [archive_path]
