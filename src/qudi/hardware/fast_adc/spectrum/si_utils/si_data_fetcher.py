# -*- coding: utf-8 -*-
"""
This file contains the data fetcher class for spectrum instrumentation ADC.

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
import numpy as np
import ctypes as c


class DataFetcher:
    """
    This class can fetch the data from the data buffer and the timestamps from the timestamp buffer.
    """

    def __init__(self):
        self.hw_dt = None
        self.ts_dt = None

    def init_buffer(self, ms):
        """
        @param MeasurementSettings ms: MeasurementSettings instance from the main.
        """
        self.hw_dt = DataTransfer(ms.c_buf_ptr, ms.data_type,
                                  ms.data_bytes_B, ms.seq_size_S, ms.reps_per_buf)
        if ms.gated:
            self.ts_dt = DataTransfer(ms.c_ts_buf_ptr, ms.ts_data_type,
                                      ms.ts_data_bytes_B, ms.ts_seq_size_S, ms.reps_per_buf)

    def fetch_data(self, curr_avail_reps, user_pos_B):
        data = self.hw_dt.get_new_data(curr_avail_reps, user_pos_B)
        return data

    def fetch_ts_data(self, curr_avail_reps, ts_user_pos_B):
        ts_row = self.ts_dt.get_new_data(curr_avail_reps, ts_user_pos_B).astype(np.uint64)
        ts_r, ts_f = self._get_ts_rf(ts_row)
        return ts_r, ts_f

    def _get_ts_rf(self, ts_row):
        """
        Get the timestamps for the rising and falling edges.
        Refer to 'Timestamps' in the documentation for the timestamp data format.
        """
        ts_used = ts_row[::2]  # odd data is always filled with zero
        ts_r = ts_used[::2]
        ts_f = ts_used[1::2]
        return ts_r, ts_f


class DataTransfer:
    '''
    This class can access the data stored in the buffer by specifying the buffer pointer.
    Refer to the chapter 'Buffer handling' in the manual.
    '''

    def __init__(self, c_buf_ptr, data_type, data_bytes_B, seq_size_S, reps_per_buf):
        self.c_buf_ptr = c_buf_ptr
        self.data_type = data_type
        self.data_bytes_B = data_bytes_B
        self.seq_size_S = seq_size_S
        self.seq_size_B = seq_size_S * self.data_bytes_B
        self.reps_per_buf = reps_per_buf

    def _cast_buf_ptr(self, user_pos_B):
        """
        Return the pointer of the specified data type at the user position

        @params int user_pos_B: user position in bytes

        @return ctypes pointer
        """
        c_buffer = c.cast(c.addressof(self.c_buf_ptr) + user_pos_B, c.POINTER(self.data_type))
        return c_buffer

    def _asnparray(self, c_buffer, shape):
        """
        Create a numpy array from the ctypes pointer with selected shape.

        @param ctypes pointer for the buffer

        @return numpy array
        """

        np_buffer = np.ctypeslib.as_array(c_buffer, shape=shape)
        return np_buffer

    def _fetch_from_buf(self, user_pos_B, sample_S):
        """
        Fetch given number of samples at the user position.

        @params int user_pos_B: user position in bytes
        @params int sample_S: number of samples to fetch

        @return numpy array: 1D data
        """
        shape = (sample_S,)
        c_buffer = self._cast_buf_ptr(user_pos_B)
        np_buffer = self._asnparray(c_buffer, shape)
        return np_buffer

    def get_new_data(self, curr_avail_reps, user_pos_B):
        """
        Get the currently available new data at the user position
        dependent on the relative position of the user position in the buffer.

        @params int user_pos_B: user position in bytes
        @params int curr_avail_reps: the number of currently available repetitions

        @return numpy array
        """
        rep_end = int(user_pos_B / self.seq_size_B) + curr_avail_reps

        if 0 < rep_end <= self.reps_per_buf:
            np_data = self._fetch_data(curr_avail_reps, user_pos_B)

        elif self.reps_per_buf < rep_end < 2 * self.reps_per_buf:
            np_data = self._fetch_data_buf_end(curr_avail_reps, user_pos_B)
        else:
            print('error: rep_end {} is out of range'.format(rep_end))
            return

        return np_data

    def _fetch_data(self, curr_avail_reps, user_pos_B):
        """
        Fetch currently available data at user position in one shot
        """
        np_data = self._fetch_from_buf(user_pos_B, curr_avail_reps * self.seq_size_S)
        return np_data

    def _fetch_data_buf_end(self, curr_avail_reps, user_pos_B):
        """
        Fetch currently available data at user position which exceeds the end of the buffer.
        """
        processed_rep = int((user_pos_B / self.seq_size_B))
        reps_tail = self.reps_per_buf - processed_rep
        reps_head = curr_avail_reps - reps_tail

        np_data_tail = self._fetch_data(user_pos_B, reps_tail)
        np_data_head = self._fetch_data(0, reps_head)
        np_data = np.append(np_data_tail, np_data_head)

        return np_data


