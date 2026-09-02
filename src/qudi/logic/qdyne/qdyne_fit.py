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
from PySide6 import QtCore
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
        # Second container for the time-domain plot. It shares fit_config_model deliberately - both
        # plots offer the same fit menu - but keeps its own result, so fitting one does not clobber
        # the other. Its absence is why _activate_plot2_widget() was commented out.
        self.fit_container2 = FitContainer(parent=self, config_model=self.fit_config_model)

    def activate(self):
        pass
        # Fitting

    def perform_fit(self, data, fit_config, container=None):
        """
        Performs the chosen fit on the measured data.

        @param data: [x, y] to fit
        @param str fit_config: name of the fit configuration to use
        @param container: which FitContainer to fit in. Defaults to fit_container (the spectrum);
                          pass fit_container2 for the time-domain plot so the two keep separate
                          results.

        @return result_object: the lmfit result object
        """
        container = container if container is not None else self.fit_container
        config, result = container.fit_data(fit_config, data[0], data[1])
        if result:
            result.result_str = container.formatted_result(result)
        return config, result

