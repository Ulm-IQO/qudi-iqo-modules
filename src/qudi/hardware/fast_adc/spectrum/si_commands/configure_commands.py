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
import pyspcm as spcm
from qudi.hardware.fast_adc.spectrum.si_utils.si_settings import CardSettings


class ConfigureCommands:
    """
    This class instantiates all the classes for configuration and input the card settings.
    """

    def __init__(self, card, log):
        """
        @param str card: The card handle.
        @param log: qudi logger from the SpectrumInstrumentation.
        """

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
            self._ai.set_ch0(cs.ai0_range_mV, cs.ai0_offset_mV, cs.ai0_term, cs.ai0_coupling)
        if 'CH1' in cs.ai_ch:
            self._ai.set_ch1(cs.ai1_range_mV, cs.ai1_offset_mV, cs.ai1_term, cs.ai1_coupling)

        self._acq.set_acquisition_mode(cs.acq_mode, cs.acq_pre_trigs_S, cs.acq_post_trigs_S,
                                       cs.acq_mem_size_S, cs.acq_seg_size_S,
                                       cs.acq_loops, cs.acq_HW_avg_num)
        self._acq.set_sampling_clock(cs.clk_ref_Hz, cs.clk_samplerate_Hz)
        self._trig.set_trigger(cs.trig_mode, cs.trig_level_mV)
        self._buf.invalidate_buffer(spcm.SPCM_BUF_DATA)
        self._c_buf_ptr = self._buf.configure_data_transfer(spcm.SPCM_BUF_DATA, self._c_buf_ptr,
                                                            cs.buf_size_B, cs.buf_notify_size_B)
        if cs.gated:
            self._buf.invalidate_buffer(spcm.SPCM_BUF_TIMESTAMP)
            self._c_ts_buf_ptr = self._buf.configure_data_transfer(spcm.SPCM_BUF_TIMESTAMP, self._c_ts_buf_ptr,
                                                                   cs.ts_buf_size_B, cs.ts_buf_notify_size_B)
            self._ts.configure_ts_standard()

    def return_c_buf_ptr(self):
        return self._c_buf_ptr

    def return_c_ts_buf_ptr(self):
        return self._c_ts_buf_ptr


class AnalogInputConfigureCommands:
    """
    This class configures the analog input.
    Refer to the chapter 'Analog Inputs' in the manual for more information.
    """
    ai_ch_dict = {'CH0': spcm.CHANNEL0, 'CH1': spcm.CHANNEL1}
    ai_term_dict = {'1MOhm': 0, '50Ohm': 1}
    ai_coupling_dict = {'DC': 0, 'AC': 1}

    def __init__(self, card, log):
        """
        @param str card: The card handle.
        @param log: qudi logger from the SpectrumInstrumentation.
        """
        self._card = card
        self._log = log

    def set_analog_input_conditions(self, ai_ch):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TIMEOUT, 5000)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_CHENABLE, self._get_ch_bitmap(ai_ch))
        return

    def _get_ch_bitmap(self, ai_ch):
        if len(ai_ch) == 1:
            enabled_ch_bitmap = self.ai_ch_dict[ai_ch[0]]
        else:
            enabled_ch_bitmap = reduce(lambda x, y: self.ai_ch_dict[x]|self.ai_ch_dict[y], ai_ch)
        return enabled_ch_bitmap

    def set_ch0(self, ai_range_mV, ai_offset_mV, ai_term, ai_coupling):
        """
        @param int ai_range_mV: Analog input range up to 10 V.
        @param int ai_offset_mV: Analog input offset.
        @param str ai_term: Analog input termination. '1MOhm' or '50Ohm'
        @param str ai_coupling: Analog input coupling. 'DC' or 'AC'
        """
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_AMP0, ai_range_mV)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_OFFS0, ai_offset_mV)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_50OHM0, self.ai_term_dict[ai_term])
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_ACDC0, self.ai_coupling_dict[ai_coupling])

    def set_ch1(self, ai_range_mV, ai_offset_mV, ai_term, ai_coupling):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_AMP1, ai_range_mV)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_OFFS1, ai_offset_mV)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_50OHM1, self.ai_term_dict[ai_term])
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_ACDC1, self.ai_coupling_dict[ai_coupling])

