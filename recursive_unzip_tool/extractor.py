from __future__ import annotations

import os
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Iterable


ARCHIVE_EXTENSIONS = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tbz2",
    ".tgz",
    ".txz",
    ".zip",
    ".7z",
    ".tar",
)

ProgressCallback = Callable[[int, int, Path, str, "ExtractResult | None"], None]


@dataclass(slots=True)
class ExtractResult:
    path: Path
    archive_format: str
    status: str
    error: str = ""
    deleted_source: bool = False


class CancelToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()


def find_archives(root_dir: str | Path, enabled_formats: Iterable[str]) -> list[Path]:
    root_path = Path(root_dir).expanduser()
    if not root_path.exists():
        raise FileNotFoundError(f"目录不存在: {root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"不是目录: {root_path}")

    extensions = _normalize_enabled_formats(enabled_formats)
    archives = [
        path
        for path in root_path.rglob("*")
        if path.is_file() and _archive_extension(path) in extensions
    ]
    return sorted(archives, key=lambda item: str(item).lower())


def extract_archive(path: str | Path, delete_source: bool = False) -> ExtractResult:
    archive_path = Path(path)
    archive_format = _archive_extension(archive_path)
    if archive_format == "":
        return ExtractResult(
            path=archive_path,
            archive_format="unknown",
            status="failed",
            error="不支持的压缩包格式",
        )

    try:
        if archive_format == ".zip":
            _extract_zip(archive_path)
        elif archive_format == ".7z":
            _extract_7z(archive_path)
        elif archive_format in {
            ".tar",
            ".tar.gz",
            ".tgz",
            ".tar.bz2",
            ".tbz2",
            ".tar.xz",
            ".txz",
        }:
            _extract_tar(archive_path)
        else:
            raise ValueError("不支持的压缩包格式")

        deleted_source = False
        if delete_source:
            archive_path.unlink()
            deleted_source = True

        return ExtractResult(
            path=archive_path,
            archive_format=archive_format,
            status="success",
            deleted_source=deleted_source,
        )
    except Exception as exc:  # noqa: BLE001 - surface archive-specific errors in the UI.
        return ExtractResult(
            path=archive_path,
            archive_format=archive_format,
            status="failed",
            error=str(exc),
        )


def extract_recursive(
    root_dir: str | Path,
    enabled_formats: Iterable[str],
    delete_source: bool = False,
    progress_callback: ProgressCallback | None = None,
    cancel_token: CancelToken | None = None,
) -> list[ExtractResult]:
    archives = find_archives(root_dir, enabled_formats)
    total = len(archives)
    results: list[ExtractResult] = []

    for index, archive_path in enumerate(archives, start=1):
        if cancel_token and cancel_token.is_cancelled:
            skipped = [
                ExtractResult(
                    path=path,
                    archive_format=_archive_extension(path) or "unknown",
                    status="skipped",
                    error="用户取消",
                )
                for path in archives[index - 1 :]
            ]
            results.extend(skipped)
            for offset, result in enumerate(skipped, start=index):
                if progress_callback:
                    progress_callback(offset, total, result.path, "skipped", result)
            break

        if progress_callback:
            progress_callback(index, total, archive_path, "processing", None)
        result = extract_archive(archive_path, delete_source=delete_source)
        results.append(result)
        if progress_callback:
            progress_callback(index, total, archive_path, result.status, result)

    return results


def _normalize_enabled_formats(enabled_formats: Iterable[str]) -> set[str]:
    normalized = {item.lower().strip() for item in enabled_formats}
    if not normalized:
        return set()

    expanded: set[str] = set()
    for item in normalized:
        value = item if item.startswith(".") else f".{item}"
        if value == ".tar*":
            expanded.update(
                {
                    ".tar",
                    ".tar.gz",
                    ".tgz",
                    ".tar.bz2",
                    ".tbz2",
                    ".tar.xz",
                    ".txz",
                }
            )
        else:
            expanded.add(value)
    return expanded & set(ARCHIVE_EXTENSIONS)


def _archive_extension(path: Path) -> str:
    lower_name = path.name.lower()
    for extension in sorted(ARCHIVE_EXTENSIONS, key=len, reverse=True):
        if lower_name.endswith(extension):
            return extension
    return ""


def _extract_zip(archive_path: Path) -> None:
    extract_dir = archive_path.parent
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            _reject_unsafe_zip_member(member, extract_dir)
        archive.extractall(extract_dir)


def _extract_tar(archive_path: Path) -> None:
    extract_dir = archive_path.parent
    with tarfile.open(archive_path) as archive:
        archive.extractall(extract_dir, filter=_tar_filter(extract_dir))


def _extract_7z(archive_path: Path) -> None:
    try:
        import py7zr
    except ImportError as exc:
        raise RuntimeError("缺少 py7zr 依赖，无法解压 .7z 文件") from exc

    extract_dir = archive_path.parent
    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        for name in archive.getnames():
            _safe_destination(extract_dir, name)
        archive.extractall(extract_dir)


def _reject_unsafe_zip_member(member: zipfile.ZipInfo, extract_dir: Path) -> None:
    _safe_destination(extract_dir, member.filename)
    mode = member.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise ValueError(f"压缩包包含不安全的符号链接: {member.filename}")


def _tar_filter(extract_dir: Path) -> Callable[[tarfile.TarInfo, str], tarfile.TarInfo | None]:
    def filter_member(member: tarfile.TarInfo, destination: str) -> tarfile.TarInfo | None:
        _safe_destination(Path(destination or extract_dir), member.name)
        if member.issym() or member.islnk():
            return None
        return member

    return filter_member


def _safe_destination(extract_dir: Path, member_name: str) -> Path:
    destination = (extract_dir / member_name).resolve()
    root = extract_dir.resolve()
    if os.path.commonpath([root, destination]) != str(root):
        raise ValueError(f"压缩包包含不安全路径: {member_name}")
    return destination
