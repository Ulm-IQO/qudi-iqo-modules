# written by Thomas Oeckinghaus, 3. Physikalisches Institut, University of Stuttgart
# 2018-01-28 modified by Amit Finkler to be compatible with python 3.5
#  changes: exec has () to conform to python 3
#           print has () to conform to python 3
#           strings sent to Spectrum get a "create_string_buffer" from ctypes after being byted in utf8
#           appending np.zeros to the list --> introduced int(max_chunk_size * factor) to overcome float error
#           changed the / in data /= 2 and data1 /= 4 to //. If data is integer then it is ok, otherwise very wrong
#           (This is a true_divide issue between python 2 and python 3)

from qudi.hardware.thirdparty.spectrum.pyspcm import *
import numpy as np
import time
import threading
import pylab
import copy
import pickle
from ctypes import *

def get_multiple(N, m):
    N_multiple = N
    while not (N_multiple % m == 0):
        N_multiple += 1
    return N_multiple

class AWG:
    """ atm this class assumes only 2 channels per card """

    def __init__(self, _ip: object, _card_ids: object, _hub_id: object, enable_debug = False) -> object:
        self.ip = _ip
        self.number_of_cards = len(_card_ids)
        self.card_ids = _card_ids
        self.cards = list()
        self._enable_debug = enable_debug
        print('enable_debug:', self._enable_debug, flush=True)
        for i in self.card_ids: # i = 1 including error 04.19.2024
            self.cards.append(Card(self.ip, i, enable_debug=self._enable_debug))
        self.hub = Hub(_hub_id, enable_debug=self._enable_debug)  # error  1264   self.number_of_cards = self.get_card_count()  04.19.2024 
        
        self.data_list = [None, None, None, None, None, None, None, None, None, None]
        self.sequence = None
        self.save_data = True
        # number of samples that are transferred in one data transfer (reduce value to save memory)
        # self.max_chunk_size = 5000000
        # Increased by a factor of 20, does not affect stability or pulse characteristics
        self.max_chunk_size = 5000000 * 20
        self.empty_chunk_array_factor = 1.3
        self.uploading = False

        # Set it to single mode by default
        self.sync_all_cards()
        self.set_selected_channels(0b1111)
        self.set_output(0b1111)
        self.set_samplerate(1250000000)
        self.init_single_mode()

    def init_single_mode(self):
        if self._enable_debug: print('run AWG.init_single_mode()')
        self.set_mode('single')
        self.set_loops(0)

    def init_multi_mode(self, memsize, segsize):
        # get the maximum segments value (only power of two is allowed)
        if self._enable_debug: print('run Card.init_multi_mode()')
        self.set_mode('multi')
        for card in self.cards:
            card.set_memory_size(memsize)
            card.set_segment_size(segsize)

    def init_ext_trigger(self):
        if self._enable_debug: print('run AWG.init_ext_trigger()')
        c0 = self.cards[0]
        c1 = self.cards[1]
        c1.set_trigger_mode(0, 'pos_edge')
        c1.set_trigger_mode(1, 'pos_edge')
        c1.set_trigger_level0(0, 200)
        c1.set_trigger_level0(1, 200)
        c0.set_trigger_ormask(0, 0)
        c1.set_trigger_ormask(1, 1)

    def start(self, forced_trigger=False):
        # forced_trigger
        #   True:   Waveform output continuously without the need of external trigger
        #   False:  Waveform only output once after each external trigger event.

        if self._enable_debug: print('run AWG.start()')
        res_final = 1
        if forced_trigger:
            return self.hub.start_triggered()
            # for card in self.cards:
            #     res = card.start_triggered()
            #     res_final = min(res, res_final)
        else:
            return self.hub.start_enable_trigger()
            # for card in self.cards:
            #     res = card.start()
            #     res_final = min(res, res_final)
            # return res_final
    
    def stop(self):
        if self._enable_debug: print('run AWG.stop()')
        return self.hub.stop()
        # res_final = 1
        # for card in self.cards:
        #     res = card.stop()
        #     res_final = min(res, res_final)
        # return res_final

    def sync_all_cards(self):
        if self._enable_debug: print('run AWG.sync_all_cards()')
        self.hub.sync_all_cards()

    def set_selected_channels(self, channels):
        # To enable the channels that you want to use
        # The argument "channels" is binary with a flag (bit) for each channel:
        #       e.g. 0b0101 -> ch 0 & 2 active
        if self._enable_debug: print('run AWG.set_selected_channels({0:b})'.format(channels))
        self.cards[0].set_selected_channels(channels & 0b11)

        c2_channels = 0
        if channels & 0b100:
            c2_channels += 0b01
        if channels & 0b1000:
            c2_channels += 0b10

        self.cards[1].set_selected_channels(c2_channels)

    def set_output(self, channels):
        """ enables/disables the output of the channels. channels is binary with a flag (bit) for each channel: 0b0101 -> ch 0 & 2 active  """
        if self._enable_debug: print('run AWG.set_output()')
        self.cards[0].set_channel_output(0, channels & 0b0001 >> 0)
        self.cards[0].set_channel_output(1, channels & 0b0010 >> 1)
        self.cards[1].set_channel_output(0, channels & 0b0100 >> 2)
        self.cards[1].set_channel_output(1, channels & 0b1000 >> 3)

    def set_loops(self, loops):
        if self._enable_debug: print('run AWG.set_loops()')
        for card in self.cards:
            card.set_loops(loops)

    def set_samplerate(self, rate):
        if self._enable_debug: print('run AWG.set_samplerate()')
        for c in self.cards:
            c.set_samplerate(rate)

    def set_mode(self, mode):
        if self._enable_debug: print('run AWG.set_mode()', mode)
        for card in self.cards:
            card.set_mode(mode)
    
    def set_amplitude(self, amp_list):
        if self._enable_debug: print('run AWG.set_amplitude()')
        self.cards[0].set_amplitude(0, int(amp_list[0]))
        self.cards[0].set_amplitude(1, int(amp_list[1]))
        self.cards[1].set_amplitude(0, int(amp_list[2]))
        self.cards[1].set_amplitude(1, int(amp_list[3]))
    
    def get_amplitude(self):
        if self._enable_debug: print('run AWG.set_amplitude()')
        amp_list = []
        amp_list.append(self.cards[0].get_amplitude(0))
        amp_list.append(self.cards[0].get_amplitude(1))
        amp_list.append(self.cards[1].get_amplitude(0))
        amp_list.append(self.cards[1].get_amplitude(1))
        return amp_list


    def set_memory_size(self, mem_size, is_sequence_segment=False):
        if self._enable_debug: print('run AWG.set_memory_size()')
        for card in self.cards:
            card.set_memory_size(mem_size, is_seq_segment=is_sequence_segment)

    def upload_wfm_thread(self, wfm, wfm_size):
        self.uploading = True
        if self._enable_debug: print('run AWG.upload_wfm_thread()')
        # if not data0 == None or not data1 == None:

        self.start_upload_time = time.time()
        t1 = threading.Thread(
            target=self._run_upload_wfm,
            args=(self.cards[0], wfm_size),
            kwargs={'data':wfm[0], 'data1':wfm[1]},
        )
        t2 = threading.Thread(
            target=self._run_upload_wfm,
            args=(self.cards[1], wfm_size),
            kwargs={'data':wfm[2], 'data1':wfm[3]},
        )
        del wfm
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.finish_time = time.time()
        # self.uploading = False

        if not hasattr(self, 'start_time'):
            return

    def upload_wfm(self, wfm, wfm_size):
        self.uploading = True
        if self._enable_debug: print('run AWG.upload_wfm()')
        # if not data0 == None or not data1 == None:

        self.start_upload_time = time.time()
        self._run_upload_wfm(
            self.cards[0], wfm_size, data=wfm[0], data1=wfm[1],
        )
        self._run_upload_wfm(
            self.cards[1], wfm_size, data=wfm[2], data1=wfm[3],
        )

        del wfm

        self.finish_time = time.time()
        self.uploading = False

        if not hasattr(self, 'start_time'):
            return

    def _run_upload_wfm(self, card, mem_size, **kwargs):
        if self._enable_debug:
            print('run AWG._run_upload_wfm()')

        res = card.upload(mem_size, **kwargs)
        if res == 0:
            pass
        else:
            print('Error on card ' + str(card.cardNo) + ': ' + str(res))

        # self.hub.chkError()

    def load_sequence(self, seq, forced_trigger=False):
        if self._enable_debug: print('run AWG.load_multi()')
        self.uploading = True
        self.stop()
        self.start_time = time.time()

        num_step = len(seq)
        if forced_trigger:
            nextcond = 'always'
        else:
            nextcond = 'on_trig'
            self.init_ext_trigger()

        max_segment_size = 0
        for step in seq:
            for wfm in step:
                max_segment_size = max(wfm.size, max_segment_size)

        print(max_segment_size)
        segsize_multiple = get_multiple(max_segment_size, 32)
        print(segsize_multiple)
        memsize = num_step*segsize_multiple
        self.init_multi_mode(memsize, segsize_multiple)

        for i, step in enumerate(seq):
            self.upload_segment(step, i, segsize_multiple)

    def upload_segment(self, seg, segindex, segsize):
        self.uploading = True
        if self._enable_debug: print('run AWG.upload_segment()')

        self.start_upload_time = time.time()

        res = self._run_upload_wfm(
            self.cards[0], segsize, data=seg[0], data1=seg[1], mem_offset=segindex, is_segment=True,
        )
        if res : print('Error on card 0:',str(res))

        res = self._run_upload_wfm(
            self.cards[1], segsize, data=seg[2], data1=seg[3], mem_offset=segindex, is_segment=True,
        )
        if res : print('Error on card 1:',str(res))

        self.finish_time = time.time()
        self.uploading = False
        print('Uploading time:', self.finish_time - self.start_upload_time)

        if not hasattr(self, 'start_time'):
            return


