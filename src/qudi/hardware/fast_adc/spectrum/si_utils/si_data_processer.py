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
        """
        @param Data data: Data instance from the main.
        @param DataFetcher fetcher: DataFetcher instance from the main.
        """
        self.data = data
        self.fetcher = fetcher

    def process_data(self, curr_avail_reps, user_pos_B, *args):
        self._fetch_data(curr_avail_reps, user_pos_B)
        self._get_new_avg_data()

    def _fetch_data(self, curr_avail_reps, user_pos_B):
        self.data.dc_new.data = self.fetcher.fetch_data(curr_avail_reps, user_pos_B).reshape(curr_avail_reps, -1)
        if self.data.data_info.num_channels == 2:
            self.data.dc_new.data = self.data.dc_new.dual_ch_data.copy()
        self.data.dc_new.num_rep = curr_avail_reps

    def _get_new_avg_data(self):
        self.data.avg_new.data = self.data.dc_new.get_average()
        self.data.avg_new.num_rep = copy.copy(self.data.dc_new.num_rep)

    def get_initial_data(self):
        self.data.dc.data = copy.deepcopy(self.data.dc_new.data)
        self.data.dc.num_rep = copy.deepcopy(self.data.dc_new.num_rep)

    def get_initial_avg(self):
        self.data.avg.data = copy.deepcopy(self.data.avg_new.data)
        self.data.avg.num_rep = copy.copy(self.data.avg_new.num_rep)

    def update_data(self, curr_avail_reps, user_pos_B, *args):
        self.process_data(curr_avail_reps, user_pos_B)
        self.data.avg.update(self.data.avg_new)

    def stack_new_data(self):
        self.data.dc.stack(self.data.dc_new)

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
        self.data.avg.update(self.data.avg_new)

    def _fetch_ts(self, curr_avail_reps, ts_user_pos_B):
        self.data.ts_new.ts_r, self.data.ts_new.ts_f = self.fetcher.fetch_ts_data(curr_avail_reps, ts_user_pos_B)

    def stack_new_ts(self):
        self.data.ts.stack(self.data.ts_new)






