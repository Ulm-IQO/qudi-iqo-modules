# -*- coding: utf-8 -*-

"""
This file contains command classes used for spectrum instrumentation fast counting devices.

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
import time
import numpy as np

from qudi.hardware.fast_adc.spectrum_instrumentation.si_commands.card_commands import CardCommands
from qudi.hardware.fast_adc.spectrum_instrumentation.si_commands.buffer_commands \
    import DataBufferCommands, TsBufferCommands
from qudi.hardware.fast_adc.spectrum_instrumentation.si_commands.configure_commands import ConfigureCommands


class Commands:
    '''
    This class has low-level hardware commands and action commands used in the data process.
    '''
    def __init__(self, card, log):
        self.card = CardCommands(card)
        self.data_buf = DataBufferCommands(card)
        self.ts_buf = TsBufferCommands(card)
        self.cfg = ConfigureCommands(card, log)
        self.process = ProcessAction(self)

class ProcessAction:
    '''
    This class has actions used in the data process.
    '''
    def __init__(self, commands):
        self.cmd = commands

        self.wait_time_interval = 0.01

        self.trigger_enabled = False
        self._seq_size_B = 0
        self._total_gate = 0
        self._ts_seq_size_B = 0

    def input_ms(self, ms):
        self._seq_size_B = ms.seq_size_B
        self._assign_methods(ms.gated)
        self._total_gate = ms.total_gate
        if ms.gated:
            self._ts_seq_size_B = ms.ts_seq_size_B

    def _assign_methods(self, gated):
        if gated:
            self.get_trig_reps = self._get_trig_reps_gated
            self.get_curr_avail_reps = self._get_curr_avail_reps_gated
        else:
            self.get_trig_reps = self._get_trig_reps_ungated
            self.get_curr_avail_reps = self._get_curr_avail_reps_ungated


    def get_avail_user_reps(self):
        return int(np.floor(self.cmd.data_buf.get_avail_user_len_B() / self._seq_size_B))

    def get_ts_avail_user_reps(self):
        return int(self.cmd.ts_buf.get_ts_avail_user_len_B() / self._ts_seq_size_B)

    def toggle_trigger(self, trigger_on):
        if trigger_on == self.trigger_enabled:
            return
        else:
            if trigger_on:
                self.trigger_enabled = self.cmd.card.enable_trigger()
            else:
                self.trigger_enabled = self.cmd.card.disable_trigger()

    def wait_new_trig_reps(self, prev_trig_reps):
        curr_trig_reps = self.get_trig_reps()
        while curr_trig_reps == prev_trig_reps:
            curr_trig_reps = self.get_trig_reps()
            time.sleep(self.wait_time_interval)
        return curr_trig_reps

    def _get_trig_reps_ungated(self):
        return int(self.cmd.data_buf.get_trig_counter())

    def _get_trig_reps_gated(self):
        return int(self.cmd.data_buf.get_trig_counter() / self._total_gate)

    def _get_curr_avail_reps_ungated(self):
        return self.cmd.data_buf.get_avail_user_reps()

    def _get_curr_avail_reps_gated(self):
        curr_data_avail_reps = self.cmd.process.get_avail_user_reps()
        curr_ts_avail_reps = self.cmd.process.get_ts_avail_user_reps()
        return min(curr_data_avail_reps, curr_ts_avail_reps)

    def wait_avail_data(self):
        initial_time = time.time()
        curr_avail_reps = self.get_curr_avail_reps()
        while curr_avail_reps == 0:
            curr_avail_reps = self.get_curr_avail_reps()
            current_time = time.time() - initial_time
            if current_time > 10:
                time_out = True
                return time_out
            time.sleep(self.wait_time_interval)
        time_out = False
        return time_out
