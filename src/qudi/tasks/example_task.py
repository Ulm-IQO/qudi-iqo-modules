# -*- coding: utf-8 -*-
"""
Exemplary task to demonstrate interaction with other iqo-modules.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-core/>

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


from qudi.core.scripting.moduletask import ModuleTask
from qudi.core.connector import Connector

import time


class ExampleTask(ModuleTask):
    """ This task pauses pulsed measurement, run laser_on, does a poi refocus then goes back to the pulsed acquisition.

    It uses poi manager refocus duration as input.

    Example:
    taskrunnerlogic:
        module.Class: taskrunner.TaskRunnerLogic
        module_tasks:
            test_task:
                module.Class: qudi.tasks.example_task.ExampleTask
                connect:
                    pulsedmasterlogic: pulsed_master_logic
    """

    pulsedmasterlogic = Connector(name='pulsedmasterlogic', interface='PulsedMasterLogic')
    # poimanager not available yet
    #_poi_manager = Connector(interface='PoiManager')

    def _setup(self):
        self.init()
        generator = self.pulsedmasterlogic().sequencegeneratorlogic()
        generator.sample_pulse_block_ensemble('laser_on')
        generator.load_ensemble('laser_on')


    def _run(self):
        """ Stop pulsed with backup , start laser_on, do refocus """
        self.log.info("Task would now optimize, if there was and optimiserlogic yet.")
        # self._poi_manager.optimise_poi_position()


    def _cleanup(self):
        """ go back to pulsed acquisition from backup """
        self._measurement.pulse_generator_off()

