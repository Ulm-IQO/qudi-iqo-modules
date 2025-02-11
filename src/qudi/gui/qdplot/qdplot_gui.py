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
"""

__all__ = ['PlotAlignment', 'QDPlotterGui']

import numpy as np
from enum import Enum
from functools import partial
from itertools import cycle
from PySide2 import QtCore, QtGui
from pyqtgraph import mkColor
from typing import Optional, Mapping, Sequence, Union, Tuple, List
from lmfit.model import ModelResult as _ModelResult

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.core.module import GuiBase

from qudi.util.widgets.fitting import FitConfigurationDialog
from qudi.util.colordefs import QudiPalette
from qudi.gui.qdplot.main_window import QDPlotMainWindow
from qudi.gui.qdplot.plot_widget import QDPlotDockWidget
from qudi.logic.qdplot_logic import QDPlotConfig


class PlotAlignment(Enum):
    tabbed = 0
    side_by_side = 1
    arc = 2


class QDPlotterGui(GuiBase):
    """ GUI  for displaying up to 3 custom plots.
    The plots are held in tabified DockWidgets and can either be manipulated in the logic
    or by corresponding parameter DockWidgets.

    Example config for copy-paste:

    qdplotter:
        module.Class: 'qdplot.qdplot_gui.QDPlotterGui'
        options:
            pen_color_list: [[100, 100, 100], 'c', 'm', 'g']
        connect:
            qdplot_logic: 'qdplotlogic'
    """

    sigPlotConfigChanged = QtCore.Signal(int, object)     # plot_index, QDPlotConfig
    sigAutoRangeClicked = QtCore.Signal(int, bool, bool)  # plot_index, scale_x, scale_y
    sigDoFit = QtCore.Signal(int, str)                    # plot_index, fit_config_name
    sigRemovePlotClicked = QtCore.Signal(int)             # plot_index
    sigSaveData = QtCore.Signal(int, str)                 # plot index, postfix_string

    # declare connectors
    _qdplot_logic = Connector(interface='QDPlotLogic', name='qdplot_logic')

    # declare config options
    _default_pen_color_list = ConfigOption(name='pen_color_list', default=None)

    # declare status variables
    _widget_alignment = StatusVar(name='widget_alignment', default='tabbed')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._mw = None
        self._fit_config_dialog = None

        self._pen_color_list = list()

        self._plot_dockwidgets = list()
        self._color_cyclers = list()

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """
        try:
            if self._default_pen_color_list is not None:
                self._default_pen_color_list = list(self._default_pen_color_list)
                if len(self._default_pen_color_list) < 1:
                    self.log.warning('ConfigOption pen_color_list is empty. '
                                     'Falling back to qudi default palette.')
                    self._default_pen_color_list = None
        except TypeError:
            self.log.warning(
                f'ConfigOption pen_color_list needs to be an iterable of color definitions. '
                f'Got a non-iterable type instead ({type(self._default_pen_color_list)}) and '
                f'falling back to qudi default palette.'
            )
            self._default_pen_color_list = None

        try:
            self.pen_color_list = self._default_pen_color_list
        except:
            self.log.warning(
                'The ConfigOption pen_color_list needs to be a list of items accepted by '
                'pyqtgraph.mkColor(). Falling back to qudi default palette'
            )
            self.pen_color_list = None

        # Use the inherited class 'QDPlotMainWindow' to create the GUI window
        self._mw = QDPlotMainWindow()

        logic = self._qdplot_logic()

        self._fit_config_dialog = FitConfigurationDialog(parent=self._mw,
                                                         fit_config_model=logic.fit_config_model)

        # Connect the main window restore view actions
        self._mw.action_show_fit_configuration.triggered.connect(self._fit_config_dialog.show)
        self._mw.action_restore_tabbed_view.triggered.connect(self.restore_tabbed_view)
        self._mw.action_restore_side_by_side_view.triggered.connect(self.restore_side_by_side_view)
        self._mw.action_restore_arced_view.triggered.connect(self.restore_arc_view)
        self._mw.action_save_all.triggered.connect(self._save_all_clicked)
        self._mw.action_new_plot.triggered.connect(logic.add_plot, QtCore.Qt.QueuedConnection)

        # Initialize dock widgets
        self._plot_dockwidgets = list()
        self._color_cyclers = list()

        # Connect signal to logic
        self.sigPlotConfigChanged.connect(logic.set_plot_config, QtCore.Qt.QueuedConnection)
        self.sigAutoRangeClicked.connect(logic.set_auto_limits, QtCore.Qt.QueuedConnection)
        self.sigDoFit.connect(logic.do_fit, QtCore.Qt.QueuedConnection)
        self.sigRemovePlotClicked.connect(logic.remove_plot, QtCore.Qt.QueuedConnection)
        self.sigSaveData.connect(logic.save_data, QtCore.Qt.BlockingQueuedConnection)

        # Connect signals from logic
        logic.sigPlotDataChanged.connect(self._update_data, QtCore.Qt.QueuedConnection)
        logic.sigPlotConfigChanged.connect(self._update_plot_config, QtCore.Qt.QueuedConnection)
        logic.sigPlotAdded.connect(self._plot_added, QtCore.Qt.QueuedConnection)
        logic.sigPlotRemoved.connect(self._plot_removed, QtCore.Qt.QueuedConnection)
        logic.sigFitChanged.connect(self._update_fit_data, QtCore.Qt.QueuedConnection)

        self._init_plots(logic.plot_count)

        self.show()
        self.restore_view()

    def show(self):
        """ Make window visible and put it above all other windows. """
        self._restore_window_geometry(self._mw)
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
        self.sigPlotConfigChanged.disconnect()
        self.sigAutoRangeClicked.disconnect()
        self.sigDoFit.disconnect()
        self.sigRemovePlotClicked.disconnect()

        # Disconnect signals from logic
        logic = self._qdplot_logic()
        logic.sigPlotDataChanged.disconnect(self._update_data)
        logic.sigPlotConfigChanged.disconnect(self._update_plot_config)
        logic.sigPlotAdded.disconnect(self._plot_added)
        logic.sigPlotRemoved.disconnect(self._plot_removed)
        logic.sigFitChanged.disconnect(self._update_fit_data)

        # disconnect GUI elements
        self._clear_plots()

        self._fit_config_dialog.close()
        self._save_window_geometry(self._mw)
        self._mw.close()

        self._fit_config_dialog = None
        self._mw = None

    @staticmethod
    @_widget_alignment.representer
    def __repr_widget_alignment(value: PlotAlignment) -> str:
        return value.name

    @staticmethod
    @_widget_alignment.constructor
    def __constr_widget_alignment(value: str) -> PlotAlignment:
        try:
            return PlotAlignment[value]
        except KeyError:
            return PlotAlignment(0)

    @property
    def pen_color_list(self) -> List[QtGui.QColor]:
        return self._pen_color_list.copy()

    @pen_color_list.setter
    def pen_color_list(self,
                       colors: Union[None, Sequence[str], Sequence[Tuple[int, int, int]]]
                       ) -> None:
        if (colors is None) or (len(colors) < 1):
            if self._default_pen_color_list is None:
                self._pen_color_list = [QudiPalette.c1,
                                        QudiPalette.c2,
                                        QudiPalette.c3,
                                        QudiPalette.c4,
                                        QudiPalette.c5,
                                        QudiPalette.c6]
            else:
                self._pen_color_list = self._default_pen_color_list.copy()
        else:
            self._pen_color_list = [mkColor(c) for c in colors]

    def _clear_plots(self) -> None:
        while len(self._plot_dockwidgets) > 0:
            self._plot_removed(-1)

    def _init_plots(self, plot_count: int) -> None:
        while len(self._plot_dockwidgets) < plot_count:
            self._plot_added()

    ##########################
    # GUI callback slots below
    ##########################

    def restore_side_by_side_view(self) -> None:
        """ Restore the arrangement of DockWidgets to the default """
        self._widget_alignment = PlotAlignment.side_by_side
        self._mw.setDockNestingEnabled(True)
        for ii, dockwidget in enumerate(self._plot_dockwidgets):
            widget = dockwidget.widget()
            widget.toggle_fit(widget.show_fit)
            widget.toggle_editor(False)
            dockwidget.show()
            dockwidget.setFloating(False)
            self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)
        self._mw.resizeDocks(self._plot_dockwidgets,
                             [1] * len(self._plot_dockwidgets),
                             QtCore.Qt.Horizontal)

    def restore_arc_view(self) -> None:
        """ Restore the arrangement of DockWidgets to the default """
        self._widget_alignment = PlotAlignment.arc
        self._mw.setDockNestingEnabled(True)
        for ii, dockwidget in enumerate(self._plot_dockwidgets):
            widget = dockwidget.widget()
            widget.toggle_fit(widget.show_fit)
            widget.toggle_editor(False)
            dockwidget.show()
            dockwidget.setFloating(False)
            mod = ii % 3
            if mod == 0:
                self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)
                if ii > 2:
                    self._mw.tabifyDockWidget(self._plot_dockwidgets[0], dockwidget)
            elif mod == 1:
                self._mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dockwidget)
                if ii > 2:
                    self._mw.tabifyDockWidget(self._plot_dockwidgets[1], dockwidget)
            elif mod == 2:
                self._mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, dockwidget)
                if ii > 2:
                    self._mw.tabifyDockWidget(self._plot_dockwidgets[2], dockwidget)
        try:
            resize_docks = [self._plot_dockwidgets[1], self._plot_dockwidgets[2]]
        except IndexError:
            pass
        else:
            self._mw.resizeDocks(resize_docks, [1, 1], QtCore.Qt.Horizontal)

    def restore_tabbed_view(self) -> None:
        """ Restore the arrangement of DockWidgets to the default """
        self._widget_alignment = PlotAlignment.tabbed
        self._mw.setDockNestingEnabled(True)
        for ii, dockwidget in enumerate(self._plot_dockwidgets):
            widget = dockwidget.widget()
            widget.toggle_fit(widget.show_fit)
            widget.toggle_editor(False)
            dockwidget.show()
            dockwidget.setFloating(False)
            self._mw.addDockWidget(QtCore.Qt.TopDockWidgetArea, dockwidget)
            if ii > 0:
                self._mw.tabifyDockWidget(self._plot_dockwidgets[0], dockwidget)
        try:
            self._plot_dockwidgets[0].raise_()
        except IndexError:
            pass

    def restore_view(self, alignment: Optional[Union[str, int, PlotAlignment]] = None) -> None:
        """ Restore the arrangement of DockWidgets to the default """
        if alignment is None:
            alignment = self._widget_alignment
        elif isinstance(alignment, str):
            try:
                alignment = PlotAlignment[alignment]
            except KeyError as err:
                raise ValueError(f'Invalid view alignment specifier "{alignment}"') from err
        elif isinstance(alignment, int):
            alignment = PlotAlignment(alignment)
        else:
            alignment = PlotAlignment(alignment.value)

        if not isinstance(alignment, PlotAlignment):
            raise TypeError('plot view alignment must be PlotAlignment Enum or valid name str')

        if alignment == PlotAlignment.tabbed:
            return self.restore_tabbed_view()
        elif alignment == PlotAlignment.arc:
            return self.restore_arc_view()
        elif alignment == PlotAlignment.side_by_side:
            return self.restore_side_by_side_view()

    def _plot_config_changed(self, dockwidget: QDPlotDockWidget) -> None:
        try:
            plot_index = self._plot_dockwidgets.index(dockwidget)
        except ValueError:
            return
        widget = dockwidget.widget()
        config = QDPlotConfig(labels=widget.labels, units=widget.units, limits=widget.limits)
        self.sigPlotConfigChanged.emit(plot_index, config)

    def _auto_range_clicked(self,
                            dockwidget: QDPlotDockWidget,
                            x: Optional[bool] = None,
                            y: Optional[bool] = None) -> None:
        try:
            plot_index = self._plot_dockwidgets.index(dockwidget)
        except ValueError:
            return
        self.sigAutoRangeClicked.emit(plot_index, x, y)

    def _save_clicked(self, dockwidget: QDPlotDockWidget) -> None:
        self._plot_config_changed(dockwidget)
        try:
            plot_index = self._plot_dockwidgets.index(dockwidget)
        except ValueError:
            return
        self.sigSaveData.emit(plot_index, '')

    def _save_all_clicked(self) -> None:
        for dockwidget in self._plot_dockwidgets:
            self._save_clicked(dockwidget)

    def _remove_clicked(self, dockwidget: QDPlotDockWidget) -> None:
        try:
            plot_index = self._plot_dockwidgets.index(dockwidget)
        except ValueError:
            return
        self.sigRemovePlotClicked.emit(plot_index)

    def _fit_clicked(self, dockwidget: QDPlotDockWidget, fit_config: str) -> None:
        try:
            plot_index = self._plot_dockwidgets.index(dockwidget)
        except ValueError:
            return
        self.sigDoFit.emit(plot_index, fit_config)

    ##########################
    # Logic update slots below
    ##########################

    def _plot_added(self) -> None:
        index = len(self._plot_dockwidgets)
        dockwidget = QDPlotDockWidget(fit_container=self._qdplot_logic().get_fit_container(index),
                                      plot_number=index + 1,
                                      show_fit=False)
        self._plot_dockwidgets.append(dockwidget)
        self._color_cyclers.append(cycle(self._pen_color_list))

        widget = dockwidget.widget()
        widget.sigPlotParametersChanged.connect(partial(self._plot_config_changed, dockwidget))
        widget.sigAutoLimitsApplied.connect(partial(self._auto_range_clicked, dockwidget))
        widget.sigSaveClicked.connect(partial(self._save_clicked, dockwidget))
        widget.sigRemoveClicked.connect(partial(self._remove_clicked, dockwidget))
        widget.sigFitClicked.connect(partial(self._fit_clicked, dockwidget))

        # Update infos from logic
        self._update_data(index)
        self._update_fit_data(index)
        self._update_plot_config(index)

        self.restore_view()

    def _plot_removed(self, plot_index: int) -> None:
        try:
            dockwidget = self._plot_dockwidgets.pop(plot_index)
            del self._color_cyclers[plot_index]
        except IndexError:
            return
        dockwidget.close()
        dockwidget.setParent(None)
        dockwidget.deleteLater()
        widget = dockwidget.widget()
        widget.sigPlotParametersChanged.disconnect()
        widget.sigAutoLimitsApplied.disconnect()
        widget.sigSaveClicked.disconnect()
        widget.sigRemoveClicked.disconnect()
        widget.sigFitClicked.disconnect()
        # Update dockwidget titles for higher indices
        trailing_dockwidgets = self._plot_dockwidgets[plot_index:]
        start_index = len(self._plot_dockwidgets) - len(trailing_dockwidgets) + 1
        for ii, dockwidget in enumerate(trailing_dockwidgets, start_index):
            dockwidget.setWindowTitle(f'Plot {ii:d}')

    def _update_data(self,
                     plot_index: int,
                     data: Optional[Mapping[str, np.ndarray]] = None
                     ) -> None:
        """ Function creates empty plots, grabs the data and sends it to them. """
        if data is None:
            data = self._qdplot_logic().get_data(plot_index)

        widget = self._plot_dockwidgets[plot_index].widget()
        current_plots = set(widget.plot_names)
        removed_plots = current_plots.difference(data)
        # Clear the entire plot and redraw all curves if a curve has been removed
        if removed_plots:
            widget.clear()
            current_plots.clear()
            color_cycler = cycle(self._pen_color_list)
        else:
            color_cycler = self._color_cyclers[plot_index]

        try:
            for name, (x_data, y_data) in data.items():
                if name in current_plots:
                    widget.set_data(name, x=x_data, y=y_data)
                else:
                    color = next(color_cycler)
                    widget.plot(x=x_data,
                                y=y_data,
                                pen=color,
                                symbol='d',
                                symbolSize=6,
                                symbolBrush=color,
                                name=name)
        finally:
            self._color_cyclers[plot_index] = color_cycler

    def _update_plot_config(self,
                            plot_index: int,
                            config: Optional[QDPlotConfig] = None
                            ) -> None:
        """ Function updated limits, labels and units in the plot and parameter widgets. """
        if config is None:
            config = self._qdplot_logic().get_plot_config(plot_index)
        widget = self._plot_dockwidgets[plot_index].widget()
        widget.blockSignals(True)
        try:
            widget.set_labels(*config.labels)
            widget.set_units(*config.units)
            widget.set_limits(*config.limits)
        finally:
            widget.blockSignals(False)

    def _update_fit_data(self,
                         plot_index: int,
                         fit_config: Optional[str] = None,
                         fit_results: Optional[Mapping[str, Union[None, _ModelResult]]] = None
                         ) -> None:
        """ Function that handles the fit results received from the logic via a signal """
        if fit_config is None or fit_results is None:
            fit_config, fit_results = self._qdplot_logic().get_fit_results(plot_index)
        if not fit_config:
            fit_config = 'No Fit'

        widget = self._plot_dockwidgets[plot_index].widget()

        if fit_config == 'No Fit':
            widget.clear_fits()
        else:
            try:
                fit_data = {
                    name: None if result is None else result.high_res_best_fit for name, result in
                    fit_results.items()
                }
            except AttributeError:
                fit_data = {name: None for name in fit_results}

            widget.toggle_fit(True)
            for name, data in fit_data.items():
                try:
                    x_data, y_data = data
                except TypeError:
                    widget.remove_fit_plot(name)
                else:
                    widget.set_fit_data(name=name,
                                        x=x_data,
                                        y=y_data,
                                        pen='r')
