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
"""

__all__ = ['QDPlotLogic', 'QDPlotConfig', 'QDPlotFitContainer']

import numpy as np
import matplotlib.pyplot as plt
from PySide2 import QtCore
from lmfit.model import ModelResult as _ModelResult
from typing import Tuple, Optional, Sequence, Union, List, Dict, Any, Mapping

from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex
from qudi.core.module import LogicBase
from qudi.util.datastorage import TextDataStorage
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
    def data_labels(self) -> List[str]:
        return self._data_labels.copy()

    @property
    def data(self) -> List[np.ndarray]:
        return self._data.copy()

    @property
    def config(self) -> Dict[str, Any]:
        return {'labels'     : self.labels,
                'units'      : self.units,
                'limits'     : self.limits,
                'data_labels': self.data_labels}

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
                padding = 0.5 if x_min == x_max else self.auto_padding * (x_max - x_min)
                self.set_limits(x=(x_min - padding, x_max + padding))
            if y:
                y_min = min(y_data.min() for _, y_data in self._data)
                y_max = max(y_data.max() for _, y_data in self._data)
                padding = 0.5 if y_min == y_max else self.auto_padding * (y_max - y_min)
                self.set_limits(y=(y_min - padding, y_max + padding))

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

    def __copy__(self) -> object:
        return self.copy()

    def copy(self) -> object:
        return QDPlotConfig(**self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        params = self.config
        params['auto_padding'] = self.auto_padding
        params['data'] = self.data
        return params

    @classmethod
    def from_dict(cls, init_dict: Mapping[str, Any]) -> object:
        return cls(**init_dict)


class QDPlotFitContainer(FitContainer):
    """ Customized FitContainer object that takes multiple datasets at once and performs the same
    fit on each of them
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._last_fit_results = list()
        self._plot_config = None

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
        self._plot_config = plot_config
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
    sigPlotDataChanged = QtCore.Signal(int, list, list)  # plot_index, data_array, data_labels
    sigPlotConfigChanged = QtCore.Signal(int, dict)  # plot_index, config_dict
    sigPlotAdded = QtCore.Signal()
    sigPlotRemoved = QtCore.Signal(int)  # plot_index
    sigFitChanged = QtCore.Signal(int, str, list)  # plot_index, fit_name, fit_results

    _default_plot_count = ConfigOption(name='default_plot_number', default=3)

    _fit_config_model = StatusVar(name='fit_configs', default=list())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self._thread_lock = Mutex()

        self._plot_configs = list()
        self._fit_containers = list()

    def on_activate(self):
        """ Initialisation performed during activation of the module. """
        # Sanity-check ConfigOptions
        if not isinstance(self._default_plot_count, int) or self._default_plot_count < 1:
            self.log.warning('Invalid number of plots encountered in config. Falling back to 1.')
            self._default_plot_count = 1

        self._fit_containers = list()
        self._plot_configs = list()

        self._set_plot_count(self._default_plot_count)

    def on_deactivate(self):
        """ De-initialisation performed during deactivation of the module. """
        for i in reversed(range(self.plot_count)):
            self.remove_plot(i)

    @staticmethod
    @_fit_config_model.representer
    def __repr_fit_configs(value: FitConfigurationsModel) -> List[Dict[str, Any]]:
        return value.dump_configs()

    @_fit_config_model.constructor
    def __constr_fit_configs(self, value: Sequence[Mapping[str, Any]]) -> FitConfigurationsModel:
        model = FitConfigurationsModel(parent=self)
        model.load_configs(value)
        return model

    @property
    def fit_config_model(self) -> FitConfigurationsModel:
        return self._fit_config_model

    @property
    def fit_containers(self) -> List[QDPlotFitContainer]:
        with self._thread_lock:
            return self._fit_containers.copy()

    @property
    def plot_configs(self) -> List[Dict[str, Any]]:
        with self._thread_lock:
            return [cfg.config for cfg in self._plot_configs]

    @property
    def plot_count(self) -> int:
        return len(self._plot_configs)

    def get_plot_config(self, plot_index: int) -> Dict[str, Any]:
        """ Returns a copy of the actual QDPlotConfig corresponding to the given plot_index """
        with self._thread_lock:
            return self._get_plot_config(plot_index).config

    def _get_plot_config(self, plot_index: int) -> QDPlotConfig:
        try:
            plot_config = self._plot_configs[plot_index]
        except IndexError:
            plot_config = None
        if plot_config is None:
            raise IndexError(f'Plot index {plot_index:d} out of bounds. To add a new plot, '
                             f'call set_plot_count(int) or add_plot() first.')
        return plot_config

    def add_plot(self) -> None:
        with self._thread_lock:
            self._add_plot()

    def _add_plot(self) -> None:
        plot_config = QDPlotConfig()
        self._plot_configs.append(plot_config)
        self._fit_containers.append(
            QDPlotFitContainer(parent=self, config_model=self._fit_config_model)
        )
        self.sigPlotAdded.emit()

    def remove_plot(self, plot_index: int) -> None:
        with self._thread_lock:
            self._remove_plot(plot_index)

    def _remove_plot(self, plot_index: int) -> None:
        del self._plot_configs[plot_index]
        del self._fit_containers[plot_index]
        self.sigPlotRemoved.emit(plot_index)

    def set_plot_count(self, plot_count: int) -> None:
        plot_count = int(plot_count)
        if plot_count < 1:
            raise ValueError('plot_count must be >= 1')
        with self._thread_lock:
            self._set_plot_count(plot_count)

    def _set_plot_count(self, plot_count: int) -> None:
        while self.plot_count < plot_count:
            self._add_plot()
        while self.plot_count > plot_count:
            self._remove_plot()

    def get_data(self, plot_index: int) -> List[np.ndarray]:
        with self._thread_lock:
            return self._get_data(plot_index)

    def _get_data(self, plot_index: int) -> List[np.ndarray]:
        return self._get_plot_config(plot_index).data

    def get_x_data(self, plot_index: int) -> List[np.ndarray]:
        """ Get the data of the x-axis being plotted """
        with self._thread_lock:
            return [data for (data, _) in self._get_data(plot_index)]

    def get_y_data(self, plot_index: Optional[int] = None) -> List[np.ndarray]:
        """ Get the data of the y-axis being plotted """
        with self._thread_lock:
            return [data for (_, data) in self._get_data(plot_index)]

    def set_data(self,
                 plot_index: int,
                 x: Union[Sequence[Sequence[float]], Sequence[float]],
                 y: Union[Sequence[Sequence[float]], Sequence[float]],
                 label: Optional[Union[str, Sequence[str]]] = None,
                 clear_old: Optional[bool] = True,
                 adjust_scale: Optional[bool] = True
                 ) -> None:
        """ Set the data to plot

        @param np.ndarray or list of np.ndarrays x: data of independents variable(s)
        @param np.ndarray or list of np.ndarrays y: data of dependent variable(s)
        @param string or list of strings label: label of the added data set
        @param bool clear_old: clear old plots in GUI if True
        @param int plot_index: index of the plot in the range from 0 to 2
        @param bool adjust_scale: Whether auto-scale should be performed after adding data or not.
        """
        # check if input is only an array (single plot) or a list of arrays (multiple plots)
        try:
            len(x[0])
        except TypeError:
            x = [x]
        try:
            len(y[0])
        except TypeError:
            y = [y]
        if label is None:
            label = [None] * len(x)
        elif isinstance(label, str):
            label = [label]

        if not (len(x) == len(y) == len(label)):
            raise ValueError(f'Number of x ({len(x)}) and y ({len(y)}) datasets as well as '
                             f'optional labels does not match')
        if not all(len(x_arr) == len(y[ii]) for ii, x_arr in enumerate(x)):
            raise ValueError('Each set of x- and y-data arrays must have matching length')

        with self._thread_lock:
            # Update data in selected QDPlotConfig
            plot_config = self._get_plot_config(plot_index)
            if clear_old:
                plot_config.clear_data()
                # reset fits for this plot
                self._do_fit(plot_index, 'No Fit')
                self.sigFitChanged.emit(plot_index, *self._fit_containers[plot_index].last_fits)
            for ii, x_data in enumerate(x):
                plot_config.add_data(data=[x_data, y[ii]], label=label[ii])

            self.sigPlotDataChanged.emit(plot_index,
                                         plot_config.data,
                                         plot_config.data_labels)

            # automatically set the correct range if requested
            if adjust_scale:
                self._set_auto_limits(plot_index, True, True)

    def do_fit(self, plot_index: int, fit_config: str) -> List[Union[None, _ModelResult]]:
        """ Perform desired fit on data of given plot_index.

        @param int plot_index: index of the plot
        @param str fit_config: name of the fit. Must match a fit configuration in fit_config_model.
        """
        with self._thread_lock:
            valid_fit_configs = self._fit_config_model.configuration_names
            if (fit_config != 'No Fit') and (fit_config not in valid_fit_configs):
                raise ValueError(f'Unknown fit configuration "{fit_config}" encountered. '
                                 f'Options are: {valid_fit_configs}')
            return self._do_fit(plot_index, fit_config)

    def _do_fit(self, plot_index: int, fit_config: str) -> List[Union[None, _ModelResult]]:
        plot_config = self._get_plot_config(plot_index)
        fit_container = self._fit_containers[plot_index]
        # do one fit for each data set in the plot
        fit_config, fit_results = fit_container.fit_plot_config(fit_config=fit_config,
                                                                plot_config=plot_config)
        self.sigFitChanged.emit(plot_index, fit_config, fit_results)
        return fit_results

    def get_fit_results(self, plot_index: int) -> Tuple[str, List[Union[None, _ModelResult]]]:
        with self._thread_lock:
            return self._get_fit_results(plot_index)

    def _get_fit_results(self, plot_index: int) -> Tuple[str, List[Union[None, _ModelResult]]]:
        return self._fit_containers[plot_index].last_fits

    def get_limits(self,
                   plot_index: int
                   ) -> Tuple[Union[None, Tuple[float, float]], Union[None, Tuple[float, float]]]:
        with self._thread_lock:
            return self._get_limits(plot_index)

    def _get_limits(self,
                    plot_index: int
                    ) -> Tuple[Union[None, Tuple[float, float]], Union[None, Tuple[float, float]]]:
        return self._get_plot_config(plot_index).limits

    def set_limits(self,
                   plot_index: int,
                   x: Optional[Tuple[float, float]] = None,
                   y: Optional[Tuple[float, float]] = None
                   ) -> None:
        with self._thread_lock:
            return self._set_limits(plot_index, x, y)

    def _set_limits(self,
                    plot_index: int,
                    x: Optional[Tuple[float, float]] = None,
                    y: Optional[Tuple[float, float]] = None
                    ) -> None:
        plot_config = self._get_plot_config(plot_index)
        old_limits = plot_config.limits
        plot_config.set_limits(x, y)
        if plot_config.limits != old_limits:
            self.sigPlotConfigChanged.emit(plot_index, plot_config.config)

    def get_x_limits(self, plot_index: int) -> Union[None, Tuple[float, float]]:
        """ Get the limits of the x-axis being plotted """
        return self.get_limits(plot_index)[0]

    def set_x_limits(self, plot_index: int, limits: Tuple[float, float]) -> None:
        """ Set the x_limits to a specified new range """
        return self.set_limits(plot_index, x=limits)

    def get_y_limits(self, plot_index: int) -> Union[None, Tuple[float, float]]:
        """ Get the limits of the y-axis being plotted """
        return self.get_limits(plot_index)[1]

    def set_y_limits(self, plot_index: int, limits: Tuple[float, float]) -> None:
        """Set the y_limits to a specified new range """
        return self.set_limits(plot_index, y=limits)

    def set_auto_limits(self,
                        plot_index: int,
                        x: Optional[bool] = None,
                        y: Optional[bool] = None
                        ) -> None:
        with self._thread_lock:
            return self._set_auto_limits(plot_index, x, y)

    def _set_auto_limits(self,
                         plot_index: int,
                         x: Optional[bool] = None,
                         y: Optional[bool] = None
                         ) -> None:
        plot_config = self._get_plot_config(plot_index)
        plot_config.set_auto_limits(x, y)
        if (x is True) or (y is True):
            self.sigPlotConfigChanged.emit(plot_index, plot_config.config)

    def get_labels(self, plot_index: int) -> Tuple[str, str]:
        with self._thread_lock:
            return self._get_labels(plot_index)

    def _get_labels(self, plot_index: int) -> Tuple[str, str]:
        return self._get_plot_config(plot_index).labels

    def set_labels(self, plot_index: int, x: Optional[str] = None, y: Optional[str] = None) -> None:
        with self._thread_lock:
            return self._set_labels(plot_index, x, y)

    def _set_labels(self,
                    plot_index: int,
                    x: Optional[str] = None,
                    y: Optional[str] = None
                    ) -> None:
        plot_config = self._get_plot_config(plot_index)
        old_labels = plot_config.labels
        plot_config.set_labels(x, y)
        if plot_config.labels != old_labels:
            self.sigPlotConfigChanged.emit(plot_index, plot_config.config)

    def get_x_label(self, plot_index: int) -> str:
        """ Get the label of the x-axis being plotted """
        return self.get_labels(plot_index)[0]

    def set_x_label(self, plot_index: int, label: str) -> None:
        """ Set the label of the x-axis being plotted """
        return self.set_labels(plot_index, x=label)

    def get_y_label(self, plot_index: int) -> str:
        """ Get the label of the y-axis being plotted """
        return self.get_labels(plot_index)[1]

    def set_y_label(self, plot_index: int, label: str) -> None:
        """ Set the label of the y-axis being plotted """
        return self.set_labels(plot_index, y=label)

    def get_data_labels(self, plot_index: int) -> List[str]:
        """ Get the dataset labels """
        with self._thread_lock:
            return self._get_data_labels(plot_index)

    def _get_data_labels(self, plot_index: int) -> List[str]:
        return self._get_plot_config(plot_index).data_labels

    def get_units(self, plot_index: int) -> Tuple[str, str]:
        with self._thread_lock:
            return self._get_units(plot_index)

    def _get_units(self, plot_index: int) -> Tuple[str, str]:
        return self._get_plot_config(plot_index).units

    def set_units(self,
                  plot_index: int,
                  x: Optional[str] = None,
                  y: Optional[str] = None
                  ) -> None:
        with self._thread_lock:
            return self._set_units(plot_index, x, y)

    def _set_units(self, plot_index: int, x: Optional[str] = None, y: Optional[str] = None) -> None:
        plot_config = self._get_plot_config(plot_index)
        old_units = plot_config.units
        plot_config.set_units(x, y)
        if plot_config.units != old_units:
            self.sigPlotConfigChanged.emit(plot_index, plot_config.config)

    def get_x_unit(self, plot_index: int) -> str:
        """ Get the unit of the x-axis being plotted """
        return self.get_units(plot_index)[0]

    def set_x_unit(self, plot_index: int, unit: str) -> None:
        """ Set the unit of the x-axis being plotted """
        return self.set_units(plot_index, x=unit)

    def get_y_unit(self, plot_index: Optional[int] = None) -> str:
        """ Get the unit of the y-axis being plotted """
        return self.get_units(plot_index)[1]

    def set_y_unit(self, plot_index: int, unit: str) -> None:
        """ Set the unit of the y-axis being plotted """
        return self.set_units(plot_index, y=unit)

    def set_plot_config(self, plot_index: int, params: Mapping[str, Any]) -> None:
        with self._thread_lock:
            self._set_limits(plot_index, *params.get('limits', [None, None]))
            self._set_labels(plot_index, *params.get('labels', [None, None]))
            self._set_units(plot_index, *params.get('units', [None, None]))

    def save_data(self, plot_index: int, postfix: Optional[str] = None) -> None:
        """ Save data of a single plot to file.

        @param int plot_index: index of the plot
        @param str postfix: optional, an additional tag added to the generic filename
        """
        with self._thread_lock:
            return self._save_data(plot_index, postfix)

    def _save_data(self, plot_index: int, postfix: Optional[str] = None) -> None:
        """
        """
        plot_config = self._get_plot_config(plot_index)
        fit_container = self._fit_containers[plot_index]
        if not plot_config.data:
            self.log.warning(f'No datasets found in plot with index {plot_index:d}. Save aborted.')
            return

        # Set the parameters:
        parameters = {'x-limits'   : plot_config.limits[0],
                      'y-limits'   : plot_config.limits[1],
                      'x-label'    : plot_config.labels[0],
                      'y-label'    : plot_config.labels[1],
                      'x-unit'     : plot_config.units[0],
                      'y-unit'     : plot_config.units[1],
                      'data-labels': plot_config.data_labels}

        # If there is a postfix then add separating underscore
        file_label = postfix if postfix else 'qdplot'
        file_label += f'_plot_{self._plot_configs.index(plot_config) + 1:d}'

        # Data labels
        x_label = f'{plot_config.labels[0]} ({plot_config.units[0]})'
        y_label = f'{plot_config.labels[1]} ({plot_config.units[1]})'

        # Arrange data to save in a container and save to file
        # Make sure all data arrays have same shape. If this is not the case, you must pad the
        # array to save with NaN values or save each dataset to a separate file.
        if any(data.shape != plot_config.data[0].shape for data in plot_config.data):
            self.log.warning(f'Datasets with unequal length encountered in plot with index '
                             f'{plot_index:d}. Data to save will be padded with NaN values which '
                             f'is inefficient and tedious to work with. Make sure to use equal '
                             f'length datasets per individual plot.')
            max_length = max(data.shape[1] for data in plot_config.data)
            save_data = np.full((plot_config.dataset_count * 2, max_length), np.nan)
            for data_set, data in enumerate(plot_config.data):
                offset = data_set * 2
                save_data[offset:offset + 2, :data.shape[1]] = data
        else:
            save_data = np.row_stack(plot_config.data)

        header = list()
        for data_set, _ in enumerate(plot_config.data, 1):
            header.append(f'{x_label} set {data_set:d}')
            header.append(f'{y_label} set {data_set:d}')

        ds = TextDataStorage(root_dir=self.module_default_data_dir,
                             column_formats='.15e',
                             include_global_metadata=True)
        file_path, _, _ = ds.save_data(save_data.T,
                                       metadata=parameters,
                                       column_headers=header,
                                       column_dtypes=[float] * len(header),
                                       nametag=file_label)

        # plot graph and save as image alongside data file
        fig = self._plot_figure(plot_config, fit_container, x_label, y_label)
        ds.save_thumbnail(fig, file_path=file_path.rsplit('.', 1)[0])

        self.log.debug(f'Data saved to: {file_path}')

    def save_all_data(self, postfix: Optional[str] = None) -> None:
        with self._thread_lock:
            for plot_index, _ in enumerate(self._plot_configs):
                self._save_data(plot_index, postfix)

    @staticmethod
    def _plot_figure(plot_config: QDPlotConfig,
                     fit_container: QDPlotFitContainer,
                     x_label: str,
                     y_label: str
                     ) -> plt.Figure:
        fit_config, fit_results = fit_container.last_fits
        # High-resolution fit data and formatted result string
        fit_data = [None if result is None else result.high_res_best_fit for result in fit_results]
        fit_result_str = fit_container.formatted_result(fit_results)
        if not fit_data:
            fit_data = [None] * plot_config.dataset_count

        fig, ax1 = plt.subplots()

        for data_set, (x_data, y_data) in enumerate(plot_config.data):
            label = plot_config.data_labels[data_set]
            ax1.plot(x_data,
                     y_data,
                     linestyle=':',
                     linewidth=1,
                     label=label)
            if fit_data[data_set] is not None:
                ax1.plot(fit_data[data_set][0],
                         fit_data[data_set][1],
                         color='r',
                         marker='None',
                         linewidth=1.5,
                         label=f'fit {label}')

        # Do not include fit parameter if there is no fit calculated.
        if fit_result_str:
            # Parameters for the text plot:
            # The position of the text annotation is controlled with the
            # relative offset in x direction and the relative length factor
            # rel_len_fac of the longest entry in one column
            rel_offset = 0.02
            rel_len_fac = 0.011
            entries_per_col = 24

            # do reverse processing to get each entry in a list
            entry_list = fit_result_str.split('\n')
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

                heading = f'Fit results for "{fit_config}":' if is_first_column else ''
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
        return fig