class AcquisitionConfigureCommands:
    """
    This class configures the acquisition mode of the card.
    Refer to the chapter 'Acquisition modes' in the manual for more information.
    """
    def __init__(self, card, log):
        """
        @param str card: The card handle.
        @param log: qudi logger from the SpectrumInstrumentation.
        """
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
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_CLOCKMODE, spcm.SPC_CM_INTPLL)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_REFERENCECLOCK, clk_ref_Hz)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_SAMPLERATE, clk_samplerate_Hz)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_CLOCKOUT, 1)
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
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_CARDMODE, spcm.SPC_REC_STD_SINGLE)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_MEMSIZE, memsize_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_POSTTRIGGER, post_trigs_S)
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
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_CARDMODE, spcm.SPC_REC_STD_MULTI)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_SEGMENTSIZE, seg_size_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_MEMSIZE, mem_size_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_POSTTRIGGER, post_trigs_S)
        return

    def _mode_STD_GATE(self, pre_trigs_S, post_trigs_S, mem_size_S):
        """
        @params int pre_trigs_S: the number of samples to be recorded prior to the gate start
        @params int post_trigs_S: the number of samples to be recorded after the gate end
        @params int mem_size_S: the total number of samples to be recorded
        """
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_CARDMODE, spcm.SPC_REC_STD_GATE)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_PRETRIGGER, pre_trigs_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_POSTTRIGGER, post_trigs_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_MEMSIZE, mem_size_S)
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

        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_CARDMODE, spcm.SPC_REC_FIFO_SINGLE)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_PRETRIGGER, pre_trigs_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_SEGMENTSIZE, seg_size_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_LOOPS, loops)
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
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_CARDMODE, spcm.SPC_REC_FIFO_MULTI)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_POSTTRIGGER, post_trigs_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_SEGMENTSIZE, seg_size_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_LOOPS, loops)
        return

    def _mode_FIFO_AVERAGE(self, post_trigs_S, seg_size_S, loops, HW_avg_num):
        max_post_trigs_S = 127984

        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_CARDMODE, spcm.SPC_REC_FIFO_AVERAGE)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_AVERAGES, HW_avg_num)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_POSTTRIGGER, post_trigs_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_SEGMENTSIZE, seg_size_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_LOOPS, loops)
        return

    def _mode_FIFO_GATE(self, pre_trigs_S, post_trigs_S, loops):
        """
        @params int pre_trigs_S: the number of samples to be recorded prior to the gate start
        @params int post_trigs_S: the number of samples to be recorded after the gate end
        @params int loops: the total number of loops
        """
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_CARDMODE, spcm.SPC_REC_FIFO_GATE)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_PRETRIGGER, pre_trigs_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_POSTTRIGGER, post_trigs_S)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_LOOPS, loops)
        return


class TriggerConfigureCommands:
    """'
    This class configures the trigger modes and the input parameters accordingly.
    Refer to the chapter 'Trigger modes and appendant registers' for more information.
    """
    def __init__(self, card, log):
        """
        @param str card: The card handle.
        @param log: qudi logger from the SpectrumInstrumentation.
        """
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
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_TERM, 0)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_EXT0_ACDC, 0)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_EXT0_MODE, spcm.SPC_TM_POS)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_EXT0_LEVEL0, trig_level_mV)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_ORMASK, spcm.SPC_TMASK_EXT0)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_ANDMASK, 0)

    def _trigger_SW(self):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_ORMASK, spcm.SPC_TMASK_SOFTWARE)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_ANDMASK, 0)

    def _trigger_CH0(self, trig_level_mV):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_ORMASK, spcm.SPC_TMASK_NONE)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_CH_ANDMASK0, spcm.SPC_TMASK0_CH0)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_CH0_LEVEL0, trig_level_mV)
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TRIG_CH0_MODE, spcm.SPC_TM_POS)


class DataTransferConfigureCommands:
    """
    This class configures the transfer buffer dependent on the buffer type specified in the argument.
    Refer to the chapter 'Commands', the section 'Data transfer' for more information.
    """
    def __init__(self, card, log):
        """
        @param str card: The card handle.
        @param log: qudi logger from the SpectrumInstrumentation.
        """
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
        c_cont_buf_len = spcm.uint64(0)
        spcm.spcm_dwGetContBuf_i64(self._card,
                                   spcm.SPCM_BUF_DATA,
                                   ctypes.byref(c_buf_ptr),
                                   ctypes.byref(c_cont_buf_len))
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

        c_buf_offset = spcm.uint64(0)
        c_buf_size_B = spcm.uint64(buf_size_B)
        spcm.spcm_dwDefTransfer_i64(self._card,
                                    buf_type,
                                    spcm.SPCM_DIR_CARDTOPC,
                                    buf_notify_size_B,
                                    ctypes.byref(c_buf_ptr),
                                    c_buf_offset,
                                    c_buf_size_B
                                    )
        return

    def invalidate_buffer(self, buf_type):
        try:
            spcm.spcm_dwInvalidateBuf(self._card, buf_type)
        except:
            pass


