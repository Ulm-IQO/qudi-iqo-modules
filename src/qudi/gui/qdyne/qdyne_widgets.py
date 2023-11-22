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
import os
from dataclasses import dataclass, fields
from PySide2 import QtCore, QtWidgets
from qudi.util import uic
from qudi.util.helpers import natural_sort
from qudi.util.widgets.scientific_spinbox import ScienDSpinBox, ScienSpinBox
from enum import Enum
import numpy as np

class DataclassWidget(QtWidgets.QWidget):
    def __init__(self, dataclass_obj: dataclass) -> None:
        super().__init__()
        self.data = dataclass_obj
        self.layout = None
        self.labels = dict()
        self.widgets = dict()
        self.init_UI()

    def init_UI(self):
        self.create_layout()
        self.set_widgets(self.data)
        self.setLayout(self.layout)

    def create_layout(self):
        self.layout = QtWidgets.QGridLayout()

    def set_widgets(self, data):
        param_index = 0
        for field in fields(data):
            label = self.create_label(field.name)
            widget = self.create_widget(field)
            if widget is None:
                continue
            widget.setMinimumSize(QtCore.QSize(80, 0))

            self.layout.addWidget(label, 0, param_index + 1, 1, 1)
            self.layout.addWidget(widget, 1, param_index + 1, 1, 1)

            self.labels[field.name] = label
            self.widgets[field.name] = widget
            param_index += 1

    def create_widget(self, field):
        widget = None
        value = getattr(self.data, field.name)

        if field.type == int:
            widget = self.int_to_widget(field, value)
        elif field.type == float:
            widget = self.float_to_widget(field, value)
        elif field.type == str:
            widget = self.str_to_widget(field, value)
        elif field.type == bool:
            widget = self.bool_to_widget(field, value)
        else:
            print('failed to convert dataclass')
            return None

        widget.setObjectName(field.name + '_widget')
        widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        return widget

    def create_label(self, name):
        label = QtWidgets.QLabel()
        label.setText(name)
        label.setObjectName(name + '_label')
        return label

    def int_to_widget(self, field, value):
        widget = ScienSpinBox()
        widget.setValue(value)
        widget.valueChanged.connect(lambda value, f=field: self.update_param(f, value))
        return widget

    def float_to_widget(self, field, value):
        widget = ScienDSpinBox()
        widget.setValue(value)
        widget.valueChanged.connect(lambda value, f=field: self.update_param(f, value))
        return widget

    def str_to_widget(self, field, value):
        widget = QtWidgets.QLineEdit()
        widget.setText(value)
        widget.editingFinished.connect(lambda value, f=field: self.update_param(f, value))
        return widget

    def bool_to_widget(self, field, value):
        widget = QtWidgets.QCheckBox()
        widget.setChecked(value)
        widget.stateChanged.connect(lambda value, f=field: self.update_param(f, value))
        return widget

    def update_param(self, field, value):
        setattr(self.data, field.name, value)

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
    def connect_signals(self):
        self.action_Predefined_Methods_Config.triggered.connect(self._gui._gsw.show_predefined_methods_config)

    def disconnect_signals(self):
        self.action_Predefined_Methods_Config.triggered.disconnect()

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

    def connect_signals(self):
        pass

    def disconnect_signals(self):
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
        # disable loading loading_indicator
        self.loading_indicator.setVisible(False)
        self.loaded_asset_updated(*self._gui.logic().pulsedmasterlogic().loaded_asset)
        # Dynamically create GUI elements for global parameters
        self._channel_selection_comboboxes = list()  # List of created channel selection ComboBoxes
        self._global_param_widgets = list()  # List of all other created global parameter widgets
        self._create_pm_global_params()
        self.generation_parameters_updated(self._gui.logic().pulsedmasterlogic().generation_parameters)
        self.measurement_settings_updated(self._gui.logic().pulsedmasterlogic().measurement_settings)
        self.fast_counter_settings_updated(self._gui.logic().pulsedmasterlogic().fast_counter_settings)

        # fill in the measurement parameter widgets
        self._pa_apply_hardware_constraints()

        # Dynamically create GUI elements for predefined methods
        self.gen_buttons = dict()
        self.samplo_buttons = dict()
        self.method_param_widgets = dict()
        self._create_predefined_methods()
        return

    def deactivate(self):
        pass

    def connect_signals(self):
        self._gui.logic().pulsedmasterlogic().sigPredefinedSequenceGenerated.connect(self.predefined_generated)
        self._gui.logic().pulsedmasterlogic().sigLoadedAssetUpdated.connect(self.predefined_generated)
        self._gui.logic().pulsedmasterlogic().sigLoadedAssetUpdated.connect(self.loaded_asset_updated)

        self._gui.logic().pulsedmasterlogic().sigSampleBlockEnsemble.connect(self.sampling_or_loading_busy)
        self._gui.logic().pulsedmasterlogic().sigLoadBlockEnsemble.connect(self.sampling_or_loading_busy)
        self._gui.logic().pulsedmasterlogic().sigLoadSequence.connect(self.sampling_or_loading_busy)
        self._gui.logic().pulsedmasterlogic().sigSampleSequence.connect(self.sampling_or_loading_busy)

        self.ana_param_invoke_settings_CheckBox.stateChanged.connect(self.measurement_settings_changed)
        self.ana_param_record_length_DoubleSpinBox.editingFinished.connect(self.fast_counter_settings_changed)
        self.ana_param_fc_bins_ComboBox.currentIndexChanged.connect(self.fast_counter_settings_changed)

        self._gui.logic().pulsedmasterlogic().sigFastCounterSettingsUpdated.connect(self.fast_counter_settings_updated)
        self._gui.logic().pulsedmasterlogic().sigMeasurementSettingsUpdated.connect(self.measurement_settings_updated)

    def disconnect_signals(self):
        self._gui.logic().pulsedmasterlogic().sigPredefinedSequenceGenerated.disconnect()
        self._gui.logic().pulsedmasterlogic().sigLoadedAssetUpdated.disconnect()
        self._gui.logic().pulsedmasterlogic().sigLoadedAssetUpdated.disconnect()

        self._gui.logic().pulsedmasterlogic().sigSampleBlockEnsemble.disconnect()
        self._gui.logic().pulsedmasterlogic().sigLoadBlockEnsemble.disconnect()
        self._gui.logic().pulsedmasterlogic().sigLoadSequence.disconnect()
        self._gui.logic().pulsedmasterlogic().sigSampleSequence.disconnect()

        self.ana_param_invoke_settings_CheckBox.stateChanged.disconnect()
        self.ana_param_record_length_DoubleSpinBox.editingFinished.disconnect()
        self.ana_param_fc_bins_ComboBox.currentIndexChanged.disconnect()

    def sampling_or_loading_busy(self):
        if self._gui.logic().pulsedmasterlogic().status_dict['sampload_busy']:
            self._gui._mainw.action_run_stop.setEnabled(False)

            self.loaded_asset_label.setText('  loading...')
            self.loading_indicator.setVisible(True)

    def loaded_asset_updated(self, asset_name, asset_type):
        """ Check the current loaded asset from the logic and update the display. """
        label = self.loaded_asset_label
        if not asset_name:
            label.setText('  No asset loaded')
        elif asset_type in ('PulseBlockEnsemble', 'PulseSequence'):
            label.setText('Currently loaded asset:  {0} ({1})'.format(asset_name, asset_type))
        else:
            label.setText('  Unknown asset type')
        return

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

    def predefined_generated(self, asset_name):
        # Enable all "GenSampLo" buttons in predefined methods tab if generation failed or
        # "sampload_busy" flag in PulsedMasterLogic status_dict is False.
        if asset_name is None or not self._gui.logic().pulsedmasterlogic().status_dict['sampload_busy']:
            for button in self.samplo_buttons.values():
                button.setEnabled(True)
            # Enable all "Generate" buttons in predefined methods tab
            for button in self.gen_buttons.values():
                button.setEnabled(True)

            self._gui._mainw.action_run_stop.setEnabled(True)
            self.loading_indicator.setVisible(False)

        return

    def generation_parameters_changed(self):
        """

        @return:
        """
        settings_dict = dict()
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
        return

    def generation_parameters_updated(self, settings_dict):
        """

        @param settings_dict:
        @return:
        """
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

        return

    @QtCore.Slot()
    def fast_counter_settings_changed(self):
        """

        @return:
        """
        if self._gui._mainw.action_run_stop.isChecked():
            return
        settings_dict = dict()
        settings_dict['record_length'] = self.ana_param_record_length_DoubleSpinBox.value()
        settings_dict['bin_width'] = float(self.ana_param_fc_bins_ComboBox.currentText())
        self._gui.logic().pulsedmasterlogic().set_fast_counter_settings(settings_dict)
        print(settings_dict)
        return

    @QtCore.Slot(dict)
    def fast_counter_settings_updated(self, settings_dict):
        """

        @param dict settings_dict:
        """

        print(settings_dict)
        # block signals
        self.ana_param_record_length_DoubleSpinBox.blockSignals(True)
        self.ana_param_fc_bins_ComboBox.blockSignals(True)
        # set widgets
        if 'record_length' in settings_dict:
            self.ana_param_record_length_DoubleSpinBox.setValue(settings_dict['record_length'])
        if 'bin_width' in settings_dict:
            index = self.ana_param_fc_bins_ComboBox.findText(str(settings_dict['bin_width']))
            self.ana_param_fc_bins_ComboBox.setCurrentIndex(index)
        if 'is_gated' in settings_dict:
            if settings_dict.get('is_gated'):
                self.toggle_global_param_enable("gate_channel", True)
            else:
                self.toggle_global_param_enable("gate_channel", False)
                self._gui.logic().pulsedmasterlogic().set_generation_parameters(gate_channel='')

        # unblock signals
        self.ana_param_record_length_DoubleSpinBox.blockSignals(False)
        self.ana_param_fc_bins_ComboBox.blockSignals(False)
        return

    def toggle_global_param_enable(self, name: str, enable: bool) -> None:
        for widget in self._global_param_widgets:
            if widget.objectName() == "global_param_" + name:
                widget.setEnabled(enable)

    @QtCore.Slot()
    def measurement_settings_changed(self):
        """

        @return:
        """
        # Do nothing if measurement is already running
        if self._gui._mainw.action_run_stop.isChecked():
            return

        settings_dict = dict()
        settings_dict['invoke_settings'] = self.ana_param_invoke_settings_CheckBox.isChecked()

        self._gui.logic().pulsedmasterlogic().set_measurement_settings(settings_dict)
        return

    @QtCore.Slot(dict)
    def measurement_settings_updated(self, settings_dict):
        """

        @param dict settings_dict:
        """
        # block signals
        self.ana_param_invoke_settings_CheckBox.blockSignals(True)

        # set widgets
        if 'invoke_settings' in settings_dict:
            self.ana_param_invoke_settings_CheckBox.setChecked(settings_dict['invoke_settings'])
            self.toggle_measurement_settings_editor(settings_dict['invoke_settings'])

        # unblock signals
        self.ana_param_invoke_settings_CheckBox.blockSignals(False)

    def toggle_measurement_settings_editor(self, hide_editor):
        """
        Shows or hides input widgets for measurement settings and fast counter settings
        """
        if hide_editor:
            self.ana_param_record_length_DoubleSpinBox.setEnabled(False)
        else:
            self.ana_param_record_length_DoubleSpinBox.setEnabled(True)
        return

    def _pa_apply_hardware_constraints(self):
        """
        Retrieve the constraints from pulser and fast counter hardware and apply these constraints
        to the analysis tab GUI elements.
        """
        fc_constraints = self._gui.logic().pulsedmasterlogic().fast_counter_constraints
        # block signals
        self.ana_param_fc_bins_ComboBox.blockSignals(True)
        # apply constraints
        self.ana_param_fc_bins_ComboBox.clear()
        for binwidth in fc_constraints['hardware_binwidth_list']:
            self.ana_param_fc_bins_ComboBox.addItem(str(binwidth))
        # unblock signals
        self.ana_param_fc_bins_ComboBox.blockSignals(False)
        return

