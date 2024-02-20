# -*- coding: utf-8 -*-
"""
This module contains a Qdyne manager class.
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
from PySide2 import QtCore
from qudi.util.datafitting import FitConfigurationsModel, FitContainer

class QdyneFit(QtCore.QObject):
    __default_fit_configs = (
        {'name': 'Lorentzian Dip',
         'model': 'Lorentzian',
         'estimator': 'Dip',
         'custom_parameters': None},

        {'name': 'Lorentzian Peak',
         'model': 'Lorentzian',
         'estimator': 'Peak',
         'custom_parameters': None},
    )

    def __init__(self, logic, fit_configs):
        super().__init__(parent=logic)
        # for fitting
        self._fit_configs = fit_configs if fit_configs is not None else self.__default_fit_configs
        self.fit_config_model = FitConfigurationsModel(parent=self)
        self.fit_config_model.load_configs(self._fit_configs)
        self.fit_container = FitContainer(parent=self, config_model=self.fit_config_model)

    def activate(self):
        pass
        # Fitting

    def perform_fit(self, data, fit_config):
        """
        Performs the chosen fit on the measured data.

        @param str fit_config: name of the fit configuration to use
        @param bool use_alternative_data: Flag indicating if the signal data (False) or the
                                          alternative signal data (True) should be fitted.
                                          Ignored if data is given as parameter

        @return result_object: the lmfit result object
        """

        config, result = self.fit_container.fit_data(fit_config, data[0], data[1])
        if result:
            result.result_str = self.fit_container.formatted_result(result)
        return config, result

