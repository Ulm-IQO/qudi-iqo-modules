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
    _pulser = Connector('PulseBlasterESRPRO', name='pulser')
    _timetagger = Connector(name='tagger', interface = "TT")

    _channel_aom = ConfigOption(name='channel_aom', missing='error')
    _channel_mw= ConfigOption(name='channel_mw', default=None)
    _channel_awg= ConfigOption(name='channel_awg', default=None)
    _channel_begin = ConfigOption(name='channel_begin', missing='error')
    _channel_end = ConfigOption(name='channel_end', missing='error')
    _channel_next = ConfigOption(name='channel_next', missing='error')

    _channel_tt_ticks = ConfigOption(name='channel_tt_ticks', missing='error')
    _channel_tt_begin = ConfigOption(name='channel_tt_begin', missing='error')
    _channel_tt_end = ConfigOption(name='channel_tt_end', missing='error')

    _analog_channel_units = ConfigOption(name='analog_channel_units', default=dict(), missing='info')

    _sample_rate_limits = ConfigOption(name='sample_rate_limits', default=(1, 1e6))
    _frame_size_limits = ConfigOption(name='frame_size_limits', default=(1, 1e9))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.channels = dict(
            aom=self._channel_aom - 1,
            mw=self._channel_mw - 1,
            awg=self._channel_awg - 1,
            begin=self._channel_begin - 1,
            end=self._channel_end - 1,
            next=self._channel_next - 1,
        )
        self.channels_tt = dict(
            ticks=self._channel_tt_ticks,
            begin=self._channel_tt_begin,
            end=self._channel_tt_end,
        )
        self._thread_lock = RecursiveMutex()
        self._sample_rate = -1
        self._sample_rate_old = -1
        self._frame_size = -1
        self._frame_size_old = -1
        self._mode = 'cw'
        self._mode_old = 'cw'
        self._runs_per_period = 40
        self._t_pre = 2.0
        self._t_delay = 1.0e-6
        self._t_pi = 1.0e-6
        self._t_readout = 10.0e-6
        self._constraints = None

    def on_activate(self):
        self.timetagger = self._timetagger()
        self.pulser = self._pulser()
        self._active_channels = 'd_ch%d' % self._channel_begin
        self._constraints = FiniteSamplingInputConstraints(
            channel_units={self._active_channels:self._analog_channel_units},
            frame_size_limits=self._frame_size_limits,
            sample_rate_limits=self._sample_rate_limits
        )

    def on_deactivate(self):
        """ Shut down the NI card.
        """
        self.pulser.pulser_off()
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
    def mode(self):
        return self._mode.lower()

    @property
    def samples_in_buffer(self):
        """ Currently available samples per channel being held in the input buffer.
        This is the current minimum number of samples to be read with "get_buffered_samples()"
        without blocking.

        @return int: Number of unread samples per channel
        """
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

    def set_mode(self, m):
        mode = m.lower()
        with self._thread_lock:
            assert self.module_state() == 'idle', \
                'Unable to set run mode. Data acquisition in progress.'
            self._mode = mode
            self.log.debug(f'set frame_size to {self._frame_size}')

    def set_runs_per_period(self, rpp):
        runs_per_period = int(rpp)
        with self._thread_lock:
            assert self.module_state() == 'idle', \
                'Unable to set runs_per_period. Data acquisition in progress.'
            self._runs_per_period = runs_per_period
            self.log.debug(f'set _runs_per_period to {self._runs_per_period}')

    def set_t_pre(self, t):
        t_pre = float(t)
        with self._thread_lock:
            assert self.module_state() == 'idle', \
                'Unable to set t_pre. Data acquisition in progress.'
            self._t_pre = t_pre
            self.log.debug(f'set _t_pre to {self._t_pre}')

    def set_t_pi(self, t):
        t_pi = float(t)
        with self._thread_lock:
            assert self.module_state() == 'idle', \
                'Unable to set t_pi. Data acquisition in progress.'
            self._t_pi = t_pi
            self.log.debug(f'set _t_pi to {self._t_pi}')

    def set_t_delay(self, t):
        t_delay = float(t)
        with self._thread_lock:
            assert self.module_state() == 'idle', \
                'Unable to set t_delay. Data acquisition in progress.'
            self._t_delay = t_delay
            self.log.debug(f'set _t_delay to {self._t_delay}')
    
    def set_t_readout(self, t):
        t_readout = float(t)
        with self._thread_lock:
            assert self.module_state() == 'idle', \
                'Unable to set t_delay. Data acquisition in progress.'
            self._t_readout = t_readout
            self.log.debug(f'set _t_readout to {self._t_readout}')

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
        self._init_tt_cbm_task()
        t_end = time.time()
        print('Time spent for _init_tt_cbm_task():', t_end - t_start, flush=True)
        
        # So that init_pulser() and init_digitizer() will be skipped starting
        # from the next scan given that the settings are kept unchanged.
        self.update_scan_settings()
        
        time.sleep(0.1)
        self.pulser.pulser_on()

    def stop_buffered_acquisition(self):
        """ Will abort the currently running data frame acquisition.
        Will return AFTER the data acquisition has been terminated without waiting for all samples
        to be acquired (if possible).

        Must NOT raise exceptions if no data acquisition is running.
        """
        if self.module_state() == 'locked':
            self.pulser.pulser_off()
            self.module_state.unlock()

    def clear_pulser(self):
        self.pulser.clear_all()

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
        while not self.timetagger_cbm_task.ready():
            time.sleep(0.2)
        line = self.timetagger_cbm_task.getData()
        
        signal = line[::2]
        ref = line[1::2]
        if self.mode in ['cw', 'cw_awg']:
            #print(ref)
            data = (signal - ref)*100.0/ref
            # data = signal
        if self.mode in ['pulsed', 'pulsed_awg']:
            signal = signal.reshape(self.frame_size, self._runs_per_period)
            ref = ref.reshape(self.frame_size, self._runs_per_period)
            diff = (signal - ref)*100.0/ref
            data = np.average(diff, axis=1)
        
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
                self.sample_rate == self._sample_rate_old and \
                self.mode == self._mode_old
    
    def update_scan_settings(self):
        # Update the scan setting after init BOTH pulser and digitizer
        self._frame_size_old = self.frame_size
        self._sample_rate_old = self.sample_rate
        self._mode_old = self.mode
    
    def _init_pulser(self):
        # if self.is_scan_settings_unchanged():
        #     if self.pulser._current_pb_waveform_name == 'ODMR_%s' % self.mode:
        #         print('No need to init pulser again')
        #         return

        if self.mode.lower() == 'cw':
            waveform = self._get_waveform_cw()
        elif self.mode.lower() == 'cw_awg':
            waveform = self._get_waveform_cw_awg()
        elif self.mode.lower() == 'pulsed':
            waveform = self._get_waveform_pulsed()
        elif self.mode.lower() == 'pulsed_awg':
            waveform = self._get_waveform_pulsed_awg()
        self.pulser.init_waveform('ODMR_%s' % self.mode)
        self.pulser._current_pb_waveform = waveform.copy()
        print(self.pulser._current_pb_waveform_name)
        self.pulser.write_pulse_loop(self.pulser._current_pb_waveform, loop=False)
     
    def _get_waveform_cw(self):
        aom = self.channels['aom']
        mw = self.channels['mw']
        next = self.channels['next']
        begin = self.channels['begin']
        end = self.channels['end']
        
        period = 1./self.sample_rate
        t_pre = self._t_pre

                
        waveform = []

        CONT = self.pulser.CONTINUE
        LOOP = self.pulser.LOOP
        END  = self.pulser.END_LOOP

        N_Loop = self.frame_size
        waveform = [
            {'active_channels':[aom                      ], 'length': t_pre,       'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom, mw,             next], 'length': period*0.35, 'inst':LOOP, 'inst_data':N_Loop},
            {'active_channels':[aom, mw, begin,          ], 'length': period*0.6,  'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,            end,     ], 'length': period*0.35, 'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,     begin           ], 'length': period*0.6,  'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,            end,     ], 'length': period*0.1,  'inst':END,  'inst_data':1     },
            {'active_channels':[                         ], 'length': period*0.1,  'inst':None, 'inst_data':None  },
        ]
        return waveform
    
    def _get_waveform_cw_awg(self):
        aom = self.channels['aom']
        mw = self.channels['mw']
        awg = self.channels['awg']
        next = self.channels['next']
        begin = self.channels['begin']
        end = self.channels['end']
        
        period = 1./self.sample_rate
        t_pre = self._t_pre

                
        waveform = []

        CONT = self.pulser.CONTINUE
        LOOP = self.pulser.LOOP
        END  = self.pulser.END_LOOP

        N_Loop = self.frame_size
        waveform = [
            {'active_channels':[aom                           ], 'length': t_pre,      'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom, mw, awg,             next], 'length': period*0.1, 'inst':LOOP, 'inst_data':N_Loop},
            {'active_channels':[aom, mw,      begin,          ], 'length': period*0.8, 'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,                 end,     ], 'length': period*0.1, 'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,                          ], 'length': period*0.1, 'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,          begin           ], 'length': period*0.8, 'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,                 end,     ], 'length': period*0.1, 'inst':END,  'inst_data':1     },
            {'active_channels':[                              ], 'length': period*0.1, 'inst':None, 'inst_data':None  },
        ]
        return waveform
    
    def _get_waveform_pulsed(self):
        aom = self.channels['aom']
        mw = self.channels['mw']
        next = self.channels['next']
        begin = self.channels['begin']
        end = self.channels['end']
        
        us = 1e-6
        period = 1./self.sample_rate
        N = self._runs_per_period

        t_next = 50*us
        T = (period - t_next)/N

               
        CONT = self.pulser.CONTINUE
        LOOP = self.pulser.LOOP
        END  = self.pulser.END_LOOP

        N_Loop = self.frame_size
        
        t_wait = 4*us
        #t_mw = 1*us
        t_pre = self._t_pre
        t_mw = self._t_pi
        t_delay = self._t_delay
        t_readout = self._t_readout

        t_init = T - t_wait - t_mw - t_delay - t_readout*2
        waveform = [
            {'active_channels':[aom                    ], 'length': t_pre,         'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,               next], 'length': t_next/2,    'inst':LOOP, 'inst_data':N_Loop},
            
            {'active_channels':[                       ], 'length': t_wait,      'inst':LOOP, 'inst_data':N     },
            {'active_channels':[     mw,               ], 'length': t_mw,        'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,                   ], 'length': t_delay,     'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,     begin         ], 'length': t_readout,   'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,           end     ], 'length': t_init*0.9,  'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,     begin         ], 'length': t_readout,   'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,           end     ], 'length': t_init*0.1,  'inst':END,  'inst_data':2     },

            {'active_channels':[                       ], 'length': t_next/2,    'inst':END,  'inst_data':1     },
            {'active_channels':[                       ], 'length': period*0.1,  'inst':None, 'inst_data':None  },
        ]        
        return waveform

    def _get_waveform_pulsed_awg(self):
        aom = self.channels['aom']
        mw = self.channels['mw']
        awg = self.channels['awg']
        next = self.channels['next']
        begin = self.channels['begin']
        end = self.channels['end']
        
        us = 1e-6
        period = 1./self.sample_rate
        N = self._runs_per_period

        t_next = 50*us
        T = (period - t_next)/N

               
        CONT = self.pulser.CONTINUE
        LOOP = self.pulser.LOOP
        END  = self.pulser.END_LOOP

        N_Loop = self.frame_size
        
        t_wait = 4*us
        #t_mw = 1*us
        t_pre = self._t_pre
        t_mw = self._t_pi
        t_delay = self._t_delay
        t_readout = self._t_readout

        t_init = T - t_wait - t_mw - t_delay - t_readout*2
        waveform = [
            {'active_channels':[aom                        ], 'length': t_pre,      'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,                   next], 'length': t_next/2,   'inst':LOOP, 'inst_data':N_Loop},
            
            {'active_channels':[                           ], 'length': t_wait,     'inst':LOOP, 'inst_data':N     },
            {'active_channels':[     mw, awg               ], 'length': t_mw,       'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,                       ], 'length': t_delay,    'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,         begin         ], 'length': t_readout,  'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,               end     ], 'length': t_init*0.9, 'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,     begin             ], 'length': t_readout,  'inst':CONT, 'inst_data':None  },
            {'active_channels':[aom,               end     ], 'length': t_init*0.1, 'inst':END,  'inst_data':2     },

            {'active_channels':[                           ], 'length': t_next/2,   'inst':END,  'inst_data':1     },
            {'active_channels':[                           ], 'length': period*0.1, 'inst':None, 'inst_data':None  },
        ]        
        return waveform

    def _init_tt_cbm_task(self):
        """
        Set up tasks for digital event counting with the TIMETAGGER
        cbm stnads for count between markers
        """        
        ch_ticks = self.channels_tt['ticks']
        ch_begin = self.channels_tt['begin']
        ch_end = self.channels_tt['end']

        if self.mode in ['cw', 'cw_awg']:
            number_of_gates = self.frame_size*2
        
        if self.mode in ['pulsed', 'pulsed_awg']:
            number_of_gates = self.frame_size*self._runs_per_period*2

        self.timetagger_cbm_task = self.timetagger.count_between_markers(
            click_channel = ch_ticks,
            begin_channel = ch_begin,
            end_channel = ch_end,
            n_values = number_of_gates,
        )
        return
