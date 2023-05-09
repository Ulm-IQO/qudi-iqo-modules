from PySide2.QtWidgets import QWidget, QLabel, QVBoxLayout,QCheckBox
from PySide2.QtCore import Qt, Signal
from PySide2.QtGui import QPalette, QColor


class ActivateTiltCorrection(QWidget):
    stateChanged = Signal(bool)

    def __init__(self):
        super().__init__()

        # Set up the UI for the toggle switch
        self.initUI()

    def initUI(self):
        # Create a label for the toggle switch
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("QLabel { background-color: red; color: white; }")
        self.label.setFixedSize(100, 40)
        self.label.setText("Tilt_correction:OFF")

        # Create a vertical layout for the toggle switch
        layout = QVBoxLayout()
        layout.addWidget(self.label)

        # Set the layout for the widget
        self.setLayout(layout)

    def mousePressEvent(self, event):
        # Toggle the state of the toggle switch
        if self.label.text() == "Tilt_correction:OFF":
            self.label.setText("Tilt_correction:ON")
            self.label.setStyleSheet("QLabel { background-color: green; color: white; }")
            self.stateChanged.emit(True)  # Emit signal for state change
        else:
            self.label.setText("Tilt_correction:OFF")
            self.label.setStyleSheet("QLabel { background-color: red; color: white; }")
            self.stateChanged.emit(False)  # Emit signal for state change

    def isChecked(self):
        return self.label.text() == "Tilt_correction:ON"