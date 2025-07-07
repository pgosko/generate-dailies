import sys
import os
import yaml
import subprocess
import tempfile
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtWidgets import QFileDialog, QMessageBox

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "dailies-config.yaml")
DAILY_SCRIPT = os.path.join(os.path.dirname(__file__), "daily3")

class EncodeThread(QtCore.QThread):
    progress = QtCore.Signal(int, int, str)  # current_frame, total_frames, image_data
    finished = QtCore.Signal(int, str, str)  # returncode, stdout, stderr

    def __init__(self, args, env, parent=None):
        super().__init__(parent)
        self.args = args
        self.env = env

    def run(self):
        import re
        process = subprocess.Popen(
            self.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
            text=True,
            bufsize=1
        )

        # Regex for the PROGRESS line
        progress_re = re.compile(r'^PROGRESS (\d+) (\d+) (.+)$')

        # Read stderr line by line
        while True:
            line = process.stderr.readline()
            if not line and process.poll() is not None:
                break
            if not line:
                QtCore.QThread.msleep(5)
                continue

            m = progress_re.match(line.strip())
            if m:
                frame = int(m.group(1))
                total = int(m.group(2))
                img_data = m.group(3)
                self.progress.emit(frame, total, img_data)

        stdout, stderr = process.communicate()
        self.finished.emit(process.returncode, stdout, stderr)

