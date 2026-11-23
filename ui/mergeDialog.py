# datview/ui/mergeDialog.py
# Dialog to merge the current selection with process_hkl or partialator without writing an
# intermediate stream file. The selected chunks are piped to the program's stdin ("-i -").

import os
import shutil
import shlex

try:
    from PyQt5.QtWidgets import (QDialog, QFormLayout, QComboBox, QLineEdit, QDialogButtonBox, QLabel,
                                 QPushButton, QHBoxLayout, QFileDialog, QProgressDialog, QMessageBox,
                                 QApplication, QVBoxLayout)
    from PyQt5.QtCore import Qt
except ImportError:
    from PyQt4.QtGui import (QDialog, QFormLayout, QComboBox, QLineEdit, QDialogButtonBox, QLabel,
                             QPushButton, QHBoxLayout, QFileDialog, QProgressDialog, QMessageBox,
                             QApplication, QVBoxLayout)
    from PyQt4.QtCore import Qt

DEFAULT_ARGS = {"process_hkl": "", "partialator": "--model=unity --iterations=1"}


class MyMergeDialog(QDialog):
    def __init__(self, model, partitions, parent=None):
        QDialog.__init__(self, parent)
        self.setWindowTitle("Merge Selection")
        self.model = model
        self.partitions = partitions

        # -- inputs ---------------------------------------------------------------
        self.program = QComboBox()
        self.program.addItems(["process_hkl", "partialator"])
        self.symmetry = QLineEdit()
        self.symmetry.setPlaceholderText("point group, e.g. 4/mmm or 2/m")
        self.output = QLineEdit("merged.hkl")
        browse = QPushButton("...")
        browse.clicked.connect(self.onBrowse)
        outrow = QHBoxLayout()
        outrow.addWidget(self.output)
        outrow.addWidget(browse)
        self.extra = QLineEdit(DEFAULT_ARGS["process_hkl"])
        self.extra.setPlaceholderText("other options, e.g. --max-adu=250000 --min-res=4")
        self.command = QLineEdit()
        self.command.setMinimumWidth(520)

        n = self.model.selectedCount()
        info = "%d rows selected (one crystal per row; unindexed rows are passed through and ignored by the merging program)." % n
        if partitions:
            info += "\nPartitions are active: one merge per partition, partition name appended to the output file."
        self.info = QLabel(info)
        self.info.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Program", self.program)
        form.addRow("Point group (-y)", self.symmetry)
        form.addRow("Output (-o)", outrow)
        form.addRow("Other options", self.extra)
        form.addRow("Command", self.command)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Run")
        buttons.accepted.connect(self.onRun)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addLayout(form)
        lay.addWidget(QLabel("The stream is sent on stdin; program output appears in the terminal DatView was started from."))
        lay.addWidget(buttons)

        self.program.currentTextChanged.connect(self.onProgram)
        for w in (self.symmetry, self.output, self.extra):
            w.textChanged.connect(self.updateCommand)
        self.updateCommand()

    def onProgram(self, name):
        self.extra.setText(DEFAULT_ARGS.get(name, ""))
        self.updateCommand()

    def onBrowse(self):
        name = QFileDialog.getSaveFileName(self, "Merged output", filter="*.hkl")
        if isinstance(name, tuple):
            name = name[0]
        if name:
            self.output.setText(name)

    def updateCommand(self):
        parts = [self.program.currentText()]
        if self.symmetry.text().strip():
            parts += ["-y", self.symmetry.text().strip()]
        if self.output.text().strip():
            parts += ["-o", self.output.text().strip()]
        cmd = shlex.join(parts)
        if self.extra.text().strip():
            cmd += " " + self.extra.text().strip()
        self.command.setText(cmd)

    def onRun(self):
        cmd = self.command.text().strip()
        prog = shlex.split(cmd)[0] if cmd else ""
        if not shutil.which(prog):
            QMessageBox.warning(self, "Merge", "'%s' was not found on PATH. Start DatView from a shell where CrystFEL is available." % prog)
            return
        total = self.model.selectedCount()
        progress = QProgressDialog("Sending selection to %s..." % prog, "Abort", 0, max(total, 1), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(500)

        def tick(n):
            progress.setValue(min(n, total))
            QApplication.processEvents()
            return not progress.wasCanceled()

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            results = self.model.mergeByPartitions(cmd, self.partitions, tick)
        finally:
            QApplication.restoreOverrideCursor()
            progress.close()
        lines = []
        for k, rc in results.items():
            name = k if k else "selection"
            if rc is None:
                lines.append("%s: aborted" % name)
            else:
                lines.append("%s: %s (exit code %d)" % (name, "done" if rc == 0 else "FAILED", rc))
        QMessageBox.information(self, "Merge", "\n".join(lines) + "\n\nWorking directory: %s" % os.getcwd())
        self.accept()
