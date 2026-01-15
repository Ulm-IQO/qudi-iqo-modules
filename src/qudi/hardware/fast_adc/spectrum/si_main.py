# -*- coding: utf-8 -*-
"""
This file contains the Qudi hardware for spectrum instrumentation ADC.

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
import numpy as np
from enum import IntEnum
import pyspcm as spcm
import spcm_tools
from PySide2.QtCore import Signal

from qudi.core.configoption import ConfigOption
from qudi.util.mutex import Mutex
from qudi.interface.fast_counter_interface import FastCounterInterface

from qudi.hardware.fast_adc.spectrum.si_utils.si_settings import CardSettings, MeasurementSettings
from qudi.hardware.fast_adc.spectrum.si_commands.si_commands import Commands
from qudi.hardware.fast_adc.spectrum.si_utils.si_dataclass import Data
from qudi.hardware.fast_adc.spectrum.si_utils.si_data_fetcher import DataFetcher
from qudi.hardware.fast_adc.spectrum.si_utils.si_data_processer import DataProcessGated, DataProcessorUngated
from qudi.hardware.fast_adc.spectrum.si_utils.si_commander import Commander
from qudi.hardware.fast_adc.spectrum.si_utils.si_loop_manager import LoopManager


class CardStatus(IntEnum):
    unconfigured = 0
    idle = 1
    running = 2
    paused = 3
    error = -1


class SpectrumInstrumentation(FastCounterInterface):

    '''
    Hardware class for the spectrum instrumentation card.
    pyspcm.py and spcm_tools.py from the spectrum instrumentation need to be stored in your library.

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
        module.Class: 'fast_adc.spectrum.si_main.SpectrumInstrumentation'
        options:
            ai_ch:
                - 'CH0'
            ai0_range_mV: 2000
            ai0_offset_mV: 0
            ai0_termination: '1MOhm'
            ai0_coupling: 'DC'
            ai1_range_mV: 2000
            ai1_offset_mV: 0
            ai1_termination: '1MOhm'
            ai1_coupling: 'DC'
            acq_mode: 'FIFO_GATE'
            acq_HW_avg_num: 1
            acq_pre_trigger_samples: 16
            acq_post_trigger_samples: 16
            buf_notify_size_B: 4096
            clk_reference_Hz: 10e6
            trig_mode: 'EXT'
            trig_level_mV: 1000
            initial_buffer_size_S: 1e9
            max_reps_per_buf: 1e4
            repetitions: 0
            double_gate_acquisition: False
            data_stack_on: True

    Basic class strutcture:
    SpectrumInstrumentation
    ├ cs (CardSettings)
    ├ ms (MeasurementSettings)
    ├ cmd (Commands)
    │  ├ card (CardCommands)
    │  ├ data_buf (DataBufferCommands)
    │  ├ ts_buf (TsBufferCommands)
    │  ├ cfg (ConfigureCommands)
    │  └ process (ProcessActions)
    ├ data(Data)
    ├ fetcher (DataFetcher)
    ├ commander (Commander)
    └ loop_manager(LoopManager)
    '''

    _modtype = 'SpectrumCard'
    _modclass = 'hardware'
    _threaded = True
    _threadlock = Mutex()
    _signal_measure = Signal()

    # ConfigOptions for card settings. See the manual for details.
    _ai_ch = ConfigOption('ai_ch', 'CH0', missing='nothing')
    _ai0_range_mV = ConfigOption('ai0_range_mV', 1000, missing='warn')
    _ai0_offset_mV = ConfigOption('ai0_offset_mV', 0, missing='warn')
    _ai0_term = ConfigOption('ai0_termination', '50Ohm', missing='warn')
    _ai0_coupling = ConfigOption('ai0_coupling', 'DC', missing='warn')
    _ai1_range_mV = ConfigOption('ai1_range_mV', 1000, missing='warn')
    _ai1_offset_mV = ConfigOption('ai1_offset_mV', 0, missing='warn')
    _ai1_term = ConfigOption('ai1_termination', '50Ohm', missing='warn')
    _ai1_coupling = ConfigOption('ai1_coupling', 'DC', missing='warn')
    _acq_mode = ConfigOption('acq_mode', 'FIFO_MULTI', missing='warn')
    _acq_HW_avg_num = ConfigOption('acq_HW_avg_num', 1, missing='nothing')
    _acq_pre_trigs_S = ConfigOption('acq_pre_trigger_samples', 16, missing='warn')
    _acq_post_trigs_S = ConfigOption('acq_post_trigger_samples', 16, missing='nothing')
    _buf_notify_size_B = ConfigOption('buf_notify_size_B', 4096, missing='warn')
    _clk_ref_Hz = ConfigOption('clk_reference_Hz', 10e6, missing='warn')
    _trig_mode = ConfigOption('trig_mode', 'EXT', missing='warn')
    _trig_level_mV = ConfigOption('trig_level_mV', '1000', missing='warn')

    #ConfigOptions for measurement settings.
    _init_buf_size_S = ConfigOption('initial_buffer_size_S', 1e9, missing='warn')
    _max_reps_per_buf = ConfigOption('max_reps_per_buf', 1e4, missing='nothing')
    _reps = ConfigOption('repetitions', 0, missing='nothing')
    _double_gate_acquisition = ConfigOption('double_gate_acquisition', False, missing='nothing')
    _data_stack_on = ConfigOption('data_stack_on', True, missing='nothing')

    _wait_time_interval = ConfigOption('wait_time_interval', 0.01, missing='nothing')

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)
        self.card = None
        self._card_on = False
        self._internal_status = CardStatus.idle

        self.cs = None
        self.ms = None
        self.cmd = None
        self.fetcher = None
        self.data = None
        self.commander = None
        self.loop_manager = None

    def on_activate(self):
        """
        Open the card by activation of the module
        """

        if not self._card_on:
            self.card = spcm.spcm_hOpen(spcm_tools.create_string_buffer(b'/dev/spcm0'))
            self._card_on = True
            self._generate_helpers()
            self._load_settings_from_config_file()

        else:
            self.log.info('SI card is already on')

        if self.card is None:
            self.log.info('No card found')

    def _generate_helpers(self):
        self.cs = CardSettings()
        self.ms = MeasurementSettings()
        self.cmd = Commands(self.card, self.log)
        self.data = Data()
        self.fetcher = DataFetcher()
        self.commander = Commander(self.cmd, self.log)
        self.loop_manager = LoopManager(self.commander, self.log)
        self._signal_measure.connect(self.loop_manager.start_data_process)

    def _load_settings_from_config_file(self):
        """
        Load the settings parameters of the configuration file to the card and measurement settings.
        """
        self.cs.ai_ch = self._ai_ch
        self.cs.ai0_range_mV = int(self._ai0_range_mV)
        self.cs.ai0_offset_mV = int(self._ai0_offset_mV)
        self.cs.ai0_term = self._ai0_term
        self.cs.ai0_coupling = self._ai0_coupling
        self.cs.ai1_range_mV = int(self._ai1_range_mV)
        self.cs.ai1_offset_mV = int(self._ai1_offset_mV)
        self.cs.ai1_term = self._ai1_term
        self.cs.ai1_coupling = self._ai1_coupling
        self.cs.acq_mode = self._acq_mode
        self.cs.acq_HW_avg_num = int(self._acq_HW_avg_num)
        self.cs.acq_pre_trigs_S = int(self._acq_pre_trigs_S)
        self.cs.acq_post_trigs_S = int(self._acq_post_trigs_S)
        self.cs.buf_notify_size_B = int(self._buf_notify_size_B)
        self.cs.clk_ref_Hz = int(self._clk_ref_Hz)
        self.cs.trig_mode = self._trig_mode
        self.cs.trig_level_mV = int(self._trig_level_mV)

        self.ms.init_buf_size_S = int(self._init_buf_size_S)
        self.ms.max_reps_per_buf = int(self._max_reps_per_buf)
        self.ms.reps = self._reps
        self.ms.data_stack_on = self._data_stack_on
        self.ms.double_gate_acquisition = self._double_gate_acquisition
        self.ms.assign_data_bit(self.cs.acq_mode)

        self.cmd.process.wait_time_interval = self._wait_time_interval

    def on_deactivate(self):
        """
        Close the card.
        """
        spcm.spcm_vClose(self.card)

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
        def set_dynamic_params(binwidth_s, record_length_s, number_of_gates):
            self.ms.load_dynamic_params(binwidth_s, record_length_s, number_of_gates)
            self.ms.calc_data_size_S(self.cs.acq_pre_trigs_S, self.cs.acq_post_trigs_S)
            self.cs.calc_dynamic_cs(self.ms)
            self.ms.calc_buf_params()
            self.ms.calc_actual_length_s()
            self.cs.get_buf_size_B(self.ms.seq_size_B, self.ms.reps_per_buf, self.ms.total_pulse)

        def get_buffer_pointer():
            self.ms.c_buf_ptr = self.cmd.cfg.return_c_buf_ptr()
            if self.ms.gated:
                self.ms.c_ts_buf_ptr = self.cmd.cfg.return_c_ts_buf_ptr()

        def initialize_all():
            self.cmd.process.input_ms(self.ms)
            self.fetcher.init_buffer(self.ms)
            self.data.initialize(self.ms, self.cs)
            self.processor = DataProcessGated(self.data, self.fetcher) if self.ms.gated \
                else DataProcessorUngated(self.data, self.fetcher)
            self.commander.init(self.ms, self.processor)
            self.loop_manager.input_ms(self.ms)

        self.cmd.card.card_reset()
        self.ms.gated = self.cs.gated
        self.ms.num_channels = len(self.cs.ai_ch)

        set_dynamic_params(binwidth_s, record_length_s, number_of_gates)

        self.cmd.cfg.configure_all(self.cs)
        get_buffer_pointer()

        initialize_all()

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
                TODO Use error state when an error is caught.
        """
        return self._internal_status

    def start_measure(self):
        """
        Start the acquisition and data process loop
        """
        self._start_acquisition()
        self._signal_measure.emit()
        self.loop_manager.start()

        return 0

    def _start_acquisition(self):
        """
        Start the data acquisition
        """
        self.log.info('Measurement started')
        if self._internal_status == CardStatus.idle:
            if self.ms.gated:
                self.cmd.ts_buf.reset_ts_counter()
                self.cmd.card.start_all_with_extradma()
            else:
                self.cmd.card.start_all()
            self.log.debug('card started')

        elif self._internal_status == CardStatus.paused:
            self.cmd.card.enable_trigger()

        self.cmd.process.trigger_enabled = True
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
        with self.commander.threadlock:
            avg_data = self.data.avg.pulse_array[0:self.data.avg.num_pulse, :] if self.ms.gated else self.data.avg.data
            #tentative workaround:
            avg_num = self.data.avg.num_rep
        info_dict = {'elapsed_sweeps': avg_num, 'elapsed_time': time.time() - self.loop_manager.start_time}

        return avg_data, info_dict

    def stop_measure(self):
        """
        Stop the measurement.
        """
        if self._internal_status == CardStatus.running:
            self.loop_manager.stop_data_process()
            self.loop_manager.wait()
            self.log.debug('Measurement thread stopped')

            self.cmd.card.disable_trigger()
            self.cmd.process.trigger_enabled = False
            self.cmd.card.stop_dma()
            self.cmd.card.card_stop()
            self.log.debug('card stopped')
            self._internal_status = CardStatus.idle
            self.log.info('Measurement stopped')
            return 0

        elif self._internal_status == CardStatus.idle:
            self.log.info('Measurement is not runnning')
            return 0


    def pause_measure(self):
        """ Pauses the current measurement.

            Fast counter must be initially in the run state to make it pause.
        """
        self.cmd.card.disable_trigger()
        self.cmd.process.trigger_enabled = False
        self.loop_manager.stop_data_process()

        self._internal_status = CardStatus.paused
        self.log.debug('Measurement paused')
        return

    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        self.log.info('Measurement continued')
        self._internal_status = CardStatus.running
        self.loop_manager.loop_on = True
        self.cmd.card.enable_trigger()
        self.cmd.process.trigger_enabled = True
        self.loop_manager.start_data_process()

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

