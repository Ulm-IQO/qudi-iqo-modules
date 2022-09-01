# -*- coding: utf-8 -*-

"""
This file contains a Qudi gui module for quick plotting.

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

Completely reworked by Kay Jahnke, May 2020
"""

import functools
import numpy as np
from itertools import cycle
from qtpy import QtCore
from pyqtgraph import SignalProxy, mkColor

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.core.module import GuiBase

from qudi.util.widgets.fitting import FitConfigurationDialog
from .qdplot_main_window import QDPlotMainWindow
from .qdplot_plot_dockwidget import PlotDockWidget


class QDPlotterGui(GuiBase):
    """ GUI  for displaying up to 3 custom plots.
    The plots are held in tabified DockWidgets and can either be manipulated in the logic
    or by corresponding parameter DockWidgets.

    Example config for copy-paste:

    qdplotter:
        module.Class: 'qdplotter.qdplotter_gui.QDPlotterGui'
        pen_color_list: [[100, 100, 100], 'c', 'm', 'g']
        connect:
            qdplot_logic: 'qdplotlogic'
    """

    sigPlotParametersChanged = QtCore.Signal(int, dict)
    sigAutoRangeClicked = QtCore.Signal(int, bool, bool)
    sigDoFit = QtCore.Signal(str, int)
    sigRemovePlotClicked = QtCore.Signal(int)

    # declare connectors
    _qdplot_logic = Connector(interface='QDPlotLogic', name='qdplot_logic')
    # declare config options
    _pen_color_list = ConfigOption(name='pen_color_list', default=['b', 'y', 'm', 'g'])
    # declare status variables
    _widget_alignment = StatusVar(name='widget_alignment', default='tabbed')

    _allowed_colors = {'b', 'g', 'r', 'c', 'm', 'y', 'k', 'w'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._fit_config_dialog = None

        self._plot_dockwidgets = list()
        self._pen_colors = list()
        self._plot_curves = list()
        self._fit_curves = list()
        self._pg_signal_proxys = list()

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """
        if not isinstance(self._pen_color_list, (list, tuple)) or len(self._pen_color_list) < 1:
            self.log.warning(
                'The ConfigOption pen_color_list needs to be a list of strings but was "{0}".'
                ' Will use the following pen colors as default: {1}.'
                ''.format(self._pen_color_list, ['b', 'y', 'm', 'g']))
            self._pen_color_list = ['b', 'y', 'm', 'g']
        else:
            self._pen_color_list = list(self._pen_color_list)

        for index, color in enumerate(self._pen_color_list):
            if (isinstance(color, (list, tuple)) and len(color) == 3) or \
                    (isinstance(color, str) and color in self._allowed_colors):
                pass
            else:
                self.log.warning('The color was "{0}" but needs to be from this list: {1} '
                                 'or a 3 element tuple with values from 0 to 255 for RGB.'
                                 ' Setting color to "b".'.format(color, self._allowed_colors))
                self._pen_color_list[index] = 'b'

        # Use the inherited class 'QDPlotMainWindow' to create the GUI window
        self._mw = QDPlotMainWindow()

        logic = self._qdplot_logic()

        self._fit_config_dialog = FitConfigurationDialog(parent=self._mw,
                                                         fit_config_model=logic.fit_config_model)

        # Connect the main window restore view actions
        self._mw.action_show_fit_configuration.triggered.connect(self._fit_config_dialog.show)
        self._mw.action_new_plot.triggered.connect(logic.add_plot, QtCore.Qt.QueuedConnection)
        self._mw.action_restore_tabbed_view.triggered.connect(self.restore_tabbed_view,
                                                              QtCore.Qt.QueuedConnection)
        self._mw.action_restore_side_by_side_view.triggered.connect(self.restore_side_by_side_view,
                                                                    QtCore.Qt.QueuedConnection)
        self._mw.action_restore_arced_view.triggered.connect(self.restore_arc_view,
                                                             QtCore.Qt.QueuedConnection)
        self._mw.action_save_all.triggered.connect(self._save_all_clicked,
                                                   QtCore.Qt.QueuedConnection)

        # Initialize dock widgets
        self._plot_dockwidgets = list()
        self._pen_colors = list()
        self._plot_curves = list()
        self._fit_curves = list()
        self.update_number_of_plots(logic.number_of_plots)

        # Update all plot parameters and data from logic
        for index, _ in enumerate(self._plot_dockwidgets):
            self.update_data(index)
            self.update_fit_data(index)
            self.update_plot_parameters(index)
        self.restore_view()

        # Connect signal to logic
        self.sigPlotParametersChanged.connect(logic.update_plot_parameters,
                                              QtCore.Qt.QueuedConnection)
        self.sigAutoRangeClicked.connect(logic.update_auto_range, QtCore.Qt.QueuedConnection)
        self.sigDoFit.connect(logic.do_fit, QtCore.Qt.QueuedConnection)
        self.sigRemovePlotClicked.connect(logic.remove_plot, QtCore.Qt.QueuedConnection)

        # Connect signals from logic
        logic.sigPlotDataUpdated.connect(self.update_data, QtCore.Qt.QueuedConnection)
        logic.sigPlotParamsUpdated.connect(self.update_plot_parameters, QtCore.Qt.QueuedConnection)
        logic.sigPlotNumberChanged.connect(self.update_number_of_plots, QtCore.Qt.QueuedConnection)
        logic.sigFitUpdated.connect(self.update_fit_data, QtCore.Qt.QueuedConnection)

        self.show()

    def show(self):
        """ Make window visible and put it above all other windows. """
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def on_deactivate(self):
        """ Deactivate the module """
        # disconnect fit
        self._mw.action_show_fit_configuration.triggered.disconnect()

        # Connect the main window restore view actions
        self._mw.action_new_plot.triggered.disconnect()
        self._mw.action_restore_tabbed_view.triggered.disconnect()
        self._mw.action_restore_side_by_side_view.triggered.disconnect()
        self._mw.action_restore_arced_view.triggered.disconnect()
        self._mw.action_save_all.triggered.disconnect()

        # Disconnect signal to logic
        self.sigPlotParametersChanged.disconnect()
        self.sigAutoRangeClicked.disconnect()
        self.sigDoFit.disconnect()
        self.sigRemovePlotClicked.disconnect()

        # Disconnect signals from logic
        logic = self._qdplot_logic()
        logic.sigPlotDataUpdated.disconnect(self.update_data)
        logic.sigPlotParamsUpdated.disconnect(self.update_plot_parameters)
        logic.sigPlotNumberChanged.disconnect(self.update_number_of_plots)
        logic.sigFitUpdated.disconnect(self.update_fit_data)

        # disconnect GUI elements
        self.update_number_of_plots(0)

        self._fit_config_dialog.close()
        self._mw.close()

    def update_number_of_plots(self, count):
        """ Adjust number of QDockWidgets to current number of plots. Does NO initialization of the
        contents.

        @param int count: Number of plots to display.
        """
        # Remove dock widgets if plot count decreased
        while count < len(self._plot_dockwidgets):
            index = len(self._plot_dockwidgets) - 1
            self._disconnect_plot_signals(index)
            dockwidget = self._plot_dockwidgets.pop(-1)
            dockwidget.setParent(None)
            del self._plot_curves[-1]
            del self._fit_curves[-1]
            del self._pen_colors[-1]

        # Add dock widgets if plot count increased
        while count > len(self._plot_dockwidgets):
            index = len(self._plot_dockwidgets)
            dockwidget = PlotDockWidget(fit_container=self._qdplot_logic().fit_container,
                                        plot_number=index + 1)
            self._plot_dockwidgets.append(dockwidget)
            self._pen_colors.append(cycle(self._pen_color_list))
            self._plot_curves.append(list())
            self._fit_curves.append(list())
            self._connect_plot_signals(index)
            self.restore_view()

    def _connect_plot_signals(self, index):
        widget = self._plot_dockwidgets[index].widget()
        widget.sigLimitsChanged.connect(
            lambda x, y: self.sigPlotParametersChanged.emit(index, {'x_limits': x, 'y_limits': y})
        )
        widget.sigLabelsChanged.connect(
            lambda x, y: self.sigPlotParametersChanged.emit(index, {'x_label': x, 'y_label': y})
        )
        widget.sigUnitsChanged.connect(
            lambda x, y: self.sigPlotParametersChanged.emit(index, {'x_unit': x, 'y_unit': y})
        )
        widget.sigAutoRangeClicked.connect(lambda x, y: self.sigAutoRangeClicked.emit(index, x, y))
        widget.sigSaveClicked.connect(functools.partial(self._save_clicked, index))
        widget.sigRemoveClicked.connect(lambda: self.sigRemovePlotClicked.emit(index))
        widget.sigFitClicked.connect(functools.partial(self._fit_clicked, index))

    def _disconnect_plot_signals(self, index):
        widget = self._plot_dockwidgets[index].widget()
        widget.sigLimitsChanged.disconnect()
        widget.sigLabelsChanged.disconnect()
        widget.sigUnitsChanged.disconnect()
        widget.sigAutoRangeClicked.disconnect()
        widget.sigSaveClicked.disconnect()
        widget.sigRemoveClicked.disconnect()
        widget.sigFitClicked.disconnect()

    @property
    def pen_color_list(self):
        return self._pen_color_list.copy()

    @pen_color_list.setter
    def pen_color_list(self, value):
        if not isinstance(value, (list, tuple)) or len(value) < 1:
            self.log.warning(
                'The parameter pen_color_list needs to be a list of strings but was "{0}".'
                ' Will use the following old pen colors: {1}.'
                ''.format(value, self._pen_color_list))
            return
        for index, color in enumerate(self._pen_color_list):
            if (isinstance(color, (list, tuple)) and len(color) == 3) or \
                    (isinstance(color, str) and color in self._allowed_colors):
                pass
            else:
                self.log.warning('The color was "{0}" but needs to be from this list: {1} '
                                 'or a 3 element tuple with values from 0 to 255 for RGB.'
                                 ''.format(color, self._allowed_colors))
                return
        else:
            self._pen_color_list = list(value)

    def restore_side_by_side_view(self):
        """ Restore the arrangement of DockWidgets to the default """
        self.restore_view(alignment='side_by_side')

    def restore_arc_view(self):
        """ Restore the arrangement of DockWidgets to the default """
        self.restore_view(alignment='arc')

    def restore_tabbed_view(self):
        """ Restore the arrangement of DockWidgets to the default """
        self.restore_view(alignment='tabbed')

    def restore_view(self, alignment=None):
        """ Restore the arrangement of DockWidgets to the default """

        if alignment is None:
            alignment = self._widget_alignment
        if alignment not in ('side_by_side', 'arc', 'tabbed'):
            alignment = 'tabbed'
        self._widget_alignment = alignment

        self._mw.setDockNestingEnabled(True)

        for i, dockwidget in enumerate(self._plot_dockwidgets):
            widget = dockwidget.widget()
            widget.toggle_fit(False)
            widget.toggle_editor(False)
            dockwidget.show()
            dockwidget.setFloating(False)
            if alignment == 'tabbed':
                self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)
                if i > 0:
                    self._mw.tabifyDockWidget(self._plot_dockwidgets[0], dockwidget)
            elif alignment == 'arc':
                mod = i % 3
                if mod == 0:
                    self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)
                    if i > 2:
                        self._mw.tabifyDockWidget(self._plot_dockwidgets[0], dockwidget)
                elif mod == 1:
                    self._mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dockwidget)
                    if i > 2:
                        self._mw.tabifyDockWidget(self._plot_dockwidgets[1], dockwidget)
                elif mod == 2:
                    self._mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dockwidget)
                    if i > 2:
                        self._mw.tabifyDockWidget(self._plot_dockwidgets[2], dockwidget)
            elif alignment == 'side_by_side':
                self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)
        if alignment == 'arc':
            if len(self._plot_dockwidgets) > 2:
                self._mw.resizeDocks([self._plot_dockwidgets[1], self._plot_dockwidgets[2]],
                                     [1, 1],
                                     QtCore.Qt.Horizontal)
        elif alignment == 'side_by_side':
            self._mw.resizeDocks(
                self._plot_dockwidgets, [1] * len(self._plot_dockwidgets), QtCore.Qt.Horizontal)

    def update_data(self, plot_index, x_data=None, y_data=None, data_labels=None):
        """ Function creates empty plots, grabs the data and sends it to them. """
        if not (0 <= plot_index < len(self._plot_dockwidgets)):
            self.log.warning('Tried to update plot with invalid index {0:d}'.format(plot_index))
            return

        logic = self._qdplot_logic()
        if x_data is None:
            x_data = logic.get_x_data(plot_index)
        if y_data is None:
            y_data = logic.get_y_data(plot_index)
        if data_labels is None:
            data_labels = logic.get_data_labels(plot_index)

        widget = self._plot_dockwidgets[plot_index].widget()
        widget.plot_widget.clear()

        self._pen_colors[plot_index] = cycle(self._pen_color_list)
        self._plot_curves[plot_index] = list()
        self._fit_curves[plot_index] = list()

        for line, xd in enumerate(x_data):
            yd = y_data[line]
            pen_color = next(self._pen_colors[plot_index])
            self._plot_curves[plot_index].append(
                widget.plot_widget.plot(
                    pen=mkColor(pen_color),
                    symbol='d',
                    symbolSize=6,
                    symbolBrush=mkColor(pen_color),
                    name=data_labels[line]
                )
            )
            self._plot_curves[plot_index][-1].setData(x=xd, y=yd)
            self._fit_curves[plot_index].append(widget.plot_widget.plot())
            self._fit_curves[plot_index][-1].setPen('r')

    def update_plot_parameters(self, plot_index, params=None):
        """ Function updated limits, labels and units in the plot and parameter widgets. """
        if not (0 <= plot_index < len(self._plot_dockwidgets)):
            self.log.warning('Tried to update plot with invalid index {0:d}'.format(plot_index))
            return

        logic = self._qdplot_logic()
        widget = self._plot_dockwidgets[plot_index].widget()
        if params is None:
            params = {'x_label' : logic.get_x_label(plot_index),
                      'y_label' : logic.get_y_label(plot_index),
                      'x_unit'  : logic.get_x_unit(plot_index),
                      'y_unit'  : logic.get_y_unit(plot_index),
                      'x_limits': logic.get_x_limits(plot_index),
                      'y_limits': logic.get_y_limits(plot_index)}
        # Update labels
        widget.set_labels(x_label=params.get('x_label', None), y_label=params.get('y_label', None))
        # Update units
        widget.set_units(x_unit=params.get('x_unit', None), y_unit=params.get('y_unit', None))
        # Update limits
        widget.set_limits(x_limits=params.get('x_limits', None),
                          y_limits=params.get('y_limits', None))

    def _save_clicked(self, plot_index):
        """ Handling the save button to save the data into a file. """
        self._qdplot_logic().save_data(plot_index=plot_index)

    def _save_all_clicked(self):
        """ Handling the save button to save the data into a file. """
        for dockwidget in self._plot_dockwidgets:
            dockwidget.widget().control_widget.save_button.click()

    def _fit_clicked(self, plot_index=0, fit_function='No Fit'):
        """ Triggers the fit to be done. Attention, this runs in the GUI thread. """
        self.sigDoFit.emit(fit_function, plot_index)

    def update_fit_data(self, plot_index, fit_data=None, formatted_fitresult=None, fit_method=None):
        """ Function that handles the fit results received from the logic via a signal.

        @param int plot_index: index of the plot the fit was performed for in the range for 0 to 2
        @param 3-dimensional np.ndarray fit_data: the fit data in a 2-d array for each data set
        @param str formatted_fitresult: string containing the parameters already formatted
        @param str fit_method: the fit_method used
        """
        widget = self._plot_dockwidgets[plot_index].widget()

        if fit_data is None or formatted_fitresult is None or fit_method is None:
            fit_data, formatted_fitresult, fit_method = self._qdplot_logic().get_fit_data(plot_index)

        if not fit_method:
            fit_method = 'No Fit'

        widget.toggle_fit(True)

        if fit_method == 'No Fit':
            for index, curve in enumerate(self._fit_curves[plot_index]):
                if curve in widget.plot_widget.items():
                    widget.plot_widget.removeItem(curve)
        else:
            widget.fit_widget.result_label.setText(formatted_fitresult)
            for index, curve in enumerate(self._fit_curves[plot_index]):
                if curve not in widget.plot_widget.items():
                    widget.plot_widget.addItem(curve)
                curve.setData(x=fit_data[index][0], y=fit_data[index][1])
