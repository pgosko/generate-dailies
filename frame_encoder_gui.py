#!/usr/bin/env python3
"""Frame Encoder GUI — queue EXR frame sequences for video encoding with ACES."""

import os
import re
import sys
import yaml
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QFileDialog, QMessageBox

DIR_PATH = os.path.dirname(os.path.realpath(__file__))
CONFIG_FILE = os.path.join(DIR_PATH, "frame_encoder_config.yaml")
DAILY_SCRIPT = os.path.join(DIR_PATH, "daily")
SETTINGS_FILE = os.path.join(DIR_PATH, ".frame_encoder_settings.yaml")


class JobStatus(Enum):
    PENDING = "Pending"
    PROCESSING = "Processing"
    DONE = "Done"
    ERROR = "Error"
    SKIPPED = "Skipped"


@dataclass
class QueueJob:
    folder: str
    status: JobStatus = JobStatus.PENDING
    message: str = ""

    def display_name(self) -> str:
        return os.path.basename(os.path.normpath(self.folder)) or self.folder


class EncodeThread(QtCore.QThread):
    progress = QtCore.Signal(int, int, str)
    finished = QtCore.Signal(int, str, str)
    started = QtCore.Signal()

    def __init__(self, args, env, parent=None):
        super().__init__(parent)
        self.args = args
        self.env = env
        self._temp_config: Optional[str] = None

    def run(self):
        progress_re = re.compile(r"^PROGRESS (\d+) (\d+)(?: (.*))?$")
        try:
            self.started.emit()
            process = subprocess.Popen(
                self.args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                text=True,
                bufsize=1,
            )

            while True:
                line = process.stderr.readline()
                if not line and process.poll() is not None:
                    break
                if not line:
                    QtCore.QThread.msleep(10)
                    continue

                line = line.strip()
                if not line:
                    continue

                match = progress_re.match(line)
                if match:
                    try:
                        frame = int(match.group(1))
                        total = int(match.group(2))
                        img_data = match.group(3) or ""
                        self.progress.emit(frame, total, img_data)
                    except (ValueError, IndexError):
                        continue

            stdout, stderr = process.communicate()
            self.finished.emit(process.returncode, stdout or "", stderr or "")
        except Exception as exc:
            self.finished.emit(-1, "", f"Thread error: {exc}")

    def set_temp_config(self, path: str):
        self._temp_config = path

    def cleanup(self):
        if self._temp_config and os.path.isfile(self._temp_config):
            try:
                os.unlink(self._temp_config)
            except OSError:
                pass


