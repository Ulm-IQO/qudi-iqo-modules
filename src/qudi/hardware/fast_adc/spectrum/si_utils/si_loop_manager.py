# -*- coding: utf-8 -*-

"""
This file contains the data classes for spectrum instrumentation ADC.

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
import time
from qudi.util.mutex import Mutex
from PySide2.QtCore import QThread


class LoopManager(QThread):
    """
    This is the main data process loop class.
    The loop keeps on asking the commander to do the measurement process.
    """
    threadlock = Mutex()

    def __init__(self, commander, log):
        """
        @param Commander commander: Commander instance from the main.
        @param log: qudi logger from the SpectrumInstrumentation.
        """
        super().__init__()
        self.commander = commander
        self._log = log

        self.loop_on = False
        self._reps = 0
        self.start_time = 0
        self.data_proc_th = None

    def input_ms(self, ms):
        self._reps = ms.reps

    def start_data_process(self):
        """
        Start the data process with creating a new thread.
        """
        self.start_time = time.time()
        self.loop_on = True
        return

    def run(self):
        self._log.debug('loop running')
        time_out = self.commander.cmd.process.wait_avail_data()
        if time_out:
            return

        self.commander.do_init_process()

        if self._reps == 0:
            self._start_inifinite_loop()
        else:
            self._start_finite_loop()
        self._log.debug('loop finished')

    def _start_inifinite_loop(self):
        self._log.debug('data process started')
        while self.loop_on:
            self.commander.command_process()
        else:
            self._log.debug('data process stopped')
            return

    def _start_finite_loop(self):
        self._log.debug('data process started')
        num_rep = self.commander.processor.data.dc.num_rep
        while self.loop_on and num_rep < self._reps:
            self.commander.command_process()
        else:
            self._log.debug('data process stopped')
            return

    def stop_data_process(self):
        with self.threadlock:
            self.loop_on = False