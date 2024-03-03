# -*- coding: utf-8 -*-

"""
This file contains configure commands classes used for spectrum instrumentation ADC.

QCopyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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
import ctypes
from functools import reduce
from pyspcm import *
from qudi.hardware.fast_adc.spectrum_instrumentation.si_settings import CardSettings


class ConfigureCommands:
    '''
    This class instantiates all the classes for configuration and input the card settings.
    '''

    def __init__(self, card, log):
        self._card = card
        self._log = log

        self._c_buf_ptr = ctypes.c_void_p()
        self._c_ts_buf_ptr = ctypes.c_void_p()

        self._ai = AnalogInputConfigureCommands(self._card, self._log)
        self._acq = AcquisitionConfigureCommands(self._card, self._log)
        self._trig = TriggerConfigureCommands(self._card, self._log)
        self._buf = DataTransferConfigureCommands(self._card, self._log)
        self._ts = TimestampConfigureCommands(self._card, self._log)

        self._csr = ConfigureRegisterChecker(self._card, self._log)

    def configure_all(self, cs: CardSettings):
        """
        Collection of all the setting methods.
        """

        self._ai.set_analog_input_conditions(cs.ai_ch)
        if 'CH0' in cs.ai_ch:
            self._ai.set_ch1(cs.ai0_range_mV, cs.ai0_offset_mV, cs.ai0_term, cs.ai0_coupling)
        if 'CH1' in cs.ai_ch:
            self._ai.set_ch0(cs.ai1_range_mV, cs.ai1_offset_mV, cs.ai1_term, cs.ai1_coupling)

        self._acq.set_acquisition_mode(cs.acq_mode, cs.acq_pre_trigs_S, cs.acq_post_trigs_S,
                                       cs.acq_mem_size_S, cs.acq_seg_size_S,
                                       cs.acq_loops, cs.acq_HW_avg_num)
        self._acq.set_sampling_clock(cs.clk_ref_Hz, cs.clk_samplerate_Hz)
        self._trig.set_trigger(cs.trig_mode, cs.trig_level_mV)
        self._buf.invalidate_buffer(SPCM_BUF_DATA)
        self._c_buf_ptr = self._buf.configure_data_transfer(SPCM_BUF_DATA, self._c_buf_ptr,
                                                            cs.buf_size_B, cs.buf_notify_size_B)
        if cs.gated:
            self._buf.invalidate_buffer(SPCM_BUF_TIMESTAMP)
            self._c_ts_buf_ptr = self._buf.configure_data_transfer(SPCM_BUF_TIMESTAMP, self._c_ts_buf_ptr,
                                                                   cs.ts_buf_size_B, cs.ts_buf_notify_size_B)
            self._ts.configure_ts_standard()

    def return_c_buf_ptr(self):
        return self._c_buf_ptr

    def return_c_ts_buf_ptr(self):
        return self._c_ts_buf_ptr


class AnalogInputConfigureCommands:
    ai_ch_dict = {'CH0': CHANNEL0, 'CH1': CHANNEL1}
    ai_term_dict = {'1MOhm': 0, '50Ohm': 1}
    ai_coupling_dict = {'DC': 0, 'AC': 1}

    def __init__(self, card, log):
        self._card = card
        self._log = log

    def set_analog_input_conditions(self, ai_ch):
        spcm_dwSetParam_i32(self._card, SPC_TIMEOUT, 5000)
        spcm_dwSetParam_i32(self._card, SPC_CHENABLE, self._get_ch_bitmap(ai_ch))
        return

    def _get_ch_bitmap(self, ai_ch):
        if len(ai_ch) == 1:
            enabled_ch_bitmap = self.ai_ch_dict[ai_ch[0]]
        else:
            enabled_ch_bitmap = reduce(lambda x, y: self.ai_ch_dict[x]|self.ai_ch_dict[y], ai_ch)
        return enabled_ch_bitmap

    def set_ch0(self, ai_range_mV, ai_offset_mV, ai_term, ai_coupling):
        spcm_dwSetParam_i32(self._card, SPC_AMP0, ai_range_mV) # +- 10 V
        spcm_dwSetParam_i32(self._card, SPC_OFFS0, ai_offset_mV)
        spcm_dwSetParam_i32(self._card, SPC_50OHM0, self.ai_term_dict[ai_term]) # A "1"("0") sets the 50(1M) ohm termination
        self._error = spcm_dwSetParam_i32(self._card, SPC_ACDC0, self.ai_coupling_dict[ai_coupling])  # A "0"("1") sets he DC(AC)coupling

    def set_ch1(self, ai_range_mV, ai_offset_mV, ai_term, ai_coupling):
        spcm_dwSetParam_i32(self._card, SPC_AMP1, ai_range_mV) # +- 10 V
        spcm_dwSetParam_i32(self._card, SPC_OFFS1, ai_offset_mV)
        spcm_dwSetParam_i32(self._card, SPC_50OHM1, self.ai_term_dict[ai_term]) # A "1"("0") sets the 50(1M) ohm termination
        self._error = spcm_dwSetParam_i32(self._card, SPC_ACDC1, self.ai_coupling_dict[ai_coupling])  # A "0"("1") sets he DC(AC)coupling

class AcquisitionConfigureCommands:

    def __init__(self, card, log):
        self._card = card
        self._log = log

    def set_acquisition_mode(self, mode, pre_trigs_S, post_trigs_S, mem_size_S, seg_size_S, loops, HW_avg_num):
        if 'STD' in mode:
            if 'GATE' in mode:
                self._set_STD_gate_mode(mode, pre_trigs_S,post_trigs_S, mem_size_S)
            else:
                self._set_STD_trigger_mode(mode, post_trigs_S, seg_size_S, mem_size_S)

        elif 'FIFO' in mode:
            if 'GATE' in mode:
                self._set_FIFO_gate_mode(mode, pre_trigs_S, post_trigs_S, loops)
            else:
                self._set_FIFO_trigger_mode(mode, pre_trigs_S, post_trigs_S, seg_size_S, loops, HW_avg_num)

        else:
            raise ValueError('The acquisition mode is not proper')

    def set_sampling_clock(self, clk_ref_Hz, clk_samplerate_Hz):
        spcm_dwSetParam_i32(self._card, SPC_CLOCKMODE, SPC_CM_INTPLL)
        spcm_dwSetParam_i32(self._card, SPC_REFERENCECLOCK, clk_ref_Hz)
        spcm_dwSetParam_i32(self._card, SPC_SAMPLERATE, clk_samplerate_Hz)
        spcm_dwSetParam_i32(self._card, SPC_CLOCKOUT, 1)
        return

    def _set_STD_trigger_mode(self, acq_mode, post_trigs_S, seg_size_S, mem_size_S):
        if acq_mode == 'STD_SINGLE':
            mem_size_S = post_trigs_S
            self._mode_STD_SINGLE(post_trigs_S, mem_size_S)

        elif acq_mode == 'STD_MULTI':
            self._mode_STD_MULTI(post_trigs_S, seg_size_S, mem_size_S)

        else:
            raise ValueError('The used acquistion mode is not defined')

    def _set_STD_gate_mode(self, acq_mode, pre_trigs_S, post_trigs_S, mem_size_S):
        if acq_mode == 'STD_GATE':
            self._mode_STD_GATE(pre_trigs_S, post_trigs_S, mem_size_S)

        else:
            raise ValueError('The used acquistion mode is not defined')

    def _set_FIFO_trigger_mode(self, acq_mode, pre_trigs_S, post_trigs_S, seg_size_S, loops, HW_avg_num=0):
        if acq_mode == 'FIFO_SINGLE':
            self._mode_FIFO_SINGLE(pre_trigs_S, seg_size_S, loops)

        elif acq_mode == 'FIFO_MULTI':
            self._mode_FIFO_MULTI(post_trigs_S, seg_size_S, loops)

        elif acq_mode == 'FIFO_AVERAGE':
            self._mode_FIFO_AVERAGE(post_trigs_S, seg_size_S, loops, HW_avg_num)

        else:
            raise ValueError('The used acquistion mode is not defined')

    def _set_FIFO_gate_mode(self, acq_mode, pre_trigs_S, post_trigs_S, loops):

        if acq_mode == 'FIFO_GATE':
            self._mode_FIFO_GATE(pre_trigs_S, post_trigs_S, loops)

        else:
            raise ValueError('The used acquistion mode is not defined')

    def _mode_STD_SINGLE(self, post_trigs_S, memsize_S):
        """
        In this mode, pre trigger = memsize - post trigger.
        @params int post_trig_S: the number of samples to be recorded after the trigger event has been detected.
        @params int memsize_S: the total number of samples to be recorded
        """
        spcm_dwSetParam_i32(self._card, SPC_CARDMODE, SPC_REC_STD_SINGLE)
        spcm_dwSetParam_i32(self._card, SPC_MEMSIZE, memsize_S)
        self._error = spcm_dwSetParam_i32(self._card, SPC_POSTTRIGGER, post_trigs_S)
        return

    def _mode_STD_MULTI(self, post_trigs_S, seg_size_S, mem_size_S):
        """
        SEGMENTSIZE is the numbe of samples recorded after detection of one trigger
        including the pre trigger.
        MEMSIZE defines the total number of samples to be recorded per channel.

        @params int post_trig_S:
        @params int seg_size_S:
        @params int reps: The number of repetitions.
        """
        spcm_dwSetParam_i32(self._card, SPC_CARDMODE, SPC_REC_STD_MULTI)
        spcm_dwSetParam_i32(self._card, SPC_SEGMENTSIZE, seg_size_S)
        spcm_dwSetParam_i32(self._card, SPC_MEMSIZE, mem_size_S)
        self._error = spcm_dwSetParam_i32(self._card, SPC_POSTTRIGGER, post_trigs_S)
        return

    def _mode_STD_GATE(self, pre_trigs_S, post_trigs_S, mem_size_S):
        """
        @params int pre_trigs_S: the number of samples to be recorded prior to the gate start
        @params int post_trigs_S: the number of samples to be recorded after the gate end
        @params int mem_size_S: the total number of samples to be recorded
        """
        spcm_dwSetParam_i32(self._card, SPC_CARDMODE, SPC_REC_STD_GATE)
        spcm_dwSetParam_i32(self._card, SPC_PRETRIGGER, pre_trigs_S)
        spcm_dwSetParam_i32(self._card, SPC_POSTTRIGGER, post_trigs_S)
        self._error = spcm_dwSetParam_i32(self._card, SPC_MEMSIZE, mem_size_S)
        return

    def _mode_FIFO_SINGLE(self, pre_trigs_S, seg_size_S, loops=1):
        """
        SEGMENTSIZE is the numbe of samples recorded after detection of one trigger
        including the pre trigger.

        @params int pre_trigs_S: the number of samples to be recorded prior to the gate start
        @params int seg_size_S: the numbe of samples recorded after detection of one trigger
                                including the pre trigger.
        @params int loops: the total number of loops
        """

        spcm_dwSetParam_i32(self._card, SPC_CARDMODE, SPC_REC_FIFO_SINGLE)
        spcm_dwSetParam_i32(self._card, SPC_PRETRIGGER, pre_trigs_S)
        spcm_dwSetParam_i32(self._card, SPC_SEGMENTSIZE, seg_size_S)
        self._error = spcm_dwSetParam_i32(self._card, SPC_LOOPS, loops)
        return

    def _mode_FIFO_MULTI(self, post_trigs_S, seg_size_S, loops=0):
        """
        SEGMENTSIZE is the numbe of samples recorded after detection of one trigger
        including the pre trigger.

        @params int pre_trigs_S: the number of samples to be recorded after the gate start
        @params int seg_size_S: the numbe of samples recorded after detection of one trigger
                                including the pre trigger.
        @params int loops: the total number of loops
        """
        spcm_dwSetParam_i32(self._card, SPC_CARDMODE, SPC_REC_FIFO_MULTI)
        spcm_dwSetParam_i32(self._card, SPC_POSTTRIGGER, post_trigs_S)
        spcm_dwSetParam_i32(self._card, SPC_SEGMENTSIZE, seg_size_S)
        self._error = spcm_dwSetParam_i32(self._card, SPC_LOOPS, loops)
        return

    def _mode_FIFO_AVERAGE(self, post_trigs_S, seg_size_S, loops, HW_avg_num):
        max_post_trigs_S = 127984

        spcm_dwSetParam_i32(self._card, SPC_CARDMODE, SPC_REC_FIFO_AVERAGE)
        spcm_dwSetParam_i32(self._card, SPC_AVERAGES, HW_avg_num)
        spcm_dwSetParam_i32(self._card, SPC_POSTTRIGGER, post_trigs_S)
        spcm_dwSetParam_i32(self._card, SPC_SEGMENTSIZE, seg_size_S)
        self._error = spcm_dwSetParam_i32(self._card, SPC_LOOPS, loops)
        return

    def _mode_FIFO_GATE(self, pre_trigs_S, post_trigs_S, loops):
        """
        @params int pre_trigs_S: the number of samples to be recorded prior to the gate start
        @params int post_trigs_S: the number of samples to be recorded after the gate end
        @params int loops: the total number of loops
        """
        spcm_dwSetParam_i32(self._card, SPC_CARDMODE, SPC_REC_FIFO_GATE)
        spcm_dwSetParam_i32(self._card, SPC_PRETRIGGER, pre_trigs_S)
        spcm_dwSetParam_i32(self._card, SPC_POSTTRIGGER, post_trigs_S)
        self._error = spcm_dwSetParam_i32(self._card, SPC_LOOPS, loops)
        return


class TriggerConfigureCommands:
    ''''
    This class configures the trigger modes and the input parameters accordingly.
    '''
    def __init__(self, card, log):
        self._card = card
        self._log = log

    def set_trigger(self, trig_mode, trig_level_mV):
        """
        set the trigger settings.
        @param str card: the handle of the card
        @param str trig_mode: trigger mode
        @param int trig_level_mV: the voltage level for triggering in mV
        """
        if trig_mode == 'EXT':
            self._trigger_EXT(trig_level_mV)

        elif trig_mode == 'SW':
            self._trigger_SW()

        elif trig_mode == 'CH0':
            self._trigger_CH0(trig_level_mV)

        else:
            self._log.error('wrong trigger mode')

    def _trigger_EXT(self, trig_level_mV):
        spcm_dwSetParam_i32(self._card, SPC_TRIG_TERM, 0)
        spcm_dwSetParam_i32(self._card, SPC_TRIG_EXT0_ACDC, 0)
        spcm_dwSetParam_i32(self._card, SPC_TRIG_EXT0_MODE, SPC_TM_POS)
        spcm_dwSetParam_i32(self._card, SPC_TRIG_EXT0_LEVEL0, trig_level_mV)
        spcm_dwSetParam_i32(self._card, SPC_TRIG_ORMASK, SPC_TMASK_EXT0)
        self._error = spcm_dwSetParam_i32(self._card, SPC_TRIG_ANDMASK, 0)

    def _trigger_SW(self):
        spcm_dwSetParam_i32(self._card, SPC_TRIG_ORMASK, SPC_TMASK_SOFTWARE)
        self._error = spcm_dwSetParam_i32(self._card, SPC_TRIG_ANDMASK, 0)

    def _trigger_CH0(self, trig_level_mV):
        spcm_dwSetParam_i32(self._card, SPC_TRIG_ORMASK, SPC_TMASK_NONE)
        spcm_dwSetParam_i32(self._card, SPC_TRIG_CH_ANDMASK0, SPC_TMASK0_CH0)
        spcm_dwSetParam_i32(self._card, SPC_TRIG_CH0_LEVEL0, trig_level_mV)
        self._error = spcm_dwSetParam_i32(self._card, SPC_TRIG_CH0_MODE, SPC_TM_POS)


class DataTransferConfigureCommands:
    '''
    This class configures the transfer buffer dependent on the buffer type specified in the argument.
    '''
    def __init__(self, card, log):
        self._card = card
        self._log = log

    def configure_data_transfer(self, buf_type, c_buf_ptr, buf_size_B, buf_notify_size_B):
        """
        Configure the data transfer buffer

        @param str buf_type: register of data or timestamp buffer
        @param c_buf_ptr: ctypes pointer for the buffer
        @param int buf_size_B: length of the buffer size in bytes
        @param int buf_notify_size_B: length of the notify size of the buffer in bytes

        @return c_buf_ptr:
        """
        c_buf_ptr = self._set_buffer(c_buf_ptr, buf_size_B)
        self._set_data_transfer(buf_type, c_buf_ptr, buf_size_B, buf_notify_size_B)
        return c_buf_ptr

    def _set_buffer(self, c_buf_ptr, buf_size_B):
        """
        Set the continuous buffer if possible. See the documentation for the details.
        """
        cont_buf_len = self._get_cont_buf_len(c_buf_ptr)
        if cont_buf_len > buf_size_B:
            self._log.info('using continuous buffer')
        else:
            c_buf_ptr = self._pvAllocMemPageAligned(buf_size_B)
            self._log.info('using scatter gather')
        return c_buf_ptr

    def _get_cont_buf_len(self, c_buf_ptr):
        """
        Get length of the continuous buffer set in the card.
        Check also the Spectrum Control Center.

        @param c_buf_ptr: ctypes pointer for the buffer

        @return int: length of the available continuous buffer
        """
        c_cont_buf_len = uint64(0)
        spcm_dwGetContBuf_i64(self._card, SPCM_BUF_DATA, byref(c_buf_ptr), byref(c_cont_buf_len))
        return c_cont_buf_len.value

    def _pvAllocMemPageAligned(self, qwBytes):
        """
        Taken from the example
        """
        dwAlignment = 4096
        dwMask = dwAlignment - 1

        # allocate non-aligned, slightly larger buffer
        qwRequiredNonAlignedBytes = qwBytes * ctypes.sizeof(ctypes.c_char) + dwMask
        pvNonAlignedBuf = (ctypes.c_char * qwRequiredNonAlignedBytes)()

        # get offset of next aligned address in non-aligned buffer
        misalignment = ctypes.addressof(pvNonAlignedBuf) & dwMask
        if misalignment:
            dwOffset = dwAlignment - misalignment
        else:
            dwOffset = 0
        buffer = (ctypes.c_char * qwBytes).from_buffer(pvNonAlignedBuf, dwOffset)
        return buffer

    def _set_data_transfer(self, buf_type, c_buf_ptr, buf_size_B, buf_notify_size_B):
        """
       set the data transfer buffer

        @param str buf_type: register of data or timestamp buffer
        @param c_buf_ptr: ctypes pointer for the buffer
        @param int buf_size_B: length of the buffer size in bytes
        @param int buf_notify_size_B: length of the notify size of the buffer in bytes
        """

        c_buf_offset = uint64(0)
        c_buf_size_B = uint64(buf_size_B)
        spcm_dwDefTransfer_i64(self._card,
                               buf_type,
                               SPCM_DIR_CARDTOPC,
                               buf_notify_size_B,
                               byref(c_buf_ptr),
                               c_buf_offset,
                               c_buf_size_B
                               )
        return

    def invalidate_buffer(self, buf_type):
        try:
            spcm_dwInvalidateBuf(self._card, buf_type)
        except:
            pass


class TimestampConfigureCommands:
    '''
    This class configures the timestamp mode.
    '''
    def __init__(self, card, log):
        self._card = card
        self._log = log

    def configure_ts_standard(self):
        self.ts_standard_mode()

    def ts_standard_mode(self):
        spcm_dwSetParam_i32(self._card, SPC_TIMESTAMP_CMD, SPC_TSMODE_STANDARD | SPC_TSCNT_INTERNAL | SPC_TSFEAT_NONE)

    def ts_internal_clock(self):
        spcm_dwSetParam_i32(self._card, SPC_TIMESTAMP_CMD, SPC_TSCNT_INTERNAL)

    def ts_no_additional_timestamp(self):
        spcm_dwSetParam_i32(self._card, SPC_TIMESTAMP_CMD, SPC_TSFEAT_NONE)


class ConfigureRegisterChecker:
    '''
    This class can be used to check if the card settings are correctly input.
    The registers can be obtained from the card to the CardSettings (csr).
    '''
    def __init__(self, card, log):
        self._card = card
        self._log = log

        self.csr = None

    def check_cs_registers(self):
        """
        Use this method to fetch the card settings stored in the card registers
        and check them all with csr.
        """

        self.csr = CardSettings()
        self._check_csr_ai()
        self._check_csr_acq()
        self._check_csr_clk()
        self._check_csr_trig()

    def _check_csr_ai(self):
        ai_term_dict = {0:'1Mohm', 1:'50Ohm'}
        ai_coupling_dict = {0:'DC', 1:'AC'}

        c_ai_range_mV = c_int32()
        c_ai_offset_mV = c_int32()
        c_ai_term = c_int32()
        c_ai_coupling = c_int32()
        spcm_dwGetParam_i32(self._card, SPC_AMP0, byref(c_ai_range_mV)) # +- 10 V
        spcm_dwGetParam_i32(self._card, SPC_OFFS0, byref(c_ai_offset_mV))
        spcm_dwGetParam_i32(self._card, SPC_50OHM0, byref(c_ai_term))
        self._error = spcm_dwGetParam_i32(self._card, SPC_ACDC0, byref(c_ai_coupling))
        self.csr.ai_range_mV = int(c_ai_range_mV.value)
        self.csr.ai_offset_mV = int(c_ai_offset_mV.value)
        self.csr.ai_term = ai_term_dict[c_ai_term.value]
        self.csr.ai_coupling = ai_coupling_dict[c_ai_coupling.value]

    def _check_csr_acq(self):
        c_acq_mode = c_int32()
        c_acq_HW_avg_num = c_int32()
        c_acq_pre_trigs_S = c_int32()
        c_acq_post_trigs_S = c_int32()
        c_acq_mem_size_S = c_int32()
        c_acq_seg_size_S = c_int32()
        spcm_dwGetParam_i32(self._card, SPC_CARDMODE, byref(c_acq_mode))
        spcm_dwGetParam_i32(self._card, SPC_AVERAGES, byref(c_acq_HW_avg_num))
        spcm_dwGetParam_i32(self._card, SPC_PRETRIGGER, byref(c_acq_pre_trigs_S))
        spcm_dwGetParam_i32(self._card, SPC_POSTTRIGGER, byref(c_acq_post_trigs_S))
        spcm_dwGetParam_i32(self._card, SPC_MEMSIZE, byref(c_acq_mem_size_S))
        self._error = spcm_dwGetParam_i32(self._card, SPC_SEGMENTSIZE, byref(c_acq_seg_size_S))
        self.csr.acq_mode = c_acq_mode.value
        self.csr.acq_HW_avg_num = int(c_acq_HW_avg_num.value)
        self.csr.acq_pre_trigs_S = int(c_acq_pre_trigs_S.value)
        self.csr.acq_post_trigs_S = int(c_acq_post_trigs_S.value)
        self.csr.acq_mem_size_S = int(c_acq_mem_size_S.value)
        self.csr.acq_seg_size_S = int(c_acq_seg_size_S.value)

    def _check_csr_clk(self):
        c_clk_samplerate_Hz = c_int32()
        c_clk_ref_Hz = c_int32()
        spcm_dwGetParam_i32(self._card, SPC_REFERENCECLOCK, byref(c_clk_ref_Hz))
        self._error = spcm_dwGetParam_i32(self._card, SPC_SAMPLERATE, byref(c_clk_samplerate_Hz))
        self.csr.clk_samplerate_Hz = int(c_clk_samplerate_Hz.value)
        self.csr.clk_ref_Hz = int(c_clk_ref_Hz.value)

    def _check_csr_trig(self):
        c_trig_mode = c_int32()
        c_trig_level_mV = c_int32()
        spcm_dwGetParam_i32(self._card, SPC_TRIG_EXT0_MODE, byref(c_trig_mode))
        self._error = spcm_dwGetParam_i32(self._card, SPC_TRIG_EXT0_LEVEL0, byref(c_trig_level_mV))
        self.csr.trig_mode = c_trig_mode.value
        self.csr.trig_level_mV = int(c_trig_level_mV.value)
