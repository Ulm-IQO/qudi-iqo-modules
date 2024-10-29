# -*- coding: utf-8 -*-

"""
Interface for input of data of a certain length at a given sampling rate and data type.

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
import numpy as np
import time
from pyspcm import *
from spcm_tools import *
from hardware.spectrum.spectrum_control import *

from qudi.util.mutex import RecursiveMutex
from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector
from qudi.interface.finite_sampling_input_interface import FiniteSamplingInputInterface, FiniteSamplingInputConstraints



class PulsedFiniteSamplingInput(FiniteSamplingInputInterface):
    """
    Interface for input of data of a certain length at a given sampling rate and data type.
    """
    _digitizer = Connector('SpectrumFastSampling', name='digitizer')
    _pulser = Connector('PulseBlasterESRPRO', name='pulser')

    _channel_aom = ConfigOption(name='channel_aom', missing='error')
    _channel_mw= ConfigOption(name='channel_mw', default=None)
    _channel_digitizer = ConfigOption(name='channel_digitizer', missing='error')
    _channel_next = ConfigOption(name='channel_next', missing='error')
    _analog_channel_units = ConfigOption(name='analog_channel_units', default=dict(), missing='info')

    _sample_rate_limits = ConfigOption(name='sample_rate_limits', default=(1, 1e6))
    _frame_size_limits = ConfigOption(name='frame_size_limits', default=(1, 1e9))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.channels = dict(
            aom=self._channel_aom - 1,
            mw=self._channel_mw - 1,
            digitizer=self._channel_digitizer - 1,
            next=self._channel_next - 1,
        )
        self._thread_lock = RecursiveMutex()
        self._sample_rate = -1
        self._sample_rate_old = -1
        self._frame_size = -1
        self._frame_size_old = -1
        self._constraints = None

    def on_activate(self):
        self.digitizer = self._digitizer()
        self.pulser = self._pulser()
        self._active_channels = 'd_ch%d' % self._channel_digitizer
        self._constraints = FiniteSamplingInputConstraints(
            channel_units={self._active_channels:self._analog_channel_units},
            frame_size_limits=self._frame_size_limits,
            sample_rate_limits=self._sample_rate_limits
        )
        self.mode = 'cw'

    def on_deactivate(self):
        """ Shut down the NI card.
        """
        self.pulser.pulser_off()
        self.digitizer.stop_measure()
        return

    @property
    def constraints(self):
        return self._constraints

    @property
    def active_channels(self):
        return self._active_channels

    @property
    def sample_rate(self):
        """ The sample rate (in Hz) at which the samples will be acquired.

        @return float: The current sample rate in Hz
        """
        return self._sample_rate

    @property
    def frame_size(self):
        """ Currently set number of samples per channel to acquire for each data frame.

        @return int: Number of samples per frame
        """
        return self._frame_size

    @property
    def samples_in_buffer(self):
        """ Currently available samples per channel being held in the input buffer.
        This is the current minimum number of samples to be read with "get_buffered_samples()"
        without blocking.

        @return int: Number of unread samples per channel
        """
        if self.digitizer._number_of_gates:
            return self.digitizer._number_of_gates
        else:
            return self.frame_size

    def set_active_channels(self, channels):
        """ Will set the currently active channels. All other channels will be deactivated.

        @param iterable(str) channels: Iterable of channel names to set active.
        """
        assert self.module_state() != 'locked', \
            'Unable to change active channels while finite sampling is running. New settings ignored.'
        
        with self._thread_lock:
            self._active_channels = 'd_ch%d' % channels

    def set_sample_rate(self, rate):
        sample_rate = float(rate)
        assert self._constraints.sample_rate_in_range(sample_rate)[0], \
            f'Sample rate "{sample_rate}Hz" to set is out of ' \
            f'bounds {self._constraints.sample_rate_limits}'
        with self._thread_lock:
            assert self.module_state() == 'idle', \
                'Unable to set sample rate. Data acquisition in progress.'
            self._sample_rate = sample_rate
            self.log.debug(f'set sample_rate to {self._sample_rate}')
        return

    def set_frame_size(self, size):
        """ Will set the number of samples per channel to acquire within one frame.

        @param int size: The sample rate to set
        """
        samples = int(round(size))
        with self._thread_lock:
            assert self.module_state() == 'idle', \
                'Unable to set frame size. Data acquisition in progress.'
            self._frame_size = samples
            self.log.debug(f'set frame_size to {self._frame_size}')

    def start_buffered_acquisition(self):
        """ Will start the acquisition of a data frame in a non-blocking way.
        Must return immediately and not wait for the data acquisition to finish.

        Must raise exception if data acquisition can not be started.
        """
        assert self.module_state() == 'idle', \
            'Unable to start data acquisition. Data acquisition already in progress.'
        self.module_state.lock()
        
        t_start = time.time()
        self._init_pulser()
        t_end = time.time()
        print('Time spent for _init_pulser():', t_end - t_start, flush=True)
        
        t_start = time.time()
        self._init_digitizer()
        t_end = time.time()
        print('Time spent for _init_digitizer():', t_end - t_start, flush=True)
        
        # So that init_pulser() and init_digitizer() will be skipped starting
        # from the next scan given that the settings are kept unchanged.
        self.update_scan_settings()
        
        self.digitizer.start_measure()
        time.sleep(0.1)
        self.pulser.pulser_on()

    def stop_buffered_acquisition(self):
        """ Will abort the currently running data frame acquisition.
        Will return AFTER the data acquisition has been terminated without waiting for all samples
        to be acquired (if possible).

        Must NOT raise exceptions if no data acquisition is running.
        """
        if self.module_state() == 'locked':
            self.digitizer.stop_measure()
            self.pulser.pulser_off()
            self.module_state.unlock()

    def get_buffered_samples(self, number_of_samples=None):
        """ Returns a chunk of the current data frame for all active channels read from the frame
        buffer.
        If parameter <number_of_samples> is omitted, this method will return the currently
        available samples within the frame buffer (i.e. the value of property <samples_in_buffer>).
        If <number_of_samples> is exceeding the currently available samples in the frame buffer,
        this method will block until the requested number of samples is available.
        If the explicitly requested number of samples is exceeding the number of samples pending
        for acquisition in the rest of this frame, raise an exception.

        Samples that have been already returned from an earlier call to this method are not
        available anymore and can be considered discarded by the hardware. So this method is
        effectively decreasing the value of property <samples_in_buffer> (until new samples have
        been read).

        If the data acquisition has been stopped before the frame has been acquired completely,
        this method must still return all available samples already read into buffer.

        @param int number_of_samples: optional, the number of samples to read from buffer

        @return dict: Sample arrays (values) for each active channel (keys)
        """
        data2D, info = self.digitizer.get_data_trace(accumulate=False)
        line = np.average(data2D, axis=1)
        signal = line[::2]
        ref = line[1::2]
        data = (signal - ref)*100.0/ref
        return {self.active_channels:data}

    def acquire_frame(self, frame_size=None):
        """ Acquire a single data frame for all active channels.
        This method call is blocking until the entire data frame has been acquired.

        If an explicit frame_size is given as parameter, it will not overwrite the property
        <frame_size> but just be valid for this single frame.

        See <start_buffered_acquisition>, <stop_buffered_acquisition> and <get_buffered_samples>
        for more details.

        @param int frame_size: optional, the number of samples to acquire in this frame

        @return dict: Sample arrays (values) for each active channel (keys)
        """
        with self._thread_lock:
            
            self.start_buffered_acquisition()
        
            t_start = time.time()
            data = self.get_buffered_samples(self._frame_size)
            t_end = time.time()
            print('Time spent for get_buffered_samples():', t_end - t_start, flush=True)
            
            self.stop_buffered_acquisition()
            return data


    # =============================================================================================
    def is_scan_settings_unchanged(self):
        # Check if the settings for scan are the same.
        # If the settings are the same, we can avoid reinitializing the hardwares
        # again to save time.
        return  self.frame_size == self._frame_size_old and \
                self.sample_rate == self._sample_rate_old
    
    def update_scan_settings(self):
        # Update the scan setting after init BOTH pulser and digitizer
        self._frame_size_old = self.frame_size
        self._sample_rate_old = self.sample_rate
    
    def _init_pulser(self):
        if self.is_scan_settings_unchanged():
            if self.pulser._current_pb_waveform_name == 'ODMR_CW':
                print('No need to init pulser again')
                return

        aom = self.channels['aom']
        mw = self.channels['mw']
        next = self.channels['next']
        readout = self.channels['digitizer']
        period = 1./self.sample_rate

        waveform = []

        waveform.append({'active_channels':[aom], 'length': 2.0})
        for i in range(self.frame_size):
            waveform.append({'active_channels':[aom, mw, next   ], 'length': period*0.4})
            waveform.append({'active_channels':[aom, mw, readout], 'length': period*0.6})
            waveform.append({'active_channels':[aom             ], 'length': period*0.4})
            waveform.append({'active_channels':[aom,     readout], 'length': period*0.6})

        self.pulser.init_waveform('ODMR_CW')
        self.pulser._current_pb_waveform = waveform.copy()
        print(self.pulser._current_pb_waveform_name)
        self.pulser.write_pulse_form(self.pulser._current_pb_waveform, loop=False)

    def _init_digitizer(self):
        if self.is_scan_settings_unchanged():
            print('No need to init digitizer again')
            return
        period = 1./self.sample_rate
        record_length_s = period*0.9
        bin_width_s = record_length_s/520
        number_of_gates = self.frame_size*2
        self.digitizer.configure(bin_width_s, record_length_s, number_of_gates)
        return

