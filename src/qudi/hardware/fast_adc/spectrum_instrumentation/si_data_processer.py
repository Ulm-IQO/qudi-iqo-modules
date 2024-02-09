# -*- coding: utf-8 -*-
"""
This file contains the data processer classes for spectrum instrumentation ADC.

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
import copy

class DataProcessorUngated:
    """
    This class brings the data through the fetcher class and store it in the data class for ungated measurement.
    """
    def __init__(self, data, fetcher):
        self.dc_new = data.dc_new
        self.dc = data.dc
        self.avg = data.avg
        self.avg_new = data.avg_new
        self.fetcher = fetcher

    def process_data(self, curr_avail_reps, user_pos_B, *args):
        self._fetch_data(curr_avail_reps, user_pos_B)
        self._get_new_avg_data()

    def get_initial_avg(self):
        self.avg.data = copy.deepcopy(self.avg_new.data)
        self.avg.num = copy.copy(self.avg_new.num)

    def update_data(self, curr_avail_reps, user_pos_B, *args):
        self.process_data(curr_avail_reps, user_pos_B)
        self._update_avg_data()

    def _fetch_data(self, curr_avail_reps, user_pos_B):
        self.dc_new.data = self.fetcher.fetch_data(curr_avail_reps, user_pos_B)
        self.dc_new.rep = curr_avail_reps
        self.dc_new.set_len()
        self.dc_new.data = self.dc_new.reshape_2d_by_rep()

    def stack_new_data(self):
        self.dc.stack_rep(self.dc_new)

    def _get_new_avg_data(self):
        self.avg_new.data = self.dc_new.avgdata()
        self.avg_new.num = copy.copy(self.dc_new.rep)

    def _update_avg_data(self):
        self.avg.update(self.avg_new)
        self.avg.set_len()

class DataProcessGated(DataProcessorUngated):
    """
    This class brings the data through the fetcher class and store it in the data class for gated measurement.
    In addition to the data, it also collects the timestamps.
    """

    def process_data(self, curr_avail_reps, user_pos_B, ts_user_pos_B):
        self._fetch_data(curr_avail_reps, user_pos_B)
        self._fetch_ts(curr_avail_reps, ts_user_pos_B)
        self._get_new_avg_data()

    def update_data(self, curr_avail_reps, user_pos_B, ts_user_pos_B):
        self.process_data(curr_avail_reps, user_pos_B, ts_user_pos_B)
        self._update_avg_data()

    def _fetch_ts(self, curr_avail_reps, ts_user_pos_B):
        self.dc_new.ts_r, self.dc_new.ts_f = self.fetcher.fetch_ts_data(curr_avail_reps, ts_user_pos_B)

    def stack_new_ts(self):
        self.dc.stack_rep_gated(self.dc_new)






