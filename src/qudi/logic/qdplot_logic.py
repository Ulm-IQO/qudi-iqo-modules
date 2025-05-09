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
from collections.abc import MutableMapping

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
                 limits: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None
                 ) -> None:
        super().__init__()
        self._labels = ('X', 'Y')
        self._units = ('arb.u.', 'arb.u.')
        self._limits = ((-0.5, 0.5), (-0.5, 0.5))
        if labels is not None:
            self.set_labels(*labels)
        if units is not None:
            self.set_units(*units)
        if limits is not None:
            self.set_limits(*limits)

    @property
    def labels(self) -> Tuple[str, str]:
        return self._labels

    @property
    def units(self) -> Tuple[str, str]:
        return self._units

    @property
    def limits(self) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        return self._limits

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

    def __copy__(self):
        return self.copy()

    def copy(self):
        return self.from_dict(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return {'labels': self.labels,
                'units' : self.units,
                'limits': self.limits}

    @classmethod
    def from_dict(cls, init_dict: Mapping[str, Any]):
        return cls(**init_dict)


class QDPlotDataSet(MutableMapping):
    """
    """

    _default_padding = 0.05

    def __init__(self,
                 data: Optional[Mapping[str, Tuple[np.ndarray, np.ndarray]]] = None,
                 config: Optional[Union[QDPlotConfig, Mapping[str, Any]]] = None
                 ) -> None:
        super().__init__()
        self._data = dict()
        if config is None:
            self.config = QDPlotConfig()
        elif isinstance(config, QDPlotConfig):
            self.config = config.copy()
        else:
            self.config = QDPlotConfig.from_dict(config)

        if data is not None:
            self.update(data)

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, key: str) -> np.ndarray:
        return self._data[key]

    def __setitem__(self, key: str, value: Tuple[np.ndarray, np.ndarray]) -> None:
        self.set_data(value, key)

    def __delitem__(self, key: str) -> None:
        del self._data[key]

    def __copy__(self):
        return self.copy()

    def remove_data(self, name: str) -> None:
        try:
            del self._data[name]
        except KeyError as err:
            raise ValueError(f'No data with name tag "{name}" present in QDPlotDataSet') from err

    def set_data(self, data: Tuple[np.ndarray, np.ndarray], name: Optional[str] = None) -> str:
        if name is None:
            name = self._get_valid_generic_name()
        elif (not name) or (not isinstance(name, str)):
            raise TypeError('data name must be non-empty str type value')
        if len(data) != 2:
            raise ValueError('Data must be length 2 iterable containing x- and y-data arrays')
        if len(data[0]) != len(data[1]):
            raise ValueError('x- and y-data arrays must be of same length')
        self._data[name] = np.asarray(data)
        return name

    def clear(self) -> None:
        self._data.clear()

    def copy(self):
        return QDPlotDataSet(self._data, self.config)

    def autoscale_limits(self, x: Optional[bool] = None, y: Optional[bool] = None) -> None:
        x_lim, y_lim = self.config.limits
        if x:
            try:
                x_min = min([x_data.min() for x_data, _ in self._data.values()])
                x_max = max([x_data.max() for x_data, _ in self._data.values()])
            except ValueError:
                x_lim = (-0.5, 0.5)
            else:
                if x_min == x_max:
                    x_lim = (-0.5, 0.5)
                else:
                    padding = self._default_padding * (x_max - x_min)
                    x_lim = (x_min - padding, x_max + padding)
        if y:
            try:
                y_min = min([y_data.min() for _, y_data in self._data.values()])
                y_max = max([y_data.max() for _, y_data in self._data.values()])
            except ValueError:
                y_lim = (-0.5, 0.5)
            else:
                if y_min == y_max:
                    y_lim = (-0.5, 0.5)
                else:
                    padding = self._default_padding * (y_max - y_min)
                    y_lim = (y_min - padding, y_max + padding)
        self.config.set_limits(x_lim, y_lim)

    def _get_valid_generic_name(self, index: Optional[int] = 1) -> str:
        name = f'Dataset {index:d}'
        if name in self._data:
            return self._get_valid_generic_name(index + 1)
        return name

    def to_dict(self) -> Dict[str, Any]:
        return {'data': self._data.copy(),
                'config': self.config.to_dict()}

    @classmethod
    def from_dict(cls, init_dict: Mapping[str, Any]):
        return cls(**init_dict)


