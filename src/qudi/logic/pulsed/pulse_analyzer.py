# -*- coding: utf-8 -*-
"""
This file contains the Qudi logic for analysis of laser pulses.

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

import inspect
import importlib
from functools import partial


from qudi.util.helpers import natural_sort
from qudi.util.module_finder import get_modules_from_ns, get_modules_from_path, is_subclass



class PulseAnalyzerBase:
    """
    All analyzer classes to import from must inherit exclusively from this base class.
    This base class enables analyzer classes masked read-only access to settings from
    PulsedMeasurementLogic.

    See BasicPulseAnalyzer class for an example usage.
    """
    def __init__(self, pulsedmeasurementlogic):
        self.__pulsedmeasurementlogic = pulsedmeasurementlogic

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
    def log(self):
        return self.__pulsedmeasurementlogic.log


class PulseAnalyzer(PulseAnalyzerBase):
    """
    Management class to automatically combine and interface analysis methods and associated
    parameters from analyzer classes defined in several modules.

    Analyzer class to import from must comply to the following rules:
    1) Exclusive inheritance from PulseAnalyzerBase class
    2) No direct access to PulsedMeasurementLogic instance except through properties defined in
       base class (read-only access)
    3) Analysis methods must be bound instance methods
    4) Analysis methods must be named starting with "analyse_"
    5) Analysis methods must have as first argument "laser_data"
    6) Apart from "laser_data" analysis methods must have exclusively keyword arguments with
       default values of the right data type. (e.g. differentiate between 42 (int) and 42.0 (float))
    7) Make sure that no two analysis methods in any module share a keyword argument of different
       default data type.
    8) The keyword "method" must not be used in the analysis method parameters

    See BasicPulseAnalyzer class for an example usage.
    """

    def __init__(self, pulsedmeasurementlogic):
        # Init base class
        super().__init__(pulsedmeasurementlogic)

        # Dictionary holding references to all analysis methods
        self._analysis_methods = dict()
        # The real, shared AnalysisParameters instance held by PulsedMeasurementLogic's settings
        self._parameters = pulsedmeasurementlogic.analysis_parameters
        # Currently selected analysis method
        self._current_analysis_method = None
        # Analyzer sub class predicate
        self.is_analyzer_class = partial(is_subclass, base=PulseAnalyzerBase)


        # import analysis modules from default namespace package
        # "qudi.logic.pulsed.pulsed_analysis_methods"
        try:
            _default_analysis_ns = importlib.reload(_default_analysis_ns)
        except NameError:
            import qudi.logic.pulsed.pulsed_analysis_methods as _default_analysis_ns

        # Import analysis modules and get a dict of analysis classes
        analysis_classes = list(get_modules_from_ns(_default_analysis_ns,
                                                      self.is_analyzer_class,
                                                      logger=self.log).values())

        # Get analysis modules from non-default directory if a path has been given
        if isinstance(pulsedmeasurementlogic.analysis_import_path, str):
            try:
                analysis_classes.extend(
                    get_modules_from_path(pulsedmeasurementlogic.analysis_import_path,
                                          self.is_analyzer_class)
                )
            except:
                self.log.exception(
                    f'Unable to import analysis methods from '
                    f'"{pulsedmeasurementlogic.analysis_import_path}":'
                )

        # create an instance of each class and put them in a temporary list
        analyzer_instances = [cls(pulsedmeasurementlogic) for cls in analysis_classes]

        # add references to all analysis methods in each instance to a dict
        self.__populate_method_dict(instance_list=analyzer_instances)

        # Drop any persisted parameter that no longer corresponds to a real method 
        valid_parameter_names = set()
        for method in self._analysis_methods.values():
            valid_parameter_names.update(inspect.signature(method).parameters.keys())
        valid_parameter_names.discard('laser_data')
        for param in [p for p in list(self._parameters) if p not in valid_parameter_names and p != 'method']:
            del self._parameters[param]

        # Fill in defaults (from method signatures) for any valid parameter name not already
        # present in the shared, persisted self._parameters dict 
        self.__populate_parameter_dict()

        # Ensure a valid current method is selected
        if self._parameters.get('method') not in self._analysis_methods:
            invalid_method = self._parameters.get('method')
            if invalid_method is not None:
                self.log.warning(
                    'Analysis method "{0}" could not be found in PulseAnalyzer. '
                    'Falling back to default.'.format(invalid_method)
                )
            self._parameters['method'] = natural_sort(self._analysis_methods)[0]
        return

    @property
    def analysis_settings(self):
        """
        This property holds all parameters needed for the currently selected analysis_method as
        well as the currently selected method name.

        @return dict: dictionary with keys being the parameter name and values being the parameter
        """
        current_method = self._parameters.get('method')
        # Get reference to the extraction method
        method = self._analysis_methods.get(current_method)

        # Get keyword arguments for the currently selected method
        settings_dict = self._get_analysis_method_kwargs(method)

        # Attach current analysis method name
        settings_dict['method'] = current_method
        return settings_dict

    @analysis_settings.setter
    def analysis_settings(self, settings_dict):
        """
        Update parameters contained in self._parameters by values in settings_dict.
        Also sets the current analysis method by passing its name using key "method".
        Parameters not included in self._parameters (except "method") will be ignored.

        @param dict settings_dict: dictionary containing the parameters to set (name, value)
        """
        if not isinstance(settings_dict, dict):
            return

        # go through all key-value pairs in settings_dict and update self._parameters
        # (including the current method, stored under the 'method' key) accordingly. Ignore
        # unknown parameters.
        for parameter, value in settings_dict.items():
            if parameter == 'method':
                if value in self._analysis_methods:
                    self._parameters['method'] = value
                else:
                    self.log.error('Analysis method "{0}" could not be found in PulseAnalyzer.'
                                   ''.format(value))
            elif parameter in self._parameters:
                self._parameters[parameter] = value
            else:
                self.log.warning('No analysis parameter "{0}" found in PulseAnalyzer.\n'
                                 'Parameter will be ignored.'.format(parameter))
        return

    @property
    def analysis_methods(self):
        """
        Return available analysis methods.

        @return dict: Dictionary with keys being the method names and values being the methods.
        """
        return self._analysis_methods

    def analyse_laser_pulses(self, laser_data):
        """
        Wrapper method to call the currently selected analysis method with laser_data and the
        appropriate keyword arguments.

        @param numpy.ndarray laser_data: 2D numpy array (dtype='int64') containing the timetraces
                                         for all extracted laser pulses.
        @return (numpy.ndarray, numpy.ndarray): tuple of two numpy arrays containing the evaluated
                                                signal data (one data point for each laser pulse)
                                                and the measurement error corresponding to each
                                                data point.
        """
        current_method = self._parameters.get('method')
        analysis_method = self._analysis_methods[current_method]

        kwargs = self._get_analysis_method_kwargs(analysis_method)
        return analysis_method(laser_data=laser_data, **kwargs)

    def _get_analysis_method_kwargs(self, method):
        """
        Get the proper values for keyword arguments other than "laser_data" for <method>.
        Try to take the values from self._parameters. If the keyword is missing in the dictionary,
        take the default values from the method signature.

        @param method: reference to a callable analysis method
        @return dict: A dictionary containing the argument keywords for <method> and corresponding
                      values from self._parameters.
        """
        kwargs_dict = dict()
        method_signature = inspect.signature(method)
        for name in method_signature.parameters.keys():
            if name == 'laser_data':
                continue

            default = method_signature.parameters[name].default
            recalled = self._parameters.get(name)

            if recalled is not None and type(recalled) == type(default):
                kwargs_dict[name] = recalled
            else:
                kwargs_dict[name] = default
        return kwargs_dict

    def __populate_method_dict(self, instance_list):
        """
        Helper method to populate the dictionaries containing all references to callable analysis
        methods contained in analyzer instances passed to this method.

        Parameters
        ----------
        instance_list : list
            List containing instances of analyzer classes.
        """
        self._analysis_methods = dict()
        for instance in instance_list:
            for method_name, method_ref in inspect.getmembers(instance, inspect.ismethod):
                if method_name.startswith('analyse_'):
                    self._analysis_methods[method_name[8:]] = method_ref
        return

    def __populate_parameter_dict(self):
        """
        Helper method to fill in default values (from method signatures) for any analysis
        method keyword argument not already present in the shared self._parameters dict. Never
        overwrites an already-present (e.g. persisted) value.
        """
        for method in self._analysis_methods.values():
            for name, default in self._get_analysis_method_kwargs(method=method).items():
                self._parameters.setdefault(name, default)
        return

