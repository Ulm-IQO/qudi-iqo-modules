# -*- coding: utf-8 -*-
"""
Base class and management class for the "alternative plot" (second plot) of the pulsed measurement
analysis tab.

This mirrors the design of ``pulse_analyzer.PulseAnalyzer``: drop a module containing a subclass of
``AltPlotMethodBase`` into the ``alt_plot_methods`` namespace package (or a configured additional
import directory) and the method automatically becomes selectable in the GUI.

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

from __future__ import annotations

import os
import sys
import inspect
import importlib
from collections.abc import Callable
from typing import Any
import numpy as np

from qudi.util.helpers import natural_sort, iter_modules_recursive


class AltPlotMethodBase:
    """ Base class for alternative plot methods. Must be inherited from *exclusively*.

    A subclass must set a unique ``name`` (falls back to the class name) and implement
    :meth:`compute`. It may override :meth:`is_available` and the ``x_axis_*``/``y_axis_*`` label
    properties. Any keyword arguments of :meth:`compute` (with correctly-typed defaults) are
    auto-discovered as configurable, persisted parameters. The reserved kwarg name is "method".
    """

    name = None

    def __init__(self, pulsedmeasurementlogic):
        self.__pulsedmeasurementlogic = pulsedmeasurementlogic

    # --- masked read-only access to the measurement logic ---
    @property
    def is_gated(self):
        return self.__pulsedmeasurementlogic.fast_counter_settings.get('is_gated')

    @property
    def measurement_settings(self):
        return self.__pulsedmeasurementlogic.measurement_settings

    @property
    def sampling_information(self):
        return self.__pulsedmeasurementlogic.sampling_information

    @property
    def fast_counter_settings(self):
        return self.__pulsedmeasurementlogic.fast_counter_settings

    @property
    def is_alternating(self):
        return bool(self.measurement_settings.get('alternating'))

    @property
    def data_labels(self):
        labels = self.measurement_settings.get('labels')
        return tuple(labels) if labels else ('', '')

    @property
    def data_units(self):
        units = self.measurement_settings.get('units')
        return tuple(units) if units else ('', '')

    @property
    def log(self):
        return self.__pulsedmeasurementlogic.log

    # --- interface to implement ---
    def compute(self, signal_data: np.ndarray) -> np.ndarray | None:
        """ Transform ``signal_data`` (row 0: x-axis, rows 1..: trace(s); row 2 is the alternating
        trace) into a 2D array with the same layout. Return None if it cannot be evaluated; the
        caller then falls back to a flat default plot. """
        raise NotImplementedError('Alternative plot methods must implement "compute".')

    def is_available(self) -> bool:
        """ Whether the method may be selected/computed for the active measurement settings. """
        return True

    # Default axis labels mirror the primary data; the figure/GUI prepends an SI prefix to x_unit.
    @property
    def x_axis_label(self):
        return self.data_labels[0]

    @property
    def x_axis_unit(self):
        return self.data_units[0]

    @property
    def y_axis_label(self):
        return self.data_labels[1]

    @property
    def y_axis_unit(self):
        return self.data_units[1]

    @property
    def labels_dict(self):
        """ Axis labels as primitive strings. """
        return {'x_label': str(self.x_axis_label), 'x_unit': str(self.x_axis_unit),
                'y_label': str(self.y_axis_label), 'y_unit': str(self.y_axis_unit)}


class AltPlotAnalyzer(AltPlotMethodBase):
    """ Discovers, instantiates and interfaces all available ``AltPlotMethodBase`` subclasses.

    Methods are imported from the ``qudi.logic.pulsed.alt_plot_methods`` namespace package and from
    the optional ``PulsedMeasurementLogic.alt_plot_import_path`` directory. The selected method and
    all parameters are held on ``PulsedMeasurementLogic.alternate_signal_settings`` (an
    ``AlternativeSignalSettings`` instance) - mutated in place as this class's live state, and
    persisted automatically as part of the measurement logic's regular settings StatusVar.
    """

    def __init__(self, pulsedmeasurementlogic):
        super().__init__(pulsedmeasurementlogic)
        self._methods: dict[str, AltPlotMethodBase] = dict()      # {name: instance}
        # The real, shared AlternativeSignalSettings instance held by PulsedMeasurementLogic's
        # settings - not a copy. Mutated in place (attribute/dict assignment) as this class's
        # real, live, authoritative state, the same pattern PulseExtractor/PulseAnalyzer already
        # use for their own ExtractionParameters/AnalysisParameters.
        self.__settings = pulsedmeasurementlogic.alternate_signal_settings

        # Import from the default namespace package
        import qudi.logic.pulsed.alt_plot_methods as default_ns
        method_classes = list()
        for mod_finder in iter_modules_recursive(default_ns.__path__, default_ns.__name__ + '.'):
            try:
                module = importlib.import_module(mod_finder.name)
                method_classes.extend(c for _, c in inspect.getmembers(module, self.is_alt_plot_class))
            except:
                self.log.exception(f'Exception while importing alt_plot_methods sub-module '
                                   f'"{mod_finder.name}":')

        # Import from the optional additional directory
        import_path = getattr(pulsedmeasurementlogic, 'alt_plot_import_path', None)
        if isinstance(import_path, str):
            try:
                method_classes.extend(self.__import_external_methods(import_path))
            except:
                self.log.exception(f'Unable to import alternative plot methods from '
                                   f'"{import_path}":')

        for cls in method_classes:
            instance = cls(pulsedmeasurementlogic)
            method_name = instance.name if instance.name else cls.__name__
            if method_name in self._methods:
                self.log.warning(f'Duplicate alternative plot method name "{method_name}". '
                                 f'Overwriting the previously registered one.')
            self._methods[method_name] = instance
            # Values already persisted in self.__settings.parameters (e.g. from a previous
            # session) win over compute()'s own signature defaults - see _get_method_kwargs().
            self.__settings.parameters.update(self._get_method_kwargs(instance.compute))

        # Drop any persisted parameter that no longer corresponds to a real method (e.g. removed
        # or renamed since last session) - mirrors PulseExtractor/PulseAnalyzer's same cleanup, so
        # a stale value can't accumulate forever or get silently adopted by an unrelated future
        # method that happens to reuse the same parameter name.
        valid_parameter_names = set()
        for instance in self._methods.values():
            valid_parameter_names.update(inspect.signature(instance.compute).parameters.keys())
        valid_parameter_names.discard('signal_data')
        for param in [p for p in list(self.__settings.parameters) if p not in valid_parameter_names]:
            del self.__settings.parameters[param]

        # A persisted selection that no longer matches any loaded method (e.g. its plugin file
        # was removed, or this is the very first activation) must not be kept as "selected".
        if self.__settings.method not in self._methods:
            if self.__settings.method is not None:
                self.log.warning(f'Previously selected alternative plot method '
                                 f'"{self.__settings.method}" is no longer available. Disabling '
                                 f'alternative plot.')
            self.__settings.method = None

    @property
    def alt_plot_methods(self) -> list[str]:
        """ Naturally sorted list of all available method names. """
        return natural_sort(self._methods)

    @property
    def current_method(self) -> str | None:
        return self.__settings.method

    @current_method.setter
    def current_method(self, name: str | None) -> None:
        if name in (None, '', 'None'):
            self.__settings.method = None
        elif name in self._methods:
            self.__settings.method = name
        else:
            self.log.error(f'Alternative plot method "{name}" not found in AltPlotAnalyzer.')

    @property
    def alt_plot_settings(self) -> dict[str, Any]:
        """ Current method's parameters plus the selected method name (key "method"). """
        method = self._methods.get(self.__settings.method)
        settings = self._get_method_kwargs(method.compute) if method is not None else dict()
        settings['method'] = self.__settings.method
        return settings

    @alt_plot_settings.setter
    def alt_plot_settings(self, settings_dict: dict[str, Any]) -> None:
        if not isinstance(settings_dict, dict):
            return
        for parameter, value in settings_dict.items():
            if parameter == 'method':
                self.current_method = value
            elif parameter in self.__settings.parameters:
                self.__settings.parameters[parameter] = value
            else:
                self.log.warning(f'No alternative plot parameter "{parameter}" in AltPlotAnalyzer. '
                                 f'Ignoring it.')

    @property
    def alt_plot_labels(self) -> dict[str, dict[str, str]]:
        """ {method_name: labels_dict} for every method (primitive strings; rpyc-safe). """
        return {name: instance.labels_dict for name, instance in self._methods.items()}

    @property
    def current_labels(self) -> dict[str, str]:
        method = self._methods.get(self.__settings.method)
        return method.labels_dict if method is not None else dict()

    def is_available(self, name: str | None = None) -> bool:
        """ Whether the given (default: current) method can be used for the active settings. """
        method = self._methods.get(self.__settings.method if name is None else name)
        if method is None:
            return False
        try:
            return bool(method.is_available())
        except:
            self.log.exception(f'Error checking availability of alternative plot method "{name}":')
            return False

    def compute_alt_data(self, signal_data: np.ndarray) -> np.ndarray | None:
        """ Evaluate the current method, or return None if none selected/unavailable/not evaluable. """
        if self.__settings.method is None or not self.is_available(self.__settings.method):
            return None
        method = self._methods[self.__settings.method]
        return method.compute(signal_data=signal_data, **self._get_method_kwargs(method.compute))

    # --- helpers ---
    def _get_method_kwargs(self, compute_method: Callable) -> dict[str, Any]:
        """ Build the kwargs (other than ``signal_data``) for a compute method, preferring stored
        parameter values over the signature defaults (matching by data type). """
        kwargs = dict()
        for name, param in inspect.signature(compute_method).parameters.items():
            if name == 'signal_data':
                continue
            stored = self.__settings.parameters.get(name)
            kwargs[name] = stored if type(stored) == type(param.default) else param.default
        return kwargs

    def __import_external_methods(self, path: str) -> list[type[AltPlotMethodBase]]:
        if path not in sys.path:
            sys.path.append(path)
        class_list = list()
        for name in os.listdir(path):
            if name.endswith('.py') and os.path.isfile(os.path.join(path, name)):
                module = importlib.reload(importlib.import_module(name[:-3]))
                class_list.extend(c for _, c in inspect.getmembers(module, self.is_alt_plot_class))
        return class_list

    @staticmethod
    def is_alt_plot_class(obj: Any) -> bool:
        return inspect.isclass(obj) and AltPlotMethodBase in obj.mro() and obj is not AltPlotMethodBase
