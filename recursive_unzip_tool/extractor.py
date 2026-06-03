from __future__ import annotations

import concurrent.futures
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

TAR_EXTENSIONS = {".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"}

ScanProgressCallback = Callable[[int, int, Path | None], None]
ExecutionProgressCallback = Callable[[int, int, Path, str, "ExtractResult | None"], None]
ProgressCallback = ExecutionProgressCallback


@dataclass(slots=True)
class ScanOptions:
    enabled_formats: tuple[str, ...] = (".zip", ".7z", ".tar*")
    max_depth: int | None = None
    show_all_files: bool = False


@dataclass(slots=True)
class ExecutionOptions:
    delete_source: bool = False
    worker_count: int = 4


@dataclass(slots=True)
class ArchiveItem:
    path: Path
    relative_path: Path
    archive_format: str
    size: int
    is_archive: bool
    selected: bool = True
    status: str = "pending"
    error: str = ""
    error_type: str = ""
    suggestion: str = ""


@dataclass(slots=True)
class ScanResult:
    root_dir: Path
    items: list[ArchiveItem]
    scanned_files: int
    scanned_dirs: int


@dataclass(slots=True)
class ExtractResult:
    path: Path
    archive_format: str
    status: str
    error: str = ""
    deleted_source: bool = False
    error_type: str = ""
    suggestion: str = ""


class CancelToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()


def scan_archives(
    root_dir: str | Path,
    options: ScanOptions,
    progress_callback: ScanProgressCallback | None = None,
    cancel_token: CancelToken | None = None,
) -> ScanResult:
    root_path = Path(root_dir).expanduser().resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"目录不存在: {root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"不是目录: {root_path}")

    enabled_extensions = _normalize_enabled_formats(options.enabled_formats)
    items: list[ArchiveItem] = []
    scanned_files = 0
    scanned_dirs = 0

    for current_root, dir_names, file_names in os.walk(root_path):
        current_path = Path(current_root)
        relative_dir = current_path.relative_to(root_path)
        depth = 0 if str(relative_dir) == "." else len(relative_dir.parts)

        if cancel_token and cancel_token.is_cancelled:
            break

        if options.max_depth is not None and depth >= options.max_depth:
            dir_names[:] = []

        scanned_dirs += 1
        scanned_files += len(file_names)

        for file_name in file_names:
            file_path = current_path / file_name
            archive_format = _archive_extension(file_path)
            is_archive = archive_format in enabled_extensions
            if not is_archive and not options.show_all_files:
                continue

            try:
                size = file_path.stat().st_size
            except OSError:
                size = 0

            items.append(
                ArchiveItem(
                    path=file_path,
                    relative_path=file_path.relative_to(root_path),
                    archive_format=archive_format or "file",
                    size=size,
                    is_archive=is_archive,
                    selected=True,
                )
            )

        if progress_callback:
            progress_callback(scanned_dirs, scanned_files, current_path)

    items.sort(key=lambda item: str(item.relative_path).lower())
    return ScanResult(
        root_dir=root_path,
        items=items,
        scanned_files=scanned_files,
        scanned_dirs=scanned_dirs,
    )


def find_archives(root_dir: str | Path, enabled_formats: Iterable[str]) -> list[Path]:
    scan_result = scan_archives(
        root_dir,
        ScanOptions(enabled_formats=tuple(enabled_formats), show_all_files=False),
    )
    return [item.path for item in scan_result.items if item.is_archive]


def extract_archive(path: str | Path, delete_source: bool = False) -> ExtractResult:
    archive_path = Path(path)
    archive_format = _archive_extension(archive_path)
    if archive_format == "":
        return _failed_result(
            archive_path,
            "unknown",
            ValueError("不支持的压缩包格式"),
        )

    try:
        if archive_format == ".zip":
            _extract_zip(archive_path)
        elif archive_format == ".7z":
            _extract_7z(archive_path)
        elif archive_format in TAR_EXTENSIONS:
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
    except Exception as exc:  # noqa: BLE001 - expose archive-specific failures to the UI.
        return _failed_result(archive_path, archive_format, exc)


