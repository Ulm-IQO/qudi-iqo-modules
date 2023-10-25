# -*- coding: utf-8 -*-

"""
This file contains the GUI for qdyne measurements.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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
from logging import Logger
import os

from PySide2 import QtCore, QtWidgets
from qudi.util import uic
from qudi.util.helpers import natural_sort
from qudi.core.statusvariable import StatusVar
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox
from enum import Enum

class QdyneMainWindow(QtWidgets.QMainWindow):
    def __init__(self, gui):
        self._gui = gui
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\maingui.ui')

        # Load it
        super(QdyneMainWindow, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        pass

    def deactivate(self):
        pass

    def connect(self):
        self.action_Predefined_Methods_Config.triggered.connect(self._gui._gsw.show)

    def disconnect(self):
        pass

class MeasurementWidget(QtWidgets.QWidget):
    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\measurement_widget.ui')

        # Load it
        super(MeasurementWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        pass

    def deactivate(self):
        pass

    def connect(self):
        pass

    def disconnect(self):
        pass

class GenerationWidget(QtWidgets.QWidget):
    def __init__(self, gui):
        self._gui = gui
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\generation_widget.ui')

        # Load it
        super(GenerationWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        # Dynamically create GUI elements for global parameters
        self._channel_selection_comboboxes = list()  # List of created channel selection ComboBoxes
        self._global_param_widgets = list()  # List of all other created global parameter widgets
        self._create_pm_global_params()
        self.generation_parameters_updated(self._gui.logic().pulsedmasterlogic().generation_parameters)

        # Dynamically create GUI elements for predefined methods
        self.gen_buttons = dict()
        self.samplo_buttons = dict()
        self.method_param_widgets = dict()
        self._create_predefined_methods()
        return

    def deactivate(self):
        pass

    def connect(self):
        pass

    def disconnect(self):
        pass

    def _create_pm_global_params(self):
        """
        Create GUI elements for global parameters of sequence generation
        """
        col_count = 0
        row_count = 1
        combo_count = 0
        for param, value in self._gui.logic().pulsedmasterlogic().generation_parameters.items():
            # Create ComboBoxes for parameters ending on '_channel' to only be able to select
            # active channels. Also save references to those widgets in a list for easy access in
            # case of a change of channel activation config.
            if param.endswith('_channel') and (value is None or type(value) is str):
                widget = QtWidgets.QComboBox()
                widget.setObjectName('global_param_' + param)
                widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
                widget.addItem('')
                widget.addItems(natural_sort(self._gui.logic().pulsedmasterlogic().digital_channels))
                widget.addItems(natural_sort(self._gui.logic().pulsedmasterlogic().analog_channels))
                index = widget.findText(value)
                if index >= 0:
                    widget.setCurrentIndex(index)
                label = QtWidgets.QLabel(param + ':')
                label.setAlignment(QtCore.Qt.AlignRight)
                self.global_param_gridLayout.addWidget(label, 0, combo_count, QtCore.Qt.AlignVCenter)
                self.global_param_gridLayout.addWidget(widget, 0, combo_count + 1)
                combo_count += 2
                self._channel_selection_comboboxes.append(widget)
                widget.currentIndexChanged.connect(lambda: self.generation_parameters_changed())
                continue

            # Create all other widgets for int, float, bool and str and save them in a list for
            # later access. Also connect edited signals.
            if isinstance(value, str) or value is None:
                if value is None:
                    value = ''
                widget = QtWidgets.QLineEdit()
                widget.setText(value)
                widget.editingFinished.connect(self.generation_parameters_changed)
            elif type(value) is int:
                widget = ScienSpinBox()
                widget.setValue(value)
                widget.editingFinished.connect(self.generation_parameters_changed)
            elif type(value) is float:
                widget = ScienDSpinBox()
                widget.setValue(value)
                if 'amp' in param or 'volt' in param:
                    widget.setSuffix('V')
                elif 'freq' in param:
                    widget.setSuffix('Hz')
                elif any(x in param for x in ('tau', 'period', 'time', 'delay', 'laser_length')):
                    widget.setSuffix('s')
                widget.editingFinished.connect(self.generation_parameters_changed)
            elif type(value) is bool:
                widget = QtWidgets.QCheckBox()
                widget.setChecked(value)
                widget.stateChanged.connect(self.generation_parameters_changed)
            elif issubclass(type(value), Enum):
                widget = QtWidgets.QComboBox()
                for option in type(value):
                    widget.addItem(option.name, option)
                widget.setCurrentText(value.name)
                widget.currentTextChanged.connect(self.generation_parameters_changed)

            widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)

            # Create label
            label = QtWidgets.QLabel(param + ':')
            label.setAlignment(QtCore.Qt.AlignRight)

            # Rename widget to a naming convention
            widget.setObjectName('global_param_' + param)

            # Save widget in list
            self._global_param_widgets.append(widget)

            # Add widget to GUI layout
            print(f"param: {param}, col_count: {col_count}")
            if col_count > 6:
                col_count = 0
                row_count += 1
            self.global_param_gridLayout.addWidget(label, row_count, col_count, QtCore.Qt.AlignVCenter)
            self.global_param_gridLayout.addWidget(widget, row_count, col_count + 1)
            col_count += 2
        spacer = QtWidgets.QSpacerItem(20, 0,
                                       QtWidgets.QSizePolicy.Expanding,
                                       QtWidgets.QSizePolicy.Minimum)
        if row_count > 1:
            self.global_param_gridLayout.addItem(spacer, 1, 6)
        else:
            self.global_param_gridLayout.addItem(spacer, 0, max(col_count, combo_count))
        return

    def _create_predefined_methods(self):
        """
        Initializes the GUI elements for the predefined methods
        """
        # Empty reference containers
        self.gen_buttons = dict()
        self.samplo_buttons = dict()
        self.method_param_widgets = dict()

        method_params = self._gui.logic().pulsedmasterlogic().generate_method_params
        for method_name in natural_sort(self._gui.logic().pulsedmasterlogic().generate_methods):
            # Create the widgets for the predefined methods dialogue
            # Create GroupBox for the method to reside in
            groupBox = QtWidgets.QGroupBox(self)
            groupBox.setAlignment(QtCore.Qt.AlignLeft)
            groupBox.setTitle(method_name)
            # Create layout within the GroupBox
            gridLayout = QtWidgets.QGridLayout(groupBox)
            # Create generate buttons
            gen_button = QtWidgets.QPushButton(groupBox)
            gen_button.setText('Generate')
            gen_button.setObjectName('gen_' + method_name)
            gen_button.clicked.connect(self.__generate_predefined_slot(method_name, False))
            samplo_button = QtWidgets.QPushButton(groupBox)
            samplo_button.setText('GenSampLo')
            samplo_button.setObjectName('samplo_' + method_name)
            samplo_button.clicked.connect(self.__generate_predefined_slot(method_name, True))
            gridLayout.addWidget(gen_button, 0, 0, 1, 1)
            gridLayout.addWidget(samplo_button, 1, 0, 1, 1)
            self.gen_buttons[method_name] = gen_button
            self.samplo_buttons[method_name] = samplo_button

            # run through all parameters of the current method and create the widgets
            self.method_param_widgets[method_name] = dict()
            for param_index, (param_name, param) in enumerate(method_params[method_name].items()):
                    # create a label for the parameter
                    param_label = QtWidgets.QLabel(groupBox)
                    param_label.setText(param_name)
                    # create proper input widget for the parameter depending on default value type
                    if type(param) is bool:
                        input_obj = QtWidgets.QCheckBox(groupBox)
                        input_obj.setChecked(param)
                    elif type(param) is float:
                        input_obj = ScienDSpinBox(groupBox)
                        if 'amp' in param_name or 'volt' in param_name:
                            input_obj.setSuffix('V')
                        elif 'freq' in param_name:
                            input_obj.setSuffix('Hz')
                        elif 'time' in param_name or 'period' in param_name or 'tau' in param_name:
                            input_obj.setSuffix('s')
                        input_obj.setMinimumSize(QtCore.QSize(80, 0))
                        input_obj.setValue(param)
                    elif type(param) is int:
                        input_obj = ScienSpinBox(groupBox)
                        input_obj.setValue(param)
                    elif type(param) is str:
                        input_obj = QtWidgets.QLineEdit(groupBox)
                        input_obj.setMinimumSize(QtCore.QSize(80, 0))
                        input_obj.setText(param)
                    elif issubclass(type(param), Enum):
                        input_obj = QtWidgets.QComboBox(groupBox)
                        for option in type(param):
                            input_obj.addItem(option.name, option)
                        input_obj.setCurrentText(param.name)
                        # Set size constraints
                        input_obj.setMinimumSize(QtCore.QSize(80, 0))
                    else:
                        self._gui.log.error('The predefined method "{0}" has an argument "{1}" which '
                                       'has no default argument or an invalid type (str, float, '
                                       'int, bool or Enum allowed)!\nCreation of the viewbox aborted.'
                                       ''.format('generate_' + method_name, param_name))
                        continue
                    # Adjust size policy
                    input_obj.setMinimumWidth(75)
                    input_obj.setMaximumWidth(100)
                    gridLayout.addWidget(param_label, 0, param_index + 1, 1, 1)
                    gridLayout.addWidget(input_obj, 1, param_index + 1, 1, 1)
                    self.method_param_widgets[method_name][param_name] = input_obj
            h_spacer = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Expanding,
                                             QtWidgets.QSizePolicy.Minimum)
            gridLayout.addItem(h_spacer, 1, param_index + 2, 1, 1)

            # attach the GroupBox widget to the predefined methods widget.
            setattr(self, method_name + '_GroupBox', groupBox)
            self.verticalLayout.addWidget(groupBox)
        self.verticalLayout.addStretch()
        return

    def __generate_predefined_slot(self, method_name, sample_and_load):
        assert method_name and isinstance(method_name, str)
        assert isinstance(sample_and_load, bool)
        def slot():
            self.generate_predefined_clicked(method_name, sample_and_load)
        return slot

    def generate_predefined_clicked(self, method_name, sample_and_load=False):
        """

        @param str method_name:
        @param bool sample_and_load:
        """
        # get parameters from input widgets
        # Store parameters together with the parameter names in a dictionary
        param_dict = dict()
        for param_name, widget in self.method_param_widgets[method_name].items():
            if hasattr(widget, 'isChecked'):
                param_dict[param_name] = widget.isChecked()
            elif hasattr(widget, 'value'):
                param_dict[param_name] = widget.value()
            elif hasattr(widget, 'text'):
                param_dict[param_name] = widget.text()
            elif hasattr(widget, 'currentIndex') and hasattr(widget, 'itemData'):
                param_dict[param_name] = widget.itemData(widget.currentIndex())
            else:
                self._gui.error('Not possible to get the value from the widgets, since it does not '
                               'have one of the possible access methods!')
                return

        if sample_and_load:
            # disable buttons
            for button in self.gen_buttons.values():
                button.setEnabled(False)
            for button in self.samplo_buttons.values():
                button.setEnabled(False)

        self._gui.logic().pulsedmasterlogic().generate_predefined_sequence(
            method_name, param_dict, sample_and_load
        )

    @QtCore.Slot()
    def generation_parameters_changed(self):
        """

        @return:
        """
        settings_dict = dict()
        settings_dict['laser_channel'] = self._pg.gen_laserchannel_ComboBox.currentText()
        settings_dict['sync_channel'] = self._pg.gen_syncchannel_ComboBox.currentText()
        settings_dict['gate_channel'] = self._pg.gen_gatechannel_ComboBox.currentText()
        # Add channel specifiers from predefined methods tab
        if hasattr(self, '_channel_selection_comboboxes'):
            for combobox in self._channel_selection_comboboxes:
                # cut away 'global_param_' from beginning of the objectName
                param_name = combobox.objectName()[13:]
                settings_dict[param_name] = combobox.currentText()
        # Add remaining global parameter widgets
        if hasattr(self, '_global_param_widgets'):
            for widget in self._global_param_widgets:
                # cut away 'global_param_' from beginning of the objectName
                param_name = widget.objectName()[13:]
                if hasattr(widget, 'isChecked'):
                    settings_dict[param_name] = widget.isChecked()
                elif hasattr(widget, 'value'):
                    settings_dict[param_name] = widget.value()
                elif hasattr(widget, 'text'):
                    settings_dict[param_name] = widget.text()
                elif hasattr(widget, 'currentText'):
                    settings_dict[param_name] = widget.currentData()

        self._gui.logic().pulsedmasterlogic().set_generation_parameters(settings_dict)

        self._pg.block_editor.set_laser_channel_is_digital(settings_dict['laser_channel'].startswith('d'))
        return

    @QtCore.Slot(dict)
    def generation_parameters_updated(self, settings_dict):
        """

        @param settings_dict:
        @return:
        """
        # block signals
        self.laserchannel_combobox.blockSignals(True)
        self.syncchannel_combobox.blockSignals(True)
        self.gatechannel_combobox.blockSignals(True)

        if 'laser_channel' in settings_dict:
            index = self.laserchannel_combobox.findText(settings_dict['laser_channel'])
            self.laserchannel_combobox.setCurrentIndex(index)
            # self._pg.block_editor.set_laser_channel_is_digital(settings_dict['laser_channel'].startswith('d'))
        if 'sync_channel' in settings_dict:
            index = self.syncchannel_combobox.findText(settings_dict['sync_channel'])
            self.syncchannel_combobox.setCurrentIndex(index)
        if 'gate_channel' in settings_dict:
            index = self.gatechannel_combobox.findText(settings_dict['gate_channel'])
            self.gatechannel_combobox.setCurrentIndex(index)
        if hasattr(self, '_channel_selection_comboboxes'):
            for combobox in self._channel_selection_comboboxes:
                param_name = combobox.objectName()[13:]
                if param_name in settings_dict:
                    combobox.blockSignals(True)
                    index = combobox.findText(settings_dict[param_name])
                    combobox.setCurrentIndex(index)
                    combobox.blockSignals(False)
        if hasattr(self, '_global_param_widgets'):
            for widget in self._global_param_widgets:
                param_name = widget.objectName()[13:]
                if param_name in settings_dict:
                    widget.blockSignals(True)
                    if hasattr(widget, 'setChecked'):
                        widget.setChecked(settings_dict[param_name])
                    elif hasattr(widget, 'setValue'):
                        widget.setValue(settings_dict[param_name])
                    elif hasattr(widget, 'setText'):
                        widget.setText(settings_dict[param_name])
                    elif hasattr(widget, 'currentText'):
                        index = widget.findText(str(settings_dict[param_name].name))
                        widget.setCurrentIndex(index)
                    widget.blockSignals(False)

        # unblock signals
        self.laserchannel_combobox.blockSignals(False)
        self.syncchannel_combobox.blockSignals(False)
        self.gatechannel_combobox.blockSignals(False)
        return

class StateEstimatorWidget(QtWidgets.QWidget):
    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\state_estimator_widget.ui')

        # Load it
        super(StateEstimatorWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        pass

    def deactivate(self):
        pass

    def connect(self):
        pass

    def disconnect(self):
        pass


class TimeTraceAnalysisWidget(QtWidgets.QWidget):
    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\time_trace_analysis_widget.ui')

        # Load it
        super(TimeTraceAnalysisWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self, analyzer, settings):
        self.analyzer = analyzer
        self.settings = settings
        pass

    def deactivate(self):
        pass

    def connect(self):
        pass

    def disconnect(self):
        pass


class PredefinedMethodsConfigDialogWidget(QtWidgets.QDialog):
    def __init__(self, gui):
        self._gui = gui
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, 'ui/predefined_methods_config.ui')

        # Load it
        super().__init__()

        uic.loadUi(ui_file, self)

    def activate(self):
        """ Initialize, connect and configure the pulse generator settings to be displayed in the
        editor.
        """
        # create all GUI elements and check all boxes listed in the methods to show
        for method_name in natural_sort(self._gui.logic().pulsedmasterlogic().generate_methods):
            # create checkboxes for the config dialogue
            name_checkbox = 'checkbox_' + method_name
            setattr(self, name_checkbox, QtWidgets.QCheckBox(self.scrollArea))
            checkbox = getattr(self, name_checkbox)
            checkbox.setObjectName(name_checkbox)
            checkbox.setText(method_name)
            checkbox.setChecked(method_name in self._gui._predefined_methods_to_show)
            self.verticalLayout.addWidget(checkbox)

        # apply the chosen methods to the methods dialogue
        self.apply_predefined_methods_config()
        return

    def deactivate(self):
        pass

    def connect(self):
        # Connect signals used in predefined methods config dialog
        self.accepted.connect(self.apply_predefined_methods_config)
        self.rejected.connect(self.keep_former_predefined_methods_config)
        self.buttonBox.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.apply_predefined_methods_config)

    def disconnect(self):
        pass

    def _deactivate_predefined_methods_settings_ui(self):
        self.close()
        return

    def show_predefined_methods_config(self):
        """ Opens the Window for the config of predefined methods."""
        self.show()
        self.raise_()
        return

    def keep_former_predefined_methods_config(self):
        for method_name in self._gui.logic().pulsedmasterlogic().generate_methods:
            groupbox = getattr(self._gui._gw, method_name + '_GroupBox')
            checkbox = getattr(self, 'checkbox_' + method_name)
            checkbox.setChecked(groupbox.isVisible())
        return

    def apply_predefined_methods_config(self):
        self._gui._predefined_methods_to_show = list()
        for method_name in self._gui.logic().pulsedmasterlogic().generate_methods:
            groupbox = getattr(self._gui._gw, method_name + '_GroupBox')
            checkbox = getattr(self, 'checkbox_' + method_name)
            groupbox.setVisible(checkbox.isChecked())
            if checkbox.isChecked():
                self._gui._predefined_methods_to_show.append(method_name)

        self._gui._gw.hintLabel.setVisible(len(self._gui._predefined_methods_to_show) == 0)
        return
