from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QPalette
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


class ScanWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, root_dir: Path, options: ScanOptions, cancel_token: CancelToken) -> None:
        super().__init__()
        self.root_dir = root_dir
        self.options = options
        self.cancel_token = cancel_token

    @Slot()
    def run(self) -> None:
        try:
            result = scan_archives(
                self.root_dir,
                self.options,
                progress_callback=self._emit_progress,
                cancel_token=self.cancel_token,
            )
        except Exception as exc:  # noqa: BLE001 - show setup errors in the UI.
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)

    def _emit_progress(self, scanned_dirs: int, scanned_files: int, current_path: Path | None) -> None:
        self.progress.emit(scanned_dirs, scanned_files, str(current_path or ""))


class ExecutionWorker(QObject):
    progress = Signal(int, int, str, str, object)
    finished = Signal(object)
    failed = Signal(str)

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

    @Slot()
    def run(self) -> None:
        try:
            results = extract_selected(
                self.items,
                self.options,
                progress_callback=self._emit_progress,
                cancel_token=self.cancel_token,
            )
        except Exception as exc:  # noqa: BLE001 - keep worker boundary robust.
            self.failed.emit(str(exc))
            return
        self.finished.emit(results)

    def _emit_progress(
        self,
        index: int,
        total: int,
        archive_path: Path,
        status: str,
        result: ExtractResult | None,
    ) -> None:
        self.progress.emit(index, total, str(archive_path), status, result)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        format_group = QGroupBox("文件类型过滤")
        format_layout = QHBoxLayout(format_group)
        self.format_checks: dict[str, QCheckBox] = {}
        for value, label in FORMAT_OPTIONS.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(value in settings.enabled_formats)
            self.format_checks[value] = checkbox
            format_layout.addWidget(checkbox)
        format_layout.addStretch(1)
        layout.addWidget(format_group)

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
        enabled_formats = tuple(
            value for value, checkbox in self.format_checks.items() if checkbox.isChecked()
        )
        return AppSettings(
            enabled_formats=enabled_formats,
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
        self.settings_button = QPushButton("设置")
        self.settings_button.clicked.connect(self._open_settings)

        button_row = QHBoxLayout()
        button_row.addWidget(self.scan_button)
        button_row.addWidget(self.execute_button)
        button_row.addWidget(self.retry_button)
        button_row.addStretch(1)
        button_row.addWidget(self.settings_button)
        button_row.addWidget(self.cancel_button)
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
        self.tree.setHeaderLabels(["名称", "相对路径", "格式", "大小", "状态", "错误/建议"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.tree.itemChanged.connect(self._handle_tree_item_changed)
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
                selection-background-color: #0f8b8d;
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
            self._append_log(
                "设置已更新："
                f"格式={', '.join(self.settings.enabled_formats)}，"
                f"线程={self.settings.worker_count}，"
                f"显示所有文件={'是' if self.settings.show_all_files else '否'}，"
                f"删除源文件={'是' if self.settings.delete_source else '否'}"
            )

    @Slot()
    def _start_scan(self) -> None:
        root_dir = Path(self.path_input.text().strip()).expanduser()
        if not root_dir.exists() or not root_dir.is_dir():
            QMessageBox.warning(self, "目录无效", "请选择一个存在的目标目录。")
            return

        self.scan_result = None
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
            QMessageBox.information(self, "没有选中文件", "请先勾选至少一个压缩包。")
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
        self._run_execution(retry_items, "重试失败")

    def _run_execution(self, items: list[ArchiveItem], action_name: str) -> None:
        self.progress_bar.setValue(0)
        self._append_log(
            f"{action_name}：{len(items)} 个文件，"
            f"线程={self.settings.worker_count}，"
            f"删除源文件={'是' if self.settings.delete_source else '否'}"
        )

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
        deleted_count = sum(1 for item in results if item.deleted_source)
        self.progress_bar.setValue(100 if results else 0)
        summary = (
            f"执行完成：成功 {success_count}，失败 {failed_count}，"
            f"跳过 {skipped_count}，删除 {deleted_count}"
        )
        self.summary_label.setText(summary)
        self._append_log(summary)

    @Slot(str)
    def _handle_worker_failed(self, message: str) -> None:
        self.progress_bar.setRange(0, 100)
        self._set_running(None)
        self.summary_label.setText("任务失败")
        self._append_log(f"[错误] {message}")
        QMessageBox.critical(self, "任务失败", message)

    @Slot()
    def _clear_worker_refs(self) -> None:
        self.worker = None
        self.worker_thread = None
        self.cancel_token = None
        self.current_mode = None

    @Slot(QTreeWidgetItem, int)
    def _handle_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return
        archive_item = self.items_by_path.get(path)
        if archive_item:
            archive_item.selected = item.checkState(0) == Qt.CheckState.Checked
        self._refresh_action_state()

    def _set_running(self, mode: str | None) -> None:
        self.current_mode = mode
        running = mode is not None
        self.scan_button.setEnabled(not running)
        self.execute_button.setEnabled(not running and bool(self._selected_archive_items()))
        self.retry_button.setEnabled(not running and bool(self.failed_paths))
        self.cancel_button.setEnabled(running)
        self.settings_button.setEnabled(not running)
        self.browse_button.setEnabled(not running)
        self.path_input.setEnabled(not running)

    def _refresh_action_state(self) -> None:
        if self.current_mode:
            return
        self.execute_button.setEnabled(bool(self._selected_archive_items()))
        self.retry_button.setEnabled(bool(self.failed_paths))

    def _populate_tree(self, scan_result: ScanResult) -> None:
        self.tree.blockSignals(True)
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
                    node = QTreeWidgetItem([parts[depth], str(Path(*dir_key)), "", "", "", ""])
                    node.setFirstColumnSpanned(False)
                    node.setExpanded(True)
                    directory_nodes[dir_key] = node
                    parent.addChild(node)
                parent = directory_nodes[dir_key]

            leaf = QTreeWidgetItem()
            leaf.setText(0, item.path.name)
            leaf.setText(1, str(item.relative_path))
            leaf.setText(2, item.archive_format)
            leaf.setText(3, _format_size(item.size))
            leaf.setText(4, _display_status(item.status))
            leaf.setText(5, "")
            leaf.setData(0, Qt.ItemDataRole.UserRole, path_key)
            if item.is_archive:
                leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                leaf.setCheckState(0, Qt.CheckState.Checked if item.selected else Qt.CheckState.Unchecked)
            else:
                leaf.setFlags(leaf.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                leaf.setText(4, "不可执行")
            parent.addChild(leaf)
            self.tree_items_by_path[path_key] = leaf

        self.tree.expandAll()
        self.tree.blockSignals(False)
        self._refresh_action_state()

    def _selected_archive_items(self) -> list[ArchiveItem]:
        return [item for item in self.items_by_path.values() if item.is_archive and item.selected]

    def _update_tree_item(self, item: ArchiveItem) -> None:
        tree_item = self.tree_items_by_path.get(str(item.path))
        if not tree_item:
            return
        tree_item.setText(4, _display_status(item.status))
        details = item.error
        if item.suggestion:
            details = f"{details}；建议：{item.suggestion}" if details else item.suggestion
        tree_item.setText(5, details)
        if item.is_archive:
            tree_item.setCheckState(0, Qt.CheckState.Checked if item.selected else Qt.CheckState.Unchecked)

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"[{timestamp}] {message}")


def _display_status(status: str) -> str:
    return {
        "pending": "等待",
        "processing": "处理中",
        "success": "成功",
        "failed": "失败",
        "skipped": "已跳过",
    }.get(status, status)


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def main() -> None:
    app = QApplication([])
    app.setApplicationName("Recursive Unzip Tool")
    window = MainWindow()
    window.show()
    app.exec()
