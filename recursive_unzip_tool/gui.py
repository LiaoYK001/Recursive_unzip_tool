from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .extractor import CancelToken, ExtractResult, extract_recursive


FORMAT_OPTIONS = {
    ".zip": "ZIP",
    ".7z": "7Z",
    ".tar*": "TAR 系列",
}


class ExtractionWorker(QObject):
    progress = Signal(int, int, str, str, object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        root_dir: Path,
        enabled_formats: list[str],
        delete_source: bool,
        cancel_token: CancelToken,
    ) -> None:
        super().__init__()
        self.root_dir = root_dir
        self.enabled_formats = enabled_formats
        self.delete_source = delete_source
        self.cancel_token = cancel_token

    @Slot()
    def run(self) -> None:
        try:
            results = extract_recursive(
                self.root_dir,
                self.enabled_formats,
                delete_source=self.delete_source,
                progress_callback=self._emit_progress,
                cancel_token=self.cancel_token,
            )
        except Exception as exc:  # noqa: BLE001 - send filesystem setup errors to UI.
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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Recursive Unzip Tool")
        self.resize(1040, 700)
        self.worker_thread: QThread | None = None
        self.worker: ExtractionWorker | None = None
        self.cancel_token: CancelToken | None = None
        self.rows_by_path: dict[str, int] = {}

        self._build_ui()
        self._apply_style()
        self._set_running(False)

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001 - Qt override.
        if self.worker_thread and self.worker_thread.isRunning():
            self.cancel_token.cancel() if self.cancel_token else None
            self.worker_thread.quit()
            self.worker_thread.wait(1200)
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(24, 22, 24, 22)
        root_layout.setSpacing(16)

        title = QLabel("Recursive Unzip Tool")
        title.setObjectName("Title")
        subtitle = QLabel("递归解压目标目录下的压缩包")
        subtitle.setObjectName("Subtitle")

        title_area = QVBoxLayout()
        title_area.setSpacing(2)
        title_area.addWidget(title)
        title_area.addWidget(subtitle)
        root_layout.addLayout(title_area)

        control_panel = QFrame()
        control_panel.setObjectName("Panel")
        control_layout = QGridLayout(control_panel)
        control_layout.setContentsMargins(18, 18, 18, 18)
        control_layout.setHorizontalSpacing(12)
        control_layout.setVerticalSpacing(14)

        path_label = QLabel("目标目录")
        self.path_input = QLineEdit(str(Path.cwd()))
        self.path_input.setPlaceholderText("选择要递归解压的目录")
        self.browse_button = QPushButton("选择")
        self.browse_button.clicked.connect(self._choose_directory)

        control_layout.addWidget(path_label, 0, 0)
        control_layout.addWidget(self.path_input, 0, 1)
        control_layout.addWidget(self.browse_button, 0, 2)

        format_group = QGroupBox("压缩格式")
        format_layout = QHBoxLayout(format_group)
        format_layout.setContentsMargins(12, 8, 12, 8)
        self.format_checks: dict[str, QCheckBox] = {}
        for value, label in FORMAT_OPTIONS.items():
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)
            self.format_checks[value] = checkbox
            format_layout.addWidget(checkbox)
        format_layout.addStretch(1)

        self.delete_checkbox = QCheckBox("解压成功后删除源文件")
        self.delete_checkbox.setObjectName("DangerOption")

        option_row = QHBoxLayout()
        option_row.addWidget(format_group, 1)
        option_row.addSpacing(12)
        option_row.addWidget(self.delete_checkbox)

        control_layout.addLayout(option_row, 1, 0, 1, 3)

        self.start_button = QPushButton("开始解压")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self._start_extraction)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self._cancel_extraction)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.start_button)
        control_layout.addLayout(button_row, 2, 0, 1, 3)

        root_layout.addWidget(control_panel)

        status_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.summary_label = QLabel("就绪")
        self.summary_label.setObjectName("Summary")
        status_row.addWidget(self.progress_bar, 1)
        status_row.addWidget(self.summary_label)
        root_layout.addLayout(status_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["文件", "格式", "状态", "源文件"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root_layout.addWidget(self.table, 1)

        log_label = QLabel("日志")
        log_label.setObjectName("SectionLabel")
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(1000)
        self.log_output.setMinimumHeight(120)
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
                font-size: 28px;
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
            QLineEdit, QPlainTextEdit, QTableWidget {
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
            QGroupBox {
                border: 1px solid #d9e1ec;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 8px;
                font-weight: 600;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
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
            QCheckBox::indicator:hover {
                border-color: #0f8b8d;
            }
            QCheckBox::indicator:checked {
                background: #0f8b8d;
                border-color: #0f8b8d;
            }
            QCheckBox::indicator:checked:hover {
                background: #0b7476;
                border-color: #0b7476;
            }
            QCheckBox::indicator:disabled {
                background: #e8edf3;
                border-color: #c9d3df;
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
            QTableWidget {
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
    def _start_extraction(self) -> None:
        root_dir = Path(self.path_input.text().strip()).expanduser()
        enabled_formats = [
            value for value, checkbox in self.format_checks.items() if checkbox.isChecked()
        ]

        if not enabled_formats:
            QMessageBox.warning(self, "缺少格式", "请至少选择一种压缩格式。")
            return
        if not root_dir.exists() or not root_dir.is_dir():
            QMessageBox.warning(self, "目录无效", "请选择一个存在的目标目录。")
            return

        self.table.setRowCount(0)
        self.rows_by_path.clear()
        self.log_output.clear()
        self.progress_bar.setValue(0)
        self.summary_label.setText("正在扫描...")
        self._append_log(f"目标目录: {root_dir}")
        self._append_log(f"删除源文件: {'开启' if self.delete_checkbox.isChecked() else '关闭'}")

        self.cancel_token = CancelToken()
        self.worker_thread = QThread(self)
        self.worker = ExtractionWorker(
            root_dir=root_dir,
            enabled_formats=enabled_formats,
            delete_source=self.delete_checkbox.isChecked(),
            cancel_token=self.cancel_token,
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._handle_progress)
        self.worker.finished.connect(self._handle_finished)
        self.worker.failed.connect(self._handle_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(self._clear_worker_refs)
        self._set_running(True)
        self.worker_thread.start()

    @Slot()
    def _cancel_extraction(self) -> None:
        if self.cancel_token:
            self.cancel_token.cancel()
            self.summary_label.setText("正在取消...")
            self.cancel_button.setEnabled(False)
            self._append_log("已请求取消，当前文件处理结束后停止。")

    @Slot(int, int, str, str, object)
    def _handle_progress(
        self,
        index: int,
        total: int,
        archive_path: str,
        status: str,
        result: ExtractResult | None,
    ) -> None:
        if total > 0:
            self.progress_bar.setValue(int(index / total * 100))

        row = self._ensure_row(archive_path, result)
        display_status = _display_status(status)
        self.table.setItem(row, 2, QTableWidgetItem(display_status))
        self.summary_label.setText(f"{index}/{total} {display_status}")

        if result:
            source_state = "已删除" if result.deleted_source else "保留"
            self.table.setItem(row, 3, QTableWidgetItem(source_state))
            message = f"[{display_status}] {archive_path}"
            if result.error:
                message += f" - {result.error}"
            self._append_log(message)

    @Slot(object)
    def _handle_finished(self, results: list[ExtractResult]) -> None:
        self._set_running(False)
        success_count = sum(1 for item in results if item.status == "success")
        failed_count = sum(1 for item in results if item.status == "failed")
        skipped_count = sum(1 for item in results if item.status == "skipped")
        deleted_count = sum(1 for item in results if item.deleted_source)

        if not results:
            self.progress_bar.setValue(0)
            self.summary_label.setText("未找到压缩包")
            self._append_log("未找到匹配的压缩包。")
            return

        self.progress_bar.setValue(100)
        summary = (
            f"完成：成功 {success_count}，失败 {failed_count}，"
            f"跳过 {skipped_count}，删除 {deleted_count}"
        )
        self.summary_label.setText(summary)
        self._append_log(summary)

    @Slot(str)
    def _handle_failed(self, message: str) -> None:
        self._set_running(False)
        self.summary_label.setText("任务失败")
        self._append_log(f"[错误] {message}")
        QMessageBox.critical(self, "任务失败", message)

    @Slot()
    def _clear_worker_refs(self) -> None:
        self.worker = None
        self.worker_thread = None
        self.cancel_token = None

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.browse_button.setEnabled(not running)
        self.path_input.setEnabled(not running)
        self.delete_checkbox.setEnabled(not running)
        for checkbox in self.format_checks.values():
            checkbox.setEnabled(not running)

    def _ensure_row(self, archive_path: str, result: ExtractResult | None) -> int:
        if archive_path in self.rows_by_path:
            return self.rows_by_path[archive_path]

        row = self.table.rowCount()
        self.table.insertRow(row)
        self.rows_by_path[archive_path] = row
        path_obj = Path(archive_path)
        archive_format = result.archive_format if result else _format_from_name(path_obj)
        self.table.setItem(row, 0, QTableWidgetItem(path_obj.name))
        self.table.setItem(row, 1, QTableWidgetItem(archive_format))
        self.table.setItem(row, 2, QTableWidgetItem("等待"))
        self.table.setItem(row, 3, QTableWidgetItem("保留"))
        self.table.item(row, 0).setToolTip(str(path_obj))
        return row

    def _append_log(self, message: str) -> None:
        self.log_output.appendPlainText(message)


def _display_status(status: str) -> str:
    return {
        "processing": "处理中",
        "success": "成功",
        "failed": "失败",
        "skipped": "已跳过",
    }.get(status, status)


def _format_from_name(path: Path) -> str:
    lower_name = path.name.lower()
    for extension in (".tar.gz", ".tar.bz2", ".tar.xz", ".tbz2", ".tgz", ".txz", ".zip", ".7z", ".tar"):
        if lower_name.endswith(extension):
            return extension
    return "unknown"


def main() -> None:
    app = QApplication([])
    app.setApplicationName("Recursive Unzip Tool")
    window = MainWindow()
    window.show()
    app.exec()