class DailyGUI(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Daily GUI")
        self.config = self.load_config()
        self.input_dimensions = None
        self.encode_thread = None
        self.init_ui()

    def load_config(self):
        if not os.path.isfile(CONFIG_FILE):
            QMessageBox.critical(self, "Error", f"Could not find config file: {CONFIG_FILE}")
            sys.exit(1)
        with open(CONFIG_FILE, "r") as f:
            return yaml.safe_load(f)

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # Sequence folder selection
        seq_layout = QtWidgets.QHBoxLayout()
        self.seq_folder_edit = QtWidgets.QLineEdit()
        seq_btn = QtWidgets.QPushButton("Browse")
        seq_btn.clicked.connect(self.select_seq_folder)
        seq_layout.addWidget(QtWidgets.QLabel("Sequence Folder:"))
        seq_layout.addWidget(self.seq_folder_edit)
        seq_layout.addWidget(seq_btn)
        layout.addLayout(seq_layout)

        # Output folder selection
        out_layout = QtWidgets.QHBoxLayout()
        self.out_folder_edit = QtWidgets.QLineEdit()
        default_out = self.config.get("globals", {}).get("movie_location", "")
        self.out_folder_edit.setText(default_out)
        out_btn = QtWidgets.QPushButton("Browse")
        out_btn.clicked.connect(self.select_out_folder)
        out_layout.addWidget(QtWidgets.QLabel("Output Folder:"))
        out_layout.addWidget(self.out_folder_edit)
        out_layout.addWidget(out_btn)
        layout.addLayout(out_layout)

        # Encoding preset
        enc_layout = QtWidgets.QHBoxLayout()
        self.codec_combo = QtWidgets.QComboBox()
        codecs = list(self.config.get("output_codecs", {}).keys())
        self.codec_combo.addItems(codecs)
        default_codec = self.config.get("globals", {}).get("output_codec", "")
        if default_codec in codecs:
            self.codec_combo.setCurrentText(default_codec)
        enc_layout.addWidget(QtWidgets.QLabel("Encoding Preset:"))
        enc_layout.addWidget(self.codec_combo)
        layout.addLayout(enc_layout)

        # Dimension fields
        dim_layout = QtWidgets.QHBoxLayout()
        self.input_dim_label = QtWidgets.QLabel("Input Dimensions: N/A")
        dim_layout.addWidget(self.input_dim_label)
        self.out_width = QtWidgets.QSpinBox()
        self.out_width.setMaximum(16384)
        self.out_width.setMinimum(1)
        self.out_height = QtWidgets.QSpinBox()
        self.out_height.setMaximum(16384)
        self.out_height.setMinimum(1)
        dim_layout.addWidget(QtWidgets.QLabel("Output Width:"))
        dim_layout.addWidget(self.out_width)
        dim_layout.addWidget(QtWidgets.QLabel("Output Height:"))
        dim_layout.addWidget(self.out_height)
        layout.addLayout(dim_layout)

        # Aspect ratio and fit options
        self.keep_ar_chk = QtWidgets.QCheckBox("Maintain Aspect Ratio")
        self.keep_ar_chk.setChecked(True)
        self.scale_fit_chk = QtWidgets.QCheckBox("Scale to Fit Height")
        layout.addWidget(self.keep_ar_chk)
        layout.addWidget(self.scale_fit_chk)

        # Status/progress bar
        status_layout = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("Ready")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.progress_bar)
        layout.addLayout(status_layout)

        # Preview image area
        self.preview_label = QtWidgets.QLabel("Preview")
        self.preview_label.setFixedSize(640, 480)
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setStyleSheet("border: 1px solid gray; background: #222;")
        layout.addWidget(self.preview_label)

        # Generate button
        self.generate_btn = QtWidgets.QPushButton("Generate Daily")
        self.generate_btn.clicked.connect(self.generate_daily)
        layout.addWidget(self.generate_btn)

        # Connect signals for dimension recalculation
        self.seq_folder_edit.editingFinished.connect(self.update_input_dim)
        self.codec_combo.currentTextChanged.connect(self.update_output_dim)
        self.keep_ar_chk.stateChanged.connect(self.sync_output_dim)
        self.scale_fit_chk.stateChanged.connect(self.sync_output_dim)
        self.out_width.valueChanged.connect(self.sync_output_dim)
        self.out_height.valueChanged.connect(self.sync_output_dim)

    def select_seq_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Sequence Folder")
        if folder:
            self.seq_folder_edit.setText(folder)
            self.update_input_dim()

    def select_out_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.out_folder_edit.setText(folder)

    def find_first_image(self, folder):
        exts = self.config.get("globals", {}).get("input_image_formats", ['exr', 'tif', 'tiff', 'png', 'jpg', 'jpeg'])
        files = []
        for ext in exts:
            files += [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(ext)]
        files = sorted(files)
        return files[0] if files else None

    def get_image_dimensions(self, image_path):
        try:
            import OpenImageIO as oiio
            buf = oiio.ImageBuf(image_path)
            spec = buf.spec()
            return (spec.width, spec.height)
        except Exception:
            return None

    def update_input_dim(self):
        folder = self.seq_folder_edit.text()
        first_image = self.find_first_image(folder)
        if not first_image:
            self.input_dim_label.setText("Input Dimensions: N/A")
            return
        dims = self.get_image_dimensions(first_image)
        if not dims:
            self.input_dim_label.setText("Input Dimensions: N/A")
            return
        self.input_dimensions = dims
        self.input_dim_label.setText(f"Input Dimensions: {dims[0]} x {dims[1]}")
        self.out_width.setValue(dims[0])
        self.out_height.setValue(dims[1])
        try:
            import base64
            with open(first_image, "rb") as f:
                img_data = base64.b64encode(f.read()).decode("ascii")
        except Exception:
            img_data = ""
        self.update_preview_image(img_data)

    def update_output_dim(self):
        if self.input_dimensions:
            self.out_width.setValue(self.input_dimensions[0])
            self.out_height.setValue(self.input_dimensions[1])

    def sync_output_dim(self):
        if not self.input_dimensions:
            return
        in_w, in_h = self.input_dimensions
        aspect = in_w / in_h if in_h != 0 else 1
        if self.keep_ar_chk.isChecked():
            sender = self.sender()
            if sender == self.out_width:
                self.out_height.blockSignals(True)
                self.out_height.setValue(int(self.out_width.value() / aspect))
                self.out_height.blockSignals(False)
            elif sender == self.out_height or self.scale_fit_chk.isChecked():
                self.out_width.blockSignals(True)
                self.out_width.setValue(int(self.out_height.value() * aspect))
                self.out_width.blockSignals(False)
        if self.scale_fit_chk.isChecked():
            self.out_height.setValue(in_h)
            self.out_width.setValue(int(in_h * aspect))

    def update_preview_image(self, image_data):
        try:
            import base64
            img_bytes = base64.b64decode(image_data)
            pix = QtGui.QPixmap()
            if pix.loadFromData(img_bytes, "JPEG"):
                pix = pix.scaled(640, 480, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                self.preview_label.setPixmap(pix)
                return
        except Exception:
            pass
        self.preview_label.clear()
        self.preview_label.setText("Preview not available")

    def generate_daily(self):
        seq_folder = self.seq_folder_edit.text().strip()
        out_folder = self.out_folder_edit.text().strip()
        codec = self.codec_combo.currentText()
        width = self.out_width.value()
        height = self.out_height.value()

        if not os.path.isdir(seq_folder):
            QMessageBox.critical(self, "Error", "Please select a valid sequence folder.")
            return

        input_file = self.find_first_image(seq_folder)
        if not input_file:
            QMessageBox.critical(self, "Error", "No image sequence found in folder.")
            return

        temp_config = tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".yaml")
        temp_config_path = temp_config.name
        config_copy = yaml.safe_load(open(CONFIG_FILE))

        config_copy["globals"]["width"] = width
        config_copy["globals"]["height"] = height
        config_copy["globals"]["fit"] = bool(self.scale_fit_chk.isChecked())

        yaml.safe_dump(config_copy, temp_config)
        temp_config.close()

        args = [
            sys.executable, DAILY_SCRIPT, input_file,
            "-c", codec
        ]
        if out_folder:
            args += ["-o", out_folder]

        env = os.environ.copy()
        env["DAILIES_CONFIG"] = temp_config_path

        self.status_label.setText("Encoding...")
        self.progress_bar.setValue(0)
        self.generate_btn.setEnabled(False)

        self.encode_thread = EncodeThread(args, env)
        self.encode_thread.progress.connect(self.on_progress)
        self.encode_thread.finished.connect(self.on_finished)
        self.encode_thread.start()

    @QtCore.Slot(int, int, str)
    def on_progress(self, frame, total, img_data):
        percent = int((frame / total) * 100) if total else 0
        self.progress_bar.setValue(percent)
        self.status_label.setText(f"Frame {frame}/{total} ({percent}%)")
        self.update_preview_image(img_data)

    @QtCore.Slot(int, str, str)
    def on_finished(self, retcode, stdout, stderr):
        self.generate_btn.setEnabled(True)
        if retcode == 0:
            self.status_label.setText("Success!")
            QMessageBox.information(self, "Success", "Daily generated successfully!\n\n" + (stdout or ""))
        else:
            self.status_label.setText("Failed")
            QMessageBox.critical(self, "Daily Failed", f"Daily exited with code {retcode}\n\nStdout:\n{stdout}\n\nStderr:\n{stderr}")
        self.progress_bar.setValue(0)
        # Clean up temp config
        try:
            os.unlink(self.encode_thread.env["DAILIES_CONFIG"])
        except Exception:
            pass

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    gui = DailyGUI()
    gui.resize(900, 820)
    gui.show()
    sys.exit(app.exec())