class TimestampConfigureCommands:
    """
    This class configures the timestamp mode.
    Refer to the chapter 'Timestamps', the section 'Timestamps mode' for more information.
    """
    def __init__(self, card, log):
        """
        @param str card: The card handle.
        @param log: qudi logger from the SpectrumInstrumentation.
        """
        self._card = card
        self._log = log

    def configure_ts_standard(self):
        self.ts_standard_mode()

    def ts_standard_mode(self):
        spcm.spcm_dwSetParam_i32(self._card,
                                 spcm.SPC_TIMESTAMP_CMD,
                                 spcm.SPC_TSMODE_STANDARD | spcm.SPC_TSCNT_INTERNAL | spcm.SPC_TSFEAT_NONE)

    def ts_internal_clock(self):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TIMESTAMP_CMD, spcm.SPC_TSCNT_INTERNAL)

    def ts_no_additional_timestamp(self):
        spcm.spcm_dwSetParam_i32(self._card, spcm.SPC_TIMESTAMP_CMD, spcm.SPC_TSFEAT_NONE)


class ConfigureRegisterChecker:
    """
    This class can be used to check if the card settings are correctly input.
    The registers can be obtained from the card to the CardSettings (csr).
    """
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

        c_ai_range_mV = spcm.c_int32()
        c_ai_offset_mV = spcm.c_int32()
        c_ai_term = spcm.c_int32()
        c_ai_coupling = spcm.c_int32()
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_AMP0, ctypes.byref(c_ai_range_mV))
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_OFFS0, ctypes.byref(c_ai_offset_mV))
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_50OHM0, ctypes.byref(c_ai_term))
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_ACDC0, ctypes.byref(c_ai_coupling))
        self.csr.ai_range_mV = int(c_ai_range_mV.value)
        self.csr.ai_offset_mV = int(c_ai_offset_mV.value)
        self.csr.ai_term = ai_term_dict[c_ai_term.value]
        self.csr.ai_coupling = ai_coupling_dict[c_ai_coupling.value]

    def _check_csr_acq(self):
        c_acq_mode = spcm.c_int32()
        c_acq_HW_avg_num = spcm.c_int32()
        c_acq_pre_trigs_S = spcm.c_int32()
        c_acq_post_trigs_S = spcm.c_int32()
        c_acq_mem_size_S = spcm.c_int32()
        c_acq_seg_size_S = spcm.c_int32()
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_CARDMODE, ctypes.byref(c_acq_mode))
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_AVERAGES, ctypes.byref(c_acq_HW_avg_num))
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_PRETRIGGER, ctypes.byref(c_acq_pre_trigs_S))
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_POSTTRIGGER, ctypes.byref(c_acq_post_trigs_S))
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_MEMSIZE, ctypes.byref(c_acq_mem_size_S))
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_SEGMENTSIZE, ctypes.byref(c_acq_seg_size_S))
        self.csr.acq_mode = c_acq_mode.value
        self.csr.acq_HW_avg_num = int(c_acq_HW_avg_num.value)
        self.csr.acq_pre_trigs_S = int(c_acq_pre_trigs_S.value)
        self.csr.acq_post_trigs_S = int(c_acq_post_trigs_S.value)
        self.csr.acq_mem_size_S = int(c_acq_mem_size_S.value)
        self.csr.acq_seg_size_S = int(c_acq_seg_size_S.value)

    def _check_csr_clk(self):
        c_clk_samplerate_Hz = spcm.c_int32()
        c_clk_ref_Hz = spcm.c_int32()
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_REFERENCECLOCK, ctypes.byref(c_clk_ref_Hz))
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_SAMPLERATE, ctypes.byref(c_clk_samplerate_Hz))
        self.csr.clk_samplerate_Hz = int(c_clk_samplerate_Hz.value)
        self.csr.clk_ref_Hz = int(c_clk_ref_Hz.value)

    def _check_csr_trig(self):
        c_trig_mode = spcm.c_int32()
        c_trig_level_mV = spcm.c_int32()
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_TRIG_EXT0_MODE, ctypes.byref(c_trig_mode))
        spcm.spcm_dwGetParam_i32(self._card, spcm.SPC_TRIG_EXT0_LEVEL0, ctypes.byref(c_trig_level_mV))
        self.csr.trig_mode = c_trig_mode.value
        self.csr.trig_level_mV = int(c_trig_level_mV.value)
