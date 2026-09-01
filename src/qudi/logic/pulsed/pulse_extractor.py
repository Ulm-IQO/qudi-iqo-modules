# -*- coding: utf-8 -*-
"""
This file contains the Qudi helper classes for the extraction of laser pulses.

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
import sys
import inspect
import importlib

from qudi.util.helpers import natural_sort, iter_modules_recursive


class PulseExtractorBase:
    """
    All extractor classes to import from must inherit exclusively from this base class.
    This base class enables extractor classes masked read-only access to settings from
    PulsedMeasurementLogic.

    See BasicPulseExtractor class for an example usage.
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


class PulseExtractor(PulseExtractorBase):
    """
    Management class to automatically combine and interface extraction methods and associated
    parameters from extractor classes defined in several modules.

    Extractor class to import from must comply to the following rules:
    1) Exclusive inheritance from PulseExtractorBase class
    2) No direct access to PulsedMeasurementLogic instance except through properties defined in
       base class (read-only access)
    3) Extraction methods must be bound instance methods
    4) Extraction methods must be named starting with "ungated_" or "gated_" accordingly
    5) Extraction methods must have as first argument "count_data"
    6) Apart from "count_data" extraction methods must have exclusively keyword arguments with
       default values of the right data type. (e.g. differentiate between 42 (int) and 42.0 (float))
    7) Make sure that no two extraction methods in any module share a keyword argument of different
       default data type.
    8) The keyword "method" must not be used in the extraction method parameters

    See BasicPulseExtractor class for an example usage.
    """

    def __init__(self, pulsedmeasurementlogic):
        # Init base class
        super().__init__(pulsedmeasurementlogic)

        # Dictionaries holding references to the extraction methods
        self._gated_extraction_methods = dict()
        self._ungated_extraction_methods = dict()
        # The real, shared ExtractionParameters instance held by PulsedMeasurementLogic's
        # settings - not a copy. Mutated in place (dict item assignment) as this class's real,
        # live, authoritative state; nothing else needs to be kept in sync with it.
        self._parameters = pulsedmeasurementlogic.extraction_parameters

        # import extraction modules from default namespace package
        # "qudi.logic.pulse_extraction_methods"
        try:
            _default_extraction_ns = importlib.reload(_default_extraction_ns)
        except NameError:
            import qudi.logic.pulsed.pulse_extraction_methods as _default_extraction_ns

        # Import extraction modules and get a dict of extractor classes
        extractor_classes = list()
        for mod_finder in iter_modules_recursive(_default_extraction_ns.__path__,
                                                 _default_extraction_ns.__name__ + '.'):
            try:
                extractor_classes.extend(
                    [cls for _, cls in inspect.getmembers(importlib.import_module(mod_finder.name),
                                                          self.is_extractor_class)]
                )
            except:
                self.log.exception(
                    f'Exception while importing qudi.logic.pulse_extraction_methods sub-module '
                    f'"{mod_finder.name}":'
                )

        # Get extraction modules from non-default directory if a path has been given
        if isinstance(pulsedmeasurementlogic.extraction_import_path, str):
            try:
                extractor_classes.extend(
                    self.__import_external_extractors(
                        path=pulsedmeasurementlogic.extraction_import_path
                    )
                )
            except:
                self.log.exception(
                    f'Unable to import extraction methods from '
                    f'"{pulsedmeasurementlogic.extraction_import_path}":'
                )

        # create an instance of each class and put them in a temporary list
        extractor_instances = [cls(pulsedmeasurementlogic) for cls in extractor_classes]

        # add references to all extraction methods in each instance to a dict
        self.__populate_method_dicts(instance_list=extractor_instances)

        # Drop any persisted parameter that no longer corresponds to a real method (e.g.
        # removed or renamed since last session)
        valid_parameter_names = set()
        for method in list(self._ungated_extraction_methods.values()) + list(self._gated_extraction_methods.values()):
            valid_parameter_names.update(inspect.signature(method).parameters.keys())
        valid_parameter_names.discard('count_data')
        for param in [p for p in list(self._parameters) if p not in valid_parameter_names and p != 'method']:
            del self._parameters[param]

        # Fill in defaults (from method signatures) for any valid parameter name not already
        # present in the shared, persisted self._parameters dict (e.g. a newly discovered
        # method). Never overwrites an already-persisted value.
        self.__populate_parameter_dict()

        # Ensure a valid current method is selected for the current gated/ungated mode
        available_methods = self._gated_extraction_methods if self.is_gated else self._ungated_extraction_methods
        if self._parameters.get('method') not in available_methods:
            invalid_method = self._parameters.get('method')
            if invalid_method is not None:
                self.log.warning(
                    'Extraction method "{0}" could not be found in PulseExtractor. '
                    'Falling back to default.'.format(invalid_method)
                )
            self._parameters['method'] = natural_sort(available_methods)[0]
        return

    @property
    def extraction_settings(self):
        """
        This property holds all parameters needed for the currently selected extraction_method as
        well as the currently selected method name.

        Returns
        -------
        dict
            Dictionary with keys being the parameter name and values being the parameter.
        """
        current_method = self._parameters.get('method')
        # Get reference to the extraction method
        if self.is_gated:
            method = self._gated_extraction_methods.get(current_method)
        else:
            method = self._ungated_extraction_methods.get(current_method)

        # Get keyword arguments for the currently selected method
        settings_dict = self._get_extraction_method_kwargs(method)

        # Attach current extraction method name
        settings_dict['method'] = current_method
        return settings_dict

    @extraction_settings.setter
    def extraction_settings(self, settings_dict):
        """
        Update parameters contained in self._parameters by values in settings_dict.
        Also sets the current extraction method by passing its name using key "method".
        Parameters not included in self._parameters (except "method") will be ignored.

        Parameters
        ----------
        settings_dict : dict
            Dictionary containing the parameters to set (name, value).
        """
        if not isinstance(settings_dict, dict):
            return

        # go through all key-value pairs in settings_dict and update self._parameters
        # (including the current method, stored under the 'method' key) accordingly. Ignore
        # unknown parameters.
        for parameter, value in settings_dict.items():
            if parameter == 'method':
                if (value in self._gated_extraction_methods and self.is_gated) or (
                        value in self._ungated_extraction_methods and not self.is_gated):
                    self._parameters['method'] = value
                else:
                    self.log.error('Extraction method "{0}" could not be found in PulseExtractor.'
                                   ''.format(value))
            elif parameter in self._parameters:
                self._parameters[parameter] = value
            else:
                self.log.warning('No extraction parameter "{0}" found in PulseExtractor.\n'
                                 'Parameter will be ignored.'.format(parameter))
        return

    @property
    def extraction_methods(self):
        """
        Return available extraction methods depending on if the fast counter is gated or not.

        Returns
        -------
        dict
            Dictionary with keys being the method names and values being the methods.
        """
        if self.is_gated:
            return self._gated_extraction_methods
        else:
            return self._ungated_extraction_methods

    def extract_laser_pulses(self, count_data):
        """
        Wrapper method to call the currently selected extraction method with count_data and the
        appropriate keyword arguments.

        Parameters
        ----------
        count_data : numpy.ndarray
            1D (ungated) or 2D (gated) numpy array (dtype='int64') containing the timetrace to
            extract laser pulses from.

        Returns
        -------
        dict
            Result dictionary of the extraction method.
        """
        if count_data.ndim > 1 and not self.is_gated:
            self.log.error('"is_gated" flag is set to False but the count data to extract laser '
                           'pulses from is in the format of a gated timetrace (2D numpy array).')
        elif count_data.ndim == 1 and self.is_gated:
            self.log.error('"is_gated" flag is set to True but the count data to extract laser '
                           'pulses from is in the format of an ungated timetrace (1D numpy array).')

        current_method = self._parameters.get('method')
        if self.is_gated:
            extraction_method = self._gated_extraction_methods[current_method]
        else:
            extraction_method = self._ungated_extraction_methods[current_method]
        kwargs = self._get_extraction_method_kwargs(extraction_method)
        return extraction_method(count_data=count_data, **kwargs)

    def _get_extraction_method_kwargs(self, method):
        """
        Get the proper values for keyword arguments other than "count_data" for <method>.
        Try to take the values from self._parameters. If the keyword is missing in the dictionary,
        take the default values from the method signature.

        Parameters
        ----------
        method
            Reference to a callable extraction method.

        Returns
        -------
        dict
            A dictionary containing the argument keywords for <method> and corresponding values
            from self._parameters.
        """
        kwargs_dict = dict()
        method_signature = inspect.signature(method)
        for name in method_signature.parameters.keys():
            if name == 'count_data':
                continue

            default = method_signature.parameters[name].default
            recalled = self._parameters.get(name)

            if recalled is not None and type(recalled) == type(default):
                kwargs_dict[name] = recalled
            else:
                kwargs_dict[name] = default
        return kwargs_dict

    def __import_external_extractors(self, path):
        """ Helper method to import all modules from a given directory.
        Find all classes in those modules that inherit exclusively from PulseExtractorBase and
        return a list of them.

        Parameters
        ----------
        path : str
            Path to import modules from.

        Returns
        -------
        list
            A list of imported valid extractor classes.
        """
        class_list = list()
        # Get all python modules to import from.
        # The assumption is that in the directory pulse_extraction_methods, there are
        # *.py files, which contain only extractor classes!
        module_list = [name[:-3] for name in os.listdir(path) if
                       os.path.isfile(os.path.join(path, name)) and name.endswith('.py')]

        # append import path to sys.path
        if path not in sys.path:
            sys.path.append(path)

        # Go through all modules and create instances of each class found.
        for module_name in module_list:
            # import module
            mod = importlib.import_module(str(module_name))
            importlib.reload(mod)
            # get all extractor class references defined in the module
            tmp_list = [m[1] for m in inspect.getmembers(mod, self.is_extractor_class)]
            # append to class_list
            class_list.extend(tmp_list)
        return class_list

    def __populate_method_dicts(self, instance_list):
        """
        Helper method to populate the dictionaries containing all references to callable extraction
        methods contained in extractor instances passed to this method.

        Parameters
        ----------
        instance_list : list
            List containing instances of extractor classes.
        """
        self._ungated_extraction_methods = dict()
        self._gated_extraction_methods = dict()
        for instance in instance_list:
            for method_name, method_ref in inspect.getmembers(instance, inspect.ismethod):
                if method_name.startswith('gated_'):
                    self._gated_extraction_methods[method_name[6:]] = method_ref
                elif method_name.startswith('ungated_'):
                    self._ungated_extraction_methods[method_name[8:]] = method_ref
        return

    def __populate_parameter_dict(self):
        """
        Helper method to fill in default values (from method signatures) for any extraction
        method keyword argument not already present in the shared self._parameters dict. Never
        overwrites an already-present (e.g. persisted) value.
        """
        for method in self._ungated_extraction_methods.values():
            for name, default in self._get_extraction_method_kwargs(method=method).items():
                self._parameters.setdefault(name, default)
        for method in self._gated_extraction_methods.values():
            for name, default in self._get_extraction_method_kwargs(method=method).items():
                self._parameters.setdefault(name, default)
        return

    @staticmethod
    def is_extractor_class(obj):
        """
        Helper method to check if an object is a valid extractor class.

        Parameters
        ----------
        obj : object
            Object to check.

        Returns
        -------
        bool
            True if obj is a valid extractor class, False otherwise.
        """
        if inspect.isclass(obj):
            return PulseExtractorBase in obj.__bases__ and len(obj.__bases__) == 1
        return False
