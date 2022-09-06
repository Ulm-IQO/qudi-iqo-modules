# -*- coding: utf-8 -*-
"""
This file contains the Qudi QDPlotter logic class.

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

import numpy as np
import matplotlib.pyplot as plt
from PySide2 import QtCore
from lmfit.model import ModelResult as _ModelResult
from typing import Tuple, Optional, Sequence, Union, List, Dict, Any, Mapping

from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.util.helpers import is_integer
from qudi.core.module import LogicBase
from qudi.util.datastorage import NpyDataStorage, TextDataStorage
from qudi.util.datafitting import FitContainer, FitConfigurationsModel


class QDPlotConfig:
    """
    """

    def __init__(self,
                 labels: Optional[Tuple[str]] = None,
                 units: Optional[Tuple[str]] = None,
                 limits: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None,
                 data: Optional[Sequence[Tuple[Sequence[float], Sequence[float]]]] = None,
                 data_labels: Optional[Sequence[str]] = None,
                 auto_padding: Optional[float] = None
                 ) -> None:
        self._labels = ('X', 'Y')
        self._units = ('arb.u.', 'arb.u.')
        self._limits = ((-0.5, 0.5), (-0.5, 0.5))
        self._data = list()
        self._data_labels = list()
        self.auto_padding = 0.02 if auto_padding is None else float(auto_padding)
        if labels is not None:
            self.set_labels(*labels)
        if units is not None:
            self.set_units(*units)
        if limits is not None:
            self.set_limits(*limits)
        if data is None:
            self.add_data(data=np.zeros((2, 1)))
        else:
            for index, dataset in enumerate(data):
                try:
                    label = data_labels[index]
                except (IndexError, TypeError, AttributeError):
                    label = None
                self.add_data(dataset, label=label)

    @property
    def dataset_count(self) -> int:
        return len(self._data_labels)

    @property
    def labels(self) -> Tuple[str, str]:
        return self._labels

    @property
    def units(self) -> Tuple[str, str]:
        return self._units

    @property
    def limits(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        return self._limits

    @property
    def data(self) -> List[np.ndarray]:
        return self._data.copy()

    @property
    def data_labels(self) -> List[str]:
        return self._data_labels.copy()

    def set_labels(self, x: Optional[str] = None, y: Optional[str] = None) -> None:
        self._labels = (self._labels[0] if x is None else str(x),
                        self._labels[1] if y is None else str(y))

    def set_units(self, x: Optional[str] = None, y: Optional[str] = None) -> None:
        self._units = (self._units[0] if x is None else str(x),
                       self._units[1] if y is None else str(y))

    def set_limits(self,
                   x: Optional[Tuple[float, float]] = None,
                   y: Optional[Tuple[float, float]] = None
                   ) -> None:
        x = self._limits[0] if x is None else tuple(float(val) for val in sorted(x))
        y = self._limits[1] if y is None else tuple(float(val) for val in sorted(y))
        if len(x) != len(y) != 2:
            raise ValueError('x and y limits must be 2-item-tuples (min, max)')
        self._limits = (x, y)

    def set_auto_limits(self, x: Optional[bool] = None, y: Optional[bool] = None) -> None:
        if self._data:
            if x:
                x_min = min(x_data.min() for x_data, _ in self._data)
                x_max = max(x_data.max() for x_data, _ in self._data)
                x_range = x_max - x_min
                self.set_limits(
                    x=(x_min - self.auto_padding * x_range, x_max + self.auto_padding * x_range)
                )
            if y:
                y_min = min(y_data.min() for _, y_data in self._data)
                y_max = max(y_data.max() for _, y_data in self._data)
                y_range = y_max - y_min
                self.set_limits(
                    y=(y_min - self.auto_padding * y_range, y_max + self.auto_padding * y_range)
                )

    def add_data(self,
                 data: Union[np.ndarray, Tuple[Sequence[float], Sequence[float]]],
                 label: Optional[str] = None
                 ) -> None:
        index = len(self._data)
        self._data.append(None)
        self._data_labels.append(f'Dataset {index+1:d}')
        try:
            self.set_data(index=index, data=data, label=label)
        except:
            self.remove_data(-1)
            raise

    def remove_data(self, index: int) -> None:
        del self._data[index]
        del self._data_labels[index]

    def clear_data(self) -> None:
        """
        """
        try:
            while True:
                self.remove_data(-1)
        except IndexError:
            pass

    def set_data(self,
                 index: int,
                 data: Union[np.ndarray, Tuple[Sequence[float], Sequence[float]]],
                 label: Optional[str] = None
                 ) -> None:
        if len(data[0]) != len(data[1]):
            raise ValueError('Data must contain x and y array of equal length')
        if len(data[0]) == 0:
            return
        self._data[index] = np.asarray(data)
        if label is not None:
            self.set_data_label(index=index, label=label)

    def set_data_label(self, index: int, label: str) -> None:
        if not isinstance(label, str):
            raise TypeError('data_label must be str type')
        self._data_labels[index] = str(label)

    def to_dict(self) -> Dict[str, Any]:
        return {'labels'     : self.labels,
                'units'      : self.units,
                'limits'     : self.limits,
                'data'       : self.data,
                'data_labels': self.data_labels}

    @classmethod
    def from_dict(cls, init_dict: Mapping[str, Any]) -> object:
        return cls(**init_dict)


class QDPlotFitContainer(FitContainer):
    """ Customized FitContainer object that takes multiple datasets at once and performs the same
    fit on each of them
    """

    def __init__(self, *args, plot_config: QDPlotConfig, **kwargs):
        super().__init__(*args, **kwargs)

        self._last_fit_results = list()
        self._plot_config = plot_config

    @property
    def last_fits(self):
        with self._access_lock:
            return self._last_fit_config, self._last_fit_results.copy()

    def fit_plot_config(self,
                        fit_config: str,
                        plot_config: QDPlotConfig
                        ) -> Tuple[str, List[_ModelResult]]:
        results = list()
        self.blockSignals(True)
        try:
            for x_data, y_data in plot_config.data:
                # only fit if the is enough data to actually do the fit
                if (len(x_data) < 2) or (len(y_data) < 2) or (np.min(x_data) == np.max(x_data)):
                    results.append(None)
                else:
                    results.append(self.fit_data(fit_config=fit_config, x=x_data, data=y_data)[1])
        finally:
            self.blockSignals(False)
        self._last_fit_results = results
        self.sigLastFitResultChanged.emit(self._last_fit_config, self._last_fit_results)
        return self._last_fit_config, self._last_fit_results.copy()

    def formatted_result(self, fit_result, parameters_units=None):
        try:
            result_str = ''
            for ii, result in enumerate(fit_result):
                single_result_str = FitContainer.formatted_result(result, parameters_units)
                if single_result_str:
                    tabbed_result = '\n  '.join(single_result_str.split('\n'))
                    result_str += f'{self._plot_config.data_labels[ii]}:\n  {tabbed_result}\n'
            return result_str
        except TypeError:
            return FitContainer.formatted_result(fit_result, parameters_units)


class QDPlotLogic(LogicBase):
    """ This logic module helps display user data in plots, and makes it easy to save.
    
    There are phythonic setters and getters for each of the parameter and data. 
    They can be called by "plot_<plot_number>_parameter". plot_number ranges from 1 to 3.
    Parameters are: x_limits, y_limits, x_label, y_label, x_unit, y_unit, x_data, y_data, clear_old_data
    
    All parameters and data can also be interacted with by calling get_ and set_ functions.

    Example config for copy-paste:

    qdplotlogic:
        module.Class: 'qdplot_logic.QDPlotLogic'
        options:
            default_plot_number: 3
    """
    sigPlotDataUpdated = QtCore.Signal(int, object, list)  # plot_index, data_array, data_labels
    sigPlotParamsUpdated = QtCore.Signal(int, dict)
    sigPlotNumberChanged = QtCore.Signal(int)
    sigFitUpdated = QtCore.Signal(int, str, list)

    _default_plot_number = ConfigOption(name='default_plot_number', default=3)

    _fit_configs = StatusVar(name='fit_configs', default=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self.threadlock = RecursiveMutex()

        self._plot_configs = list()
        self._fit_containers = list()

        self._fit_config_model = None

    def on_activate(self):
        """ Initialisation performed during activation of the module. """
        # Sanity-check ConfigOptions
        if not isinstance(self._default_plot_number, int) or self._default_plot_number < 1:
            self.log.warning('Invalid number of plots encountered in config. Falling back to 1.')
            self._default_plot_number = 1

        self._fit_config_model = FitConfigurationsModel(parent=self)
        self._fit_config_model.load_configs(self._fit_configs)

        self._fit_containers = list()
        self._plot_configs = list()

        self.set_number_of_plots(self._default_plot_number)

    def on_deactivate(self):
        """ De-initialisation performed during deactivation of the module. """
        for i in reversed(range(self.number_of_plots)):
            self.remove_plot(i)

    @_fit_configs.representer
    def __repr_fit_configs(self, value):
        configs = self.fit_config_model.dump_configs()
        if len(configs) < 1:
            configs = None
        return configs

    @_fit_configs.constructor
    def __constr_fit_configs(self, value):
        if not value:
            return dict()
        return value

    @property
    def fit_config_model(self):
        return self._fit_config_model

    @property
    def fit_containers(self):
        return self._fit_containers

    @property
    def number_of_plots(self) -> int:
        return len(self._plot_configs)

    def add_plot(self) -> None:
        with self.threadlock:
            plot_index = self.number_of_plots

            plot_config = QDPlotConfig()
            self._plot_configs.append(plot_config)
            self._fit_containers.append(QDPlotFitContainer(parent=self,
                                                           plot_config=plot_config,
                                                           config_model=self._fit_config_model))

            self.sigPlotNumberChanged.emit(self.number_of_plots)
            self.sigPlotDataUpdated.emit(plot_index, plot_config.data, plot_config.data_labels)
            self.sigPlotParamsUpdated.emit(plot_index,
                                           {'x_label' : plot_config.labels[0],
                                            'y_label' : plot_config.labels[1],
                                            'x_unit'  : plot_config.units[0],
                                            'y_unit'  : plot_config.units[1],
                                            'x_limits': plot_config.limits[0],
                                            'y_limits': plot_config.limits[1]})

    def remove_plot(self, plot_index: Optional[int] = None) -> None:
        with self.threadlock:
            if plot_index is None:
                plot_index = -1

            invalid_start_index = self.number_of_plots - len(self._plot_configs[plot_index:])

            del self._plot_configs[plot_index]
            del self._fit_containers[plot_index]

            self.sigPlotNumberChanged.emit(self.number_of_plots)

            for index in range(invalid_start_index, self.number_of_plots):
                self.sigPlotDataUpdated.emit(index,
                                             self._plot_configs[index].data,
                                             self._plot_configs[index].data_labels)
                self.sigFitUpdated.emit(index, *self._fit_containers[index].last_fits)
                self.sigPlotParamsUpdated.emit(plot_index,
                                               {'x_label' : self._plot_configs[index].labels[0],
                                                'y_label' : self._plot_configs[index].labels[1],
                                                'x_unit'  : self._plot_configs[index].units[0],
                                                'y_unit'  : self._plot_configs[index].units[1],
                                                'x_limits': self._plot_configs[index].limits[0],
                                                'y_limits': self._plot_configs[index].limits[1]})

    def set_number_of_plots(self, plt_count: int) -> None:
        if not is_integer(plt_count):
            raise TypeError('number_of_plots must be integer')
        if plt_count < 1:
            raise ValueError('number_of_plots must be >= 1')
        with self.threadlock:
            while self.number_of_plots < plt_count:
                self.add_plot()
            while self.number_of_plots > plt_count:
                self.remove_plot()

    def get_data(self, plot_index: Optional[int] = None) -> List[np.ndarray]:
        if plot_index is None:
            plot_index = 0
        with self.threadlock:
            return self._plot_configs[plot_index].data

    def get_x_data(self, plot_index: Optional[int] = None) -> List[np.ndarray]:
        """ Get the data of the x-axis being plotted """
        if plot_index is None:
            plot_index = 0
        with self.threadlock:
            return [data[0] for data in self._plot_configs[plot_index].data]

    def get_y_data(self, plot_index: Optional[int] = None) -> List[np.ndarray]:
        """ Get the data of the y-axis being plotted """
        if plot_index is None:
            plot_index = 0
        with self.threadlock:
            return [data[1] for data in self._plot_configs[plot_index].data]

    def set_data(self,
                 x: Union[Sequence[Sequence[float]], Sequence[float]],
                 y: Union[Sequence[Sequence[float]], Sequence[float]],
                 label: Optional[Union[str, Sequence[str]]] = None,
                 clear_old: Optional[bool] = None,
                 plot_index: Optional[int] = None,
                 adjust_scale: Optional[bool] = None
                 ) -> None:
        """ Set the data to plot

        @param np.ndarray or list of np.ndarrays x: data of independents variable(s)
        @param np.ndarray or list of np.ndarrays y: data of dependent variable(s)
        @param string or list of strings label: label of the added data set
        @param bool clear_old: clear old plots in GUI if True
        @param int plot_index: index of the plot in the range from 0 to 2
        @param bool adjust_scale: Whether auto-scale should be performed after adding data or not.
        """
        if clear_old is None:
            clear_old = True
        if adjust_scale is None:
            adjust_scale = True
        if plot_index is None:
            plot_index = 0
        x = np.asarray(x)
        y = np.asarray(y)
        if x.shape != y.shape:
            raise ValueError('x- and y-data must have same size and dimensions')
        # check if input is only an array (single plot) or a list of arrays (multiple plots)
        if x.ndim == 1:
            x = [x]
            y = [y]
        if isinstance(label, str):
            label = [label]
        elif label is None:
            label = [None] * len(x)
        elif len(label) != len(x):
            raise ValueError('Must provide as many data labels as x-y datasets or None')
        with self.threadlock:
            try:
                plot_config = self._plot_configs[plot_index]
            except IndexError:
                plot_config = None
            if plot_config is None:
                raise IndexError(f'Plot index {plot_index:d} out of bounds. To add a new plot, '
                                 f'call set_number_of_plots(int) or add_plot() first.')

            # Update data in selected QDPlotConfig
            if clear_old:
                plot_config.clear_data()
            for ii, x_data in enumerate(x):
                plot_config.add_data(data=[x_data, y[ii]], label=label[ii])

            # reset fit for this plot
            self._do_fit('No Fit', plot_index)
            # self._fit_containers[plot_index].fit_plot_config('No Fit', plot_config)

            self.sigPlotDataUpdated.emit(plot_index,
                                         plot_config.data,
                                         plot_config.data_labels)

            # automatically set the correct range if requested
            if adjust_scale:
                self._set_auto_limits(True, True, plot_index)

    def do_fit(self,
               fit_config: str,
               plot_index: Optional[int] = None
               ) -> Tuple[int, List[Union[None, np.ndarray]], str, str]:
        """ Get the data of the x-axis being plotted.
        
        @param str fit_config: name of the fit_method, this needs to match the methods in
                               fit_container.
        @param int plot_index: index of the plot in the range from 0 to 2
        @return int plot_index, 3D np.ndarray fit_data, str result, str fit_method: result of fit
        """
        with self.threadlock:
            return self._do_fit(fit_config=fit_config, plot_index=plot_index)

    def _do_fit(self,
                fit_config: str,
                plot_index: Optional[int] = None
                ) -> Tuple[int, List[Union[None, np.ndarray]], str, str]:
        """ Get the data of the x-axis being plotted.

        @param str fit_config: name of the fit_method, this needs to match the methods in
                               fit_container.
        @param int plot_index: index of the plot in the range from 0 to 2
        @return int plot_index, 3D np.ndarray fit_data, str result, str fit_method: result of fit
        """
        if plot_index is None:
            plot_index = 0
        valid_fit_configs = self._fit_config_model.configuration_names
        if (fit_config != 'No Fit') and (fit_config not in valid_fit_configs):
            raise ValueError(f'Unknown fit configuration "{fit_config}" encountered. '
                             f'Options are: {valid_fit_configs}')
        try:
            plot_config = self._plot_configs[plot_index]
        except IndexError:
            plot_config = None
        if plot_config is None:
            raise IndexError(f'Plot index {plot_index:d} out of bounds. '
                             f'Unable to perform data fit.')

        # do one fit for each data set in the plot
        fit_config, fit_results = self._fit_containers[plot_index].fit_plot_config(
            fit_config=fit_config,
            plot_config=plot_config
        )

        self.sigFitUpdated.emit(plot_index, fit_config, fit_results)

        # Legacy return values
        fit_data, result_str, fit_config = self._get_fit_data(plot_index)
        return plot_index, fit_data, result_str, fit_config

    def get_fit_data(self, plot_index: int) -> Tuple[List[Union[None, np.ndarray]], str, str]:
        with self.threadlock:
            return self._get_fit_data(plot_index)

    def _get_fit_data(self, plot_index: int) -> Tuple[List[Union[None, np.ndarray]], str, str]:
        fit_container = self._fit_containers[plot_index]
        fit_config, fit_results = fit_container.last_fits
        fit_data = [
            None if result is None else np.array(result.high_res_best_fit) for result in fit_results
        ]
        result_str = fit_container.formatted_result(fit_results)
        return fit_data, result_str, fit_config

    def save_data(self, postfix: Optional[str] = None, plot_index: Optional[int] = None) -> None:
        """ Save the data to a file.

        @param str postfix: an additional tag, which will be added to the filename upon save
        @param int plot_index: index of the plot in the range for 0 to 2
        """
        if postfix is None:
            postfix = ''
        if plot_index is None:
            plot_index = 0
        with self.threadlock:
            try:
                plot_config = self._plot_configs[plot_index]
                fit_data, result_str, fit_config = self._get_fit_data(plot_index)
            except IndexError:
                fit_data = result_str = fit_config = plot_config = None
            if plot_config is None:
                raise IndexError(f'Plot index {plot_index:d} out of bounds. Unable to save data.')

            # Set the parameters:
            parameters = {'user-selected x-limits'   : plot_config.limits[0],
                          'user-selected y-limits'   : plot_config.limits[1],
                          'user-selected x-label'    : plot_config.labels[0],
                          'user-selected y-label'    : plot_config.labels[1],
                          'user-selected x-unit'     : plot_config.units[0],
                          'user-selected y-unit'     : plot_config.units[1],
                          'user-selected data-labels': plot_config.data_labels}

            # If there is a postfix then add separating underscore
            file_label = postfix if postfix else 'qdplot'
            file_label += f'_plot_{self._plot_configs.index(plot_config) + 1:d}'

            # Data labels
            x_label = f'{plot_config.labels[0]} ({plot_config.units[0]})'
            y_label = f'{plot_config.labels[1]} ({plot_config.units[1]})'

            fig, ax1 = plt.subplots()

            for data_set, (x_data, y_data) in enumerate(plot_config.data):
                ax1.plot(x_data,
                         y_data,
                         linestyle=':',
                         linewidth=1,
                         label=plot_config.data_labels[data_set])
                if fit_data is not None:
                    try:
                        ax1.plot(fit_data[data_set][0],
                                 fit_data[data_set][1],
                                 color='r',
                                 marker='None',
                                 linewidth=1.5,
                                 label=f'fit {plot_config.data_labels[data_set]}')
                    except IndexError:
                        pass

            # Do not include fit parameter if there is no fit calculated.
            if fit_data is not None:
                # Parameters for the text plot:
                # The position of the text annotation is controlled with the
                # relative offset in x direction and the relative length factor
                # rel_len_fac of the longest entry in one column
                rel_offset = 0.02
                rel_len_fac = 0.011
                entries_per_col = 24

                # do reverse processing to get each entry in a list
                entry_list = result_str.split('\n')
                # slice the entry_list in entries_per_col
                chunks = [entry_list[x:x + entries_per_col] for x in
                          range(0, len(entry_list), entries_per_col)]

                is_first_column = True  # first entry should contain header or \n

                for column in chunks:
                    max_length = max(column, key=len)  # get the longest entry
                    column_text = ''

                    for entry in column:
                        column_text += entry.rstrip() + '\n'

                    column_text = column_text[:-1]  # remove the last new line

                    heading = f'Fit results for method: {fit_config}' if is_first_column else ''
                    column_text = f'{heading}\n{column_text}'

                    ax1.text(1.00 + rel_offset,
                             0.99,
                             column_text,
                             verticalalignment='top',
                             horizontalalignment='left',
                             transform=ax1.transAxes,
                             fontsize=12)

                    # the rel_offset in position of the text is a linear function
                    # which depends on the longest entry in the column
                    rel_offset += rel_len_fac * len(max_length)

                    is_first_column = False

            # set labels, units and limits
            ax1.set_xlabel(x_label)
            ax1.set_ylabel(y_label)

            ax1.set_xlim(plot_config.limits[0])
            ax1.set_ylim(plot_config.limits[1])
            ax1.legend()

            fig.tight_layout()

            # prepare the data in a dict:
            data = list()
            header = list()
            for data_set, (x_data, y_data) in enumerate(plot_config.data):
                header.append(f'{x_label} set {data_set + 1:d}')
                header.append(f'{y_label} set {data_set + 1:d}')
                data.append(x_data)
                data.append(y_data)

            data = np.array(data).T

            ds = TextDataStorage(root_dir=self.module_default_data_dir,
                                 column_formats='.15e',
                                 include_global_metadata=True)

            file_path, _, _ = ds.save_data(data,
                                           metadata=parameters,
                                           column_headers=header,
                                           column_dtypes=[float] * len(header),
                                           nametag='qd_plot')

            ds.save_thumbnail(fig, file_path=file_path.rsplit('.', 1)[0])
            self.log.debug('Data saved to:\n{0}'.format(file_path))

    def get_limits(self,
                   plot_index: Optional[int] = None
                   ) -> Tuple[Union[None, Tuple[float, float]], Union[None, Tuple[float, float]]]:
        if plot_index is None:
            plot_index = 0
        with self.threadlock:
            return self._plot_configs[plot_index].limits

    def set_limits(self,
                   limits: Tuple[Union[None, Tuple[float, float]], Union[None, Tuple[float, float]]] = None,
                   plot_index: Optional[int] = None
                   ) -> None:
        with self.threadlock:
            return self._set_limits(limits=limits, plot_index=plot_index)

    def _set_limits(self,
                    limits: Tuple[Union[None, Tuple[float, float]], Union[None, Tuple[float, float]]] = None,
                    plot_index: Optional[int] = None
                    ) -> None:
        if plot_index is None:
            plot_index = 0
        if limits is None:
            limits = [None, None]
        plot_config = self._plot_configs[plot_index]
        old_limits = plot_config.limits
        plot_config.set_limits(*limits)
        new_limits = plot_config.limits
        update_dict = dict()
        if new_limits[0] != old_limits[0]:
            update_dict['x_limits'] = new_limits[0]
        if new_limits[1] != old_limits[1]:
            update_dict['y_limits'] = new_limits[1]
        if update_dict:
            self.sigPlotParamsUpdated.emit(plot_index, update_dict)

    def get_x_limits(self, plot_index: Optional[int] = None) -> Union[None, Tuple[float, float]]:
        """ Get the limits of the x-axis being plotted.

        @param int plot_index: index of the plot in the range from 0 to 2
        @return 2-element list: limits of the x-axis e.g. as [0, 1]
        """
        return self.get_limits(plot_index)[0]

    def set_x_limits(self, limits: Tuple[float, float], plot_index: Optional[int] = None) -> None:
        """Set the x_limits, to match the data (default) or to a specified new range

        @param float limits: 2-element list containing min and max x-values
        @param int plot_index: index of the plot in the range for 0 to 2
        """
        return self.set_limits(limits=(limits, None), plot_index=plot_index)

    def get_y_limits(self, plot_index):
        """ Get the limits of the y-axis being plotted.

        @param int plot_index: index of the plot in the range from 0 to 2
        @return 2-element list: limits of the y-axis e.g. as [0, 1]
        """
        return self.get_limits(plot_index)[1]

    def set_y_limits(self, limits: Tuple[float, float], plot_index: Optional[int] = None) -> None:
        """Set the y_limits, to match the data (default) or to a specified new range

        @param float limits: 2-element list containing min and max y-values
        @param int plot_index: index of the plot in the range for 0 to 2
        """
        return self.set_limits(limits=(None, limits), plot_index=plot_index)

    def set_auto_limits(self,
                        x: Optional[bool] = None,
                        y: Optional[bool] = None,
                        plot_index: Optional[int] = None
                        ) -> None:
        with self.threadlock:
            return self._set_auto_limits(x, y, plot_index)

    def _set_auto_limits(self,
                         x: Optional[bool] = None,
                         y: Optional[bool] = None,
                         plot_index: Optional[int] = None
                         ) -> None:
        if plot_index is None:
            plot_index = 0
        plot_config = self._plot_configs[plot_index]
        plot_config.set_auto_limits(x, y)
        update_dict = dict()
        if x:
            update_dict['x_limits'] = plot_config.limits[0]
        if y:
            update_dict['y_limits'] = plot_config.limits[1]
        if update_dict:
            self.sigPlotParamsUpdated.emit(plot_index, update_dict)

    def get_labels(self, plot_index: Optional[int] = None) -> Tuple[str, str]:
        if plot_index is None:
            plot_index = 0
        with self.threadlock:
            return self._plot_configs[plot_index].labels

    def set_labels(self,
                   labels: Tuple[Union[None, str], Union[None, str]],
                   plot_index: Optional[int] = None
                   ) -> None:
        with self.threadlock:
            return self._set_labels(labels=labels, plot_index=plot_index)

    def _set_labels(self,
                    labels: Tuple[Union[None, str], Union[None, str]],
                    plot_index: Optional[int] = None
                    ) -> None:
        if plot_index is None:
            plot_index = 0
        plot_config = self._plot_configs[plot_index]
        old_labels = plot_config.labels
        plot_config.set_labels(*labels)
        new_labels = plot_config.labels
        update_dict = dict()
        if old_labels[0] != new_labels[0]:
            update_dict['x_label'] = new_labels[0]
        if old_labels[1] != new_labels[1]:
            update_dict['y_label'] = new_labels[1]
        if update_dict:
            self.sigPlotParamsUpdated.emit(plot_index, update_dict)

    def get_x_label(self, plot_index: Optional[int] = None) -> str:
        """ Get the label of the x-axis being plotted.

        @param int plot_index: index of the plot
        @return str: current label of the x-axis
        """
        return self.get_labels(plot_index)[0]

    def set_x_label(self, value: str, plot_index: Optional[int] = None) -> None:
        """ Set the label of the x-axis being plotted.

        @param str value: label to be set
        @param int plot_index: index of the plot
        """
        return self.set_labels(labels=(value, None), plot_index=plot_index)

    def get_y_label(self, plot_index: Optional[int] = None) -> str:
        """ Get the label of the y-axis being plotted.

        @param int plot_index: index of the plot
        @return str: current label of the y-axis
        """
        return self.get_labels(plot_index)[1]

    def set_y_label(self, value: str, plot_index: Optional[int] = None) -> None:
        """ Set the label of the y-axis being plotted.

        @param str value: label to be set
        @param int plot_index: index of the plot
        """
        return self.set_labels(labels=(None, value), plot_index=plot_index)

    def get_data_labels(self, plot_index: Optional[int] = None) -> List[str]:
        """ Get the data set labels.

        @param int plot_index: index of the plot
        @return list(str): current labels of the data sets
        """
        if plot_index is None:
            plot_index = 0
        with self.threadlock:
            return self._plot_configs[plot_index].data_labels

    def get_units(self, plot_index: Optional[int] = None) -> Tuple[str, str]:
        if plot_index is None:
            plot_index = 0
        with self.threadlock:
            return self._plot_configs[plot_index].units

    def set_units(self,
                  units: Tuple[Union[None, str], Union[None, str]],
                  plot_index: Optional[int] = None
                  ) -> None:
        with self.threadlock:
            return self._set_units(units=units, plot_index=plot_index)

    def _set_units(self,
                   units: Tuple[Union[None, str], Union[None, str]],
                   plot_index: Optional[int] = None
                   ) -> None:
        if plot_index is None:
            plot_index = 0
        plot_config = self._plot_configs[plot_index]
        old_units = plot_config.units
        plot_config.set_units(*units)
        new_units = plot_config.units
        update_dict = dict()
        if old_units[0] != new_units[0]:
            update_dict['x_unit'] = new_units[0]
        if old_units[1] != new_units[1]:
            update_dict['y_unit'] = new_units[1]
        if update_dict:
            self.sigPlotParamsUpdated.emit(plot_index, update_dict)

    def get_x_unit(self, plot_index: Optional[int] = None) -> str:
        """ Get the unit of the x-axis being plotted.

        @param int plot_index: index of the plot
        @return str: current unit of the x-axis
        """
        return self.get_units(plot_index)[0]

    def set_x_unit(self, value: str, plot_index: Optional[int] = None) -> None:
        """ Set the unit of the x-axis being plotted.

        @param str value: label to be set
        @param int plot_index: index of the plot
        """
        return self.set_units(units=(value, None), plot_index=plot_index)

    def get_y_unit(self, plot_index: Optional[int] = None) -> str:
        """ Get the unit of the y-axis being plotted.

        @param int plot_index: index of the plot
        @return str: current unit of the y-axis
        """
        return self.get_units(plot_index)[1]

    def set_y_unit(self, value: str, plot_index: Optional[int] = None) -> None:
        """ Set the unit of the y-axis being plotted.

        @param str value: label to be set
        @param int plot_index: index of the plot
        """
        return self.set_units(units=(None, value), plot_index=plot_index)

    def update_plot_parameters(self, plot_index: int, params: Mapping[str, Any]) -> None:
        with self.threadlock:
            self._set_limits(
                limits=(params.get('x_limits', None), params.get('y_limits', None)),
                plot_index=plot_index
            )
            self._set_labels(
                labels=(params.get('x_label', None), params.get('y_label', None)),
                plot_index=plot_index
            )
            self._set_units(
                units=(params.get('x_unit', None), params.get('y_unit', None)),
                plot_index=plot_index
            )
