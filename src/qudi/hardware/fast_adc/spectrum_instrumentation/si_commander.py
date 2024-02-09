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
from qudi.hardware.fast_adc.spectrum_instrumentation.si_settings import MeasurementSettings

class Commander:
    def __init__(self, cmd, log):
        self.cmd = cmd
        self.processor = None
        self._log = log

        self._seq_size_B = 0
        self._reps = 0
        self._gated = False
        self.unprocessed_reps_limit = 0

    def init(self, ms: MeasurementSettings, data_processor):
        self._seq_size_B = ms.seq_size_B
        self._reps = ms.reps
        self._gated = ms.gated
        self.unprocessed_reps_limit = ms.reps_per_buf

        self._assign_process(ms.gated, ms.data_stack_on)

        self.processor = data_processor

    def _assign_process(self, gated, stack_on):

        def process_data_ungated_stackoff():
            curr_avail_reps = self.cmd.process.get_curr_avail_reps()
            if curr_avail_reps == 0:
                return
            user_pos_B = self.cmd.data_buf.get_avail_user_pos_B()
            self.processor.update_data(curr_avail_reps, user_pos_B)
            self.cmd.data_buf.set_avail_card_len_B(curr_avail_reps * self._seq_size_B)

        def process_data_ungated_stackon():
            process_data_ungated_stackoff()
            self.processor.stack_new_data()

        def process_data_gated_stackoff():
            curr_avail_reps = self.cmd.process.get_curr_avail_reps()
            if curr_avail_reps == 0:
                return
            user_pos_B = self.cmd.data_buf.get_avail_user_pos_B()
            ts_user_pos_B = self.cmd.ts_buf.get_ts_avail_user_pos_B()
            self.processor.update_data(curr_avail_reps, user_pos_B, ts_user_pos_B)
            self.cmd.data_buf.set_avail_card_len_B(curr_avail_reps * self._seq_size_B)

        def process_data_gated_stackon():
            process_data_gated_stackoff()
            self.processor.stack_new_data()
            self.processor.stack_new_ts()

        if gated:
            if stack_on:
                self._process_curr_avail_data = process_data_gated_stackon
            else:
                self._process_curr_avail_data = process_data_gated_stackoff
        else:
            if stack_on:
                self._process_curr_avail_data = process_data_ungated_stackon
            else:
                self._proess_curr_avail_data = process_data_ungated_stackoff

    def do_init_process(self):
        curr_avail_reps = self.cmd.process.get_curr_avail_reps()
        user_pos_B = self.cmd.data_buf.get_avail_user_pos_B()
        ts_user_pos_B = self.cmd.ts_buf.get_ts_avail_user_pos_B() if self._gated else None
        self.processor.process_data(curr_avail_reps, user_pos_B, ts_user_pos_B)
        self.processor.get_initial_avg()
        self.cmd.data_buf.set_avail_card_len_B(curr_avail_reps * self._seq_size_B)

    def command_process(self):
        """
        Command the main process dependent on the repetitions of unprocessed data.
        """

        trig_reps = self.cmd.process.get_trig_reps()
        processed_reps = self.processor.avg.num
        unprocessed_reps = trig_reps - processed_reps
        if self._reps != 0 and processed_reps >= self._reps:
            return

        if trig_reps == 0 or unprocessed_reps == 0:
            self.cmd.process.toggle_trigger(True)
            self.cmd.process.wait_new_trig_reps(trig_reps)

        elif unprocessed_reps < self.unprocessed_reps_limit:
            self.cmd.process.toggle_trigger(True)
            self._process_curr_avail_data()

        elif unprocessed_reps >= self.unprocessed_reps_limit:
            self._log.info('trigger off for too much unprocessed data')
            self.cmd.process.toggle_trigger(False)
            self._process_curr_avail_data()
