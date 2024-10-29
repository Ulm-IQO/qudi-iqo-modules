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
from pyspcm import *
from spcm_tools import *
from hardware.spectrum.spectrum_control import *

from qudi.util.mutex import RecursiveMutex
from qudi.core.configoption import ConfigOption
from qudi.interface.finite_sampling_input_interface import FiniteSamplingInputInterface, FiniteSamplingInputConstraints


#use spectrum card to acquire data keep the same functionality as ni_x_series_finite_sampling_input.py but useing spectrum card
class SpectrumFiniteSamplingInput(FiniteSamplingInputInterface):
    """
    Interface for input of data of a certain length atspectrum_finite_sampling_input:
        module.Class: 'spectrum.spectrum_finite_sampling_input.SpectrumFiniteSamplingInput'
        options:
            buffer_size: 4096       # unit: uint64(MEGA_B(4)), as huge as possible
            segment_size: 4096         # samples per trigger
            samples_per_loop: 1024  # unit: uint64(KILO_B( ))
            sample_rate: 20            # unit: int64(MEGA( ))
            channel: 1                 # channel = 1
            timeout: 5000              # unit: ms
            input_range: 5000          # unit: mV
    """

        # samplerate = int64(MEGA(20))
        # channel = 1
        # timeout = 5000
        # lNotifySize = int32(KILO_B(16))
        # qwBufferSize = uint64(MEGA_B(1))
        # lSegmentSize = 3072
        # qwToTransfer = uint64(MEGA_B(8))
    #config options
    qwBufferSize = ConfigOption(name='buffer_size', default=4, missing="info")
    lSegmentSize = ConfigOption(name='segment_size', default=3072, missing="info")
    #lNotifySize = ConfigOption(name='notify_size', default=6, missing="info")
    qwToTransfer = ConfigOption(name='samples_per_loop', default=512, missing="info") #loop size
    samplerate = ConfigOption(name='sample_rate', default=20, missing="info")
    _analog_channel = ConfigOption(name='analog_channel', default=1, missing="info")
    _timeout = ConfigOption(name='timeout', default=5000, missing="info")
    input_range = ConfigOption(name='input_range', default=5000, missing="info")
    _enable_debug = ConfigOption('enable_debug', default=False)

    #set limit
    _sample_rate_limits = ConfigOption(name='sample_rate_limits', default=(1, 1e6))
    _frame_size_limits = ConfigOption(name='frame_size_limits', default=(1, 1e9))
    _channel_units = ConfigOption(name='channel', default=dict(), missing='info')
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        #keep the same as spectrum example simple_rec_fifo.py
        self.szErrorTextBuffer = create_string_buffer(ERRORTEXTLEN)
        self.dwError = uint32()
        self.lStatus = int32()
        self.lAvailUser = int32()
        self.lPCPos = int32()
        self.lChCount = int32()
        self.lSegmentIndex = uint32(0)
        self.lSegmentCnt = uint32(0)
        self.llSamplingrate = int64(0)
        self.qwTotalMem = uint64(0)
        self._thread_lock = RecursiveMutex()


        #get the parameters from the config options
        self.samplerate = int64(MEGA(20))
        self.analog_channel = self._analog_channel
        self.timeout = 5000
        self.qwBufferSize = uint64(MEGA_B(4))
        self.lSegmentSize = 3072
        self.qwToTransfer = uint64(MEGA_B(4))
        self.lNotifySize = int32(self.qwToTransfer.value)
        
        #print out the parameters
        sys.stdout.write(f"samples per loop: {self.qwToTransfer.value // 2 - 1}\n")

        # open card
        # uncomment the second line and replace the IP address to use remote
        # cards like in a digitizerNETBOX
        self.hCard = spcm_hOpen(create_string_buffer(b'/dev/spcm0'))
        # hCard = spcm_hOpen(create_string_buffer(b'TCPIP::192.168.1.10::inst0::INSTR'))
        if not self.hCard:
            sys.stdout.write("no card found...\n")
            exit(1)

        # get card type name from driver
        # self.qwValueBufferLen = 20
        # self.pValueBuffer = pvAllocMemPageAligned(self.qwValueBufferLen)
        # spcm_dwGetParam_ptr(self.hCard, SPC_PCITYP, self.pValueBuffer, self.qwValueBufferLen)
        # self.sCardName = self.pValueBuffer.value.decode('UTF-8')
        # sys.stdout.write(f"card found: {self.sCardName}\n")

        # read type, function and sn and check for A/D card
        self.lCardType = int32(0)
        spcm_dwGetParam_i32(self.hCard, SPC_PCITYP, byref(self.lCardType))
        self.lSerialNumber = int32(0)
        spcm_dwGetParam_i32(self.hCard, SPC_PCISERIALNO, byref(self.lSerialNumber))
        self.lFncType = int32(0)
        spcm_dwGetParam_i32(self.hCard, SPC_FNCTYPE, byref(self.lFncType))
            #check A/D cards
        self.sCardName = szTypeToName(self.lCardType.value)
        if self.lFncType.value == SPCM_TYPE_AI:
            sys.stdout.write("Found: {0} sn {1:05d}\n".format(self.sCardName, self.lSerialNumber.value))
        else:
            sys.stdout.write("Card: {0} sn {1:05d} not supported by this program !\n".format(self.sCardName, self.lSerialNumber.value))
            spcm_vClose(self.hCard)
            exit(1)

        # do a simple standard setup
        spcm_dwSetParam_i32(self.hCard, SPC_CHENABLE,         1)           # just 1 channel enabled
        spcm_dwSetParam_i32(self.hCard, SPC_PRETRIGGER,       1024)                   # 1k of pretrigger data at start of FIFO mode
        spcm_dwSetParam_i32(self.hCard, SPC_CARDMODE,         SPC_REC_FIFO_MULTI)     # multiple recording FIFO mode
        spcm_dwSetParam_i32(self.hCard, SPC_SEGMENTSIZE,      self.lSegmentSize)           # set segment size
        spcm_dwSetParam_i32(self.hCard, SPC_POSTTRIGGER,      self.lSegmentSize - 128)     # set posttrigger
        spcm_dwSetParam_i32(self.hCard, SPC_LOOPS,            0)                      # set loops
        spcm_dwSetParam_i32(self.hCard, SPC_CLOCKMODE,        SPC_CM_INTPLL)          # clock mode internal PLL
        spcm_dwSetParam_i32(self.hCard, SPC_CLOCKOUT,         0)                      # no clock output
        spcm_dwSetParam_i32(self.hCard, SPC_TRIG_EXT0_MODE,   SPC_TM_POS)             # set trigger mode
        spcm_dwSetParam_i32(self.hCard, SPC_TRIG_TERM,        0)                      # set trigger termination !!!
        #spcm_dwSetParam_i32(self.hCard, SPC_TRIG_ORMASK,      SPC_TMASK_EXT0)         # trigger set to external
        spcm_dwSetParam_i32(self.hCard, SPC_TRIG_ANDMASK,     0)                      # ...
        spcm_dwSetParam_i32(self.hCard, SPC_TRIG_EXT0_ACDC,   COUPLING_DC)            # trigger coupling
        spcm_dwSetParam_i32(self.hCard, SPC_TRIG_EXT0_LEVEL0, 1500)                   # trigger level of 1.5 Volt
        spcm_dwSetParam_i32(self.hCard, SPC_TRIG_EXT0_LEVEL1, 0)                      # unused
        spcm_dwSetParam_i32(self.hCard, SPC_TIMEOUT,          5000)                   # timeout 5 s
        spcm_dwSetParam_i32(self.hCard, SPC_PATH0,            1)                      # 50 Ohm termination
        spcm_dwSetParam_i32(self.hCard, SPC_ACDC0,            0)                      # clock mode internal PLL


        spcm_dwGetParam_i32(self.hCard, SPC_CHCOUNT, byref(self.lChCount))            # get the number of activated channels

        for lChannel in range(0, self.lChCount.value, 1):
            spcm_dwSetParam_i32(self.hCard, SPC_AMP0 + lChannel * (SPC_AMP1 - SPC_AMP0), 5000)  # set input range to +/- 1000 mV

        # we try to set the samplerate to 100 kHz (M2i) or 20 MHz on internal PLL, no clock output
        spcm_dwSetParam_i64(self.hCard, SPC_SAMPLERATE, self.samplerate)

                # read back current sampling rate from driver
        spcm_dwGetParam_i64(self.hCard, SPC_SAMPLERATE, byref(self.llSamplingrate))  # get the current sampling rate
        sys.stdout.write(f"actual samplerate: {self.llSamplingrate.value}\n")

        # define the data buffer
        # we try to use continuous memory if available and big enough
        self.pvBuffer = ptr8()  # will be cast to correct type later
        self.qwContBufLen = uint64(0)
        spcm_dwGetContBuf_i64(self.hCard, SPCM_BUF_DATA, byref(self.pvBuffer), byref(self.qwContBufLen))
        sys.stdout.write("ContBuf length: {0:d}\n".format(self.qwContBufLen.value))
        if self.qwContBufLen.value >= self.qwBufferSize.value:
            sys.stdout.write("Using continuous buffer\n")
        else:
            self.pvBuffer = cast(pvAllocMemPageAligned(self.qwBufferSize.value), ptr8)  # cast to ptr8 to make it behave like the continuous memory
            sys.stdout.write("Using buffer allocated by user program\n")

        # set up buffer for data transfer
        spcm_dwDefTransfer_i64(self.hCard, SPCM_BUF_DATA, SPCM_DIR_CARDTOPC, self.lNotifySize, self.pvBuffer, uint64(0), self.qwBufferSize)

    def on_activate(self) -> None:

        #constraints
        self.channel_units = {'APD counts': 'c/s', 'Photodiode': 'V'}
        self._constraints = FiniteSamplingInputConstraints(
            channel_units=self.channel_units,
            frame_size_limits=self._frame_size_limits,
            sample_rate_limits=self._sample_rate_limits
        )
        # Make sure the ConfigOptions have correct values and types
        # (ensured by FiniteSamplingInputConstraints)
        self._sample_rate_limits = self._constraints.sample_rate_limits
        self._frame_size_limits = self._constraints.frame_size_limits
        self._channel_units = self._constraints.channel_units

        self._sample_rate = self._constraints.max_sample_rate
        self._active_channels = frozenset(self._constraints.channel_names)

    def on_deactivate(self) -> None:
        self.dwError = spcm_dwSetParam_i32(self.hCard, SPC_M2CMD, M2CMD_CARD_STOP | M2CMD_DATA_STOPDMA)
        spcm_vClose(self.hCard)
        sys.stdout.write("card closed...\n")

    @property
    def constraints(self):
        return self._constraints
    
    @property
    def active_channels(self):
        return frozenset([f"Channel {self.analog_channel}"])
    
    @property
    def sample_rate(self):
        return self.samplerate
    
    @property
    def frame_size(self):
        ##how many point in each frame

        # this is for if qwToTransfer is not same of lNotifySize.value
        # sys.stdout.write(f"the frame size is {(self.lNotifySize.value//2 - 1) * (self.qwToTransfer.value // self.lNotifySize.value)}\n")
        # return (self.lNotifySize.value//2 - 1) * (self.qwToTransfer.value // self.lNotifySize.value)
        return self.qwToTransfer.value // 2 - 1  #qwToTransfer is same of lNotifySize.value
    
    @property
    def samples_in_buffer(self):
        with self._thread_lock:
            if self.module_state() == 'locked':
            #check how many points in buffer
                spcm_dwGetParam_i32(self.hCard, SPC_DATA_AVAIL_USER_LEN, byref(self.lAvailUser)) # get how many available data in user buffer
                if not self.lAvailUser.value:
                    return 0
                else:
                    return  self.qwToTransfer.value // 2 - 1
    
    def set_sample_rate(self, rate):
        with self._thread_lock:
            sample_rate = float(rate)
            assert self._constraints.sample_rate_in_range(sample_rate)[0], \
                f'Sample rate "{sample_rate}Hz" to set is out of ' \
                f'bounds {self._constraints.sample_rate_limits}'
            with self._thread_lock:
                assert self.module_state() == 'idle', \
                    'Unable to set sample rate. Data acquisition in progress.'
                self.samplerate = sample_rate
                # spcm_dwSetParam_i64(self.hCard, SPC_SAMPLERATE, self.samplerate)
            

    def set_active_channels(self, channels):
        with self._thread_lock:
            assert self.module_state() != 'locked', \
                'Unable to change active channels while finite sampling is running. New settings ignored.'
            
            self._analog_channel = channels
            return self.analog_channel
        

    def set_frame_size(self, size):

        size = int(round(size))
        with self._thread_lock:
            assert self.module_state() == 'idle', \
                'Unable to set frame size. Data acquisition in progress.'

        # this is for if qwToTransfer is not same of lNotifySize.value
        # tempvalue = size // (self.lNotifySize.value//2 - 1) * self.lNotifySize.value
        # self.qwToTransfer = uint64(tempvalue)
        # self.frameSize = size
        # return (self.lNotifySize.value//2 - 1) * (self.qwToTransfer.value // self.lNotifySize.value)

        #for the case of qwToTransfer is same of lNotifySize.value
            self.qwToTransfer = uint64((size + 1) * 2)
            self.qwBufferSize = uint64(self.qwToTransfer.value)
            self.lNotifySize = int32(self.qwToTransfer.value)

    
    def start_buffered_acquisition(self):

        assert self.module_state() == 'idle', \
            'Unable to start data acquisition. Data acquisition already in progress.'
        self.module_state.lock()
                    # start everything
        self.dwError = spcm_dwSetParam_i32(self.hCard, SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER | M2CMD_DATA_STARTDMA) # start card
        print('start sampling1')
            

    def stop_buffered_acquisition(self):
        
        if self.module_state() == 'locked':
                print('stop sampling')
                # send stop command
                self.dwError = spcm_dwSetParam_i32(self.hCard, SPC_M2CMD, M2CMD_CARD_STOP | M2CMD_DATA_STOPDMA)
                print("Stop/Pause.... \n")
                self.module_state.unlock()

    
    def get_buffered_samples(self):
        
        # check for error
        if self.dwError != 0:  # != ERR_OK
            print('get sampling1')
            spcm_dwGetErrorInfo_i32(self.hCard, None, None, self.szErrorTextBuffer)
            sys.stdout.write("{0}\n".format(self.szErrorTextBuffer.value))
            spcm_vClose(self.hCard)
            exit(1)


        else:
            data = dict()
            self.qwTotalMem = uint64(0)
            self.lSegmentCnt = uint32(0)
            print('get sampling2')
            while self.qwTotalMem.value < self.qwToTransfer.value:
                temp = np.array([])
                self.dwError = spcm_dwSetParam_i32(self.hCard, SPC_M2CMD, M2CMD_DATA_WAITDMA) # wait for data
                
                if self.dwError != ERR_OK:
                    if self.dwError == ERR_TIMEOUT:
                        sys.stdout.write("... Timeout\n")
                        break
                    else:
                        sys.stdout.write("... Error: {0:d}\n".format(self.dwError))
                        break
                
                else:
                    print('get sampling3')
                    spcm_dwGetParam_i32(self.hCard, SPC_M2STATUS, byref(self.lStatus)) # get status
                    spcm_dwGetParam_i32(self.hCard, SPC_DATA_AVAIL_USER_LEN, byref(self.lAvailUser)) # get how many available data in user buffer
                    spcm_dwGetParam_i32(self.hCard, SPC_DATA_AVAIL_USER_POS, byref(self.lPCPos)) # get position in user buffer
                    # if self.lAvailUser.value >= self.lNotifySize.value:
                    #     self.qwTotalMem.value += self.lNotifySize.value
                        
                    # sys.stdout.write("Stat:{0:08x} Pos:{1:08x} Avail:{2:08x} Total:{3:.2f}MB/{4:.2f}MB\n".format(self.lStatus.value, self.lPCPos.value, self.lAvailUser.value, c_double(self.qwTotalMem.value).value / MEGA_B(1), c_double(self.qwToTransfer.value).value / MEGA_B(1)))
                    self.qwTotalMem.value += self.qwToTransfer.value
                    self.pnData = cast(addressof(self.pvBuffer.contents) + self.lPCPos.value, ptr16)
                    temp = np.array(self.pnData[:self.qwToTransfer.value//2 - 1])
                    # if not len(store_data[0]):
                    #     store_data = np.array([temp])
                    # else:
                    #     store_data = np.append(store_data, [temp], axis=0)
                    spcm_dwSetParam_i32(self.hCard, SPC_DATA_AVAIL_CARD_LEN, self.qwToTransfer)
            #convert temp to a dictionary
                    print(temp.size)
            data[1] = temp

            return data

    def acquire_frame(self):
        
        #     if frame_size is None:
        #         buffered_frame_size = None
        #     else:
        #         buffered_frame_size = frame_size
        #         self.set_frame_size(buffered_frame_size)

        self.start_buffered_acquisition()
        data = self.get_buffered_samples()
        self.stop_buffered_acquisition()
        
        return data







    
    