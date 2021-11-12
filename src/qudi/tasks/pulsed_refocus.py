# -*- coding: utf-8 -*-
"""
Optimizer refocus task with laser on.

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


class PulsedRefocusTask(ModuleTask):
    """ This task pauses pulsed measurement, run laser_on, does a poi refocus then goes back to the pulsed acquisition.

    It uses poi manager refocus duration as input.

    Example:
    taskrunnerlogic:
        module.Class: taskrunner.TaskRunnerLogic
        module_tasks:
            pulsed_refocus_task:
                module.Class: qudi.tasks.pulsed_refocus.PulsedRefocusTask
                connect:
                    pulsedmasterlogic: pulsed_master_logic
    """

    pulsedmasterlogic = Connector(name='pulsedmasterlogic', interface='PulsedMasterLogic')
    # poimanager not available yet
    #_poi_manager = Connector(interface='PoiManager')

    def _setup(self):
        self.init()

        self._was_running = self._measurement.module_state() == 'locked'
        if self._was_running:
            self._measurement.stop_pulsed_measurement('refocus')
        self._was_loaded = tuple(self._generator.loaded_asset)
        # self._was_power = self._laser.get_power_setpoint()
        self._was_invoke_settings = self._measurement.measurement_settings['invoke_settings']

        self.wait_for_idle()
        self._generator.generate_predefined_sequence(predefined_sequence_name='laser_on', kwargs_dict={})
        self._generator.sample_pulse_block_ensemble('laser_on')
        self._generator.load_ensemble('laser_on')
        #self._measurement.set_measurement_settings(invoke_settings=False)
        #self._measurement.start_pulsed_measurement()
        self._measurement.pulse_generator_on()

    def _run(self):
        """ Stop pulsed with backup , start laser_on, do refocus """
        self.log.info("Task would now optimize, if there was an optimiserlogic yet.")
        # self._poi_manager.optimise_poi_position()


    def _cleanup(self):
        """ go back to pulsed acquisition from backup """
        self._measurement.pulse_generator_off()
        ##self._measurement.stop_pulsed_measurement()
        self.wait_for_idle()
        # todo what about sequences
        if self._was_loaded[1] == 'PulseBlockEnsemble' and self._was_loaded[0] != "":
            self._generator.sample_pulse_block_ensemble(self._was_loaded[0])
            self._generator.load_ensemble(self._was_loaded[0])
        # self._laser.set_power(self._was_power)
        if self._was_invoke_settings:
            self._measurement.set_measurement_settings(invoke_settings=True)
        if self._was_running:
            self._measurement.start_pulsed_measurement('refocus')

    def wait_for_idle(self, timeout=20):
        """ Function to wait for the measurement to be idle

        @param timeout: the maximum time to wait before causing an error (in seconds)
        """
        counter = 0
        while self._measurement.module_state() != 'idle' and self._master.status_dict['measurement_running'] and\
                counter < timeout:
            time.sleep(0.1)
            counter += 0.1
        if counter >= timeout:
            self.log.warning('Measurement is too long to stop, continuing anyway')

    def init(self):
        """
        Setup class variables. Not using a constructor as connector would be no ready yet.
        """
        self._was_invoke_settings = None
        self._was_running = None
        self._was_loaded = None

        self._generator = self.pulsedmasterlogic().sequencegeneratorlogic()
        self._measurement = self.pulsedmasterlogic().pulsedmeasurementlogic()

