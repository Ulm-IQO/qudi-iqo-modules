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


# from multiprocessing import Process
# import gc
# import pdb
# setup awg
# ip = '192.168.150.10'
# cards = [0, 1]
# hub = 0
# channel_setup = [
#     {
#         'name': 'mw',
#         'ch': 0,
#         'type': 'sin',
#         'amp': 1.0,
#         'phase': 0.0,
#         'freq': 30e6,
#         'freq2': 0.0
#     },
#     {
#         'name': 'mwy',
#         'ch': 1,
#         'type': 'sin',
#         'amp': 1.0,
#         'phase': np.pi/2.,
#         'freq': 30e6,
#         'freq2': 0.0
#     },
#     {
#         'name': 'rf',
#         'ch': 2,
#         'type': 'DC',
#         'amp': 1.0,
#         'phase': 0.0,  # np.pi/2.,
#         'freq': 0.0,   # 'rf_freq'
#         'freq2': 0.0
#     },
#     {
#         'name': 'mw3',
#         'ch': 3,
#         'type': 'DC',
#         'amp': 1.0,
#         'phase': 0.0,
#         'freq': 0.0,
#         'freq2': 0.0
#     },
#     {
#         'name': 'laser',
#         'ch': 4,
#         'type': 'DC',
#         'amp': 1.0,
#         'phase': 0.0,
#         'freq': 0.0,
#         'freq2': 0.0
#     },
#     {
#         'name': 'aom',
#         'ch': 5,
#         'type': 'DC',
#         'amp': 1.0,
#         'phase': 0.0,
#         'freq': 0.0,
#         'freq2': 0.0
#     },
#     {
#         'name': 'trig',
#         'ch': 6,
#         'type': 'DC',
#         'amp': 1.0,
#         'phase': 0.0,
#         'freq': 0.0,
#         'freq2': 0.0
#     },
#     {
#         'name': 'mw_gate',
#         'ch': 7,
#         'type': 'DC',
#         'amp': 1.0,
#         'phase': 0.0,
#         'freq': 0.0,
#         'freq2': 0.0
#     }
# ]

# pin_switch_wait = 50 # delay time to make sure pin switches do not overlap between successive pulses
# laser_prewait = 500 # delay time before laser pulses to make sure that the laser does not flash into the microwave sequence