def extract_selected(
    items: Iterable[ArchiveItem],
    options: ExecutionOptions,
    progress_callback: ExecutionProgressCallback | None = None,
    cancel_token: CancelToken | None = None,
) -> list[ExtractResult]:
    selected_items = [item for item in items if item.selected and item.is_archive]
    total = len(selected_items)
    if total == 0:
        return []

    worker_count = max(1, min(options.worker_count, 16, total))
    completed = 0
    results: list[ExtractResult] = []
    pending_items = list(selected_items)

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures: dict[concurrent.futures.Future[ExtractResult], ArchiveItem] = {}

        while pending_items and len(futures) < worker_count:
            item = pending_items.pop(0)
            if progress_callback:
                progress_callback(completed, total, item.path, "processing", None)
            futures[executor.submit(extract_archive, item.path, options.delete_source)] = item

        while futures:
            done, _ = concurrent.futures.wait(
                futures,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                item = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:  # pragma: no cover - extract_archive should contain failures.
                    result = _failed_result(item.path, item.archive_format, exc)

                completed += 1
                results.append(result)
                if progress_callback:
                    progress_callback(completed, total, item.path, result.status, result)

                if cancel_token and cancel_token.is_cancelled:
                    continue
                if pending_items:
                    next_item = pending_items.pop(0)
                    if progress_callback:
                        progress_callback(completed, total, next_item.path, "processing", None)
                    futures[executor.submit(extract_archive, next_item.path, options.delete_source)] = next_item

            if cancel_token and cancel_token.is_cancelled and pending_items:
                for item in pending_items:
                    completed += 1
                    skipped = ExtractResult(
                        path=item.path,
                        archive_format=item.archive_format,
                        status="skipped",
                        error="用户取消",
                        error_type="cancelled",
                        suggestion="如需继续处理，请重新扫描或执行剩余文件。",
                    )
                    results.append(skipped)
                    if progress_callback:
                        progress_callback(completed, total, item.path, "skipped", skipped)
                pending_items.clear()

    results.sort(key=lambda item: str(item.path).lower())
    return results


def extract_recursive(
    root_dir: str | Path,
    enabled_formats: Iterable[str],
    delete_source: bool = False,
    progress_callback: ProgressCallback | None = None,
    cancel_token: CancelToken | None = None,
) -> list[ExtractResult]:
    scan_result = scan_archives(
        root_dir,
        ScanOptions(enabled_formats=tuple(enabled_formats), show_all_files=False),
        cancel_token=cancel_token,
    )
    return extract_selected(
        scan_result.items,
        ExecutionOptions(delete_source=delete_source, worker_count=1),
        progress_callback=progress_callback,
        cancel_token=cancel_token,
    )


def _normalize_enabled_formats(enabled_formats: Iterable[str]) -> set[str]:
    normalized = {item.lower().strip() for item in enabled_formats}
    if not normalized:
        return set()

    expanded: set[str] = set()
    for item in normalized:
        value = item if item.startswith(".") else f".{item}"
        if value == ".tar*":
            expanded.update(TAR_EXTENSIONS)
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


def _failed_result(path: Path, archive_format: str, exc: Exception) -> ExtractResult:
    error_type, suggestion = classify_error(exc)
    return ExtractResult(
        path=path,
        archive_format=archive_format,
        status="failed",
        error=str(exc),
        error_type=error_type,
        suggestion=suggestion,
    )


def classify_error(exc: Exception) -> tuple[str, str]:
    message = str(exc).lower()
    if isinstance(exc, PermissionError) or "permission" in message or "access is denied" in message:
        return "permission", "请检查文件或目标目录权限，关闭正在占用该文件的程序后重试。"
    if isinstance(exc, FileNotFoundError):
        return "missing", "文件可能已被移动或删除，请重新扫描目录。"
    if isinstance(exc, zipfile.BadZipFile) or "crc" in message or "checksum" in message:
        return "corrupt", "文件可能损坏或 CRC 校验失败，请重新下载/复制压缩包后重试。"
    if isinstance(exc, tarfile.TarError) or "not a 7z file" in message or "invalid" in message:
        return "corrupt", "压缩包格式异常或文件损坏，请用压缩软件单独测试该文件。"
    if "unsupported" in message or "不支持" in message:
        return "unsupported", "当前版本不支持该压缩格式，请转换为 ZIP、7Z 或 TAR 系列后重试。"
    if "unsafe" in message or "不安全" in message:
        return "unsafe_path", "压缩包包含不安全路径，已阻止解压以避免写出目标目录。"
    return "unknown", "请查看原始错误信息；如文件来自网络，建议重新获取后重试。"
