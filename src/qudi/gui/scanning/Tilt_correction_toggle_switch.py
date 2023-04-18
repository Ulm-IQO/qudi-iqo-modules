from PySide2.QtWidgets import QWidget, QLabel, QVBoxLayout
from PyQt5.QtCore import Qt
class ActivateTiltCorrection(QWidget):
    def __init__(self):
        super().__init__()

        # Set up the UI for the toggle switch
        self.initUI()

    def initUI(self):
        # Create a label for the toggle switch
        self.label = QLabel("Tilt Correction: OFF")
        #self.label.setAlignment(Qt.AlignCenter)

        # Create a vertical layout for the toggle switch
        layout = QVBoxLayout()
        layout.addWidget(self.label)

        # Set the layout for the widget
        self.setLayout(layout)

        # Connect the widget to a slot for handling the toggle switch state
        self.label.mousePressEvent = self.toggleSwitch

    def toggleSwitch(self, event):
        # Toggle the state of the toggle switch
        if self.label.text() == "Tilt Correction: OFF":
            self.label.setText("Tilt Correction: ON")
        else:
            self.label.setText("Tilt Correction: OFF")
