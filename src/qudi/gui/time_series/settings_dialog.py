# -*- coding: utf-8 -*-

"""
This file contains the qudi time series streaming gui main window QWidget.

Copyright (c) 2023, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

This file is part of qudi.

Qudi is free software: you can redistribute it and/or modify it under the terms of
the GNU Lesser General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version.

Qudi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with qudi.
If not, see <https://www.gnu.org/licenses/>.
"""

__all__ = ['ChannelSettingsDialog', 'TraceViewDialog']

from typing import Iterable, Mapping, Dict, Tuple, Union
from PySide2 import QtCore, QtWidgets


class ChannelSettingsDialog(QtWidgets.QDialog):
    def __init__(self, channels: Iterable[str], **kwargs):
        super().__init__(**kwargs)
        self.setWindowTitle('Data Channel Settings')
        # Create main layout with buttonbox and scroll area
        self.buttonbox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            parent=self
        )
        self.buttonbox.accepted.connect(self.accept)
        self.buttonbox.rejected.connect(self.reject)
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)

        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)
        layout.setStretch(0, 1)
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.buttonbox)

        # Create editor widgets for each channel
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout()
        widget.setLayout(layout)
        self.scroll_area.setWidget(widget)
        layout.setHorizontalSpacing(20)
        layout.addWidget(QtWidgets.QLabel('Channel Name'), 0, 0)
        layout.addWidget(QtWidgets.QLabel('Is Active?'), 0, 1)
        layout.addWidget(QtWidgets.QLabel('Moving Average?'), 0, 2)
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(line, 1, 0, 1, 3)

        self.channel_widgets = dict()
        for row, ch in enumerate(channels, 2):
            label = QtWidgets.QLabel(f'{ch}:')
            label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
            checkbox_1 = QtWidgets.QCheckBox()
            checkbox_2 = QtWidgets.QCheckBox()
            checkbox_1.stateChanged.connect(checkbox_2.setEnabled)
            layout.addWidget(label, row, 0)
            layout.addWidget(checkbox_1, row, 1)
            layout.addWidget(checkbox_2, row, 2)
            self.channel_widgets[ch] = (checkbox_1, checkbox_2)
        layout.setRowStretch(len(self.channel_widgets) + 2, 1)

    def get_channel_states(self) -> Dict[str, Tuple[bool, bool]]:
        return {ch: (w[0].isChecked(), w[0].isChecked() and w[1].isChecked()) for ch, w in
                self.channel_widgets.items()}

    def set_channel_states(self, channel_states: Mapping[str, Tuple[bool, bool]]) -> None:
        for ch, state in channel_states.items():
            try:
                widgets = self.channel_widgets[ch]
            except KeyError:
                pass
            else:
                widgets[0].setChecked(state[0])
                widgets[1].setChecked(state[0] and state[1])


class TraceViewDialog(QtWidgets.QDialog):
    def __init__(self, channels: Iterable[str], **kwargs):
        super().__init__(**kwargs)
        self.setWindowTitle('View Trace Selection')
        # Create main layout with buttonbox and scroll area
        self.buttonbox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            QtCore.Qt.Horizontal,
            parent=self
        )
        self.buttonbox.accepted.connect(self.accept)
        self.buttonbox.rejected.connect(self.reject)
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)

        layout = QtWidgets.QVBoxLayout()
        self.setLayout(layout)
        layout.setStretch(0, 1)
        layout.addWidget(self.scroll_area)
        layout.addWidget(self.buttonbox)

        # Create editor widgets for each channel
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout()
        widget.setLayout(layout)
        self.scroll_area.setWidget(widget)
        layout.setHorizontalSpacing(20)
        layout.addWidget(QtWidgets.QLabel('Channel Name'), 0, 0, 2, 1)
        layout.addWidget(QtWidgets.QLabel('Show Data?'), 0, 1, 2, 1)
        layout.addWidget(QtWidgets.QLabel('Show Average?'), 0, 2, 2, 1)
        layout.addWidget(QtWidgets.QLabel('Display Precision'), 0, 3, 1, 2)
        layout.addWidget(QtWidgets.QLabel('Digits'), 1, 3)
        layout.addWidget(QtWidgets.QLabel('Auto?'), 1, 4)
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        layout.addWidget(line, 2, 0, 1, 5)

        self.channel_widgets = dict()
        for row, ch in enumerate(channels, 3):
            label = QtWidgets.QLabel(f'{ch}:')
            label.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
            checkbox_1 = QtWidgets.QCheckBox()
            checkbox_2 = QtWidgets.QCheckBox()
            checkbox_3 = QtWidgets.QCheckBox()
            spinbox = QtWidgets.QSpinBox()
            spinbox.setRange(0, 15)
            spinbox.setValue(5)
            layout.addWidget(label, row, 0)
            layout.addWidget(checkbox_1, row, 1)
            layout.addWidget(checkbox_2, row, 2)
            layout.addWidget(spinbox, row, 3)
            layout.addWidget(checkbox_3, row, 4)
            checkbox_3.toggled[bool].connect(spinbox.setDisabled)
            self.channel_widgets[ch] = (checkbox_1, checkbox_2, spinbox, checkbox_3)
        layout.setRowStretch(len(self.channel_widgets) + 3, 1)
        layout.setColumnStretch(5, 1)

        # Restrict width of scroll area to only be needed to scroll vertically
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setMinimumWidth(
            widget.sizeHint().width() +
            2 * self.scroll_area.frameWidth() +
            self.scroll_area.verticalScrollBar().sizeHint().width()
        )

    def get_channel_states(self) -> Dict[str, Tuple[bool, bool, Union[int, None]]]:
        return {
            ch: (w[0].isChecked(), w[1].isChecked(), None if w[3].isChecked() else w[2].value()) for
            ch, w in self.channel_widgets.items()
        }

    def set_channel_states(self,
                           channel_states: Mapping[str, Tuple[bool, bool, Union[int, None]]]
                           ) -> None:
        for ch, state in channel_states.items():
            try:
                widgets = self.channel_widgets[ch]
            except KeyError:
                pass
            else:
                widgets[0].setChecked(state[0])
                widgets[1].setChecked(state[1])
                if state[2] is None:
                    widgets[3].setChecked(True)
                else:
                    widgets[3].setChecked(False)
                    widgets[2].setValue(state[2])