class QDPlotFitContainer(FitContainer):
    """ Customized FitContainer object that takes multiple datasets at once and performs the same
    fit on each of them
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._last_fit_results = dict()

    @property
    def last_fits(self):
        with self._access_lock:
            return self._last_fit_config, self._last_fit_results.copy()

    def fit_plot_config(self,
                        fit_config: str,
                        data_set: QDPlotDataSet
                        ) -> Tuple[str, Dict[str, Union[None, _ModelResult]]]:
        results = dict()
        self.blockSignals(True)
        try:
            for name, (x_data, y_data) in data_set.items():
                # only fit if the is enough data to actually do the fit
                if (len(x_data) < 2) or (len(y_data) < 2) or (np.min(x_data) == np.max(x_data)):
                    results[name] = None
                else:
                    results[name] = self.fit_data(fit_config=fit_config, x=x_data, data=y_data)[1]
        finally:
            self.blockSignals(False)
        self._last_fit_results = results
        self.sigLastFitResultChanged.emit(self._last_fit_config, self._last_fit_results)
        return self._last_fit_config, self._last_fit_results.copy()

    def formatted_result(self, fit_result, parameters_units=None):
        try:
            result_str = ''
            for name, result in fit_result.items():
                single_result_str = FitContainer.formatted_result(result, parameters_units)
                if single_result_str:
                    tabbed_result = '\n  '.join(single_result_str.split('\n'))
                    result_str += f'{name}:\n  {tabbed_result}\n'
            return result_str
        except (TypeError, AttributeError):
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

    sigPlotDataChanged = QtCore.Signal(int, object)  # plot_index, QDPlotDataSet
    sigPlotConfigChanged = QtCore.Signal(int, object)  # plot_index, QDPlotConfig
    sigPlotAdded = QtCore.Signal()
    sigPlotRemoved = QtCore.Signal(int)  # plot_index
    sigFitChanged = QtCore.Signal(int, str, dict)  # plot_index, fit_name, fit_results

    _default_plot_count = ConfigOption(name='default_plot_number', default=3)

    _fit_config_model = StatusVar(name='fit_configs', default=list())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # locking for thread safety
        self._thread_lock = Mutex()

        self._plot_data_sets = list()
        self._fit_containers = list()

    def on_activate(self):
        """ Initialisation performed during activation of the module. """
        # Sanity-check ConfigOptions
        if not isinstance(self._default_plot_count, int) or self._default_plot_count < 1:
            self.log.warning('Invalid number of plots encountered in config. Falling back to 1.')
            self._default_plot_count = 1

        self._fit_containers = list()
        self._plot_data_sets = list()

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
    def plot_count(self) -> int:
        return len(self._plot_data_sets)

    def get_plot_config(self, plot_index: int) -> QDPlotConfig:
        """ Returns a copy of the actual QDPlotConfig corresponding to the given plot_index """
        with self._thread_lock:
            return self._get_plot_data_set(plot_index).config

    def _get_plot_data_set(self, plot_index: int) -> QDPlotDataSet:
        try:
            return self._plot_data_sets[plot_index]
        except IndexError:
            pass
        raise IndexError(f'Plot index {plot_index:d} out of bounds. To add a new plot, '
                         f'call set_plot_count(int) or add_plot() first.')

    def add_plot(self) -> None:
        with self._thread_lock:
            self._add_plot()

    def _add_plot(self) -> None:
        self._plot_data_sets.append(QDPlotDataSet())
        self._fit_containers.append(
            QDPlotFitContainer(parent=self, config_model=self._fit_config_model)
        )
        self.sigPlotAdded.emit()

    def remove_plot(self, plot_index: int) -> None:
        with self._thread_lock:
            self._remove_plot(plot_index)

    def _remove_plot(self, plot_index: int) -> None:
        del self._plot_data_sets[plot_index]
        del self._fit_containers[plot_index]
        self.sigPlotRemoved.emit(plot_index)

    def set_plot_count(self, plot_count: int) -> None:
        with self._thread_lock:
            self._set_plot_count(plot_count)

    def _set_plot_count(self, plot_count: int) -> None:
        plot_count = int(plot_count)
        if plot_count < 1:
            raise ValueError('plot_count must be >= 1')
        while self.plot_count < plot_count:
            self._add_plot()
        while self.plot_count > plot_count:
            self._remove_plot(-1)

    def get_data(self, plot_index: int) -> QDPlotDataSet:
        with self._thread_lock:
            return self._get_data(plot_index).copy()

    def _get_data(self, plot_index: int) -> QDPlotDataSet:
        return self._get_plot_data_set(plot_index)

    def set_data(self,
                 plot_index: int,
                 data: Union[Tuple[np.ndarray, np.ndarray], Mapping[str, Tuple[np.ndarray, np.ndarray]]],
                 name: Optional[str] = None,
                 clear_old: Optional[bool] = False,
                 adjust_scale: Optional[bool] = True
                 ) -> Union[None, str]:
        """ Set the data to plot """
        with self._thread_lock:
            data_set = self._get_plot_data_set(plot_index)
            if clear_old:
                data_set.clear()
                # reset fits for this plot
                self._do_fit(plot_index, 'No Fit')
            # Either accept single data array and name argument OR
            # dict with array values and name keys
            try:
                for arr_name, arr in data.items():
                    data_set.set_data(arr, name=arr_name)
            except (TypeError, AttributeError):
                name = data_set.set_data(data, name=name)

            self.sigPlotDataChanged.emit(plot_index, data_set.copy())

            # automatically set the correct range if requested
            if adjust_scale:
                self._set_auto_limits(plot_index, True, True)
            return name

    def do_fit(self, plot_index: int, fit_config: str) -> Dict[str, Union[None, _ModelResult]]:
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

    def _do_fit(self, plot_index: int, fit_config: str) -> Dict[str, Union[None, _ModelResult]]:
        data_set = self._get_plot_data_set(plot_index)
        fit_container = self._get_fit_container(plot_index)
        # do one fit for each data set in the plot
        fit_config, fit_results = fit_container.fit_plot_config(fit_config=fit_config,
                                                                data_set=data_set)
        self.sigFitChanged.emit(plot_index, fit_config, fit_results)
        return fit_results

    def get_fit_container(self, plot_index: int) -> QDPlotFitContainer:
        with self._thread_lock:
            return self._get_fit_container(plot_index)

    def _get_fit_container(self, plot_index: int) -> QDPlotFitContainer:
        return self._fit_containers[plot_index]

    def get_fit_results(self, plot_index: int) -> Tuple[str, Dict[str, Union[None, _ModelResult]]]:
        with self._thread_lock:
            return self._get_fit_results(plot_index)

    def _get_fit_results(self, plot_index: int) -> Tuple[str, Dict[str, Union[None, _ModelResult]]]:
        return self._get_fit_container(plot_index).last_fits

    def get_limits(self, plot_index: int) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        with self._thread_lock:
            return self._get_limits(plot_index)

    def _get_limits(self, plot_index: int) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        return self._get_plot_data_set(plot_index).config.limits

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
        data_set = self._get_plot_data_set(plot_index)
        old_limits = data_set.config.limits
        data_set.config.set_limits(x, y)
        if data_set.config.limits != old_limits:
            self.sigPlotConfigChanged.emit(plot_index, data_set.config.copy())

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
        data_set = self._get_plot_data_set(plot_index)
        data_set.autoscale_limits(x, y)
        if (x is True) or (y is True):
            self.sigPlotConfigChanged.emit(plot_index, data_set.config.copy())

    def get_labels(self, plot_index: int) -> Tuple[str, str]:
        with self._thread_lock:
            return self._get_labels(plot_index)

    def _get_labels(self, plot_index: int) -> Tuple[str, str]:
        return self._get_plot_data_set(plot_index).config.labels

    def set_labels(self, plot_index: int, x: Optional[str] = None, y: Optional[str] = None) -> None:
        with self._thread_lock:
            return self._set_labels(plot_index, x, y)

    def _set_labels(self,
                    plot_index: int,
                    x: Optional[str] = None,
                    y: Optional[str] = None
                    ) -> None:
        data_set = self._get_plot_data_set(plot_index)
        old_labels = data_set.config.labels
        data_set.config.set_labels(x, y)
        if data_set.config.labels != old_labels:
            self.sigPlotConfigChanged.emit(plot_index, data_set.config.copy())

    def get_data_labels(self, plot_index: int) -> List[str]:
        """ Get the dataset labels """
        with self._thread_lock:
            return self._get_data_labels(plot_index)

    def _get_data_labels(self, plot_index: int) -> List[str]:
        return list(self._get_plot_data_set(plot_index))

    def get_units(self, plot_index: int) -> Tuple[str, str]:
        with self._thread_lock:
            return self._get_units(plot_index)

    def _get_units(self, plot_index: int) -> Tuple[str, str]:
        return self._get_plot_data_set(plot_index).config.units

    def set_units(self,
                  plot_index: int,
                  x: Optional[str] = None,
                  y: Optional[str] = None
                  ) -> None:
        with self._thread_lock:
            return self._set_units(plot_index, x, y)

    def _set_units(self, plot_index: int, x: Optional[str] = None, y: Optional[str] = None) -> None:
        data_set = self._get_plot_data_set(plot_index)
        old_units = data_set.config.units
        data_set.config.set_units(x, y)
        if data_set.config.units != old_units:
            self.sigPlotConfigChanged.emit(plot_index, data_set.config.copy())

    def set_plot_config(self, plot_index: int, config: QDPlotConfig) -> None:
        with self._thread_lock:
            data_set = self._get_plot_data_set(plot_index)
            data_set.config.set_units(*config.units)
            data_set.config.set_labels(*config.labels)
            data_set.config.set_limits(*config.limits)

    def save_data(self, plot_index: int, postfix: Optional[str] = None, root_dir: Optional[str] = None) -> str:
        """ Save data of a single plot to file.

        @param int plot_index: index of the plot
        @param str postfix: optional, an additional tag added to the generic filename
        @param str root_dir: optional, define a deviating folder for the data to be saved into
        @return str: file path the data was saved to
        """
        with self._thread_lock:
            return self._save_data(plot_index, postfix, root_dir)

    def _save_data(self, plot_index: int, postfix: Optional[str] = None, root_dir: Optional[str] = None) -> str:
        """ Save data of a single plot to file.

        @param int plot_index: index of the plot
        @param str postfix: optional, an additional tag added to the generic filename
        @param str root_dir: optional, define a deviating folder for the data to be saved into
        @return str: file path the data was saved to
        """
        data_set = self._get_plot_data_set(plot_index)
        fit_container = self._get_fit_container(plot_index)
        if len(data_set) < 1:
            self.log.warning(f'No datasets found in plot with index {plot_index:d}. Save aborted.')
            return ''

        # Set the parameters:
        parameters = {'x-limits'   : data_set.config.limits[0],
                      'y-limits'   : data_set.config.limits[1],
                      'x-label'    : data_set.config.labels[0],
                      'y-label'    : data_set.config.labels[1],
                      'x-unit'     : data_set.config.units[0],
                      'y-unit'     : data_set.config.units[1],
                      'data-labels': list(data_set)}

        # If there is a postfix then add separating underscore
        file_label = postfix if postfix else 'qdplot'
        file_label += f'_plot_{plot_index + 1:d}'

        # Data labels
        x_label = f'{data_set.config.labels[0]} ({data_set.config.units[0]})'
        y_label = f'{data_set.config.labels[1]} ({data_set.config.units[1]})'

        # Arrange data to save in a container and save to file
        # Make sure all data arrays have same shape. If this is not the case, you must pad the
        # array to save with NaN values or save each dataset to a separate file.
        data = list(data_set.values())
        if any(arr.shape != data[0].shape for arr in data):
            self.log.warning(f'Datasets with unequal length encountered in plot with index '
                             f'{plot_index:d}. Data to save will be padded with NaN values which '
                             f'is inefficient and tedious to work with. Make sure to use equal '
                             f'length datasets per individual plot.')
            max_length = max(arr.shape[1] for arr in data)
            save_data = np.full((len(data) * 2, max_length), np.nan)
            for ii, arr in enumerate(data):
                offset = ii * 2
                save_data[offset:offset + 2, :arr.shape[1]] = arr
        else:
            save_data = np.row_stack(data)

        header = list()
        for ii, _ in enumerate(data, 1):
            header.append(f'{x_label} set {ii:d}')
            header.append(f'{y_label} set {ii:d}')

        ds = TextDataStorage(root_dir=self.module_default_data_dir if root_dir is None else root_dir,
                             column_formats='.15e',
                             include_global_metadata=True)
        file_path, _, _ = ds.save_data(save_data.T,
                                       metadata=parameters,
                                       column_headers=header,
                                       column_dtypes=[float] * len(header),
                                       nametag=file_label)

        # plot graph and save as image alongside data file
        fig = self._plot_figure(data_set, fit_container, x_label, y_label)
        ds.save_thumbnail(fig, file_path=file_path.rsplit('.', 1)[0])

        self.log.debug(f'Data saved to: {file_path}')
        return file_path

    def save_all_data(self, postfix: Optional[str] = None, root_dir: Optional[str] = None) -> Tuple[str]:
        """ Save data of all the available plots.

        @param str postfix: optional, an additional tag added to the generic filename
        @param str root_dir: optional, define a deviating folder for the data to be saved into
        @return str: list of all the file paths the data was saved to
        """
        with self._thread_lock:
            file_path = list()
            for plot_index, _ in enumerate(self._plot_data_sets):
                file_path.append(self._save_data(plot_index, postfix, root_dir))
        return tuple(file_path)

    @staticmethod
    def _plot_figure(data_set: QDPlotDataSet,
                     fit_container: QDPlotFitContainer,
                     x_label: str,
                     y_label: str
                     ) -> plt.Figure:
        fit_config, fit_results = fit_container.last_fits
        # High-resolution fit data and formatted result string
        fit_data = [None if result is None else result.high_res_best_fit for result in fit_results.values()]
        fit_result_str = fit_container.formatted_result(fit_results)
        if not fit_data:
            fit_data = [None] * len(data_set)

        fig, ax1 = plt.subplots()

        for ii, (label, (x_data, y_data)) in enumerate(data_set.items()):
            ax1.plot(x_data,
                     y_data,
                     linestyle=':',
                     linewidth=1,
                     label=label)
            if fit_data[ii] is not None:
                ax1.plot(fit_data[ii][0],
                         fit_data[ii][1],
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

        limits = data_set.config.limits
        ax1.set_xlim(limits[0])
        ax1.set_ylim(limits[1])
        ax1.legend()

        fig.tight_layout()
        return fig
