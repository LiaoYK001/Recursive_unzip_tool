from __future__ import annotations

import os
import multiprocessing
import queue
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QProgressBar,
    QSizePolicy,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .extractor import (
    ArchiveItem,
    CancelToken,
    ExecutionOptions,
    ExtractResult,
    ScanOptions,
    ScanResult,
    extract_selected,
    scan_archives,
)


FORMAT_OPTIONS = {
    ".zip": "ZIP",
    ".7z": "7Z",
    ".tar*": "TAR 系列",
}

CHECK_MARK = "√"
TAR_FORMATS = {".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"}

COL_NAME = 0
COL_EXECUTE = 1
COL_RELATIVE_PATH = 2
COL_FORMAT = 3
COL_COMPRESSED_SIZE = 4
COL_UNCOMPRESSED_SIZE = 5
COL_STATUS = 6
COL_DETAILS = 7
COLUMN_COUNT = 8


def _scan_process_entry(root_dir: str, options: ScanOptions, output_queue, cancel_event) -> None:
    try:
        result = scan_archives(
            Path(root_dir),
            options,
            progress_callback=lambda dirs, files, path: output_queue.put(
                ("progress", dirs, files, str(path or ""))
            ),
            cancel_token=_ProcessCancelToken(cancel_event),
        )
    except Exception as exc:  # noqa: BLE001 - process boundary should surface failures.
        output_queue.put(("failed", str(exc)))
        return
    output_queue.put(("finished", result))


def _execution_process_entry(items: list[ArchiveItem], options: ExecutionOptions, output_queue, cancel_event) -> None:
    try:
        results = extract_selected(
            items,
            options,
            progress_callback=lambda index, total, path, status, result: output_queue.put(
                ("progress", index, total, str(path), status, result)
            ),
            cancel_token=_ProcessCancelToken(cancel_event),
        )
    except Exception as exc:  # noqa: BLE001 - process boundary should surface failures.
        output_queue.put(("failed", str(exc)))
        return
    output_queue.put(("finished", results))


@dataclass(slots=True)
class AppSettings:
    enabled_formats: tuple[str, ...] = (".zip", ".7z", ".tar*")
    max_depth_enabled: bool = False
    max_depth: int = 5
    show_all_files: bool = False
    delete_source: bool = False
    worker_count: int = 4

    def scan_options(self) -> ScanOptions:
        return ScanOptions(
            enabled_formats=self.enabled_formats,
            max_depth=self.max_depth if self.max_depth_enabled else None,
            show_all_files=self.show_all_files,
        )

    def execution_options(self) -> ExecutionOptions:
        return ExecutionOptions(
            delete_source=self.delete_source,
            worker_count=self.worker_count,
        )


class _ProcessCancelToken:
    def __init__(self, event) -> None:
        self.event = event

    @property
    def is_cancelled(self) -> bool:
        return self.event.is_set()


class ScanWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)
    interrupted = Signal()

    def __init__(self, root_dir: Path, options: ScanOptions, cancel_token: CancelToken) -> None:
        super().__init__()
        self.root_dir = root_dir
        self.options = options
        self.cancel_token = cancel_token
        self.process: multiprocessing.Process | None = None
        self.cancel_event = None

    @Slot()
    def run(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        output_queue = ctx.Queue()
        self.cancel_event = ctx.Event()
        self.process = ctx.Process(
            target=_scan_process_entry,
            args=(str(self.root_dir), self.options, output_queue, self.cancel_event),
            daemon=True,
        )
        self.process.start()

        while True:
            if self.cancel_token.is_cancelled and self.cancel_event is not None:
                self.cancel_event.set()

            try:
                message = output_queue.get(timeout=0.1)
            except queue.Empty:
                if self.process.exitcode is not None:
                    if self.process.exitcode != 0:
                        self.interrupted.emit()
                    return
                continue

            kind = message[0]
            if kind == "progress":
                _, scanned_dirs, scanned_files, current_path = message
                self.progress.emit(scanned_dirs, scanned_files, current_path)
            elif kind == "finished":
                self.finished.emit(message[1])
                return
            elif kind == "failed":
                self.failed.emit(message[1])
                return

    def force_stop(self) -> None:
        if self.process and self.process.is_alive():
            self.process.terminate()
            self.process.join(2)


class ExecutionWorker(QObject):
    progress = Signal(int, int, str, str, object)
    finished = Signal(object)
    failed = Signal(str)
    interrupted = Signal()

    def __init__(
        self,
        items: list[ArchiveItem],
        options: ExecutionOptions,
        cancel_token: CancelToken,
    ) -> None:
        super().__init__()
        self.items = items
        self.options = options
        self.cancel_token = cancel_token
        self.process: multiprocessing.Process | None = None
        self.cancel_event = None

    @Slot()
    def run(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        output_queue = ctx.Queue()
        self.cancel_event = ctx.Event()
        self.process = ctx.Process(
            target=_execution_process_entry,
            args=(self.items, self.options, output_queue, self.cancel_event),
            daemon=True,
        )
        self.process.start()

        while True:
            if self.cancel_token.is_cancelled and self.cancel_event is not None:
                self.cancel_event.set()

            try:
                message = output_queue.get(timeout=0.1)
            except queue.Empty:
                if self.process.exitcode is not None:
                    if self.process.exitcode != 0:
                        self.interrupted.emit()
                    return
                continue

            kind = message[0]
            if kind == "progress":
                _, index, total, archive_path, status, result = message
                self.progress.emit(index, total, archive_path, status, result)
            elif kind == "finished":
                self.finished.emit(message[1])
                return
            elif kind == "failed":
                self.failed.emit(message[1])
                return

    def force_stop(self) -> None:
        if self.process and self.process.is_alive():
            self.process.terminate()
            self.process.join(2)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        scan_group = QGroupBox("扫描参数")
        scan_layout = QGridLayout(scan_group)
        self.max_depth_check = QCheckBox("限制扫描深度")
        self.max_depth_check.setChecked(settings.max_depth_enabled)
        self.max_depth_spin = QSpinBox()
        self.max_depth_spin.setRange(0, 99)
        self.max_depth_spin.setValue(settings.max_depth)
        self.max_depth_spin.setSuffix(" 层")
        self.show_all_check = QCheckBox("显示所有文件")
        self.show_all_check.setChecked(settings.show_all_files)
        scan_layout.addWidget(self.max_depth_check, 0, 0)
        scan_layout.addWidget(self.max_depth_spin, 0, 1)
        scan_layout.addWidget(self.show_all_check, 1, 0, 1, 2)
        layout.addWidget(scan_group)

        execute_group = QGroupBox("执行参数")
        execute_layout = QGridLayout(execute_group)
        self.delete_check = QCheckBox("解压成功后删除源文件")
        self.delete_check.setObjectName("DangerOption")
        self.delete_check.setChecked(settings.delete_source)
        self.worker_spin = QSpinBox()
        self.worker_spin.setRange(1, 16)
        self.worker_spin.setValue(settings.worker_count)
        self.worker_spin.setSuffix(" 线程")
        execute_layout.addWidget(self.delete_check, 0, 0, 1, 2)
        execute_layout.addWidget(QLabel("执行线程数"), 1, 0)
        execute_layout.addWidget(self.worker_spin, 1, 1)
        layout.addWidget(execute_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_settings(self) -> AppSettings:
        return AppSettings(
            enabled_formats=self.settings.enabled_formats,
            max_depth_enabled=self.max_depth_check.isChecked(),
            max_depth=self.max_depth_spin.value(),
            show_all_files=self.show_all_check.isChecked(),
            delete_source=self.delete_check.isChecked(),
            worker_count=self.worker_spin.value(),
        )


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Recursive Unzip Tool")
        self.resize(1280, 780)
        self.settings = AppSettings()
        self.worker_thread: QThread | None = None
        self.worker: QObject | None = None
        self.cancel_token: CancelToken | None = None
        self.current_mode: str | None = None
        self.scan_result: ScanResult | None = None
        self.items_by_path: dict[str, ArchiveItem] = {}
        self.tree_items_by_path: dict[str, QTreeWidgetItem] = {}
        self.failed_paths: set[str] = set()
        self.active_items: list[ArchiveItem] = []

        self._build_ui()
        self._apply_style()
        self._set_running(None)

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001 - Qt override.
        if self.worker_thread and self.worker_thread.isRunning():
            if self.cancel_token:
                self.cancel_token.cancel()
            self.worker_thread.quit()
            self.worker_thread.wait(1200)
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 16, 18, 16)
        root_layout.setSpacing(12)

        title = QLabel("Recursive Unzip Tool")
        title.setObjectName("Title")
        subtitle = QLabel("先扫描目标目录，再选择需要执行的压缩包")
        subtitle.setObjectName("Subtitle")
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        control_panel = QFrame()
        control_panel.setObjectName("Panel")
        control_layout = QGridLayout(control_panel)
        control_layout.setContentsMargins(16, 14, 16, 14)
        control_layout.setHorizontalSpacing(10)
        control_layout.setVerticalSpacing(12)

        self.path_input = QLineEdit(str(Path.cwd()))
        self.path_input.setPlaceholderText("选择要递归扫描的目录")
        self.browse_button = QPushButton("浏览")
        self.browse_button.clicked.connect(self._choose_directory)
        control_layout.addWidget(QLabel("目标目录"), 0, 0)
        control_layout.addWidget(self.path_input, 0, 1)
        control_layout.addWidget(self.browse_button, 0, 2)

        self.scan_button = QPushButton("扫描")
        self.scan_button.setObjectName("PrimaryButton")
        self.scan_button.clicked.connect(self._start_scan)
        self.execute_button = QPushButton("执行")
        self.execute_button.setObjectName("PrimaryButton")
        self.execute_button.clicked.connect(self._start_execution)
        self.retry_button = QPushButton("重试失败")
        self.retry_button.clicked.connect(self._retry_failed)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self._cancel_current_task)
        self.force_button = QPushButton("强制中断")
        self.force_button.clicked.connect(self._force_interrupt_current_task)
        self.settings_button = QPushButton("设置")
        self.settings_button.clicked.connect(self._open_settings)
        self.show_all_checkbox = QCheckBox("显示所有文件")
        self.show_all_checkbox.setChecked(self.settings.show_all_files)
        self.show_all_checkbox.toggled.connect(self._on_show_all_toggled)
        self.format_checks: dict[str, QCheckBox] = {}
        for value, label in FORMAT_OPTIONS.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(value in self.settings.enabled_formats)
            checkbox.toggled.connect(self._on_format_toggled)
            self.format_checks[value] = checkbox

        button_row = QHBoxLayout()
        button_row.addWidget(self.scan_button)
        button_row.addWidget(self.execute_button)
        button_row.addWidget(self.retry_button)
        button_row.addSpacing(8)
        for checkbox in self.format_checks.values():
            button_row.addWidget(checkbox)
        button_row.addWidget(self.show_all_checkbox)
        button_row.addStretch(1)
        button_row.addWidget(self.settings_button)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.force_button)
        control_layout.addLayout(button_row, 1, 0, 1, 3)
        root_layout.addWidget(control_panel)

        self.summary_label = QLabel("就绪")
        self.summary_label.setObjectName("Summary")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.summary_label)
        root_layout.addLayout(progress_row)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "执行", "相对路径", "格式", "压缩包大小", "解压后大小", "状态", "错误/建议"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setIndentation(32)
        self.tree.header().setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(COL_EXECUTE, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(COL_EXECUTE, 56)
        self.tree.header().setSectionResizeMode(COL_RELATIVE_PATH, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(COL_FORMAT, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(COL_COMPRESSED_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(COL_UNCOMPRESSED_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(COL_DETAILS, QHeaderView.ResizeMode.Stretch)
        self.tree.itemClicked.connect(self._handle_tree_item_clicked)
        self.tree.customContextMenuRequested.connect(self._open_tree_context_menu)
        self.tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root_layout.addWidget(self.tree, 1)

        log_label = QLabel("日志")
        log_label.setObjectName("SectionLabel")
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(3000)
        self.log_output.setMinimumHeight(130)
        root_layout.addWidget(log_label)
        root_layout.addWidget(self.log_output)

        self.setCentralWidget(root)

    def _apply_style(self) -> None:
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#f5f7fb"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#172033"))
        self.setPalette(palette)

        self.setStyleSheet(
            """
            QWidget {
                color: #172033;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QMainWindow, QWidget {
                background: #f5f7fb;
            }
            QLabel#Title {
                font-size: 26px;
                font-weight: 700;
            }
            QLabel#Subtitle, QLabel#Summary {
                color: #667085;
            }
            QLabel#SectionLabel {
                font-weight: 600;
            }
            QFrame#Panel {
                background: #ffffff;
                border: 1px solid #d9e1ec;
                border-radius: 8px;
            }
            QLineEdit, QPlainTextEdit, QTreeWidget {
                background: #ffffff;
                border: 1px solid #cfd8e5;
                border-radius: 6px;
                selection-background-color: #d7efef;
                selection-color: #172033;
            }
            QLineEdit {
                padding: 8px 10px;
            }
            QPlainTextEdit {
                padding: 8px;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #c6d1df;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #eef6f6;
                border-color: #0f8b8d;
            }
            QPushButton:disabled {
                color: #9aa4b2;
                background: #eef1f5;
                border-color: #d8dee8;
            }
            QPushButton#PrimaryButton {
                color: #ffffff;
                background: #0f8b8d;
                border-color: #0f8b8d;
            }
            QPushButton#PrimaryButton:hover {
                background: #0b7476;
            }
            QCheckBox {
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 2px solid #8da1b7;
                border-radius: 4px;
                background: #ffffff;
            }
            QCheckBox::indicator:checked {
                background: #0f8b8d;
                border-color: #0f8b8d;
            }
            QCheckBox#DangerOption {
                color: #9b2c2c;
                font-weight: 600;
            }
            QProgressBar {
                height: 18px;
                background: #e6ebf2;
                border: 1px solid #cfd8e5;
                border-radius: 6px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #0f8b8d;
                border-radius: 5px;
            }
            QHeaderView::section {
                background: #eef2f7;
                border: 0;
                border-bottom: 1px solid #d9e1ec;
                padding: 8px;
                font-weight: 600;
            }
            QTreeWidget {
                gridline-color: #e4e9f0;
                alternate-background-color: #fafbfd;
            }
            QTreeWidget::branch {
                border-left: 1px solid #cfd8e5;
            }
            """
        )

    @Slot()
    def _choose_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "选择目标目录",
            self.path_input.text().strip() or str(Path.cwd()),
        )
        if directory:
            self.path_input.setText(directory)

    @Slot()
    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = dialog.selected_settings()
            if not selected.enabled_formats:
                QMessageBox.warning(self, "缺少格式", "请至少选择一种压缩格式。")
                return
            self.settings = selected
            self.show_all_checkbox.setChecked(self.settings.show_all_files)
            self._append_log(
                "设置已更新："
                f"线程={self.settings.worker_count}，"
                f"显示所有文件={'是' if self.settings.show_all_files else '否'}，"
                f"删除源文件={'是' if self.settings.delete_source else '否'}"
            )

    @Slot(bool)
    def _on_format_toggled(self, _checked: bool) -> None:
        self.settings.enabled_formats = self.enabled_formats_from_main_controls()
        formats = ", ".join(self.settings.enabled_formats) if self.settings.enabled_formats else "无"
        self._append_log(f"格式过滤已更新：{formats}（重新扫描后生效）")

    @Slot(bool)
    def _on_show_all_toggled(self, checked: bool) -> None:
        self.settings.show_all_files = checked
        self._append_log(f"{'显示' if checked else '隐藏'}所有文件（重新扫描后生效）")

    @Slot()
    def _start_scan(self) -> None:
        root_dir = Path(self.path_input.text().strip()).expanduser()
        self.settings.enabled_formats = self.enabled_formats_from_main_controls()
        if not self.settings.enabled_formats:
            QMessageBox.warning(self, "缺少格式", "请至少选择一种压缩格式。")
            return
        if not root_dir.exists() or not root_dir.is_dir():
            QMessageBox.warning(self, "目录无效", "请选择一个存在的目标目录。")
            return

        self.scan_result = None
        self.active_items = []
        self.items_by_path.clear()
        self.tree_items_by_path.clear()
        self.failed_paths.clear()
        self.tree.clear()
        self.progress_bar.setValue(0)
        self.summary_label.setText("正在扫描...")
        self._append_log(f"开始扫描：{root_dir}")

        self.cancel_token = CancelToken()
        self.worker_thread = QThread(self)
        self.worker = ScanWorker(root_dir, self.settings.scan_options(), self.cancel_token)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._handle_scan_progress)
        self.worker.finished.connect(self._handle_scan_finished)
        self.worker.failed.connect(self._handle_worker_failed)
        self.worker.interrupted.connect(self._handle_worker_interrupted)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(self._clear_worker_refs)
        self._set_running("scan")
        self.worker_thread.start()

    @Slot()
    def _start_execution(self) -> None:
        selected_items = self._selected_archive_items()
        if not selected_items:
            QMessageBox.information(self, "没有选中文件", "请先选择至少一个压缩包。")
            return
        self._run_execution(selected_items, "开始执行")

    @Slot()
    def _retry_failed(self) -> None:
        retry_items = [
            item for path, item in self.items_by_path.items() if path in self.failed_paths and item.is_archive
        ]
        if not retry_items:
            QMessageBox.information(self, "没有失败项", "当前没有可重试的失败文件。")
            return
        for item in retry_items:
            item.selected = True
            self._update_tree_item(item)
        self._run_execution(retry_items, "重试失败")

    def _run_execution(self, items: list[ArchiveItem], action_name: str) -> None:
        self.progress_bar.setValue(0)
        self._append_log(
            f"{action_name}：{len(items)} 个文件，"
            f"线程={self.settings.worker_count}，"
            f"删除源文件={'是' if self.settings.delete_source else '否'}"
        )
        self.active_items = list(items)

        for item in items:
            item.status = "pending"
            item.error = ""
            item.error_type = ""
            item.suggestion = ""
            self._update_tree_item(item)

        self.cancel_token = CancelToken()
        self.worker_thread = QThread(self)
        self.worker = ExecutionWorker(items, self.settings.execution_options(), self.cancel_token)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._handle_execution_progress)
        self.worker.finished.connect(self._handle_execution_finished)
        self.worker.failed.connect(self._handle_worker_failed)
        self.worker.interrupted.connect(self._handle_worker_interrupted)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(self._clear_worker_refs)
        self._set_running("execute")
        self.worker_thread.start()

    @Slot()
    def _cancel_current_task(self) -> None:
        if self.cancel_token:
            self.cancel_token.cancel()
            self.cancel_button.setEnabled(False)
            self.summary_label.setText("正在取消...")
            self._append_log("已请求取消，正在等待当前任务收尾。")

    @Slot()
    def _force_interrupt_current_task(self) -> None:
        if not self.worker:
            return
        self.summary_label.setText("正在强制中断...")
        self.force_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self._append_log("已请求强制中断。当前正在写出的解压内容可能不完整，请检查目标目录或重试该文件。")
        force_stop = getattr(self.worker, "force_stop", None)
        if callable(force_stop):
            force_stop()

    @Slot(int, int, str)
    def _handle_scan_progress(self, scanned_dirs: int, scanned_files: int, current_path: str) -> None:
        self.progress_bar.setRange(0, 0)
        self.summary_label.setText(f"扫描中：目录 {scanned_dirs}，文件 {scanned_files}")
        if scanned_dirs % 25 == 0 and current_path:
            self._append_log(f"扫描到：{current_path}")

    @Slot(object)
    def _handle_scan_finished(self, scan_result: ScanResult) -> None:
        self.scan_result = scan_result
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)
        self._populate_tree(scan_result)
        archive_count = sum(1 for item in scan_result.items if item.is_archive)
        self.summary_label.setText(
            f"扫描完成：目录 {scan_result.scanned_dirs}，文件 {scan_result.scanned_files}，压缩包 {archive_count}"
        )
        self._append_log(
            f"扫描完成：发现 {len(scan_result.items)} 个显示项，其中 {archive_count} 个可执行压缩包。"
        )
        self.active_items = []
        self._set_running(None)

    @Slot(int, int, str, str, object)
    def _handle_execution_progress(
        self,
        index: int,
        total: int,
        archive_path: str,
        status: str,
        result: ExtractResult | None,
    ) -> None:
        if total > 0:
            self.progress_bar.setValue(int(index / total * 100))
        item = self.items_by_path.get(archive_path)
        if item:
            item.status = status
            if result:
                item.status = result.status
                item.error = result.error
                item.error_type = result.error_type
                item.suggestion = result.suggestion
                if result.status == "failed":
                    self.failed_paths.add(archive_path)
                elif result.status == "success":
                    self.failed_paths.discard(archive_path)
            self._update_tree_item(item)
        self.summary_label.setText(f"执行中：{index}/{total} {_display_status(status)}")

        if result:
            message = f"[{_display_status(result.status)}] {archive_path}"
            if result.error:
                message += f" - {result.error}"
            if result.suggestion:
                message += f"；建议：{result.suggestion}"
            self._append_log(message)

    @Slot(object)
    def _handle_execution_finished(self, results: list[ExtractResult]) -> None:
        self._set_running(None)
        success_count = sum(1 for item in results if item.status == "success")
        failed_count = sum(1 for item in results if item.status == "failed")
        skipped_count = sum(1 for item in results if item.status == "skipped")
        interrupted_count = sum(1 for item in results if item.status == "interrupted")
        deleted_count = sum(1 for item in results if item.deleted_source)
        self.progress_bar.setValue(100 if results else 0)
        summary = (
            f"执行完成：成功 {success_count}，失败 {failed_count}，"
            f"跳过 {skipped_count}，中断 {interrupted_count}，删除 {deleted_count}"
        )
        self.summary_label.setText(summary)
        self._append_log(summary)
        self.active_items = []

    @Slot(str)
    def _handle_worker_failed(self, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        self._set_running(None)
        self.summary_label.setText("任务失败")
        self._append_log(f"[错误] {message}")
        QMessageBox.critical(self, "任务失败", message)

    @Slot()
    def _handle_worker_interrupted(self) -> None:
        self.mark_unfinished_items_interrupted()
        self.progress_bar.setRange(0, 100)
        self._set_running(None)
        self.summary_label.setText("已强制中断")
        self._append_log("任务已强制中断。未完成项目已标记为“已中断”，可用“重试失败”继续处理。")

    @Slot()
    def _clear_worker_refs(self) -> None:
        self.worker = None
        self.worker_thread = None
        self.cancel_token = None
        self.current_mode = None

    @Slot(QTreeWidgetItem, int)
    def _handle_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if column != COL_EXECUTE or self.current_mode:
            return
        path = item.data(COL_NAME, Qt.ItemDataRole.UserRole)
        if not path:
            return
        archive_item = self.items_by_path.get(path)
        if not archive_item or not archive_item.is_archive:
            return
        archive_item.selected = not archive_item.selected
        self._update_tree_item(archive_item)
        self._refresh_action_state()

    @Slot(QPoint)
    def _open_tree_context_menu(self, position: QPoint) -> None:
        tree_item = self.tree.itemAt(position)
        if not tree_item:
            return

        path = self._path_from_tree_item(tree_item)
        if not path:
            return

        menu = QMenu(self)
        open_action = menu.addAction("打开")
        location_action = menu.addAction("打开文件位置")
        menu.addSeparator()
        properties_action = menu.addAction("属性")

        selected_action = menu.exec(self.tree.viewport().mapToGlobal(position))
        if selected_action == open_action:
            self._open_path(path)
        elif selected_action == location_action:
            self._open_path_location(path)
        elif selected_action == properties_action:
            self._show_properties(tree_item, path)

    def _set_running(self, mode: str | None) -> None:
        self.current_mode = mode
        running = mode is not None
        self.scan_button.setEnabled(not running)
        self.execute_button.setEnabled(not running and bool(self._selected_archive_items()))
        self.retry_button.setEnabled(not running and bool(self.failed_paths))
        self.cancel_button.setEnabled(running)
        self.force_button.setEnabled(running)
        self.settings_button.setEnabled(not running)
        self.show_all_checkbox.setEnabled(not running)
        for checkbox in self.format_checks.values():
            checkbox.setEnabled(not running)
        self.browse_button.setEnabled(not running)
        self.path_input.setEnabled(not running)

    def _refresh_action_state(self) -> None:
        if self.current_mode:
            return
        self.execute_button.setEnabled(bool(self._selected_archive_items()))
        self.retry_button.setEnabled(bool(self.failed_paths))

    def enabled_formats_from_main_controls(self) -> tuple[str, ...]:
        return tuple(value for value, checkbox in self.format_checks.items() if checkbox.isChecked())

    def mark_unfinished_items_interrupted(self) -> None:
        for item in self.active_items:
            if item.is_archive and item.status not in {"success", "failed", "skipped"}:
                item.status = "interrupted"
                item.error = "任务被强制中断"
                item.error_type = "interrupted"
                item.suggestion = "请检查目标目录是否存在不完整解压内容，然后重试该文件。"
                self.failed_paths.add(str(item.path))
                self._update_tree_item(item)
        self._refresh_action_state()

    def _populate_tree(self, scan_result: ScanResult) -> None:
        self.tree.clear()
        self.items_by_path.clear()
        self.tree_items_by_path.clear()
        directory_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}

        for item in scan_result.items:
            path_key = str(item.path)
            self.items_by_path[path_key] = item
            parent = self.tree.invisibleRootItem()
            parts = item.relative_path.parts

            for depth in range(len(parts) - 1):
                dir_key = parts[: depth + 1]
                if dir_key not in directory_nodes:
                    directory_path = scan_result.root_dir / Path(*dir_key)
                    node = QTreeWidgetItem(
                        [
                            parts[depth],
                            "",
                            str(Path(*dir_key)),
                            "",
                            "",
                            "",
                            "",
                            "",
                        ]
                    )
                    node.setData(COL_NAME, Qt.ItemDataRole.UserRole, str(directory_path))
                    node.setExpanded(True)
                    for col in range(COLUMN_COUNT):
                        node.setForeground(col, QColor("#344054"))
                    node.setFont(COL_NAME, _bold_font())
                    directory_nodes[dir_key] = node
                    parent.addChild(node)
                parent = directory_nodes[dir_key]

            leaf = QTreeWidgetItem()
            leaf.setText(COL_NAME, item.path.name)
            leaf.setText(COL_EXECUTE, CHECK_MARK if item.is_archive and item.selected else "")
            leaf.setText(COL_RELATIVE_PATH, str(item.relative_path))
            leaf.setText(COL_FORMAT, item.archive_format)
            leaf.setText(COL_COMPRESSED_SIZE, _format_size(item.compressed_size))
            leaf.setText(COL_UNCOMPRESSED_SIZE, _format_optional_size(item.uncompressed_size) if item.is_archive else "-")
            leaf.setText(COL_STATUS, _display_status(item.status) if item.is_archive else "不可执行")
            leaf.setText(COL_DETAILS, "")
            leaf.setData(COL_NAME, Qt.ItemDataRole.UserRole, path_key)
            leaf.setTextAlignment(COL_EXECUTE, Qt.AlignmentFlag.AlignCenter)
            leaf.setToolTip(COL_EXECUTE, "点击切换是否执行" if item.is_archive else "普通文件不可执行")
            leaf.setFont(COL_EXECUTE, _check_font())
            leaf.setForeground(COL_EXECUTE, QColor("#063b3c"))

            if item.is_archive:
                color = archive_color(item.archive_format)
                for col in range(COLUMN_COUNT):
                    if col == COL_EXECUTE:
                        continue
                    leaf.setForeground(col, color)
                    leaf.setFont(col, _bold_font())
                leaf.setBackground(COL_EXECUTE, QColor("#e3f5f5"))
                leaf.setFont(COL_FORMAT, _bold_font())
            else:
                for col in range(COLUMN_COUNT):
                    leaf.setForeground(col, QColor("#98a2b3"))
            parent.addChild(leaf)
            self.tree_items_by_path[path_key] = leaf

        self.tree.expandAll()
        self._refresh_action_state()

    def _selected_archive_items(self) -> list[ArchiveItem]:
        return [item for item in self.items_by_path.values() if item.is_archive and item.selected]

    def _update_tree_item(self, item: ArchiveItem) -> None:
        tree_item = self.tree_items_by_path.get(str(item.path))
        if not tree_item:
            return
        tree_item.setText(COL_EXECUTE, CHECK_MARK if item.is_archive and item.selected else "")
        tree_item.setBackground(
            COL_EXECUTE,
            QColor("#e3f5f5") if item.selected and item.is_archive else QColor("#ffffff"),
        )
        tree_item.setForeground(
            COL_EXECUTE,
            QColor("#063b3c") if item.selected and item.is_archive else QColor("#98a2b3"),
        )
        tree_item.setText(COL_STATUS, _display_status(item.status) if item.is_archive else "不可执行")
        details = item.error
        if item.suggestion:
            details = f"{details}；建议：{item.suggestion}" if details else item.suggestion
        tree_item.setText(COL_DETAILS, details)

    def _path_from_tree_item(self, tree_item: QTreeWidgetItem) -> Path | None:
        raw_path = tree_item.data(COL_NAME, Qt.ItemDataRole.UserRole)
        if not raw_path:
            return None
        return Path(str(raw_path))

    def _open_path(self, path: Path) -> None:
        if not path.exists():
            QMessageBox.warning(self, "路径不存在", f"路径不存在：\n{path}")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_path_location(self, path: Path) -> None:
        if not path.exists():
            QMessageBox.warning(self, "路径不存在", f"路径不存在：\n{path}")
            return
        if os.name == "nt" and path.is_file():
            subprocess.Popen(["explorer", f"/select,{path}"])
            return
        directory = path if path.is_dir() else path.parent
        self._open_path(directory)

    def _show_properties(self, tree_item: QTreeWidgetItem, path: Path) -> None:
        archive_item = self.items_by_path.get(str(path))
        dialog = QDialog(self)
        dialog.setWindowTitle("属性")
        dialog.resize(680, 460)
        layout = QVBoxLayout(dialog)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText(self._properties_text(tree_item, path, archive_item))
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _properties_text(
        self,
        tree_item: QTreeWidgetItem,
        path: Path,
        archive_item: ArchiveItem | None,
    ) -> str:
        if archive_item:
            modified = _format_datetime(archive_item.modified_time)
            selected = "是" if archive_item.selected and archive_item.is_archive else "否"
            uncompressed_size = (
                _format_optional_size(archive_item.uncompressed_size)
                if archive_item.is_archive
                else "-"
            )
            return "\n".join(
                [
                    f"名称: {archive_item.path.name}",
                    f"绝对路径: {archive_item.path}",
                    f"相对路径: {archive_item.relative_path}",
                    f"格式: {archive_item.archive_format}",
                    f"压缩包大小: {_format_size(archive_item.compressed_size)}",
                    f"解压后大小: {uncompressed_size}",
                    f"修改时间: {modified}",
                    f"是否选中: {selected}",
                    f"是否可执行: {'是' if archive_item.is_archive else '否'}",
                    f"状态: {_display_status(archive_item.status)}",
                    f"错误原因: {archive_item.error or '-'}",
                    f"建议: {archive_item.suggestion or '-'}",
                ]
            )

        return "\n".join(
            [
                f"名称: {tree_item.text(COL_NAME)}",
                f"路径: {path}",
                "类型: 目录",
            ]
        )

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{timestamp}] {message}")


def _bold_font() -> QFont:
    font = QFont()
    font.setBold(True)
    return font


def _check_font() -> QFont:
    font = QFont()
    font.setBold(True)
    font.setPointSize(16)
    return font


def _display_status(status: str) -> str:
    return {
        "pending": "等待",
        "processing": "处理中",
        "success": "成功",
        "failed": "失败",
        "skipped": "已跳过",
        "interrupted": "已中断",
    }.get(status, status)


def archive_color(archive_format: str) -> QColor:
    normalized = archive_format.lower()
    if normalized == ".zip":
        return QColor("#0f8b8d")
    if normalized == ".7z":
        return QColor("#7c3aed")
    if normalized in TAR_FORMATS or normalized == ".tar*":
        return QColor("#2563eb")
    return QColor("#98a2b3")


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _format_optional_size(size: int | None) -> str:
    return _format_size(size) if size is not None else "未知"


def _format_datetime(timestamp: float | None) -> str:
    if timestamp is None:
        return "未知"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    app = QApplication([])
    app.setApplicationName("Recursive Unzip Tool")
    window = MainWindow()
    window.show()
    app.exec()
