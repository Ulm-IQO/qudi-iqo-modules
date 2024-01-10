# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware for spectrum instrumentation fast counting devices.

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
from enum import IntEnum

import numpy as np
from pyspcm import *
from spcm_tools import *

from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex
from qudi.interface.fast_counter_interface import FastCounterInterface
from qudi.hardware.fast_adc.si_dataclass import *
from qudi.hardware.fast_adc.si_commands import *

class CardStatus(IntEnum):
    unconfigured = 0
    idle = 1
    running = 2
    paused = 3
    error = -1

class SpectrumInstrumentation(FastCounterInterface):

    '''
    Hardware class for the spectrum instrumentation card

    Implemented mode:
    - trigger_mode:
        'EXT' (External trigger),
        'SW' (Software trigger),
        'CH0' (Channel0 trigger)
    - acquisition_mode:
        'STD_SINGLE'
        'STD_MULTI'
        'STD_GATE'
        'FIFO_SINGLE',
        'FIFO_GATE',
        'FIFO_MULTI',
        'FIFO_AVERAGE'

    Config example:
    si:
        module.Class: 'fast_adc.spectrum_instrumentation.SpectrumInstrumentationTest'
        ai_ch: 'CH0'
        ai_range_mV: 2000
        ai_offset_mV: 0
        ai_termination: '50Ohm'
        ai_coupling: 'DC'
        acq_mode: 'FIFO_GATE'
        acq_HW_avg_num: 1
        acq_pre_trigger_samples: 16
        acq_post_trigger_samples: 16
        buf_notify_size_B: 4096
        clk_reference_Hz: 10e6
        trig_mode: 'EXT'
        trig_level_mV: 1000
        initial_buffer_size_S: 1e9
        repetitions: 0
        row_data_save: False

    Basic class strutcture:
    SpectrumInstrumentation
    ├ cs (Card_settings)
    ├ ms (Meassurement_settings)
    ├ ccmd (Card_command)
    ├ cfg (Configure_command)
    └ pl (Process_loop)
        ├ cp (Card_process)
        │   ├ dcmd (Data_buffer_command)
        │   └ tscmd (TS_buffer_command)
        └ dp (Data_process)
            ├ df (Data_fetch)
            │   ├ hw_dt (Data_transfer)
            │   └ ts_dt (Data_transfer)
            ├ dc (Data_class)
            ├ dc_new (Data_class)
            ├ avg (Avg_data)
            └ avg_new (Avg_data)

    '''

    _modtype = 'SpectrumCard'
    _modclass = 'hardware'
    _threaded = True
    _threadlock = Mutex()

    _ai_ch = ConfigOption('ai_ch', 'CH0', missing='nothing')
    _ai_range_mV = ConfigOption('ai_range_mV', 1000, missing='warn')
    _ai_offset_mV = ConfigOption('ai_offset_mV', 0, missing='warn')
    _ai_term = ConfigOption('ai_termination', '50Ohm', missing='warn')
    _ai_coupling = ConfigOption('ai_coupling', 'DC', missing='warn')
    _acq_mode = ConfigOption('acq_mode', 'FIFO_MULTI', missing='warn')
    _acq_HW_avg_num = ConfigOption('acq_HW_avg_num', 1, missing='nothing')
    _acq_pre_trigs_S = ConfigOption('acq_pre_trigger_samples', 16, missing='warn')
    _acq_post_trigs_S = ConfigOption('acq_post_trigger_samples', 16, missing='nothing')

    _buf_notify_size_B = ConfigOption('buf_notify_size_B', 4096, missing='warn')
    _clk_ref_Hz = ConfigOption('clk_reference_Hz', 10e6, missing='warn')
    _trig_mode = ConfigOption('trig_mode', 'EXT', missing='warn')
    _trig_level_mV = ConfigOption('trig_level_mV', '1000', missing='warn')

    _init_buf_size_S = ConfigOption('initial_buffer_size_S', 1e9, missing='warn')
    _reps = ConfigOption('repetitions', 0, missing='nothing')
    _double_gate_acquisition = ConfigOption('double_gate_acquisition', False, missing='nothing')

    _row_data_save = ConfigOption('row_data_save', True, missing='nothing')
    _timer = ConfigOption('_timer', 'logic', missing='nothing')

    _cfg_error_check = False
    _ccmd_error_check = False
    _dcmd_error_check = False


    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self._card_on = False
        self._internal_status = CardStatus.idle

    def on_activate(self):
        """
        Open the card by activation of the module
        """

        self.cs = Card_settings()
        self.ms = Measurement_settings()

        self._load_settings_from_config_file()
        self.pl = Process_loop()
        self.cfg = Configure_command()
        self.cfg.error_check =self._cfg_error_check
        self.pl.row_data_save = self._row_data_save

        if self._card_on == False:
            self.card = spcm_hOpen(create_string_buffer(b'/dev/spcm0'))
            self._card_on = True
            self.ccmd = Card_command(self.card)
            self.ccmd.error_check = self._ccmd_error_check

        else:
            self.log.info('SI card is already on')

        if self.card == None:
            self.log.info('No card found')


    def _load_settings_from_config_file(self):
        """
        Load the settings parameters of the configuration file to the card and measurement settings.
        """
        self.cs.ai_ch = self._ai_ch
        self.cs.ai_range_mV = int(self._ai_range_mV)
        self.cs.ai_offset_mV = int(self._ai_offset_mV)
        self.cs.ai_term = self._ai_term
        self.cs.ai_coupling = self._ai_coupling
        self.cs.acq_mode = self._acq_mode
        self.cs.acq_HW_avg_num = int(self._acq_HW_avg_num)
        self.cs.acq_pre_trigs_S = int(self._acq_pre_trigs_S)
        self.cs.acq_post_trigs_S = int(self._acq_post_trigs_S)
        self.cs.buf_notify_size_B = int(self._buf_notify_size_B)
        self.cs.clk_ref_Hz = int(self._clk_ref_Hz)
        self.cs.trig_mode = self._trig_mode
        self.cs.trig_level_mV = int(self._trig_level_mV)

        self.ms.init_buf_size_S = int(self._init_buf_size_S)
        self.ms.reps = self._reps
        self.ms.double_gate_acquisition = self._double_gate_acquisition
        self.ms.assign_data_bit(self.cs.acq_mode)


    def on_deactivate(self):
        """
        Close the card
        """
        spcm_vClose(self.card)

    def get_constraints(self):

        constraints = dict()

        constraints['possible_timebase_list'] = np.array([2**i for i in range(18)])
        constraints['hardware_binwidth_list'] = (constraints['possible_timebase_list']) / 250e6 #maximum sampling rate 250 MHz

        return constraints

    def configure(self, binwidth_s, record_length_s, number_of_gates=1):
        """
        Configure the card parameters.
        @param float binwidth_s: Length of a single time bin in the time trace
                                  histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single
                                      gate in seconds.
        @param int number_of_gates: optional, number of gates in the pulse
                                    sequence. Ignore for not gated counter.

        @return tuple(binwidth_s, gate_length_s, number_of_gates):
                    binwidth_s: float the actual set binwidth in seconds
                    gate_length_s: the actual set gate length in seconds
                    number_of_gates: the number of gated, which are accepted
        """
        self.ccmd.card_reset()
        self.ms.gated = self.cs.gated
        self.cfg.load_static_cfg_params(self.card, self.cs, self.ms)

        self.ms.load_dynamic_params(binwidth_s, record_length_s, number_of_gates)
        self.ms.calc_data_size_S(self.cs.acq_pre_trigs_S, self.cs.acq_post_trigs_S)
        self.cs.calc_dynamic_cs(self.ms)
        self.ms.calc_buf_params()
        self.ms.calc_actual_length_s()
        self.cs.get_buf_size_B(self.ms.seq_size_B, self.ms.reps_per_buf, self.ms.total_pulse)

        self.cfg.load_dynamic_cfg_params(self.cs, self.ms)

        self.cfg.configure_all()

        self.ms.c_buf_ptr = self.cfg.return_c_buf_ptr()
        if self.ms.gated == True:
            self.ms.c_ts_buf_ptr = self.cfg.return_c_ts_buf_ptr()

        self.pl.init_process(self.card, self.cs, self.ms)
        self.pl.cp.dcmd.error_check = self._dcmd_error_check

        return self.ms.binwidth_s, self.ms.record_length_s, self.ms.total_pulse

    def get_status(self):
        """
        Receives the current status of the Fast Counter and outputs it as
                    return value.

                0 = unconfigured
                1 = idle
                2 = running
                3 = paused
                -1 = error state
        """
        return self._internal_status

    def start_measure(self):
        """
        Start the acquisition and data process loop
        """
        self._start_acquisition()
        self.pl.loop_on = True
        if self._timer == 'hardware':
            self.pl.start_data_process_loop()

        return 0

    def _start_acquisition(self):
        """
        Start the data acquisition
        """
        if self._internal_status == CardStatus.idle:
            self.pl.init_measure_params()
            if self.ms.gated:
                self.pl.cp.tscmd.reset_ts_counter()
                self.ccmd.start_all_with_extradma()
            else:
                self.ccmd.start_all()


        elif self._internal_status == CardStatus.paused:
            self.ccmd.enable_trigger()

        self.pl.cp.trigger_enabled = True
        self.log.info('Measurement started')
        self._internal_status = CardStatus.running

    def get_data_trace(self):
        """ Polls the current timetrace data from the fast counter.

        Return value is a numpy array (dtype = int64).
        The binning, specified by calling configure() in forehand, must be
        taken care of in this hardware class. A possible overflow of the
        histogram bins must be caught here and taken care of.
        If the counter is NOT GATED it will return a tuple (1D-numpy-array, info_dict) with
            returnarray[timebin_index]
        If the counter is GATED it will return a tuple (2D-numpy-array, info_dict) with
            returnarray[gate_index, timebin_index]

        info_dict is a dictionary with keys :
            - 'elapsed_sweeps' : the elapsed number of sweeps
            - 'elapsed_time' : the elapsed time in seconds

        If the hardware does not support these features, the values should be None
        """
        if self._timer == 'logic':
            # self.pl.check_dp_status()
            # self.pl.check_ts_status()
            if self.pl.dp.avg.num != 0:
                self.pl.command_process()
            else:
                self.pl.initial_process()

        with self._threadlock:
            avg_data, avg_num = self.pl.fetch_data_trace()
        info_dict = {'elapsed_sweeps': avg_num, 'elapsed_time': time.time() - self.pl.start_time}

        return avg_data, info_dict

    def stop_measure(self):
        """
        Stop the measurement.
        """
        if self._internal_status == CardStatus.running:
            self.log.info('card stopped')
            self.pl.cp.trigger_enabled = self.ccmd.disable_trigger()
            if self.pl.loop_on:
                self.pl.stop_data_process()
            self.ccmd.stop_dma()
            self.ccmd.card_stop()

        self._internal_status = CardStatus.idle
        self.pl.loop_on = False
        self.log.info('Measurement stopped')

        return 0

    def pause_measure(self):
        """ Pauses the current measurement.

            Fast counter must be initially in the run state to make it pause.
        """
        self.ccmd.disable_trigger()
        self.pl.cp.trigger_enabled = False
        self.pl.loop_on = False
        self.pl.stop_data_process()

        self._internal_status = CardStatus.paused
        self.log.info('Measurement paused')
        return

    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        self.log.info('Measurement continued')
        self._internal_status = CardStatus.running
        self.pl.loop_on = True
        self.pl.cp.trigger_enabled = self.ccmd.enable_trigger()
        self.pl.start_data_process()

        return 0

    def is_gated(self):
        """ Check the gated counting possibility.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        return self.ms.gated

    def get_binwidth(self):
        """ Returns the width of a single timebin in the timetrace in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)
        """
        return self.ms.binwidth_s

    def _status(self):
        self.pl.cp.dcmd.check_dp_status()


class Data_transfer:
    '''
    This class can access the data stored in the buffer by specifying the buffer pointer.
    Refer to 'Buffer handling' in the documentation.
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
        c_buffer = cast(addressof(self.c_buf_ptr) + user_pos_B, POINTER(self.data_type))
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

    def get_new_data(self, user_pos_B, curr_avail_reps):
        """
        Get the currently available new data at the user position
        dependent on the relative position of the user position in the buffer.

        @params int user_pos_B: user position in bytes
        @params int curr_avail_reps: the number of currently available repetitions

        @return numpy array
        """
        rep_end = int(user_pos_B / self.seq_size_B) + curr_avail_reps

        if 0 < rep_end <= self.reps_per_buf:
            np_data = self._fetch_data(user_pos_B, curr_avail_reps)

        elif self.reps_per_buf < rep_end < 2 * self.reps_per_buf:
            np_data = self._fetch_data_buf_end(user_pos_B, curr_avail_reps)
        else:
            print('error: rep_end {} is out of range'.format(rep_end))
            return

        return np_data

    def _fetch_data(self, user_pos_B, curr_avail_reps):
        """
        Fetch currently available data at user position in one shot
        """
        np_data = self._fetch_from_buf(user_pos_B, curr_avail_reps * self.seq_size_S)
        return np_data

    def _fetch_data_buf_end(self, user_pos_B, curr_avail_reps):
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

class Data_fetch_ungated():
    '''
    This class can simply fetch the hardware data.
    '''

    def __init__(self, cs, ms):
        self.ms = ms
        self.cs = cs
    def init_data_fetch(self):
        self.input_params(self.cs, self.ms)
        self.create_data_trsnsfer()

    def input_params(self, cs, ms):
        """
        Input the parameters used in this class from the card and measurement settings.
        """
        self.c_buf_ptr = ms.c_buf_ptr
        self.data_type = ms.get_data_type()
        self.data_bytes_B = ms.get_data_bytes_B()
        self.seq_size_S = ms.seq_size_S
        self.seq_size_B = self.seq_size_S * self.data_bytes_B

    def create_data_trsnsfer(self):
        """
        Create the data transfer class for the hardware data.
        """
        self.hw_dt = Data_transfer(self.c_buf_ptr, self.data_type,
                                   self.data_bytes_B, self.seq_size_S, self.ms.reps_per_buf)

    def fetch_data(self, user_pos_B, curr_avail_reps):
        data = self.hw_dt.get_new_data(user_pos_B, curr_avail_reps)
        rep = curr_avail_reps
        return data, rep

class Data_fetch_gated(Data_fetch_ungated):
    '''
    This class can fetch the timestamp data in addition to the hardware data.
    '''
    def input_params(self, cs, ms):
        super().input_params(cs, ms)
        self.c_ts_buf_ptr = ms.c_ts_buf_ptr
        self.ts_data_type = ms.get_ts_data_type()
        self.ts_data_bytes_B = ms.ts_data_bytes_B
        self.ts_seq_size_S = ms.ts_seq_size_S

    def create_data_trsnsfer(self):
        super().create_data_trsnsfer()
        self.ts_dt = Data_transfer(self.c_ts_buf_ptr, self.ts_data_type,
                                   self.ts_data_bytes_B, self.ts_seq_size_S, self.ms.reps_per_buf)

    def fetch_ts_data(self, ts_user_pos_B, curr_avail_reps):
        ts_row = self.ts_dt.get_new_data(ts_user_pos_B, curr_avail_reps).astype(np.uint64)
        ts_r, ts_f = self._get_ts_rf(ts_row)
        return ts_r, ts_f

    def _get_ts_rf(self, ts_row):
        """
        Get the timestamps for the rising and falling edges.
        Refer to 'Timestamps' in the documentation for the timestamp data format.
        """
        ts_used = ts_row[::2] #odd data is always filled with zero
        ts_r = ts_used[::2]
        ts_f = ts_used[1::2]
        return ts_r, ts_f

class Data_process_ungated():
    '''
    This class processes the hardware data, which consists of:
    - Fetch new data and input to the new dataclass.
    - Stack the new data to the old one.
    - Average the new data and input to the new average dataclass.
    - Get the new average by weighted averaging
    '''

    def init_data_process(self, cs, ms):
        self._input_settings_to_dp(cs, ms)
        self._generate_data_cls()
        self.df.init_data_fetch()
        self.avg = AvgData()
        self.avg.num = 0
        self.avg.total_pulse_number = ms.total_pulse
        self.avg.data = np.empty((0,ms.seq_size_S))

    def _input_settings_to_dp(self, cs, ms):
        self.ms = ms
        self.cs = cs

    def _generate_data_cls(self):
        self.dc = SeqDataMulti()
        self.dc.data = np.empty((0,self.ms.seq_size_S), int)
        self.dc.total_pulse_number = self.ms.total_pulse
        self.dc.pule_len = self.ms.seg_size_S
        self.dc.data_range_mV = self.cs.ai_range_mV

        self.df = Data_fetch_ungated(self.cs, self.ms)

    def create_dc_new(self):
        self.dc_new = SeqDataMulti()
        self.dc_new.total_pulse_number = self.ms.total_pulse
        self.dc_new.pule_len = self.ms.seg_size_S
        self.dc_new.data_range_mV = self.cs.ai_range_mV

    def fetch_data_to_dc(self, user_pos_B, curr_avail_reps):
        self.dc_new.data, self.dc_new.rep = self.df.fetch_data(user_pos_B, curr_avail_reps)
        self.dc_new.set_len()
        self.dc_new.data = self.dc_new.reshape_2d_by_rep()

    def stack_new_data(self):
        self.dc.stack_rep(self.dc_new)

    def get_initial_avg_data(self):
        self.avg.num = self.dc_new.rep
        self.avg.data = self.dc_new.avgdata()

    def get_new_avg_data(self):
        self.new_avg = AvgData()
        self.new_avg.num = self.dc_new.rep
        self.new_avg.data = self.dc_new.avgdata()

    def update_avg_data(self):
        self.avg.update(self.new_avg)
        self.avg.set_len()

    def return_avg_data(self):
        return self.avg.data, self.avg.num

class Data_process_gated(Data_process_ungated):
    '''
    This class fetches the new timestamp data.
    '''

    def _generate_data_cls(self):
        self.dc = SeqDataMultiGated()
        self.dc.data = np.empty((0, self.ms.seq_size_S), int)
        self.dc.ts_r = np.empty(0, np.uint64)
        self.dc.ts_f = np.empty(0, np.uint64)
        self.df = Data_fetch_gated(self.cs, self.ms)

    def create_dc_new(self):
        self.dc_new = SeqDataMultiGated()
        self.dc_new.total_pulse_number = self.ms.total_pulse
        self.dc_new.pule_len = self.ms.seg_size_S
        self.dc_new.data_range_mV = self.cs.ai_range_mV

    def fetch_ts_data_to_dc(self, ts_user_pos_B, curr_avail_reps):
        self.dc_new.ts_r, self.dc_new.ts_f = self.df.fetch_ts_data(ts_user_pos_B, curr_avail_reps)

    def stack_new_ts_data(self):
        self.dc.stack_rep_gated(self.dc_new)

    def return_avg_data(self):
        avg_data_2d = self.avg.reshape_2d_by_pulse()
        return avg_data_2d, self.avg.num

class Card_process():
    '''
    This class gives the commands to the data buffer and the card buffer,
    required in the data process.
    '''

    def init_card_process(self, card, cs, ms):
        self._input_settings_to_cp(cs, ms)
        self._generate_buffer_command(card)

    def _input_settings_to_cp(self, cs, ms):
        self.cs = cs
        self.ms = ms

    def _generate_buffer_command(self, card):
        self.dcmd = Data_buffer_command(card, self.ms)
        self.tscmd = Ts_buffer_command(card)

    def toggle_trigger(self, trigger_on):
        if trigger_on == self.trigger_enabled:
            return
        else:
            if trigger_on == True:
                print('trigger on!!')
                self.trigger_enabled = self.dcmd.enable_trigger()
            elif trigger_on == False:
                print('trigger off!!')
                self.trigger_enabled = self.dcmd.disable_trigger()

    def wait_new_trigger(self):
        prev_trig_counts = self.dcmd.trig_counter
        curr_trig_counts = self.dcmd.get_trig_counter()
        while curr_trig_counts == prev_trig_counts:
            curr_trig_counts = self.dcmd.get_trig_counter()

        return curr_trig_counts

class Process_commander:
    '''
    This class commands the process dependent on the unprocessed data.
    '''
    buf_ratio = 1

    def init_process(self, card, cs, ms):
        self._input_settings_to_dpc(ms)
        self._create_process_cls()
        self.dp.init_data_process(cs, ms)
        self.cp.init_card_process(card, cs, ms)
        self._choose_data_process()

    def _create_process_cls(self):
        if self._gated == True:
            self.dp = Data_process_gated()
        else:
            self.dp = Data_process_ungated()

        self.cp = Card_process()

    def _input_settings_to_dpc(self, ms):
        self._gated = ms.gated
        self._reps_per_buf = ms.reps_per_buf
        self._seq_size_B = ms.seq_size_B
        if self._gated:
            self._ts_seq_size_B = ms.ts_seq_size_B

    def _choose_data_process(self):
        """
        Choose methods for the data process dependent on the gate mode and row data save mode.
        """
        if self._gated:
            self._get_curr_avail_reps = self._get_curr_avail_reps_gated

            if self.row_data_save:
                self._process_data_by_options = self._process_data_gated_saved
            else:
                self._process_data_by_options = self._process_data_gated_unsaved
        else:
            self._get_curr_avail_reps = self._get_curr_avail_reps_ungated

            if self.row_data_save:
                self._process_data_by_options = self._process_data_ungated_saved
            else:
                self._process_data_by_options = self._process_data_ungated_unsaved

    def initial_process(self):
        usr_pos_B = self.cp.dcmd.get_avail_user_pos_B()
        curr_avail_reps = self._get_curr_avail_reps()
        self._process_initial_data(usr_pos_B, curr_avail_reps)

    def _process_initial_data(self, user_pos_B, curr_avail_reps):
        """
        Process for the initial data without averaging
        """
        self.dp.create_dc_new()
        self.dp.fetch_data_to_dc(user_pos_B, curr_avail_reps)
        self.dp.get_initial_avg_data()
        self.cp.dcmd.set_avail_card_len_B(curr_avail_reps * self._seq_size_B)
        if self._gated:
            self._fetch_ts(curr_avail_reps)

    def command_process(self):
        """
        Command the main process dependent on the repetitions of unprocessed data.
        """

        trig_reps = self.cp.dcmd.get_trig_reps()
        unprocessed_reps = trig_reps - self.dp.avg.num
        if self.dp.avg. num >= self.dp.ms.reps:
            return

        if trig_reps == 0 or unprocessed_reps == 0:
            print('wait for trigger')
            self.cp.toggle_trigger(True)
            self.cp.wait_new_trigger()

        elif unprocessed_reps < self.buf_ratio * self._reps_per_buf:
            self.cp.toggle_trigger(True)
            self._process_curr_avail_data()

        elif unprocessed_reps >= self.buf_ratio * self._reps_per_buf:
            print('trigger off')
            self.cp.toggle_trigger(False)
            self._process_curr_avail_data()

    def _process_curr_avail_data(self):
        curr_avail_reps = self._get_curr_avail_reps()
        user_pos_B = self.cp.dcmd.get_avail_user_pos_B()
        if curr_avail_reps == 0:
            return
        self._process_data_by_options(user_pos_B, curr_avail_reps)

    def _process_data_ungated_unsaved(self, user_pos_B, curr_avail_reps):
        self.dp.create_dc_new()
        self.dp.fetch_data_to_dc(user_pos_B, curr_avail_reps)
        self._average_data(curr_avail_reps)

    def _process_data_ungated_saved(self, user_pos_B, curr_avail_reps):
        self._process_data_ungated_unsaved(user_pos_B, curr_avail_reps)
        self.dp.stack_new_data()

    def _process_data_gated_unsaved(self, user_pos_B, curr_avail_reps):
        self._process_data_ungated_unsaved(user_pos_B, curr_avail_reps)
        self._fetch_ts(curr_avail_reps)

    def _process_data_gated_saved(self, user_pos_B, curr_avail_reps):
        self._process_data_ungated_unsaved(user_pos_B, curr_avail_reps)
        self.dp.stack_new_data()
        self._fetch_ts(curr_avail_reps)
        self.dp.stack_new_ts_data()

    def _get_curr_avail_reps_ungated(self):
        return self.cp.dcmd.get_avail_user_reps()

    def _get_curr_avail_reps_gated(self):
        curr_data_avail_reps = self.cp.dcmd.get_avail_user_reps()
        curr_ts_avail_reps = self.cp.tscmd.get_ts_avail_user_reps(self.dp.ms.ts_seq_size_B)
        return min(curr_data_avail_reps, curr_ts_avail_reps)

    def _average_data(self, curr_avail_reps):
        """
        Get the new average data and update the main average data.
        After processing the data, they should be set free.
        """
        self.dp.get_new_avg_data()
        self.dp.update_avg_data()
        self.cp.dcmd.set_avail_card_len_B(curr_avail_reps * self._seq_size_B)

    def _fetch_ts(self, curr_avail_reps):
        ts_user_pos_B = self.cp.tscmd.get_ts_avail_user_pos_B()
        self.dp.fetch_ts_data_to_dc(ts_user_pos_B, curr_avail_reps)
        self.cp.tscmd.set_ts_avail_card_len_B(curr_avail_reps * self._ts_seq_size_B)

    def check_dp_status(self):
        status = self.cp.dcmd.get_status()
        avail_user_pos_B = self.cp.dcmd.get_avail_user_pos_B()
        buf_pos_pct = int(100 * avail_user_pos_B /self.dp.cs.buf_size_B)
        avail_user_len_B = self.cp.dcmd.get_avail_user_len_B()
        avail_user_reps = self.cp.dcmd.get_avail_user_reps()
        trig_reps = self.cp.dcmd.get_trig_reps()
        print("Stat:{0:04x}h Pos:{1:010}B / Buf {2}B ({3}%) Avail:{4:010}B "
              "Processed:{5:010}B :\n "
              "Avail:{6} Avg:{7} / Trig:{8} (Unprocessed:{9})\n".format(status,
                                                       avail_user_pos_B,
                                                       self.dp.cs.buf_size_B,
                                                       buf_pos_pct,
                                                       avail_user_len_B,
                                                       self.cp.dcmd.processed_data_B,
                                                       avail_user_reps,
                                                       self.dp.avg.num,
                                                       trig_reps,
                                                       trig_reps - self.dp.avg.num)
              )

    def check_ts_status(self):
        ts_avail_user_pos_B = self.cp.tscmd.get_ts_avail_user_pos_B()
        ts_avail_user_len_B = self.cp.tscmd.get_ts_avail_user_len_B()
        ts_avail_reps = int(ts_avail_user_len_B / 32 / self.dp.ms.total_pulse)
        ts_buf_pos_pct = int(100 * ts_avail_user_pos_B /self.dp.cs.ts_buf_size_B)
        print('ts Pos:{}B / Buf {}B({}%) Avail:{}B {}reps '.format(ts_avail_user_pos_B,
                                                              self.dp.cs.ts_buf_size_B,
                                                              ts_buf_pos_pct,
                                                              ts_avail_user_len_B,
                                                              ts_avail_reps))

    def wait_avail_data(self):
        initial_time = time.time()
        curr_avail_reps = self._get_curr_avail_reps()
        while curr_avail_reps == 0:
            curr_avail_reps = self._get_curr_avail_reps()
            current_time = time.time() - initial_time
            if current_time > 10:
                time_out = True
                return time_out
        time_out = False
        return time_out

class Process_loop(Process_commander):
    '''
    This is the main data process loop class.
    '''
    threadlock = Mutex()

    def init_measure_params(self):
        self.cp.dcmd.init_dp_params()
        self.start_time = time.time()
        self.loop_on = False

    def start_data_process(self):
        """
        Start the data process with creating a new thread for it.
        """
        self.loop_on = True
        self.data_proc_th = threading.Thread(target=self.start_data_process_loop)
        self.data_proc_th.start()

        return

    def start_data_process_n(self, n):
        """
        Start the data process for finite repetitions with creating a new thread for it.
        """

        self.loop_on = True
        self.data_proc_th = threading.Thread(target=self.start_data_process_loop_n, args=(n,))
        self.data_proc_th.start()

        return

    def stop_data_process(self):
        with self.threadlock:
            self.loop_on = False
#        self.data_proc_th.join()


    def start_data_process_loop(self):
        t_0 = time.time()
        self.t_p = 0
        time_out = self.wait_avail_data()
        if time_out:
           return

        self.initial_process()

        if self.dp.ms.reps == 0:
            while self.loop_on == True:
                self.command_process()
                self.t_p = self.process_speed(t_0, self.t_p)

            return

        else:
            while self.dp.avg.num < self.dp.ms.reps:
                self.command_process()
                self.t_p = self.process_speed(t_0, self.t_p)
            return

    def start_data_process_loop_n(self, n):
        self.cp.wait_new_trigger()
        self.initial_process()

        while self.loop_on == True and self.dp.avg.num < n:
            with self.threadlock:
                self.command_process()
            self.check_dp_status()

        return

    def fetch_data_trace(self):
        avg_data, avg_num = self.dp.return_avg_data()

        return avg_data, avg_num

    def process_speed(self, t_0, t_p):
        t_c = time.time() - t_0
        t_d = t_c - t_p
        try:
            if self.dp.avg.num == 0 or self.dp.new_avg == 0:
                return 0
        except :
            return 0
        ps_t = self.dp.avg.num * self.dp.ms.seq_size_B / t_c * 1e-6
        ps_c = self.dp.new_avg.num * self.dp.ms.seq_size_B / t_d * 1e-6
        print('total_process_speed {:.3g}MB/s'.format(ps_t))
        print('current_process_speed {:.3g}MB/s'.format(ps_c))
        return t_c