class Hub():
    _regs_card_index = [SPC_SYNC_READ_CARDIDX0, SPC_SYNC_READ_CARDIDX1, SPC_SYNC_READ_CARDIDX2, SPC_SYNC_READ_CARDIDX3]

    def __init__(self, hub_no, enable_debug=False):
        self.handle = None
        self.hubNo = hub_no
        self._enable_debug = enable_debug
        if self._enable_debug: print('run Hub.__init__()')
        self.open()
        if self.handle == None:
            print("Star-Hub not found.".format(hub_no))
            return
        # hub has no serial?
        # self.serial = self.get_serial()

        self.card_indices = list()
        self.number_of_cards = self.get_card_count() 
        
        if self._enable_debug: print('number_of_cards:', self.number_of_cards)
        
        for i in range(self.number_of_cards):
            self.card_indices.append(self.get_card_of_index(i))

    def sync_all_cards(self):
        if self._enable_debug: print('run Hub.sync_all_cards()')
        self.set32(SPC_SYNC_ENABLEMASK, (1 << self.number_of_cards) - 1)

    def open(self):
        if self._enable_debug: print('run Hub.open()')
        
        address = "sync{0}".format(self.hubNo)
        self.handle = spcm_hOpen(create_string_buffer(bytes(address, 'utf8')))

    def close(self):
        if self._enable_debug: print('run Hub.close()')
        spcm_vClose(self.handle)

    def start_triggered(self):
        if self._enable_debug: print('run Hub.start_triggered()')
        self.set32(SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER | M2CMD_CARD_FORCETRIGGER)
        return self.chkError()

    def start_enable_trigger(self):
        if self._enable_debug: print('run Hub.start_enable_trigger()')
        self.set32(SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER)
        # self.set32(SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER | M2CMD_CARD_WAITTRIGGER)
        return self.chkError()

    def start(self):
        if self._enable_debug: print('run Hub.start()')
        self.set32(SPC_M2CMD, M2CMD_CARD_START)
        return self.chkError()

    def stop(self):
        if self._enable_debug: print('run Hub.stop()')
        self.set32(SPC_M2CMD, M2CMD_CARD_STOP)
        return self.chkError()

    def reset(self):
        if self._enable_debug: print('run Hub.reset()')
        self.set32(SPC_M2CMD, M2CMD_CARD_RESET)
        return self.chkError()

    def write_setup(self):
        """ writes all settings to the card without starting (start will also write all settings) """
        if self._enable_debug: print('run Hub.write_setup()')
        self.set32(SPC_M2CMD, M2CMD_CARD_WRITESETUP)
        return self.chkError()

    def enable_trigger(self):
        """ triggers will have effect """
        if self._enable_debug: print('run Hub.enable_trigger()')
        self.set32(SPC_M2CMD, M2CMD_CARD_ENABLETRIGGER)
        return self.chkError()

    def force_trigger(self):
        """ forces a single trigger event """
        if self._enable_debug: print('run Hub.force_trigger()')
        self.set32(SPC_M2CMD, M2CMD_CARD_FORCETRIGGER)
        return self.chkError()

    def disable_trigger(self):
        """ all triggers will be ignored """
        if self._enable_debug: print('run Hub.disable_trigger()')
        self.set32(SPC_M2CMD, M2CMD_CARD_DISABLETRIGGER)
        return self.chkError()

    def wait_trigger(self):
        """ wait until the next trigger event is detected """
        if self._enable_debug: print('run Hub.wait_trigger()')
        self.set32(SPC_M2CMD, M2CMD_CARD_WAITTRIGGER)
        return self.chkError()

    def wait_ready(self):
        """ wait until the card completed the current run """
        if self._enable_debug: print('run Hub.wait_ready()')
        self.set32(SPC_M2CMD, M2CMD_CARD_WAITREADY)
        return self.chkError()

    def get_card_count(self):
        """ returns the number of cards that are connected to the hub """
        if self._enable_debug: print('run Hub.get_card_count()')
        res = self.get32(SPC_SYNC_READ_SYNCCOUNT)
        if self.chkError() == -1:
            return -1
        return res

    def get_card_of_index(self, index):
        """ returns the number (Card.cardNo) of the card that is connected to a index on the hub """
        if self._enable_debug: print('run Hub.get_card_of_index()', index)
        reg = self._regs_card_index[index]
        res = self.get32(reg)
        if self.chkError() == -1:
            return -1
        return res

    def get_serial(self):
        res = self.get32(SPC_PCISERIALNO)
        if self.chkError() == -1:
            return -1
        return res

    def set32(self, register, value):
        # print('Hub.set32!!!!', register, value)
        spcm_dwSetParam_i32(self.handle, register, int32(value))

    def set64(self, register, value):
        # print('Hub.set64!!!!', register, value)
        spcm_dwSetParam_i64(self.handle, register, int64(value))

    def get32(self, register):
        # print('Hub.get32!!!!', register)
        res = int32(0)
        spcm_dwGetParam_i32(self.handle, register, byref(res))
        return res.value

    def get64(self, register):
        # print('Hub.get64!!!!', register)
        res = int64(0)
        spcm_dwGetParam_i64(self.handle, register, byref(res))
        return res.value

    def chkError(self):
        error_text = create_string_buffer(ERRORTEXTLEN)
        if (spcm_dwGetErrorInfo_i32(self.handle, None, None, error_text) != ERR_OK):
            print(error_text.value)
            return -1
        return 0