class FrameEncoderGUI(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Frame Encoder")
        self.config = self.load_config()
        self.encode_thread: Optional[EncodeThread] = None
        self.queue: list[QueueJob] = []
        self.current_job_index = -1
        self.input_dimensions: Optional[tuple[int, int]] = None
        self.saved_settings = self.load_settings()
        self.init_ui()
        self.apply_saved_settings()

    def load_config(self) -> dict:
        if not os.path.isfile(CONFIG_FILE):
            QMessageBox.critical(self, "Error", f"Config not found:\n{CONFIG_FILE}")
            sys.exit(1)
        with open(CONFIG_FILE, "r") as handle:
            config = yaml.safe_load(handle)
        ocioconfig = config.get("globals", {}).get("ocioconfig") or ""
        if not ocioconfig:
            env_ocio = os.environ.get("OCIO", "")
            if env_ocio:
                config["globals"]["ocioconfig"] = env_ocio
        return config

    def load_settings(self) -> dict:
        if os.path.isfile(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as handle:
                    return yaml.safe_load(handle) or {}
            except Exception:
                pass
        return {}

    def save_settings(self):
        data = {
            "output_folder": self.out_folder_edit.text(),
            "codec": self.codec_combo.currentText(),
            "color_transform": self.color_combo.currentText(),
            "framerate": self.fps_spin.value(),
            "width": self.out_width.value(),
            "height": self.out_height.value(),
            "fit": self.scale_fit_chk.isChecked(),
            "ocio_config": self.ocio_edit.text(),
        }
        try:
            with open(SETTINGS_FILE, "w") as handle:
                yaml.safe_dump(data, handle)
        except OSError:
            pass

    def apply_saved_settings(self):
        if self.saved_settings.get("output_folder"):
            self.out_folder_edit.setText(self.saved_settings["output_folder"])
        if self.saved_settings.get("codec"):
            idx = self.codec_combo.findText(self.saved_settings["codec"])
            if idx >= 0:
                self.codec_combo.setCurrentIndex(idx)
        if self.saved_settings.get("color_transform"):
            idx = self.color_combo.findText(self.saved_settings["color_transform"])
            if idx >= 0:
                self.color_combo.setCurrentIndex(idx)
        if self.saved_settings.get("framerate"):
            self.fps_spin.setValue(self.saved_settings["framerate"])
        if self.saved_settings.get("width"):
            self.out_width.setValue(self.saved_settings["width"])
        if self.saved_settings.get("height"):
            self.out_height.setValue(self.saved_settings["height"])
        if "fit" in self.saved_settings:
            self.scale_fit_chk.setChecked(bool(self.saved_settings["fit"]))
        if self.saved_settings.get("ocio_config"):
            self.ocio_edit.setText(self.saved_settings["ocio_config"])

    def init_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # --- Queue panel ---
        queue_group = QtWidgets.QGroupBox("Encode Queue")
        queue_layout = QtWidgets.QVBoxLayout(queue_group)

        self.queue_list = QtWidgets.QListWidget()
        self.queue_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.queue_list.setMinimumHeight(140)
        queue_layout.addWidget(self.queue_list)

        queue_btn_row = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add Folder")
        add_btn.clicked.connect(self.add_folders)
        remove_btn = QtWidgets.QPushButton("Remove")
        remove_btn.clicked.connect(self.remove_selected)
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self.clear_queue)
        queue_btn_row.addWidget(add_btn)
        queue_btn_row.addWidget(remove_btn)
        queue_btn_row.addWidget(clear_btn)
        queue_btn_row.addStretch()
        queue_layout.addLayout(queue_btn_row)
        layout.addWidget(queue_group)

        # --- Settings panel ---
        settings_group = QtWidgets.QGroupBox("Encode Settings")
        form = QtWidgets.QFormLayout(settings_group)

        out_row = QtWidgets.QHBoxLayout()
        self.out_folder_edit = QtWidgets.QLineEdit(
            self.config.get("globals", {}).get("movie_location", "../output")
        )
        out_browse = QtWidgets.QPushButton("Browse")
        out_browse.clicked.connect(self.select_output_folder)
        out_row.addWidget(self.out_folder_edit)
        out_row.addWidget(out_browse)
        form.addRow("Output Folder:", out_row)

        self.codec_combo = QtWidgets.QComboBox()
        codecs = list(self.config.get("output_codecs", {}).keys())
        self.codec_combo.addItems(codecs)
        default_codec = self.config.get("globals", {}).get("output_codec", "")
        if default_codec in codecs:
            self.codec_combo.setCurrentText(default_codec)
        form.addRow("Codec:", self.codec_combo)

        self.color_combo = QtWidgets.QComboBox()
        profiles = list(self.config.get("ocio_profiles", {}).keys())
        self.color_combo.addItems(profiles)
        default_color = self.config.get("globals", {}).get("ocio_default_transform", "aces_rec709")
        if default_color in profiles:
            self.color_combo.setCurrentText(default_color)
        form.addRow("ACES Transform:", self.color_combo)

        ocio_row = QtWidgets.QHBoxLayout()
        self.ocio_edit = QtWidgets.QLineEdit(
            self.config.get("globals", {}).get("ocioconfig", "") or os.environ.get("OCIO", "")
        )
        self.ocio_edit.setPlaceholderText("Path to ACES config.ocio (or set $OCIO)")
        ocio_browse = QtWidgets.QPushButton("Browse")
        ocio_browse.clicked.connect(self.select_ocio_config)
        ocio_row.addWidget(self.ocio_edit)
        ocio_row.addWidget(ocio_browse)
        form.addRow("OCIO Config:", ocio_row)

        self.fps_spin = QtWidgets.QDoubleSpinBox()
        self.fps_spin.setRange(1, 120)
        self.fps_spin.setDecimals(3)
        self.fps_spin.setValue(float(self.config.get("globals", {}).get("framerate", 24)))
        form.addRow("Framerate:", self.fps_spin)

        dim_row = QtWidgets.QHBoxLayout()
        self.out_width = QtWidgets.QSpinBox()
        self.out_width.setRange(1, 16384)
        self.out_width.setValue(self.config.get("globals", {}).get("width", 1920))
        self.out_height = QtWidgets.QSpinBox()
        self.out_height.setRange(1, 16384)
        self.out_height.setValue(self.config.get("globals", {}).get("height", 1080))
        dim_row.addWidget(QtWidgets.QLabel("W:"))
        dim_row.addWidget(self.out_width)
        dim_row.addWidget(QtWidgets.QLabel("H:"))
        dim_row.addWidget(self.out_height)
        form.addRow("Resolution:", dim_row)

        self.scale_fit_chk = QtWidgets.QCheckBox("Scale to fit (letterbox/pad)")
        self.scale_fit_chk.setChecked(self.config.get("globals", {}).get("fit", True))
        form.addRow("", self.scale_fit_chk)

        self.input_dim_label = QtWidgets.QLabel("Input: select a queue folder to inspect")
        form.addRow("Sequence Info:", self.input_dim_label)

        layout.addWidget(settings_group)

        # --- Progress ---
        progress_group = QtWidgets.QGroupBox("Progress")
        progress_layout = QtWidgets.QVBoxLayout(progress_group)

        self.job_label = QtWidgets.QLabel("No job running")
        self.status_label = QtWidgets.QLabel("Ready")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)

        progress_layout.addWidget(self.job_label)
        progress_layout.addWidget(self.status_label)
        progress_layout.addWidget(self.progress_bar)
        layout.addWidget(progress_group)

        # --- Preview ---
        self.preview_label = QtWidgets.QLabel("Preview")
        self.preview_label.setFixedSize(640, 360)
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setStyleSheet("border: 1px solid #555; background: #1e1e1e; color: #888;")
        layout.addWidget(self.preview_label, alignment=QtCore.Qt.AlignCenter)

        # --- Actions ---
        action_row = QtWidgets.QHBoxLayout()
        self.encode_btn = QtWidgets.QPushButton("Start Queue")
        self.encode_btn.clicked.connect(self.start_queue)
        self.stop_btn = QtWidgets.QPushButton("Stop After Current")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_queue)
        action_row.addWidget(self.encode_btn)
        action_row.addWidget(self.stop_btn)
        layout.addLayout(action_row)

        self.queue_list.currentRowChanged.connect(self.on_queue_selection_changed)
        self.stop_after_current = False

    def add_folders(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Frame Sequence Folder")
        if not folder:
            return
        self._add_folder(folder)

    def _add_folder(self, folder: str):
        folder = os.path.normpath(folder)
        for job in self.queue:
            if os.path.normpath(job.folder) == folder:
                return
        if not self.find_first_image(folder):
            QMessageBox.warning(
                self,
                "No Sequence",
                f"No supported image sequence found in:\n{folder}",
            )
            return
        self.queue.append(QueueJob(folder=folder))
        self.refresh_queue_list()

    def remove_selected(self):
        rows = sorted({idx.row() for idx in self.queue_list.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.queue):
                del self.queue[row]
        self.refresh_queue_list()

    def clear_queue(self):
        if self.encode_thread and self.encode_thread.isRunning():
            QMessageBox.warning(self, "Busy", "Cannot clear queue while encoding.")
            return
        self.queue.clear()
        self.refresh_queue_list()

    def refresh_queue_list(self):
        self.queue_list.clear()
        for job in self.queue:
            item = QtWidgets.QListWidgetItem(f"[{job.status.value}] {job.display_name()}")
            if job.message:
                item.setToolTip(job.message)
            if job.status == JobStatus.DONE:
                item.setForeground(QtGui.QColor("#4caf50"))
            elif job.status == JobStatus.ERROR:
                item.setForeground(QtGui.QColor("#f44336"))
            elif job.status == JobStatus.PROCESSING:
                item.setForeground(QtGui.QColor("#2196f3"))
            self.queue_list.addItem(item)

    def on_queue_selection_changed(self, row: int):
        if row < 0 or row >= len(self.queue):
            return
        folder = self.queue[row].folder
        first_image = self.find_first_image(folder)
        if not first_image:
            self.input_dim_label.setText("Input: no images found")
            return
        dims = self.get_image_dimensions(first_image)
        if dims:
            self.input_dimensions = dims
            self.input_dim_label.setText(f"Input: {dims[0]} x {dims[1]}  ({first_image})")
        else:
            self.input_dim_label.setText("Input: could not read dimensions")

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.out_folder_edit.setText(folder)

    def select_ocio_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select OCIO Config", "", "OCIO Config (config.ocio);;All Files (*)"
        )
        if path:
            self.ocio_edit.setText(path)

    def find_first_image(self, folder: str) -> Optional[str]:
        exts = self.config.get("globals", {}).get(
            "input_image_formats", ["exr", "tif", "tiff", "png", "jpg", "jpeg"]
        )
        if not os.path.isdir(folder):
            return None
        files = []
        for name in sorted(os.listdir(folder)):
            ext = os.path.splitext(name)[1].lstrip(".").lower()
            if ext in exts:
                files.append(os.path.join(folder, name))
        return files[0] if files else None

    def get_image_dimensions(self, image_path: str) -> Optional[tuple[int, int]]:
        try:
            import OpenImageIO as oiio

            spec = oiio.ImageBuf(image_path).spec()
            return spec.width, spec.height
        except Exception:
            return None

    def update_preview_image(self, image_data: str):
        if not image_data:
            return
        try:
            import base64

            img_bytes = base64.b64decode(image_data)
            pix = QtGui.QPixmap()
            if pix.loadFromData(img_bytes, "JPEG"):
                pix = pix.scaled(
                    self.preview_label.size(),
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
                self.preview_label.setPixmap(pix)
        except Exception:
            pass

    def build_temp_config(self) -> str:
        with open(CONFIG_FILE, "r") as handle:
            config_copy = yaml.safe_load(handle)

        config_copy["globals"]["width"] = self.out_width.value()
        config_copy["globals"]["height"] = self.out_height.value()
        config_copy["globals"]["fit"] = self.scale_fit_chk.isChecked()
        config_copy["globals"]["framerate"] = self.fps_spin.value()
        config_copy["globals"]["debug"] = False

        ocio_path = self.ocio_edit.text().strip()
        if ocio_path:
            config_copy["globals"]["ocioconfig"] = ocio_path

        temp = tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".yaml")
        yaml.safe_dump(config_copy, temp)
        temp.close()
        return temp.name

    def start_queue(self):
        if not self.queue:
            QMessageBox.warning(self, "Empty Queue", "Add at least one folder to the queue.")
            return

        pending = [j for j in self.queue if j.status in (JobStatus.PENDING, JobStatus.ERROR)]
        if not pending:
            for job in self.queue:
                job.status = JobStatus.PENDING
                job.message = ""
            self.refresh_queue_list()

        ocio_path = self.ocio_edit.text().strip()
        color_transform = self.color_combo.currentText()
        if color_transform != "none" and not ocio_path:
            QMessageBox.warning(
                self,
                "OCIO Required",
                "Set an ACES OCIO config path or the $OCIO environment variable "
                "before encoding with a color transform.",
            )
            return
        if color_transform != "none" and not os.path.isfile(ocio_path):
            QMessageBox.warning(self, "OCIO Not Found", f"OCIO config not found:\n{ocio_path}")
            return

        self.save_settings()
        self.stop_after_current = False
        self.encode_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.process_next_job()

    def stop_queue(self):
        self.stop_after_current = True
        self.status_label.setText("Stopping after current job...")

    def process_next_job(self):
        next_index = -1
        for idx, job in enumerate(self.queue):
            if job.status in (JobStatus.PENDING, JobStatus.ERROR):
                next_index = idx
                break

        if next_index < 0:
            self.on_queue_complete()
            return

        if self.stop_after_current and self.current_job_index >= 0:
            self.on_queue_complete(stopped=True)
            return

        self.current_job_index = next_index
        job = self.queue[next_index]
        job.status = JobStatus.PROCESSING
        job.message = ""
        self.refresh_queue_list()
        self.queue_list.setCurrentRow(next_index)

        input_file = self.find_first_image(job.folder)
        if not input_file:
            job.status = JobStatus.ERROR
            job.message = "No images found"
            self.refresh_queue_list()
            self.process_next_job()
            return

        temp_config = self.build_temp_config()
        args = [
            sys.executable,
            DAILY_SCRIPT,
            input_file,
            "-c",
            self.codec_combo.currentText(),
            "-p",
            "encode",
            "-ct",
            self.color_combo.currentText(),
            "-o",
            self.out_folder_edit.text().strip(),
        ]

        env = os.environ.copy()
        env["DAILIES_CONFIG"] = temp_config
        ocio_path = self.ocio_edit.text().strip()
        if ocio_path:
            env["OCIO"] = ocio_path

        self.job_label.setText(f"Job {next_index + 1}/{len(self.queue)}: {job.display_name()}")
        self.status_label.setText("Starting encode...")
        self.progress_bar.setValue(0)

        self.encode_thread = EncodeThread(args, env)
        self.encode_thread.set_temp_config(temp_config)
        self.encode_thread.started.connect(self.on_encoding_started)
        self.encode_thread.progress.connect(self.on_progress)
        self.encode_thread.finished.connect(self.on_job_finished)
        self.encode_thread.start()

    @QtCore.Slot()
    def on_encoding_started(self):
        self.status_label.setText("Encoding frames...")

    @QtCore.Slot(int, int, str)
    def on_progress(self, frame: int, total: int, img_data: str):
        percent = int((frame / total) * 100) if total else 0
        self.progress_bar.setValue(percent)
        self.status_label.setText(f"Frame {frame}/{total} ({percent}%)")
        self.update_preview_image(img_data)
        QtWidgets.QApplication.processEvents()

    @QtCore.Slot(int, str, str)
    def on_job_finished(self, retcode: int, stdout: str, stderr: str):
        job = self.queue[self.current_job_index]
        if retcode == 0:
            job.status = JobStatus.DONE
            job.message = stdout.strip() or "Encode complete"
        else:
            job.status = JobStatus.ERROR
            job.message = stderr.strip() or stdout.strip() or f"Exit code {retcode}"

        self.refresh_queue_list()

        if self.encode_thread:
            self.encode_thread.cleanup()

        if self.stop_after_current:
            self.on_queue_complete(stopped=True)
        else:
            self.process_next_job()

    def on_queue_complete(self, stopped: bool = False):
        self.encode_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.stop_after_current = False
        self.progress_bar.setValue(0)

        done = sum(1 for j in self.queue if j.status == JobStatus.DONE)
        errors = sum(1 for j in self.queue if j.status == JobStatus.ERROR)

        if stopped:
            self.status_label.setText("Queue stopped")
            self.job_label.setText("Stopped")
        elif errors:
            self.status_label.setText(f"Finished with {errors} error(s)")
            self.job_label.setText(f"Complete: {done} succeeded, {errors} failed")
            QMessageBox.warning(
                self,
                "Queue Complete",
                f"{done} job(s) succeeded, {errors} failed.\nCheck queue tooltips for details.",
            )
        else:
            self.status_label.setText("All jobs complete")
            self.job_label.setText(f"Complete: {done}/{len(self.queue)}")
            QMessageBox.information(self, "Queue Complete", f"Successfully encoded {done} sequence(s).")


def main():
    if not os.path.isfile(DAILY_SCRIPT):
        print(f"Error: encoding backend not found: {DAILY_SCRIPT}", file=sys.stderr)
        sys.exit(1)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = FrameEncoderGUI()
    window.resize(720, 900)
    window.show()

    # Accept drag-and-drop of folders onto the window
    def drag_enter(event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def drop_event(event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                window._add_folder(path)
        event.acceptProposedAction()

    window.setAcceptDrops(True)
    window.dragEnterEvent = drag_enter
    window.dropEvent = drop_event

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
