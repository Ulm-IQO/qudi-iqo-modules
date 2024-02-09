# -*- coding: utf-8 -*-

"""
This file contains the data classes for spectrum instrumentation fast counting devices.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""
import threading
import time
from qudi.util.mutex import Mutex


class LoopManager:
    '''
    This is the main data process loop class.
    The loop keeps on asking the commander to do the measurement process.
    '''
    threadlock = Mutex()

    def __init__(self, commander, log):
        self.commander = commander
        self._log = log

        self.loop_on = False
        self._reps = 0
        self.start_time = 0
        self.data_proc_th = None

    def input_ms(self, ms):
        self._reps = ms.reps

    def init_measure_params(self):
        self.start_time = time.time()
        self.loop_on = False

    def start_data_process(self):
        """
        Start the data process with creating a new thread.
        """
        self.loop_on = True
        self.data_proc_th = threading.Thread(target=self._start_data_process_loop)
        self.data_proc_th.start()

        return

    def _start_data_process_loop(self):
        time_out = self.commander.cmd.process.wait_avail_data()
        if time_out:
            return

        self.commander.do_init_process()

        if self._reps == 0:
            self._start_inifinite_loop()
        else:
            self._start_finite_loop()

    def _start_inifinite_loop(self):
        self._log.info('data process started')
        while self.loop_on:
            self.commander.command_process()
        else:
            self._log.info('data process stopped')
            return

    def _start_finite_loop(self):
        self._log.info('data process started')
        while self.loop_on and self.commander.processor.avg.num < self._reps:
            self.commander.command_process()
        else:
            self._log.info('data process stopped')
            return

    def stop_data_process(self):
        with self.threadlock:
            self.loop_on = False

        self.data_proc_th.join()
