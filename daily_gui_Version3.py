import sys
import os
import yaml
import subprocess
import tempfile
import shutil
from PySide6 import QtWidgets, QtCore
from PySide6.QtWidgets import QFileDialog, QMessageBox

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "dailies-config.yaml")
DAILY_SCRIPT = os.path.join(os.path.dirname(__file__), "daily2")

class DailyGUI(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Daily GUI")
        self.config = self.load_config()
        self.input_dimensions = None
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

    def update_output_dim(self):
        # On codec change, keep current output dim unless input is set
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
            # Scale so that height fits, width is recalculated
            self.out_height.setValue(in_h)
            self.out_width.setValue(int(in_h * aspect))

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

        # Write temporary config with overridden width/height
        temp_config = tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".yaml")
        temp_config_path = temp_config.name
        config_copy = yaml.safe_load(open(CONFIG_FILE))

        # Update width/height/fit
        config_copy["globals"]["width"] = width
        config_copy["globals"]["height"] = height
        config_copy["globals"]["fit"] = bool(self.scale_fit_chk.isChecked())

        yaml.safe_dump(config_copy, temp_config)
        temp_config.close()

        # Compose CLI arguments
        args = [
            sys.executable, DAILY_SCRIPT, input_file,
            "-c", codec
        ]
        if out_folder:
            args += ["-o", out_folder]

        # Set environment variable to override config
        env = os.environ.copy()
        env["DAILIES_CONFIG"] = temp_config_path

        # Run daily as subprocess
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, text=True)
            stdout, stderr = proc.communicate()
            retcode = proc.returncode
            if retcode == 0:
                QMessageBox.information(self, "Success", f"Daily generated successfully!\n\n{stdout}")
            else:
                QMessageBox.critical(self, "Daily Failed", f"Daily exited with code {retcode}\n\nStdout:\n{stdout}\n\nStderr:\n{stderr}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to run daily: {e}")
        finally:
            os.unlink(temp_config_path)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    gui = DailyGUI()
    gui.resize(800, 250)
    gui.show()
    sys.exit(app.exec())