class StateEstimationWidget(QtWidgets.QWidget):
    def __init__(self):
        self.estimator = None
        self.settings = None
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\state_estimation_widget.ui')

        # Load it
        super(StateEstimationWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self, estimator, settings):
        self.estimator = estimator
        self.settings = settings
        self._activate_widgets()

    def _activate_widgets(self):
        print(self.settings)
        self.se_method_comboBox.addItems(self.estimator.method_lists)
        self.se_settings_widget = DataclassWidget(self.settings)
        self.se_settings_gridLayout.addWidget(self.se_settings_widget)

    def deactivate(self):
        pass

    def connect_signals(self):
        pass

    def disconnect_signals(self):
        pass

class TimeTraceAnalysisWidget(QtWidgets.QWidget):
    # declare signals
    sigTTFileNameChanged = QtCore.Signal(str)
    sigLoadTT = QtCore.Signal()

    def __init__(self, gui):
        self._gui = gui

        self.analyzer = None
        self.settings = None

        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, r'ui\time_trace_analysis_widget.ui')

        # Load it
        super(TimeTraceAnalysisWidget, self).__init__()

        uic.loadUi(ui_file, self)

    def activate(self, analyzer, settings):
        self.analyzer = analyzer
        self.settings = settings
        self._activate_widgets()

    def _activate_widgets(self):
        self.tta_method_comboBox.addItems(self.analyzer.method_lists)
        self.tta_settings_widget = DataclassWidget(self.settings)
        self.tta_settings_gridLayout.addWidget(self.tta_settings_widget)

    def deactivate(self):
        pass

    def connect_signals(self):
        # connect control signals to logic
        self.sigTTFileNameChanged.connect(
            self._gui.logic().set_tt_filename, QtCore.Qt.QueuedConnection)
        self.sigLoadTT.connect(self._gui.logic().load_tt_from_file, QtCore.Qt.QueuedConnection)

        # connect update signals from logic
        self._gui.logic().sigTTFileNameUpdated.connect(self.update_tt_filename, QtCore.Qt.QueuedConnection)

        # connect internal signals
        self.tta_filename_LineEdit.editingFinished.connect(self.tt_filename_changed)
        self.tta_browsefile_PushButton.clicked.connect(self.browse_file)
        self.tta_loadfile_PushButton.clicked.connect(self.load_time_trace)

    def disconnect_signals(self):
        self.sigTTFileNameChanged.disconnect()
        self.sigLoadTT.disconnect()

        self._gui.logic().sigTTFileNameUpdated.disconnect()

        self.tta_filename_LineEdit.editingFinished.disconnect()
        self.tta_browsefile_PushButton.clicked.disconnect()
        self.tta_loadfile_PushButton.clicked.disconnect()

    @QtCore.Slot()
    def tt_filename_changed(self):
        self.sigTTFileNameChanged.emit(self.tta_filename_LineEdit.text())
        return

    @QtCore.Slot(str)
    def update_tt_filename(self, name):
        if name is None:
            name = ''
        self.tta_filename_LineEdit.blockSignals(True)
        self.tta_filename_LineEdit.setText(name)
        self.tta_filename_LineEdit.blockSignals(False)
        return

    @QtCore.Slot()
    def browse_file(self):
        """ Browse a saved time trace from file."""
        this_file = QtWidgets.QFileDialog.getOpenFileName(self._gui._mainw,
                                                          'Open time trace file',
                                                          self._gui.logic().module_default_data_dir,
                                                          'Data files (*.npz)')[0]
        if this_file:
            self.sigTTFileNameChanged.emit(this_file)
        return

    @QtCore.Slot()
    def load_time_trace(self):
        self.sigLoadTT.emit()
        return


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
        self.close()

    def connect_signals(self):
        # Connect signals used in predefined methods config dialog
        self.accepted.connect(self.apply_predefined_methods_config)
        self.rejected.connect(self.keep_former_predefined_methods_config)
        self.buttonBox.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(self.apply_predefined_methods_config)

    def disconnect_signals(self):
        self.accepted.disconnect()
        self.rejected.disconnect()
        self.buttonBox.button(QtWidgets.QDialogButtonBox.Apply).clicked.disconnect()

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