class Card():
    # lists that hold the registers of the same type but for different channels (0-3) or trigger channels (0-1)
    # register for Amplitde in mV
    _regs_amplitude = [SPC_AMP0, SPC_AMP1, SPC_AMP2, SPC_AMP3]
    _regs_output = [SPC_ENABLEOUT0, SPC_ENABLEOUT1, SPC_ENABLEOUT2, SPC_ENABLEOUT3]
    # Filter cut off for antialiasing (pg. 73 manual)
    _regs_filter = [SPC_FILTER0, SPC_FILTER1, SPC_FILTER2, SPC_FILTER3]
    _regs_stoplevel = [SPC_CH0_STOPLEVEL, SPC_CH1_STOPLEVEL, SPC_CH2_STOPLEVEL, SPC_CH3_STOPLEVEL]
    _regs_trigger_level0 = [SPC_TRIG_EXT0_LEVEL0, SPC_TRIG_EXT1_LEVEL0]
    _regs_trigger_mode = [SPC_TRIG_EXT0_MODE, SPC_TRIG_EXT1_MODE]
    _regs_offset = [SPC_OFFS0, SPC_OFFS1, SPC_OFFS2, SPC_OFFS3]

    # values for stoplevel. defines the output after a waveform is finished
    _vals_stoplevel = {
        'zero': SPCM_STOPLVL_ZERO,  # output 0 voltage
        'low': SPCM_STOPLVL_LOW,  # output low voltage
        'high': SPCM_STOPLVL_HIGH,  # output high voltage
        'hold': SPCM_STOPLVL_HOLDLAST  # hold the last voltage of the last sample
    }

    # output mode
    _vals_mode = {
        'single': SPC_REP_STD_SINGLE,
        'multi': SPC_REP_STD_MULTI,
        'gate': SPC_REP_STD_GATE,
        'singlerestart': SPC_REP_STD_SINGLERESTART,
        'sequence': SPC_REP_STD_SEQUENCE,
        'continuous': SPC_REP_STD_CONTINUOUS,
        'fifo_single': SPC_REP_FIFO_SINGLE,
        'fifo_multi': SPC_REP_FIFO_MULTI,
        'fifo_gate': SPC_REP_FIFO_GATE
    }

    # trigger modes
    _vals_trig_mode = {
        'pos_edge': SPC_TM_POS,
        'neg_edge': SPC_TM_NEG
    }

    _vals_sequence_step = {
        'on_trig': SPCSEQ_ENDLOOPONTRIG,
        'always': SPCSEQ_ENDLOOPALWAYS,
        'stop': SPCSEQ_END
    }

    def __init__(self, _ip, CardNo, enable_debug=False):
        self.ip = _ip
        self.handle = None
        self.cardNo = CardNo
        self._enable_debug = enable_debug
        if self._enable_debug: print('run Card.__init__()')

        self.open()

        if self.handle == None:
            print("Spectrum Card No.{0} not found.".format(CardNo))
            return
        self.serial = self.get_serial()

        if self._enable_debug:
            print('serial:', self.serial)
            print('ip:', self.ip)
            print('cardNo:', self.cardNo)

        #self.init_markers()
        self.temp_data = ()  # added this for debugging purposes and future plotting of data

    def init_markers(self):
        """ set markers:
                mrkr0 -> bit 15 of ch0
                mrkr1 -> bit 15 of ch1
                mrkr2 -> bit 14 of ch1
        """
        if self._enable_debug: print('run Card.init_markers()')
        
        mrkr_mode = (SPCM_XMODE_DIGOUT | SPCM_XMODE_DIGOUTSRC_CH0 | SPCM_XMODE_DIGOUTSRC_BIT15)
        self.set32(SPCM_X0_MODE, mrkr_mode)

        mrkr_mode = (SPCM_XMODE_DIGOUT | SPCM_XMODE_DIGOUTSRC_CH1 | SPCM_XMODE_DIGOUTSRC_BIT15)
        self.set32(SPCM_X1_MODE, mrkr_mode)

        mrkr_mode = (SPCM_XMODE_DIGOUT | SPCM_XMODE_DIGOUTSRC_CH1 | SPCM_XMODE_DIGOUTSRC_BIT14)
        self.set32(SPCM_X2_MODE, mrkr_mode)

    def open(self):
        # print(str(self.ip))
        # print(str(self.cardNo))
        if self._enable_debug: print('run Card.open()')
        if 1:
            address = "TCPIP::{0}::inst{1}".format(self.ip, self.cardNo)
        else:
            address = "{0}{1}".format(self.ip, self.cardNo)
        self.handle = spcm_hOpen(create_string_buffer(bytes(address, 'utf8')))

        # (str(self.handle))

    def close(self):
        if self._enable_debug: print('run Card.close()')
        spcm_vClose(self.handle)

    def start_triggered(self):
        if self._enable_debug: print('run Card.start_triggered()')
        self.set32(SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER | M2CMD_CARD_FORCETRIGGER)
        return self.chkError()

    def start(self):
        if self._enable_debug: print('run Card.start()')
        # self.set32(SPC_M2CMD, M2CMD_CARD_START)
        self.set32(SPC_M2CMD, M2CMD_CARD_START | M2CMD_CARD_ENABLETRIGGER)
        return self.chkError()

    def stop(self):
        if self._enable_debug: print('run Card.stop()')
        self.set32(SPC_M2CMD, M2CMD_CARD_STOP)
        return self.chkError()

    def reset(self):
        if self._enable_debug: print('run Card.reset()')
        self.set32(SPC_M2CMD, M2CMD_CARD_RESET)
        return self.chkError()

    def write_setup(self):
        """ writes all settings to the card without starting (start will also write all settings) """
        if self._enable_debug: print('run Card.write_setup()')
        self.set32(SPC_M2CMD, M2CMD_CARD_WRITESETUP)
        return self.chkError()

    def enable_trigger(self):
        """ triggers will have effect """
        if self._enable_debug: print('run Card.enable_trigger()')
        self.set32(SPC_M2CMD, M2CMD_CARD_ENABLETRIGGER)
        return self.chkError()

    def force_trigger(self):
        """ forces a single trigger event """
        if self._enable_debug: print('run Card.force_trigger()')
        self.set32(SPC_M2CMD, M2CMD_CARD_FORCETRIGGER)
        return self.chkError()

    def disable_trigger(self):
        """ all triggers will be ignored """
        self.set32(SPC_M2CMD, M2CMD_CARD_DISABLETRIGGER)
        return self.chkError()

    def wait_trigger(self):
        """ wait until the next trigger event is detected """
        if self._enable_debug: print('run Card.wait_trigger()')
        self.set32(SPC_M2CMD, M2CMD_CARD_WAITTRIGGER)
        return self.chkError()

    def wait_ready(self):
        """ wait until the card completed the current run """
        if self._enable_debug: print('run Card.wait_ready()')
        self.set32(SPC_M2CMD, M2CMD_CARD_WAITREADY)
        return self.chkError()

    def get_serial(self):
        if self._enable_debug: print('run Card.get_serial()')
        res = self.get32(SPC_PCISERIALNO)
        if self.chkError() == -1:
            return -1
        return res

    def set_wait_timeout(self, timeout):
        """ sets the timeout for all wait commands in ms. set to 0 to disable timeout """
        if self._enable_debug: print('run Card.set_wait_timeout()')
        self.set32(SPC_TIMEOUT, timeout)
        return self.chkError()

    def get_wait_timeout(self):
        """ get the timeout of wait commands in ms """
        if self._enable_debug: print('run Card.get_wait_timeout()')
        res = self.get32(SPC_TIMEOUT)
        if self.chkError() == -1:
            return -1
        return res

    def set_loops(self, loops):
        """ number of times the memory is replayed in a single event. set to 0 to have continuouly generation. """
        if self._enable_debug: print('run Card.set_loops()')
        self.set32(SPC_LOOPS, loops)
        return self.chkError()

    def get_loops(self):
        if self._enable_debug: print('run Card.get_loops()')
        res = self.get32(SPC_LOOPS)
        if self.chkError() == -1:
            return -1
        return res

    def set_trigger_level0(self, trig_channel, level):
        if self._enable_debug: print('run Card.set_trigger_level0()')
        """ sets the lower trigger level of a trig channel in mV """
        reg = self._regs_trigger_level0[trig_channel]
        self.set32(reg, level)
        return self.chkError()

    def get_trigger_level0(self, trig_channel):
        if self._enable_debug: print('run Card.get_trigger_level0()')
        reg = self._regs_trigger_level0[trig_channel]
        res = self.get32(reg)
        if self.chkError() == -1:
            return -1
        return res

    def set_trigger_mode(self, trig_channel, mode):
        if self._enable_debug: print('run Card.set_trigger_mode()')
        reg = self._regs_trigger_mode[trig_channel]

        self.set32(reg, self._vals_trig_mode[mode])
        return self.chkError()

    def get_trigger_mode(self, trig_channel):
        if self._enable_debug: print('run Card.get_trigger_mode()')
        reg = self._regs_trigger_mode[trig_channel]
        res = self.get32(reg)

        # get the name of the result from the trigger mode dictionary
        for key in self._vals_trig_mode.keys():
            if self._vals_trig_mode[key] == res:
                res = key

        if self.chkError() == -1:
            return -1
        return res

    def set_trigger_ormask(self, trig0_active, trig1_active):
        """ adds/removes the given trigger channel to/from the TRIG_ORMASK of the card (see manual for details) """
        if self._enable_debug: print('run Card.set_trigger_ormask()')
        reg = SPC_TRIG_ORMASK

        com = 0
        if trig0_active:
            com += SPC_TMASK_EXT0
        if trig1_active:
            com += SPC_TMASK_EXT1

        self.set32(reg, com)
        return self.chkError()

    def get_trigger_in_ormask(self, trig_channel):
        if self._enable_debug: print('run Card.get_trigger_in_ormask()')
        reg = SPC_TRIG_ORMASK

        res = self.get32(reg)
        if self.chkError() == -1:
            return -1

        if trig_channel == 0:
            com = SPC_TMASK_EXT0
        elif trig_channel == 1:
            com = SPC_TMASK_EXT1
        else:
            print('Unknown channel {0} in get_trigger_in_ormask.'.format(trig_channel))
            return -1

        return (res & com > 0)

    def set_triggered_channels_ormask(self, channels):
        """ sets channels that react to the trigger ormask.
            Give channels as binary with flags for single channel:
            0b111 -> channel 0,1,2 active
            0b100 -> channel 2 active
        """
        if self._enable_debug: print('run Card.set_triggered_channels_ormask()')
        self.set32(SPC_TRIG_CH_ORMASK0, channels)
        return self.chkError()

    def get_state(self):
        if self._enable_debug: print('run Card.get_state()')
        res = self.get32(SPC_M2STATUS)
        if self.chkError() == -1:
            return -1
        if res & M2STAT_CARD_READY:
            return 'Ready'
        elif res & M2STAT_CARD_TRIGGER:
            return 'First trigger has been detected.'
        else:
            return 'Unknown state'

    def get_datatransfer_state(self):
        if self._enable_debug: print('run Card.get_datatransfer_state()')
        res = self.get32(SPC_M2STATUS)
        if self.chkError() == -1:
            return -1
        if res & M2STAT_DATA_END:
            return 'Data transfer finished.'
        elif res & M2STAT_DATA_ERROR:
            return 'Error during data transfer.'
        elif res & M2STAT_DATA_OVERRUN:
            return 'Overrun occured during data transfer.'
        else:
            return 'Unknown state'

    def set_memory_size(self, mem_size, is_seq_segment=False):
        """ set the amount of memory that is used for operation. Has to be set before data transfer to card. """
        if self._enable_debug: print('run Card.set_memory_size()', mem_size)
        if is_seq_segment:
            self.set32(SPC_SEQMODE_SEGMENTSIZE, mem_size)
        else:
            self.set32(SPC_MEMSIZE, mem_size)
        return self.chkError()

    def get_memory_size(self):
        if self._enable_debug: print('run Card.get_memory_size()')
        res = self.get32(SPC_MEMSIZE)
        if self.chkError() == -1:
            return -1
        return res

    # @profile
    def upload(self, mem_size, data=None, data1=None, is_segment=False, mem_offset=0):
        """
        Uploads data to the card.
        Values in 'data' can not exceed -1.0 to 1.0.
        The value of mem_size must be at least greater than the length of both data0 and data1
        """

        if self._enable_debug: print('run Card.upload()')
        
        data0_int = np.array(data )*(2**15 - 1)
        data1_int = np.array(data1)*(2**15 - 1)
        data0_int = np.asarray(data0_int, dtype=np.int16)
        data1_int = np.asarray(data1_int, dtype=np.int16)

        used_channels = self.get_selected_channels_count()
        bytes_per_sample = self.get_bytes_per_sample()
        if self._enable_debug:
            print('used_channels:', used_channels)
            print('bytes_per_sample:', bytes_per_sample)

        if is_segment:
            mem_size_multiple = mem_size
        else:
            # set the amount of memory that will be used
            mem_size_multiple = get_multiple(mem_size, 32)
            self.set_memory_size(mem_size_multiple, is_seq_segment=False)

        # set the buffer
        buffer_size = mem_size_multiple*bytes_per_sample*used_channels
        pvBuffer = create_string_buffer(buffer_size)
        pnBuffer = np.zeros(mem_size_multiple*used_channels, dtype=np.int16)

        data0_size, data1_size = data0_int.size, data1_int.size
        pnBuffer[0:data0_size*2:2] = data0_int
        pnBuffer[1:data1_size*2:2] = data1_int
        
        del data0_int
        del data1_int

        # Convert to unsigned-integer. NumPy uses two's compliment by default,
        # so it matches the format required by the AWG naturally.        
        pvBuffer.raw = np.asarray(pnBuffer, dtype=np.uint16).tostring()
        
        # Refer to P.58 of "generatorNETBOX DN2.66x Manual"
        hDevice = self.handle                       # handle to an already opened device
        dwBufType = SPCM_BUF_DATA                   # type of the buffer to define as listed above under SPCM_BUF_XXXX
        dwDirection = SPCM_DIR_PCTOCARD             # the transfer direction as defined above
        dwNotifySize = uint32(0)                    # no. of bytes after which an event is sent (0=end of transfer)
        pvDataBuffer = pvBuffer                     # pointer to the data buffer
        qwBrdOffs = uint64(mem_offset*buffer_size)  # offset for transfer in board memory (zero when using FIFO mode)
        qwTransferLen = uint64(buffer_size)         # buffer length
        spcm_dwDefTransfer_i64(
            hDevice,
            dwBufType,
            dwDirection,
            dwNotifySize,
            pvDataBuffer,
            qwBrdOffs,
            qwTransferLen,
        )

        # execute the transfer
        # SPC_M2CMD:            Executes a command for the card or data transfer
        # M2CMD_DATA_STARTDMA:  Starts the DMA transfer for an already defined buffer.
        # M2CMD_DATA_WAITDMA:   Waits until the data transfer has ended or until at least
        #                       the amount of bytes defined by notify size are available.
        self.set32(SPC_M2CMD, M2CMD_DATA_STARTDMA | M2CMD_DATA_WAITDMA)
        
        with open('C:\\Users\\yy3\\qudi\\Data\\2024\\06\\2024-06-18\\card%d_pnBuffer%d.npy' % (self.cardNo, mem_offset), 'wb+') as f:
            np.save(f, pnBuffer)
        
        del pnBuffer, pvBuffer
        err = self.chkError()
        return err

    def upload_segment(self, segsize, segidx, data=None, data1=None, block=False):
        """
        Uploads data to the card.
        Values in 'data' can not exceed -1.0 to 1.0.
        """

        if self._enable_debug: print('run Card.upload_segment()')
        
        data0_int = np.array(data )*(2**15 - 1)
        data1_int = np.array(data1)*(2**15 - 1)
        data0_int = np.asarray(data0_int, dtype=np.int16)
        data1_int = np.asarray(data1_int, dtype=np.int16)

        # set the commands that are used for the data transfer
        # M2CMD_DATA_STARTDMA:  Starts the DMA transfer for an already defined buffer.
        # M2CMD_DATA_WAITDMA:   Waits until the data transfer has ended or until at least
        #                       the amount of bytes defined by notify size are available.
        if block:
            com = M2CMD_DATA_STARTDMA | M2CMD_DATA_WAITDMA
        else:
            com = M2CMD_DATA_STARTDMA

        used_channels = self.get_selected_channels_count()
        bytes_per_sample = self.get_bytes_per_sample()
        if self._enable_debug:
            print('used_channels:', used_channels)
            print('bytes_per_sample:', bytes_per_sample)

        # set the buffer
        buffer_size = segsize * bytes_per_sample * used_channels
        BufferSize = uint64(buffer_size)
        pvBuffer = create_string_buffer(BufferSize.value)

        pnBuffer = np.zeros(segsize * used_channels, dtype=np.int16)
        
        # data0 and data1 should have the same size.
        # In case they aren't, the longer one will get truncated to match the shorter one.
        data_size = min(data0_int.size, data1_int.size)
        pnBuffer[0:data_size * 2:2] = data0_int[:data_size]
        pnBuffer[1:data_size * 2:2] = data1_int[:data_size]
        
        del data0_int
        del data1_int

        # Convert to unsigned-integer. NumPy uses two'2 compliment by default,
        # so it matches the format required by the AWG naturally.
        pnBuffer_uint = np.asarray(pnBuffer, dtype=np.uint16)
        pvBuffer.raw = pnBuffer_uint.tostring()
        
        dwBufType = SPCM_BUF_DATA
        dwDirection = SPCM_DIR_PCTOCARD
        dwNotifySize = uint32(0)
        qwBrdOffs = uint64(segidx*buffer_size)
        qwTransferLen = BufferSize
        spcm_dwDefTransfer_i64(
            self.handle, dwBufType, dwDirection, dwNotifySize, pvBuffer, qwBrdOffs, qwTransferLen
        )

        # execute the transfer
        # SPC_M2CMD:    Executes a command for the card or data transfer
        self.set32(SPC_M2CMD, com)
        if block:
            del pnBuffer, pvBuffer
        err = self.chkError()
        return err

    def init_sequence_mode(self, step_count):
        """ Sets the mode to sequence and set the maximum number of segments in the memory (max number of steps). """
        # get the maximum segments value (only power of two is allowed)
        if self._enable_debug: print('run Card.init_sequence_mode()')
        max_segments = 0
        for i in range(16):
            if step_count <= 1 << i:
                max_segments = 1 << i
                break

        self.set_mode('sequence')
        self.set32(SPC_SEQMODE_MAXSEGMENTS, max_segments)
        self.set32(SPC_SEQMODE_STARTSTEP, 0)

    def set_segment_size(self, size):
        if self._enable_debug: print('run Card.set_segment_size()', size)
        self.set32(SPC_SEGMENTSIZE, size)
        return self.chkError()

    def set_current_segment(self, index):
        if self._enable_debug: print('run Card.set_current_segment()', index)
        self.set32(SPC_SEQMODE_WRITESEGMENT, index)

    def write_sequence_segment(self, mem_segment_index, data0=None, data1=None, mrkr0=None, mrkr1=None, mrkr2=None):
        """ Creates a memory segment that can be used for a sequence step """
        if self._enable_debug: print('run Card.write_sequence_segment()', mem_segment_index)

        # select memory segment
        self.set32(SPC_SEQMODE_WRITESEGMENT, mem_segment_index)

        self.upload(data0, data1, mrkr0, mrkr1, mrkr2, is_segment=True)

        return self.chkError()

    def write_sequence_step(self, step_index, mem_segment_index, loops, goto, next_condition):
        """ Writes an entry into the the sequence memory of the card (does not upload data to the card). """
        if self._enable_debug: print('run Card.write_sequence_step()', step_index)

        f_memidx = int(mem_segment_index)
        f_goto = int(goto) << 16
        f_loops = int(loops) << 32
        f_nextcond = self._vals_sequence_step[next_condition] << 32
        flags = f_nextcond | f_loops | f_goto | f_memidx
        self.set64(SPC_SEQMODE_STEPMEM0 + step_index, flags)
        return self.chkError()

    def get_bytes_per_sample(self):
        """ return the number of bytes that are used for a single sample """
        if self._enable_debug: print('run Card.get_bytes_per_sample()')
        res = self.get32(SPC_MIINST_BYTESPERSAMPLE)
        if self.chkError() == -1:
            return -1
        return res

    def set_samplerate(self, rate):
        """ set sample rate in Hz """
        if self._enable_debug: print('run Card.set_samplerate()', rate)
        self.set32(SPC_SAMPLERATE, rate)
        return self.chkError()

    def get_samplerate(self):
        """ get sample rate in Hz """
        res = self.get32(SPC_SAMPLERATE)
        if self.chkError() == -1:
            return -1
        return res

    def set_selected_channels(self, chans):
        """
        set selected channels (channels that can be used for output)

        chans consists of binary flags for each channel:
        chans = 0b00 => not ch1, not ch2
        chans = 0b01 => ch1, not ch2
        pg 71 manual
        ...
        """
        if self._enable_debug: print('run Card.set_selected_channels()', chans)
        self.set32(SPC_CHENABLE, chans)
        return self.chkError()

    def get_selected_channels(self):
        """ get selected channels
        Sets the channel enable information for the next card run.
        pg. 73 manual
        """
        res = self.get32(SPC_CHENABLE)
        if self.chkError() == -1:
            return -1
        return res

    def get_selected_channels_count(self):
        """ get count of selected channels
        Reads back the number of currently activated channels.
        pg 73 manual
        """
        res = self.get32(SPC_CHCOUNT)
        if self.chkError() == -1:
            return -1
        return res

    def set_channel_output(self, ch, enabled):
        """ set output of channel enabled/disabled """
        if self._enable_debug: print('run Card.set_channel_output()', ch, enabled)
        ch_reg = self._regs_output[ch]  # get the enable output register of the specific channel
        self.set32(ch_reg, enabled)
        return self.chkError()

    def get_channel_output(self, ch):
        """ get whether the output of the channel is enabled """
        ch_reg = self._regs_output[ch]  # get the enable output register of the specific channel
        res = self.get32(ch_reg)
        if self.chkError() == -1:
            return -1
        return res

    def set_amplitude(self, ch, amp):
        """ set the amplitude of a channel in mV (into 50 Ohms) """
        if self._enable_debug: print('run Card.set_amplitude()', ch, amp)
        reg = self._regs_amplitude[ch]  # get the amplitude register of the specific channel
        self.set32(reg, amp)
        return self.chkError()

    def get_amplitude(self, ch):
        """ get the amplitude of a channel in mV (into 50 Ohms) """
        reg = self._regs_amplitude[ch]  # get the amplitude register of the specific channel
        res = self.get32(reg)
        if self.chkError() == -1:
            return -1
        return res

    def set_filter(self, ch, amp):
        """ set the filter cut off frequency (ls) """
        if self._enable_debug: print('run Card.set_filter()', ch, amp)
        reg = self._regs_filter[ch]  # get the filter register of the specific channel
        self.set32(reg, amp)
        return self.chkError()

    def get_filter(self, ch):
        """ get the filter cut off frequency (ls) """
        reg = self._regs_filter[ch]  # get the filter register of the specific channel
        res = self.get32(reg)
        if self.chkError() == -1:
            return -1
        return res

    def set_offset(self, ch, amp):
        """ set the ofset of a channel +/-100% in steps of 1%, pg. 74  """
        if self._enable_debug: print('run Card.set_offset()', ch, amp)
        reg = self._regs_offset[ch]  # get the offset register of the specific channel
        self.set32(reg, amp)
        return self.chkError()

    def get_offset(self, ch):
        """ get the amplitude of a channel +/-100% in steps of 1%, pg. 74   """
        reg = self._regs_offset[ch]  # get the offest register of the specific channel
        res = self.get32(reg)
        if self.chkError() == -1:
            return -1
        return res

    def set_stoplevel(self, ch, stoplevel):
        """ define what is put out after a waveform is finished

            zero: output 0V
            high: output high voltage
            low: output low voltage
            hold: hold voltage of last sample
        """
        if self._enable_debug: print('run Card.set_stoplevel()', ch, stoplevel)
        reg = self._regs_stoplevel[ch]  # get register for stoplevel

        if not self._vals_stoplevel.has_key(stoplevel):
            print('Unknown parameter value {0} in set_stoplevel.'.format(stoplevel))

        val = self._vals_stoplevel[stoplevel]  # values are stored in a dictionary
        self.set32(reg, val)
        return self.chkError()

    def get_stoplevel(self, ch):
        """ get what is put out after a waveform is finished

            zero: output 0V
            high: output high voltage
            low: output low voltage
            hold: hold voltage of last sample
        """
        reg = self._regs_stoplevel[ch]  # get register for stoplevel
        res = self.get32(reg)

        # get the name of the result from the stoplevel dictionary
        for key in self._vals_stoplevel.keys():
            if self._vals_stoplevel[key] == res:
                res = key

        if self.chkError() == -1:
            return -1
        return res

    def set_mode(self, mode):
        if self._enable_debug: print('run Card.set_mode()', mode)
        mode = self._vals_mode[mode]
        self.set32(SPC_CARDMODE, mode)
        return self.chkError()

    def get_mode(self):
        res = self.get32(SPC_CARDMODE)

        # get the name of the result from the mode dictionary
        for key in self._vals_mode.keys():
            if self._vals_mode[key] == res:
                res = key

        if self.chkError() == -1:
            return -1
        return res

    def set32(self, register, value):
        if self._enable_debug: print('Card.set32!!!!', register, value)
        spcm_dwSetParam_i32(self.handle, register, int32(value))

    def set64(self, register, value):
        if self._enable_debug: print('Card.set64!!!!', register, value)
        spcm_dwSetParam_i64(self.handle, register, int64(value))

    def get32(self, register):
        if self._enable_debug: print('Card.get32!!!!', register)
        res = int32(0)
        spcm_dwGetParam_i32(self.handle, register, byref(res))
        return res.value

    def get64(self, register):
        if self._enable_debug: print('Card.get64!!!!', register)
        res = int64(0)
        spcm_dwGetParam_i64(self.handle, register, byref(res))
        return res.value

    def chkError(self):
        error_text = create_string_buffer(ERRORTEXTLEN)
        if (spcm_dwGetErrorInfo_i32(self.handle, None, None, error_text) != ERR_OK):
            print(error_text.value)
            return -1
        return 0

    def _zeros_to_32(self, data):
        """ Fills the list with zeros until the length is a multiple of 32 (converts lists to arrays). """
        if self._enable_debug: print('run Card._zeros_to_32()')
        if not type(data) == type([]):
            data = data.tolist()

        while not len(data) % 32 == 0:
            data.append(0)

        return np.asarray(data)

    def _hold_to_32(self, data):
        """ Fills the list with last value until the length is a multiple of 32 (converts lists to arrays). """
        if self._enable_debug: print('run Card._hold_to_32()')
        val = data[-1]
        if not type(data) == type([]):
            data = data.tolist()

        while not len(data) % 32 == 0:
            data.append(val)

        return np.asarray(data)

# awg = AWG(ip, cards, hub, channel_setup)
# awg.init_all_channels()
# awg.cards[0].set_amplitude(0, 500)
# awg.cards[0].set_amplitude(1, 500)
# awg.cards[1].set_amplitude(0, 100)
# awg.cards[1].set_amplitude(1, 100)