class AWG:
    """ atm this class assumes only 2 channels per card """

    def __init__(self, _ip: object, _card_ids: object, _hub_id: object, _channel_setup: object, enable_debug = False) -> object:
        self.ip = _ip
        self.number_of_cards = len(_card_ids)
        self.card_ids = _card_ids
        self.cards = list()
        self.channel_setup = _channel_setup
        self._enable_debug = enable_debug
        print('enable_debug:', self._enable_debug, flush=True)
        for i in self.card_ids: # i = 1 including error 04.19.2024
            self.cards.append(Card(self.ip, i, enable_debug=self._enable_debug))
        self.hub = Hub(_hub_id, enable_debug=self._enable_debug)  # error  1264   self.number_of_cards = self.get_card_count()  04.19.2024 
        self.data_list = [None, None, None, None, None, None, None, None, None, None]
        self.init_all_channels()
        self.sequence = None
        self.save_data = True
        # number of samples that are transferred in one data transfer (reduce value to save memory)
        # self.max_chunk_size = 5000000
        # Increased by a factor of 20, does not affect stability or pulse characteristics
        self.max_chunk_size = 5000000 * 20
        self.empty_chunk_array_factor = 1.3
        self.uploading = False
        self.temp_sine = ()  # for square cosine debugging purposes

    def init_all_channels(self):
        if self._enable_debug: print('run AWG.init_all_channels()')

        self.set_selected_channels(0b1111)
        self.set_output(0b1111)
        self.sync_all_cards()
        self.set_loops(0)
        self.set_samplerate(1250000000)
        self.set_mode('single')

    def run_sequence(self, seq, clocking=0, fill_chans=[]):
        if self._enable_debug: print('run AWG.run_sequence()')

        self.uploading = True
        ch = self.ch
        seq = eval(seq)
        if clocking:
            seq = self.fill_sequence(seq, clocking, fill_chans)
        return self.run_sequence_from_list(seq)

    def init_ext_trigger(self):
        if self._enable_debug: print('run AWG.init_ext_trigger()')

        c1 = self.cards[1]
        c0 = self.cards[0]
        c1.set_trigger_mode(0, 'pos_edge')
        c1.set_trigger_mode(1, 'pos_edge')

        c1.set_trigger_level0(0, 200)
        c1.set_trigger_level0(1, 200)
        c0.set_trigger_ormask(0, 0)
        c1.set_trigger_ormask(1, 1)

    def run_sequence_from_list(self, seq):
        if self._enable_debug: print('run AWG.run_sequence_from_list()')
        
        fix_seq = [([],
                    64)]  # upload an empty sequence to fix a problem that occurs when the same sequence is uploaded multiple times
        self._run_sequence_from_list(fix_seq)
        self._run_sequence_from_list(seq)

    def _run_sequence_from_list(self, seq):
        self.set_mode('continuous')
        self.upload_wave_from_list(seq)
        tmp = self.start()
        return tmp

    def upload_wave_from_list(self, seq, segment_number=-1, hold=False):
        if self._enable_debug: print('run AWG.upload_wave_from_list()')

        self.uploading = True
        self.sequence = seq
        self.stop()
        sample_rate = self.get_samplerate()
        # current_chunk_size = 0

        # the data for the individual channels is stored in a list. Each entry holds the data for one channel
        # data_list = [list(), list(), list(), list(), list(), list(), list(), list(), list(), list()]
        # data_list = [None, None, None, None, None, None, None, None, None, None]

        # get total number of samples and create data arrays
        # total_duration = 0

        # chunks = list() #get the chunks that have to be uploaded
        # current_chunk_samples = 0
        # current_chunk_steps = 0
        # last_chunk_end_steps = 0
        # last_chunk_end_memory = 0
        # for i in range(len(seq)):
        #     total_duration += seq[i][1]
        #     current_chunk_samples += int(seq[i][1]*1e-9 * sample_rate)
        #     current_chunk_steps += 1
        #     if current_chunk_samples > self.max_chunk_size or i == len(seq)-1:
        #         chunks.append([last_chunk_end_steps, current_chunk_steps, current_chunk_samples, last_chunk_end_memory]) #get the number of steps and total samples size of the current chunk
        #         current_chunk_samples = 0
        #         last_chunk_end_steps = current_chunk_steps
        #         last_chunk_end_memory = int(total_duration*1e-9 * sample_rate)
        #
        # print chunks

        # total_samples = int(total_duration*1e-9 * sample_rate)

        # if there are less samples in the sequence than max_chunk_size then just write to complete sequence as one chunk
        # if len(chunks) == 0:
        #     chunks.append([len(seq), total_samples])

        # test_data_list = [list(), list(), list(), list(), list(), list(), list(), list(), list(), list()]

        # the number of samples with appended zeros to have it be a multiple of 32
        total_samples = 0
        for step in seq:
            total_samples += int(step[1] * 1e-9 * sample_rate)
        total_new_samples = total_samples
        while not total_new_samples % 32 == 0:
            total_new_samples += 1

        is_sequence_mode_segment = not (segment_number == -1)

        if not is_sequence_mode_segment:
            self.set_memory_size(int(total_new_samples))

        # print 'samples = ' + str(total_samples)
        step_counter = 0

        # for chunk in chunks:

        current_position = 0
        current_phase_positions = np.zeros(
            4)  # variable for setting the phase=0 position in a waveform for seperate channels
        memory_position = 0
        current_chunk_size = 0
        step_counter = 0
        seq_length = len(seq)

        # self.set_mode('continuous')
        # c1 = self.cards[1]
        # c0 = self.cards[0]
        # c1.set_trigger_mode(0, 'pos_edge')
        # c1.set_trigger_mode(1, 'pos_edge')
        #
        # c1.set_trigger_level0(0, 100)
        # c1.set_trigger_level0(1, 100)
        # # c1.set_triggered_channels_ormask(0b11)
        # # c0.set_triggered_channels_ormask(0b11)
        # self.set_loops(10000)
        # c1.set_trigger_ormask(True, True)

        data_list = list()
        # this looks like 4 analog channels and 6 digital
        for i in range(4):
            data_list.append(np.zeros(int(self.max_chunk_size * self.empty_chunk_array_factor), np.int16))
        for i in range(6):
            data_list.append(np.zeros(int(self.max_chunk_size * self.empty_chunk_array_factor), np.bool))

        # go through single steps in the given chunk sequence
        for step in seq:
            step_counter += 1
            duration = step[1]
            step_channels = step[0]

            # if data_list[0] is not None:
            #     current_position = len(data_list[0])

            # go through all output channels
            for i in range(len(data_list)):
                ch_data = None
                ch_id = -1

                # determine whether there is data in this step for the current output channel. If yes, get the data.
                for step_channel in step_channels:
                    if type(step_channel) == str:
                        step_channel = self.ch(step_channel)
                    if step_channel['ch'] == i:
                        if i < 4:
                            if step_channel['reset_phase']:
                                current_phase_positions[i] = 0
                            ch_data = self.get_data_from_channel(step_channel, duration, sample_rate,
                                                                 current_phase_positions[i])
                        else:
                            ch_data = self.get_marker_data(step_channel, duration, sample_rate, current_position)
                        ch_id = step_channel['ch']
                        break

                # if there is no data specified for this channel in the current step, let it idle for the duration
                if ch_id == -1:
                    ch_data = self._get_data_Idle(duration, sample_rate)

                # print '{0}, {1}, {2}, {3}'.format(i, current_position, len(ch_data), len(data_list[i]))
                data_list[i][current_chunk_size:current_chunk_size + len(ch_data)] = ch_data

                data_length = len(ch_data)
                del ch_data

                # add the data to the data list
                # data_list[i].extend(ch_data)
                #  if data_list[i] == None:
                #     data_list[i] = ch_data
                # else:
                #     data_list[i] = np.concatenate([data_list[i], ch_data])
            current_position += data_length
            current_phase_positions += data_length
            current_chunk_size += data_length

            if current_chunk_size > self.max_chunk_size or step_counter == seq_length:
                if step_counter == seq_length:
                    # print 'last step reached'
                    total_samples = current_position
                    missing_samples = 0
                    while not total_samples % 32 == 0:
                        # skip sample until get to next index that divides 32
                        total_samples += 1
                        current_chunk_size += 1
                        missing_samples += 1
                    if hold and missing_samples > 0:
                        # go through all output channels
                        for i in range(len(data_list)):
                            ch_data = None
                            ch_id = -1

                            # determine whether there is data in this step for the current output channel. If yes, get the data.
                            for step_channel in step_channels:
                                if type(step_channel) == str:
                                    step_channel = self.ch(step_channel)
                                if step_channel['ch'] == i:
                                    if i < 4:
                                        ch_data = self.get_data_from_channel(step_channel, duration, sample_rate,
                                                                             current_phase_positions[i])
                                    else:
                                        ch_data = self.get_marker_data(step_channel, duration, sample_rate,
                                                                       current_position)
                                    ch_id = step_channel['ch']
                                    break

                        # if there is no data specified for this channel in the current step, let it idle for the duration
                        if ch_id == -1:
                            ch_data = self._get_data_Idle(duration, sample_rate)

                        # print '{0}, {1}, {2}, {3}'.format(i, current_position, len(ch_data), len(data_list[i]))
                        data_list[i][current_chunk_size:current_chunk_size + len(ch_data)] = ch_data

                # print 'Uploading chunk: size {0}, pos {1}'.format(current_chunk_size, memory_position)
                if is_sequence_mode_segment:
                    for card in self.cards:
                        card.set_current_segment(segment_number)
                    self.upload(data_list, current_chunk_size, 0, is_buffered=False,
                                is_sequence_segment=is_sequence_mode_segment)
                else:
                    self.upload(data_list, current_chunk_size, memory_position, is_buffered=True)

                # for i in range(10):
                #     test_data_list[i].extend(data_list[i][0:current_chunk_size])

                memory_position += current_chunk_size
                current_chunk_size = 0
                del data_list
                if not step_counter == seq_length:
                    data_list = list()
                    for i in range(4):
                        data_list.append(np.zeros(int(self.max_chunk_size * self.empty_chunk_array_factor), np.int16))
                    for i in range(6):
                        data_list.append(np.zeros(int(self.max_chunk_size * self.empty_chunk_array_factor), np.bool))

        # if not current_chunk_size == 0:
        #     total_samples = current_position
        #     while not total_samples % 32 == 0:
        #         total_samples += 1
        #         current_chunk_size += 1
        #
        #     print 'Uploading chunk: size {0}, pos {1}'.format(current_chunk_size, memory_position)
        #     self.upload(data_list, current_chunk_size, memory_position)
        # self.upload(data_list, chunk[3])
        # memory_position += current_position
        # current_position = 0
        # self.data_list = test_data_list
        # del data_list
        del seq
        # self.data_list = test_data_list
        # return
        # self.upload(len(data_list[0]), data_list)
        # if self.save_data:
        #     self.data_list = data_list
        # else:
        #     del data_list
        # tmp = self.start()
        self.uploading = False
        # return

    def write_wave_to_dictionary(self, name, seq, segment_number=-1, hold=False):
        if self._enable_debug: print('run AWG.write_wave_to_dictionary()')

        self.sequence = seq
        self.stop()
        sample_rate = self.get_samplerate()

        path = os.path.join(os.getcwd(), 'awg', 'WaveFormDict.pkl')
        pkl_file = open(path, 'rb')
        wave_dict = pickle.load(pkl_file)
        pkl_file.close()

        total_samples = 0

        for step in seq:
            total_samples += int(step[1] * 1e-9 * sample_rate)
        total_new_samples = total_samples
        while not total_new_samples % 32 == 0:
            total_new_samples += 1

        is_sequence_mode_segment = not (segment_number == -1)

        if not is_sequence_mode_segment:
            self.set_memory_size(int(total_new_samples))
        current_position = 0
        # variable for setting the phase=0 position in a waveform for seperate channels
        current_phase_positions = np.zeros(4)
        memory_position = 0
        current_chunk_size = 0
        step_counter = 0
        seq_length = len(seq)

        data_list = list()
        # this looks like 4 analog channels and 6 digital
        for i in range(4):
            data_list.append(np.zeros(int(self.max_chunk_size * self.empty_chunk_array_factor), np.int16))
        for i in range(6):
            data_list.append(np.zeros(int(self.max_chunk_size * self.empty_chunk_array_factor), np.bool))

        # go through single steps in the given chunk sequence
        for step in seq:
            step_counter += 1
            duration = step[1]
            step_channels = step[0]

            for i in range(len(data_list)):
                ch_data = None
                ch_id = -1
                # determine whether there is data in this step for the current output channel. If yes, get the data.
                for step_channel in step_channels:
                    if type(step_channel) == str:
                        step_channel = self.ch(step_channel)
                    if step_channel['ch'] == i:
                        if i < 4:
                            if step_channel['reset_phase']:
                                current_phase_positions[i] = 0
                            ch_data = self.get_data_from_channel(step_channel, duration, sample_rate,
                                                                 current_phase_positions[i])
                            pre = 'a'
                        else:
                            ch_data = self.get_marker_data(step_channel, duration, sample_rate, current_position)
                            pre = 'd'
                        ch_id = step_channel['ch']
                        break

                # if there is no data specified for this channel in the current step, let it idle for the duration
                if ch_id == -1:
                    ch_data = self._get_data_Idle(duration, sample_rate)

                full_name = name + '_' + pre + 'ch' + str(i)
                wave_dict[full_name] = ch_data

                # print '{0}, {1}, {2}, {3}'.format(i, current_position, len(ch_data), len(data_list[i]))
                data_list[i][current_chunk_size:current_chunk_size + len(ch_data)] = ch_data

                data_length = len(ch_data)

        return data_length, data_list, wave_dict

    def run_triggered_multi_from_list(self, seq, awg_mode=''):
        if self._enable_debug: print('run AWG.run_triggered_multi_from_list()')

        fix_seq = [([],
                    64)]  # upload an empty sequence to fix a problem that occurs when the same sequence is uploaded multiple times
        self._run_sequence_from_list(fix_seq)
        self._run_triggered_multi_from_list(seq, awg_mode)

    def _run_triggered_multi_from_list(self, seqs, awg_mode=''):
        self.uploading = True
        self.sequence = seqs
        self.stop()
        sample_rate = self.get_samplerate()

        if awg_mode == '':
            self.set_mode('singlerestart')
        else:
            self.set_mode(awg_mode)
        self.init_ext_trigger()
        # c1 = self.cards[1]
        # c0 = self.cards[0]
        # c1.set_trigger_mode(0, 'pos_edge')
        # c1.set_trigger_mode(1, 'pos_edge')
        #
        # c1.set_trigger_level0(0, 100)
        # c1.set_trigger_level0(1, 100)
        # # c1.set_triggered_channels_ormask(0b11)
        # # c0.set_triggered_channels_ormask(0b11)
        # self.set_loops(0)
        # c1.set_trigger_ormask(True, True)
        # print c1.get_trigger_in_ormask(0), c1.get_trigger_in_ormask(1)
        # # self.write_setup()
        # print c1.get_trigger_in_ormask(0), c1.get_trigger_in_ormask(1)
        # get max segment size (it is assumed that the max. segment size occurs at the longest tau)

        max_seq = seqs[-1]
        segment_size = 0

        for step in max_seq:
            segment_size += int(step[1] * 1e-9 * sample_rate)
            # print segment_size
        while not segment_size % 32 == 0:
            segment_size += 1

        self.set_segment_size(segment_size)
        self.set_memory_size(segment_size * len(seqs))
        # pylab.figure()
        for iseq, seq in enumerate(seqs):

            data_list = list()
            for i in range(4):
                data_list.append(np.zeros(segment_size, np.int16))
            for i in range(6):
                data_list.append(np.zeros(segment_size, np.bool))

            step_counter = 0
            position = 0

            # go through single steps in the given chunk sequence
            for step in seq:
                step_counter += 1
                duration = step[1]
                step_channels = step[0]

                # if data_list[0] is not None:
                #     current_position = len(data_list[0])

                # go through all output channels
                for i in range(len(data_list)):
                    ch_data = None
                    ch_id = -1

                    # determine whether there is data in this step for the current output channel. If yes, get the data.
                    for step_channel in step_channels:
                        if type(step_channel) == str:
                            step_channel = self.ch(step_channel)
                        if step_channel['ch'] == i:
                            if i < 4:
                                ch_data = self.get_data_from_channel(step_channel, duration, sample_rate, 0)
                            else:
                                ch_data = self.get_marker_data(step_channel, duration, sample_rate, 0)
                            ch_id = step_channel['ch']
                            break

                    # if there is no data specified for this channel in the current step, let it idle for the duration
                    if ch_id == -1:
                        ch_data = self._get_data_Idle(duration, sample_rate)

                    # print '{0}, {1}, {2}, {3}'.format(i, current_position, len(ch_data), len(data_list[i]))
                    data_list[i][position:position + len(ch_data)] = ch_data

                    data_length = len(ch_data)
                    del ch_data

                position += duration

            # pylab.plot(data_list[0])

            self.upload(data_list, segment_size, segment_size * iseq)
            # print 'Uploaded sequence ' + str(iseq+1) + ' of ' + str(len(seqs))

        # pylab.show()
        del seqs

        tmp = self.start_enable_trigger()

        self.uploading = False

    def run_triggered_pulsed(self, taus, mwsequence, imwsequence="", dict_values=dict(), LaserOn=2000, LaserWait=2000,
                             pre_laser_sequence='', pre_laser_sequence_i='', awg_mode='', laser_prewait=500):
        if self._enable_debug: print('run AWG.run_triggered_pulsed()')

        self.uploading = True
        self.stop()
        self.start_time = time.time()
        # convert the values stored in dict_values (Tpi, etc) into local variables so they are found during eval of the sequence string
        for key in dict_values.keys():
            exec('{0} = {1}'.format(key, dict_values[key]))

        ch = self.ch

        seqs = list()

        sequence = []
        sequence.append(([], 200))
        sequence.append(([ch('trig')], 200))
        sequence.append(([], 200))
        sequence.append(([ch('laser'), ch('aom')], 200))
        sequence.append(([], LaserOn))  # fake Laser Pulse to avoid counting the next flash as double flashes

        seqs.append(sequence)

        for i, tau in enumerate(taus):

            sequence = []
            if pre_laser_sequence != '':
                for pulse in eval(pre_laser_sequence):
                    sequence.append(pulse)
                sequence.append(([ch('aom')], LaserOn))

            sequence.append(([], LaserWait))
            for pulse in eval(mwsequence):
                sequence.append(pulse)
            sequence.append(([], laser_prewait))
            sequence.append(([ch('laser'), ch('aom')], LaserOn))

            seqs.append(sequence)

            sequence = []
            if imwsequence != "":
                if pre_laser_sequence != '':
                    if pre_laser_sequence_i != '':
                        for pulse in eval(pre_laser_sequence_i):
                            sequence.append(pulse)
                    else:
                        for pulse in eval(pre_laser_sequence):
                            sequence.append(pulse)
                    sequence.append(([ch('aom')], LaserOn))
                sequence.append(([], LaserWait))
                for pulse in eval(imwsequence):
                    sequence.append(pulse)
                sequence.append(([], laser_prewait))
                sequence.append(([ch('laser'), ch('aom')], LaserOn))

                seqs.append(sequence)

        self.run_triggered_multi_from_list(seqs, awg_mode)

    def run_pulsed(self, taus, mwsequence, imwsequence="", dict_values=dict(), LaserOn=2000, LaserWait=2000,
                   pre_laser_sequence='', laser_prewait=500,
                   pre_laser_sequence_i='', clocking=0, run_cw='', detect_laser_channels="[ch('laser'), ch('aom')]",
                   sync_channels="[ch('trig')]",
                   init_laser_channels="[ch('aom')]"):
        
        if self._enable_debug: print('run AWG.run_pulsed()')

        # clocking => make the length of each measurement a multiple of clocking

        self.uploading = True
        self.stop()
        self.start_time = time.time()
        # convert the values stored in dict_values (Tpi, etc) into local variables so they are found during eval of the sequence string
        for key in dict_values.keys():
            exec('{0} = {1}'.format(key, dict_values[key]))

        ch = self.ch

        sequence = []
        sequence.append(([], 200))
        # sequence.append(  ([ch('trig')], 200)        )
        sequence.append((eval(sync_channels), 200))
        sequence.append(([], 200))
        sequence.append((eval(detect_laser_channels), 200))
        sequence.append(([], LaserOn))  # fake Laser Pulse to avoid counting the next flash as double flashes

        for tau in taus:
            subseq = []
            if pre_laser_sequence != '':
                for pulse in eval(pre_laser_sequence):
                    subseq.append(pulse)
                subseq.append((eval(init_laser_channels), LaserOn))
                # subseq.append(  ([ch('aom')], LaserOn)        )

            subseq.append(([], LaserWait))
            for pulse in eval(mwsequence):
                subseq.append(pulse)
            subseq.append(([], laser_prewait))
            subseq.append((eval(detect_laser_channels), LaserOn))
            # subseq.append(  ([ch('laser'), ch('aom')], LaserOn)        )
            if clocking:
                subseq = self.fill_sequence(subseq, clocking)
            sequence.extend(subseq)
            if imwsequence != "":
                isubseq = []
                if pre_laser_sequence != '':
                    if pre_laser_sequence_i != '':
                        for pulse in eval(pre_laser_sequence_i):
                            isubseq.append(pulse)
                    else:
                        for pulse in eval(pre_laser_sequence):
                            isubseq.append(pulse)
                    isubseq.append((eval(init_laser_channels), LaserOn))
                    # isubseq.append(  ([ch('aom')], LaserOn)        )
                isubseq.append(([], LaserWait))
                for pulse in eval(imwsequence):
                    isubseq.append(pulse)
                isubseq.append(([], laser_prewait))
                isubseq.append((eval(detect_laser_channels), LaserOn))
                # isubseq.append(  ([ch('laser'), ch('aom')], LaserOn)        )
                if clocking:
                    isubseq = self.fill_sequence(isubseq, clocking)
                sequence.extend(isubseq)

        if run_cw:
            for i, s in enumerate(sequence):
                s[0].append(eval(run_cw))

        # return sequence
        return self.run_sequence_from_list(sequence)

    def run_freq_sweep_pulsed(self, freqs, mwsequence, imwsequence="", run_cw="", repetitions=1, dict_values=dict(),
                              LaserOn=2000, LaserWait=2000, pre_laser_sequence='', pre_laser_sequence_i='',
                              laser_prewait=500, trig_seq="[([ch('trig')], 300), ([], 500)]", sweep_params=[]):
        if self._enable_debug: print('run AWG.run_freq_sweep_pulsed()')

        self.uploading = True
        self.stop()
        self.start_time = time.time()
        # convert the values stored in dict_values (Tpi, etc) into local variables so they are found during eval of the sequence string
        for key in dict_values.keys():
            exec('{0} = {1}'.format(key, dict_values[key]))

        ch = self.ch

        # samplerate = self.get_samplerate()

        self.init_sequence_mode(len(freqs) + 1)
        trig_seq = eval(trig_seq)
        # if run_cw:
        #     trig_seq[0][0].append(eval(run_cw))
        self.upload_wave_from_list(trig_seq, segment_number=0)
        segment = []

        for i, f in enumerate(freqs):
            period = 1 / float(f) * 1e9
            if sweep_params:
                p = sweep_params[i]
            segment = []
            if pre_laser_sequence != '':
                for pulse in eval(pre_laser_sequence):
                    segment.append(pulse)
                segment.append(([ch('aom')], LaserOn))

            segment.append(([], LaserWait))
            for pulse2 in eval(mwsequence):
                # print(pulse2)
                segment.append(pulse2)
            segment.append(([], laser_prewait))
            segment.append(([ch('laser'), ch('aom')], LaserOn))
            segment = self.fill_sequence(segment, period, fill_chans=[])

            if imwsequence != "":
                if pre_laser_sequence != '':
                    for pulse in eval(pre_laser_sequence):
                        segment.append(pulse)
                    segment.append(([ch('aom')], LaserOn))

                segment.append(([], LaserWait))
                for pulse2 in eval(imwsequence):
                    # print(pulse2)
                    segment.append(pulse2)
                segment.append(([], laser_prewait))
                segment.append(([ch('laser'), ch('aom')], LaserOn))
                segment = self.fill_sequence(segment, period, fill_chans=[])

            if run_cw:
                for j in range(len(segment)):
                    segment[j][0].extend(eval(run_cw))

            self.upload_wave_from_list(segment, segment_number=i + 1)
            del segment

        for i in range(len(freqs)):
            # print 'l: ' + str(len(freqs))
            # print 'i: ' + str(i)
            self.write_sequence_step(2 * i, 0, 1, 2 * i + 1, 'always')
            if i == len(freqs) - 1:
                self.write_sequence_step(2 * i + 1, i + 1, repetitions, 0, 'always')
            else:
                self.write_sequence_step(2 * i + 1, i + 1, repetitions, 2 * i + 2, 'always')

        tmp = self.start()
        return tmp

    def run_freq_sweep(self, freqs, mwsequences, run_cw='', repetitions=1, sync_seq="[([ch('trig')], 100)]",
                       trig_seq="[([ch('laser')], 100)]", sweep_params=[]):
        if self._enable_debug: print('run AWG.run_freq_sweep()')

        self.uploading = True
        self.stop()
        self.start_time = time.time()

        ch = self.ch

        # samplerate = self.get_samplerate()

        self.init_sequence_mode(len(freqs) * len(mwsequences) + 2)

        sync_seq = eval(sync_seq)
        self.upload_wave_from_list(sync_seq, segment_number=0)
        trig_seq = eval(trig_seq)
        self.upload_wave_from_list(trig_seq, segment_number=1)
        segment = []

        for i, f in enumerate(freqs):
            period = 1 / float(f) * 1e9
            if sweep_params:
                p = sweep_params[i]

            for k, seq in enumerate(mwsequences):
                segment = []
                for pulse in eval(seq):
                    segment.append(pulse)
                segment = self.fill_sequence(segment, period, fill_chans=[])

                if run_cw:
                    for j in range(len(segment)):
                        segment[j][0].extend(eval(run_cw))

                self.upload_wave_from_list(segment, segment_number=i * len(mwsequences) + k + 2)
                del segment

        self.write_sequence_step(0, 0, 1, 1, 'always')
        for i in range(len(freqs) * len(mwsequences)):
            self.write_sequence_step(2 * i + 1, 1, 1, 2 * i + 2, 'always')
            if i == len(freqs) * len(mwsequences) - 1:
                self.write_sequence_step(2 * i + 2, i + 2, repetitions, 0, 'always')
            else:
                self.write_sequence_step(2 * i + 2, i + 2, repetitions, 2 * i + 3, 'always')

        tmp = self.start()
        return tmp

    def run_in_sequence_mode(self, seq):
        if self._enable_debug: print('run AWG.run_in_sequence_mode()')
        self.uploading = True
        self.stop()
        self.start_time = time.time()

        ch = self.ch

        self.init_sequence_mode(len(seq))

        for i, s in enumerate(seq):
            tmp_seq = eval(s[0])
            self.upload_wave_from_list(tmp_seq, segment_number=i)
            if i == len(seq) - 1:
                self.write_sequence_step(i, i, s[1], 0, 'always')
            else:
                self.write_sequence_step(i, i, s[1], i + 1, 'always')

        return self.start()

    def write_sequence_step(self, step_index, mem_segment_index, loops, goto, next_condition):
        if self._enable_debug: print('run AWG.write_sequence_step()')

        print(step_index, mem_segment_index, loops, goto, next_condition)
        for card in self.cards:
            card.write_sequence_step(step_index, mem_segment_index, loops, goto, next_condition)

    def fill_sequence(self, seq, fill_to, fill_chans=[]):
        if self._enable_debug: print('run AWG.fill_sequence()')

        # get length of seq
        seq_length = 0
        for i, s in enumerate(seq):
            seq_length += s[1]
        add_length = (int(seq_length) / int(fill_to) + 1) * fill_to - seq_length
        tmp_seq = (fill_chans, add_length)
        seq.append(tmp_seq)
        # print 'filling'
        # print fill_to, seq_length, add_length
        return seq

    def init_sequence_mode(self, number_of_segments):
        if self._enable_debug: print('run AWG.init_sequence_mode()')

        for card in self.cards:
            card.init_sequence_mode(number_of_segments)

    def run_pulsed_afm(self, mwList, dict_values=dict(), LaserOn=1000, LaserWait=1000):
        if self._enable_debug: print('run AWG.run_pulsed_afm()')

        self.uploading = True
        self.stop()
        self.start_time = time.time()
        # convert the values stored in dict_values (Tpi, etc) into local variables so they are found during eval of the sequence string
        for key in dict_values.keys():
            exec('{0} = {1}'.format(key, dict_values[key]))
        ch = self.ch
        sequence = []
        sequence.append(([], 200))
        sequence.append(([ch('trig')], 200))
        sequence.append(([], 200))
        sequence.append(([ch('aom')], 200))
        # sequence.append(  ([ch('laser'), ch('aom')], 200)          )
        sequence.append(([], LaserOn))
        for element in mwList:
            sequence.append(([], LaserWait))
            for pulse in eval(str(element)):
                sequence.append(pulse)
            sequence.append(([], laser_prewait))
            sequence.append(([ch('laser'), ch('aom')], LaserOn))
        # return sequence
        return self.run_sequence_from_list(sequence)

    def run_pulsed_freq_sweep(self, taus, mwsequence, imwsequence="", dict_values=dict(), LaserOn=2000, LaserWait=2000):
        if self._enable_debug: print('run AWG.run_pulsed_freq_sweep()')

        self.uploading = True
        self.stop()
        self.start_time = time.time()
        # convert the values stored in dict_values (Tpi, etc) into local variables so they are found during eval of the sequence string
        for key in dict_values.keys():
            exec('{0} = {1}'.format(key, dict_values[key]))
        ch = self.ch
        sequence = []
        # sequence.append(  ([       ], 200)        )
        # sequence.append(  ([ch('trig')], 200)        )
        # sequence.append(  ([       ], 200)        )
        # sequence.append(  ([ch('laser'), ch('aom')], 200)          )
        # sequence.append(  ([       ], LaserOn) ) #fake Laser Pulse to avoid counting the next flash as double flashs

        # for tau in taus:
        sequence.append(([], LaserWait))
        for pulse in eval(mwsequence):
            sequence.append(pulse)
        sequence.append(([ch('trig')], laser_prewait))
        sequence.append(([ch('laser'), ch('aom')], LaserOn))
        if imwsequence != "":
            sequence.append(([], LaserWait))
            for pulse in eval(imwsequence):
                sequence.append(pulse)
            sequence.append(([ch('trig2')], laser_prewait))
            sequence.append(([ch('laser'), ch('aom')], LaserOn))
        # return sequence
        return self.run_sequence_from_list(sequence)

    def plot_data(self, channels=None):
        if self._enable_debug: print('run AWG.plot_data()')

        if channels == None:
            channels = range(len(self.data_list))

        fig = pylab.figure()
        ax = fig.add_subplot(111)
        ax.set_xlabel('Sample #')
        # ax.set_ylabel('channel')

        yticks = []
        ylabels = []

        offset = 0
        for i in channels:
            if i < 4:
                ax.plot(np.asarray(self.data_list[i], dtype=float) / (2 ** 15 - 1) - offset * 2.5, 'k-',
                        label='ch' + str(i))
            else:
                ax.plot(np.asarray(self.data_list[i], dtype=float) - offset * 2.5, 'k-', label='ch' + str(i))
            yticks.append(-offset * 2.5)
            offset += 1
            ylabels.append('Ch' + str(i))

        ax.set_ylim([-offset * 2.5 + 1.0, 1.5])
        ax.set_yticks(yticks)
        ax.set_yticklabels(ylabels)
        pylab.show()

    def eval_channel(self, ch):
        if self._enable_debug: print('run AWG.eval_channel()')

        """ insert the variable values when a channel parameter holds a string (as ref to a variable) """
        if type(ch['freq']) == str:
            ch['freq'] = getattr(self, ch['freq'])
        if type(ch['freq2']) == str:
            ch['freq2'] = getattr(self, ch['freq2'])
        if type(ch['phase']) == str:
            ch['phase'] = getattr(self, ch['phase'])
        if type(ch['amp']) == str:
            ch['amp'] = getattr(self, ch['amp'])
        return ch

    def get_data_from_channel(self, channel, duration, sample_rate, start_position):
        if self._enable_debug: print('run AWG.get_data_from_channel()')
        # channel = self.eval_channel(channel)
        data = np.zeros(int(duration * 1e-9 * sample_rate))
        if channel['type'] == 'DC':
            data = self._get_data_DC(duration, sample_rate, channel)
        if channel['type'] == 'sin':
            data = self._get_data_Sin(duration, sample_rate, channel, start_position)
        if channel['type'] == 'cos2':
            data = self._get_data_Cos2(duration, sample_rate, channel, start_position)
            self.temp_sine = data
        if channel['type'] == "chirp":
            data = self._get_data_Chirp(duration, sample_rate, channel, start_position)
        return np.asarray(data * (2 ** 15 - 1), dtype=np.int16)

    def get_marker_data(self, channel, duration, sample_rate, start_position):
        if self._enable_debug: print('run AWG.get_marker_data()')
        
        length = int(duration * 1e-9 * sample_rate)
        return self.get_marker_samples(length)

    def _get_data_DC(self, duration, sample_rate, ch):
        if self._enable_debug: print('run AWG._get_data_DC')
        length = int(duration * 1e-9 * sample_rate)
        return self._get_samples_DC(length, ch)

    def _get_samples_DC(self, samples, ch):
        if self._enable_debug: print('run AWG._get_samples_DC')
        data = np.ones(samples) * ch['amp']
        return data

    def _get_data_Sin(self, duration, sample_rate, ch, start_position):
        if self._enable_debug: print('run AWG._get_data_Sin')
        length = int(duration * 1e-9 * sample_rate)
        t = np.linspace((start_position) / float(sample_rate),
                        start_position / float(sample_rate) + (duration - 1) * 1e-9, length)
        data = ch['amp'] * np.sin(2 * np.pi * ch['freq'] * t + ch['phase'])
        # print t
        # print 'start pos: ' + str(start_position) + ' => first sample: ' + str(data[0])
        return data

    def _get_data_Cos2(self, duration, sample_rate, ch, start_position):
        if self._enable_debug: print('run AWG._get_data_Cos2')
        magic_number_for_cos2 = 4  # not clear whether 4 is the right number. need to find analytical justification.
        length = magic_number_for_cos2 * int(duration * 1e-9 * sample_rate)
        t = np.linspace((start_position) / float(sample_rate),
                        start_position / float(sample_rate) + (magic_number_for_cos2 * duration - 1) * 1e-9, length)
        data = ch['amp'] * np.sin(2 * np.pi * ch['freq'] * t + ch['phase'])
        # w = 2500.0                         # pulse width FWHM. Should be the same as the square pulse width
        w = int(duration * 1e-9 * sample_rate)  # pulse width FWHM. Should be the same as the square pulse width
        # right now it is in the units of the samples, so a factor of 0.8
        # should go there when moving from 1.25 GS/s to 1.00 GS/s.
        n = length  # number of samples
        x = np.arange(length)
        data = data * np.sin(np.pi * np.maximum((1 - magic_number_for_cos2 * np.abs(x / n - 0.5)), 0) / 2) ** 2
        # print t
        # print 'start pos: ' + str(start_position) + ' => first sample: ' + str(data[0])
        return data

    def _get_samples_Sin(self, samples, sample_rate, ch, start_position):
        if self._enable_debug: print('run AWG._get_samples_Sin')
        t = np.linspace((start_position) / float(sample_rate), (start_position + samples) / float(sample_rate), samples)
        data = ch['amp'] * np.sin(2 * np.pi * ch['freq'] * t + ch['phase'])
        return data

    def get_marker_samples(self, samples):
        if self._enable_debug: print('run AWG.get_marker_samples()')
        data = np.ones(samples, dtype=bool)
        return data

    def _get_data_Chirp(self, duration, sample_rate, ch, start_position):
        if self._enable_debug: print('run AWG._get_data_Chirp')
        length = int(duration * 1e-9 * sample_rate)
        t = np.linspace((start_position) / float(sample_rate),
                        start_position / float(sample_rate) + (duration - 1) * 1e-9, length)
        t0 = t[0]
        t1 = t[-1]
        data = ch['amp'] * np.sin(
            2 * np.pi * (ch['freq'] + (ch['freq2'] - ch['freq']) / (2 * (t1 - t0))) * (t - t0) ** 2 + ch['phase'])
        # data = ch['amp']*np.sin(2*np.pi*(ch['freq'] + (t-t0)*(ch['freq2']-ch['freq'])/(t1-t0))*t + ch['phase'])
        return data

    def _get_data_Idle(self, duration, sample_rate):
        if self._enable_debug: print('run AWG._get_data_Idle')
        length = int(duration * 1e-9 * sample_rate)
        return np.zeros(length)

    def ch(self, name, type=None, amp=None, phase=None, freq=None, freq2=None, reset_phase=False):
        """ gets the dictonary with the setup of a certain channel """
        if self._enable_debug: print('run AWG.ch()')

        # copy the dictionary so the channel setup is not changed
        setup = copy.deepcopy(self.channel_setup)

        for c in setup:
            if c['name'] == name:
                chan = c
                break

        if type:
            chan['type'] = type
        if amp:
            chan['amp'] = amp
        if phase:
            chan['phase'] = phase
        if freq:
            chan['freq'] = freq
        if freq2:
            chan['freq2'] = freq2
        # if reset_phase:
        chan['reset_phase'] = reset_phase

        chan = self.eval_channel(chan)

        return chan

    def light(self):
        # seq = "[([ch('aom'), ch('mw'), ch('mw_gate')], 64)]"
        if self._enable_debug: print('run AWG.light()')
        seq = "[([ch('aom'), ch('mw'), ch('mw_gate')], 102400)]"
        self.run_sequence(seq)

    def fixit(self):
        """
        for some reason the AWG shows problems when loading the sequence that is alreasy running (wrong output).
        As a dirty fix this method can be run to overwrite the currently running sequence. It's bad I know...
        """
        if self._enable_debug: print('run AWG.fixit()')
        # seq = "[([ch('aom'), ch('mw'), ch('mw_gate')], 64)]"
        seq = "[([], 64)]"
        self.run_sequence(seq)

    def sync_all_cards(self):
        if self._enable_debug: print('run AWG.sync_all_cards()')
        self.hub.sync_all_cards()

    def start(self):
        if self._enable_debug: print('run AWG.start()')
        return self.hub.start_triggered()

    def start_enable_trigger(self):
        if self._enable_debug: print('run AWG.start_enable_trigger()')
        return self.hub.start_enable_trigger()

    def stop(self):
        if self._enable_debug: print('run AWG.stop()')
        return self.hub.stop()

    def reset(self):
        if self._enable_debug: print('run AWG.reset()')
        return self.hub.reset()

    def write_setup(self):
        if self._enable_debug: print('run AWG.write_setup()')
        return self.hub.write_setup()

    def enable_trigger(self):
        if self._enable_debug: print('run AWG.enable_trigger()')
        return self.hub.enable_trigger()

    def force_trigger(self):
        if self._enable_debug: print('run AWG.force_trigger()')
        return self.hub.force_trigger()

    def disable_trigger(self):
        if self._enable_debug: print('run AWG.disable_trigger()')
        return self.hub.disable_trigger()

    def wait_trigger(self):
        if self._enable_debug: print('run AWG.wait_trigger()')
        return self.hub.wait_trigger()

    def wait_ready(self):
        if self._enable_debug: print('run AWG.wait_ready()')
        return self.hub.wait_ready()

    def set_samplerate(self, rate):
        if self._enable_debug: print('run AWG.set_samplerate()')
        for c in self.cards:
            c.set_samplerate(rate)

    def get_samplerate(self):
        if self._enable_debug: print('run AWG.get_samplerate()')
        return self.cards[1].get_samplerate()

    def set_loops(self, loops):
        if self._enable_debug: print('run AWG.set_loops()')
        for c in self.cards:
            c.set_loops(loops)

    def set_mode(self, mode):
        if self._enable_debug: print('run AWG.set_mode()', mode)
        for card in self.cards:
            if self._enable_debug: print(card)
            card.set_mode(mode)

    def set_segment_size(self, segment_size):
        if self._enable_debug: print('run AWG.set_segment_size()')
        for card in self.cards:
            card.set_segment_size(segment_size)

    def set_memory_size(self, mem_size, is_sequence_segment=False):
        if self._enable_debug: print('run AWG.set_memory_size()')
        for card in self.cards:
            card.set_memory_size(mem_size, is_seq_segment=is_sequence_segment)

    def set_selected_channels(self, channels):
        """ set used channels. channels is binary with a flag (bit) for each channel: 0b0101 -> ch 0 & 2 active """
        if self._enable_debug: print('run AWG.set_selected_channels()', channels)
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
        self.cards[0].set_channel_output(0, channels & 0b0001)
        self.cards[0].set_channel_output(1, channels & 0b0010)
        self.cards[1].set_channel_output(0, channels & 0b0100)
        self.cards[1].set_channel_output(1, channels & 0b1000)

    def upload(self, data, data_size, mem_offset, is_buffered=True, is_sequence_segment=False):
        self.uploading = True
        if self._enable_debug: print('run AWG.upload()')
        # if not data0 == None or not data1 == None:
        self.start_upload_time = time.time()
        t1 = threading.Thread(target=self._run_upload,
                              args=(self.cards[0], [data[0], data[1], data[4], data[5], data[6]]),
                              kwargs={'mem_offset': mem_offset, 'data_size': data_size, 'is_buffered': is_buffered,
                                      'is_sequence_segment': is_sequence_segment})
        t2 = threading.Thread(target=self._run_upload,
                              args=(self.cards[1], [data[2], data[3], data[7], data[8], data[9]]),
                              kwargs={'mem_offset': mem_offset, 'data_size': data_size, 'is_buffered': is_buffered,
                                      'is_sequence_segment': is_sequence_segment})
        del data
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.finish_time = time.time()
        # self.uploading = False

        if not hasattr(self, 'start_time'):
            return

    def wait_upload(self):
        if self._enable_debug: print('run AWG.wait_upload()')
        while self.uploading:
            time.sleep(0.1)

    def _run_upload(self, card, data, data_size, mem_offset, is_buffered=True, is_sequence_segment=False):
        if self._enable_debug:
            print('run AWG._run_upload()')
            print(mem_offset, data_size, is_buffered, is_sequence_segment)
        # print 'Uploading to card ' + str(card.cardNo) + '...'
        res = card.upload(data_size, data[0], data[1], data[2], data[3], data[4], is_buffered=is_buffered,
                          mem_offset=mem_offset, block=True, is_seq_segment=is_sequence_segment)
        if res == 0:
            pass
            # print 'Upload to card ' + str(card.cardNo) + ' finished.'
        else:
            print('Error on card ' + str(card.cardNo) + ': ' + str(res))

    def close(self):
        if self._enable_debug: print('run AWG.close()')
        for c in self.cards:
            c.close()
        self.hub.close()


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
        spcm_dwSetParam_i32(self.handle, register, int32(value))

    def set64(self, register, value):
        spcm_dwSetParam_i64(self.handle, register, int64(value))

    def get32(self, register):
        res = int32(0)
        spcm_dwGetParam_i32(self.handle, register, byref(res))
        return res.value

    def get64(self, register):
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

        self.init_markers()
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

    def start(self):
        if self._enable_debug: print('run Card.start()')
        self.set32(SPC_M2CMD, M2CMD_CARD_START)
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
    def upload(self, number_of_samples, data=None, data1=None, marker0_data=None, marker1_data=None, marker2_data=None,
               is_buffered=False, mem_offset=0, block=False, is_seq_segment=False):
        """ uploads data to the card.
        Values in 'data' can not exceed -1.0 to 1.0.
        Values in marker data can only be 0 or 1

        If marker_data parameters are all None the marker outputs will be disabled.

        PS: the current version uses a fixed setting for the markers. Therefore all marker and channels are used
            all the time, whether they output data or not. This makes the code simpler but also leads to
            unneccesary upload of zeros...
        """

        if self._enable_debug: print('run Card.upload()')
        
        new_samples = number_of_samples
        # print(data)
        with open('C:\\Users\\yy3\\qudi\\Data\\2024\\05\\2024-05-06\\card%d_data.npy' % self.cardNo, 'wb+') as f:
            np.save(f, data)
        data //= 2
        data1 //= 4


        # set the commands that are used for the data transfer
        if block:
            com = M2CMD_DATA_STARTDMA | M2CMD_DATA_WAITDMA
        else:
            com = M2CMD_DATA_STARTDMA

        used_channels = self.get_selected_channels_count()
        bytes_per_sample = self.get_bytes_per_sample()

        # set the amount of memory that will be used
        if not is_buffered:
            while (not (new_samples % 32 == 0)) or (is_seq_segment is True and new_samples < 192):
                new_samples += 1

            self.set_memory_size(new_samples, is_seq_segment=is_seq_segment)
            # print 'Setting memory size: ' + str(new_samples) + ', is_segment=' + str(is_seq_segment)

        # set the buffer
        BufferSize = uint64(new_samples * bytes_per_sample * used_channels)
        pvBuffer = create_string_buffer(BufferSize.value)
        # pnBuffer = cast(pvBuffer, ptr16)
        pnBuffer = np.zeros(new_samples * used_channels, dtype=np.int16)
        # test = np.zeros(new_samples*used_channels, dtype=np.int16)

        # print data.dtype
        # print data1.dtype
        # print marker0_data.dtype

        # print '{0}, {1}, {2}, {3}'.format(mem_offset, number_of_samples, new_samples, len(data))

        pnBuffer[0:number_of_samples * 2:2] = data[0:number_of_samples]
        
        # pdb.set_trace()
        pnBuffer[0:number_of_samples * 2:2] += np.ma.masked_where(data[0:number_of_samples] < 0,
                                                                  data[0:number_of_samples], copy=False).mask * 2 ** 15
        self.temp_data = data  # added this for debugging purposes and future plotting of data
        
        del data
        pnBuffer[0:number_of_samples * 2:2] -= marker0_data[0:number_of_samples] * 2 ** 15
        with open('C:\\Users\\yy3\\qudi\\Data\\2024\\05\\2024-05-06\\card%d_marker0.npy' % self.cardNo, 'wb+') as f:
            np.save(f, marker0_data)
        del marker0_data

        pnBuffer[1:number_of_samples * 2:2] = data1[0:number_of_samples]
        pnBuffer[1:number_of_samples * 2:2] += np.ma.masked_where(data1[0:number_of_samples] < 0,
                                                                  data1[0:number_of_samples], copy=False).mask * 2 ** 14
        del data1
        pnBuffer[1:number_of_samples * 2:2] -= marker1_data[0:number_of_samples] * 2 ** 15
        pnBuffer[1:number_of_samples * 2:2] += marker2_data[0:number_of_samples] * 2 ** 14
        # print pnBuffer.dtype
        del marker1_data
        del marker2_data

        with open('C:\\Users\\yy3\\qudi\\Data\\2024\\05\\2024-05-06\\card%d_pnBuffer.npy' % self.cardNo, 'wb+') as f:
            np.save(f, pnBuffer)

        pvBuffer.raw = pnBuffer.tostring()
        
        spcm_dwDefTransfer_i64(self.handle, SPCM_BUF_DATA, SPCM_DIR_PCTOCARD, uint32(0), pvBuffer,
                               uint64(mem_offset * used_channels * bytes_per_sample), BufferSize)

        # self.data = test.copy()
        # del test
        # del data
        # del data1

        # execute the transfer
        self.set32(SPC_M2CMD, com)
        if block:
            del pnBuffer, pvBuffer
        err = self.chkError()
        # print err
        return err  # self.chkError()

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

        val = int(mem_segment_index) | int(goto) << 16 | int(loops) << 32 | self._vals_sequence_step[
            next_condition] << 32
        self.set64(SPC_SEQMODE_STEPMEM0 + step_index, val)

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
        # print('set32!!!!', register, value)
        spcm_dwSetParam_i32(self.handle, register, int32(value))

    def set64(self, register, value):
        # print('set64!!!!', register, value)
        spcm_dwSetParam_i64(self.handle, register, int64(value))

    def get32(self, register):
        # print('get32!!!!', register)
        res = int32(0)
        spcm_dwGetParam_i32(self.handle, register, byref(res))
        return res.value

    def get64(self, register):
        # print('get64!!!!', register)
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