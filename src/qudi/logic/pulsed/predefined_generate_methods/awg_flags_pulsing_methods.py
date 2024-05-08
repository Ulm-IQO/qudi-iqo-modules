# Made by Ilia Chuprina
# Prepare pulse sequence to pulse lasers using flags AUX outputs AWG Tektr70k
# prepared for Michael O-F

import numpy as np
from qudi.logic.pulsed.pulse_objects import PulseBlock, PulseBlockEnsemble, PulseSequence
from qudi.logic.pulsed.pulse_objects import PredefinedGeneratorBase
from qudi.logic.pulsed.sampling_functions import SamplingFunctions
from qudi.util.helpers import csv_2_list

class FlagPulseGenerator(PredefinedGeneratorBase):
    """

    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def _customize_seq_para(self, seq_para_dict):
        if 'event_trigger' not in seq_para_dict:
            seq_para_dict['event_trigger'] = 'OFF'
        if 'event_jump_to' not in seq_para_dict:
            seq_para_dict['event_jump_to'] = 0
        if 'wait_for' not in seq_para_dict:
            seq_para_dict['wait_for'] = 'OFF'
        if 'repetitions' not in seq_para_dict:
            seq_para_dict['repetitions'] = 0
        if 'go_to' not in seq_para_dict:
            seq_para_dict['go_to'] = 0
        return seq_para_dict

    def _get_basic_wait_ensemble(self, name, basic_length):
        created_blocks = list()
        created_ensembles = list()

        wait_block = PulseBlock(name=name)
        waiting_element = self._get_idle_element(length=basic_length, increment=0)
        wait_block.append(waiting_element)
        created_blocks.append(wait_block)
        wait_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        wait_ensemble.append((wait_block.name, 0))
        wait_ensemble = self._add_metadata_to_settings(wait_ensemble, created_blocks=created_blocks,
                                                        alternating=False, controlled_variable=[])
        created_ensembles.append(wait_ensemble)

        return created_blocks, created_ensembles

    def generate_flags_test(self, name='flags_test', length_A=1e-6, length_B=1e-6, wait_AB=2e-6, basic_length=1e-6, seq_trig_C=True, seq_trig_D=False):
        # not sweeping any elements in this test sequence. Just consequently  turning on/off flags

        # Create empty lists
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # empty list of the sequence
        flags_pulse_params_list = list()

        # add blocks and ensemble for long waiting time
        name_wait = name + '_seq'
        wait_block, wait_ensemble = self._get_basic_wait_ensemble(name_wait, basic_length)

        # the proper list is generated only with += after using this function!
        created_blocks += wait_block
        created_ensembles += wait_ensemble

        wait_block, wait_ensemble = self._get_basic_wait_ensemble(name_wait + '-wait', basic_length)
        wait_seq_params = self._customize_seq_para({'repetitions': int(wait_AB/basic_length)-1,
                                                    'go_to': -1,
                                                    'event_jump_to': -1,
                                                    'event_trigger': 'OFF'})
        created_blocks += wait_block
        created_ensembles += wait_ensemble

        flag_A_block, flag_A_ensemble = self._get_basic_wait_ensemble(name_wait + '-A', basic_length)
        flag_A_seq_params = self._customize_seq_para({'repetitions': int(length_A/basic_length)-1,
                                                      'go_to': -1,
                                                      'event_jump_to': -1,
                                                      'event_trigger': 'OFF',
                                                      'flag_high': ['A']})
        created_blocks += flag_A_block
        created_ensembles += flag_A_ensemble

        flag_B_block, flag_B_ensemble = self._get_basic_wait_ensemble(name_wait + '-B', basic_length)
        flag_B_seq_params = self._customize_seq_para({'repetitions': int(length_B / basic_length)-1,
                                                      'go_to': -1,
                                                      'event_jump_to': -1,
                                                      'event_trigger': 'OFF',
                                                      'flag_high': ['B']})
        created_blocks += flag_B_block
        created_ensembles += flag_B_ensemble

        flag_C_block, flag_C_ensemble = self._get_basic_wait_ensemble(name_wait + '-C', basic_length)
        # basic length here = trigger length
        flag_C_trg_seq_params = self._customize_seq_para({'repetitions': 0,
                                                          'go_to': 1,
                                                          'event_jump_to': 1,
                                                          'event_trigger': 'OFF',
                                                          'flag_high': ['C']})
                                                        # 'flag_trigger': ['C']})
        created_blocks += flag_C_block
        created_ensembles += flag_C_ensemble

        # composing the sequence:
        flags_pulse_params_list.append([name_wait + '-A', flag_A_seq_params])
        flags_pulse_params_list.append([name_wait + '-wait', wait_seq_params])
        flags_pulse_params_list.append([name_wait + '-B', flag_B_seq_params])

        if seq_trig_C:
            flags_pulse_params_list.append([name_wait + '-C', flag_C_trg_seq_params])
        else:
            # Modify last element in the sequence list. to loop the sequence to the first element
            tmp_dict = dict(flags_pulse_params_list[-1][1])
            tmp_dict['go_to'] = 1
            flags_pulse_params_list[-1][1] = tmp_dict

        # give to PulseSequence the element_list containign serquence names and params. WHere are the correct ensembles and their names with params?
        flags_test_seq = PulseSequence(name=name, ensemble_list=flags_pulse_params_list, rotating_frame=True)

        flags_test_seq.refresh_parameters()
        created_sequences.append(flags_test_seq)

        return created_blocks, created_ensembles, created_sequences

    ###
    #To-Do for Flags pulsing
    # set correctly length of the pulse base on the basic pulse length and num of its repetitions
    # (keep in mind it can be short. take care about resolution in repetition)
    # make instrument delay elements for each pulse
    # make sync element and loop the sequence together
    # make the sequence work from external start trigger
    # check repetition num and pulse length!

    def generate_flags_pulses(self, name='flag_pulse', length_A=1e-6, delay_A = 1e-9,
                              length_B=1e-6, delay_B=1e-9, wait_AB=2e-6,
                              length_C=1e-6, delay_C=1e-9, wait_BC=1e-6,
                              basic_length=1e-6, seq_trig_D=True):
        # not sweeping any elements in this test sequence. Just consequently  turning on/off flags
        # next sequences could be sweeping one of these parameters

        # Create empty lists
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # empty list of the sequence
        flags_pulse_params_list = list()

        # creating elements to add to the sequence

        name_wait_AB = 'wait_AB'
        wait_AB_block, wait_AB_ensemble = self._get_basic_wait_ensemble(name_wait_AB, basic_length)
        wait_AB_seq_params = self._customize_seq_para({'repetitions': int(wait_AB/basic_length)-1,
                                                    'go_to': -1,
                                                    'event_jump_to': -1,
                                                    'event_trigger': 'OFF'})

        # the proper list is generated only with += after using this function!
        created_blocks += wait_AB_block
        created_ensembles += wait_AB_ensemble

        name_wait_BC = 'wait_BC'
        wait_BC_block, wait_BC_ensemble = self._get_basic_wait_ensemble(name_wait_BC, basic_length)
        wait_AB_seq_params = self._customize_seq_para({'repetitions': int(wait_BC/basic_length) - 1,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})
        created_blocks += wait_BC_block
        created_ensembles += wait_BC_ensemble

        name_pulse_A = 'flag_A'
        flag_A_block, flag_A_ensemble = self._get_basic_wait_ensemble(name_pulse_A, basic_length)
        flag_A_seq_params = self._customize_seq_para({'repetitions': int(length_A/basic_length)-1,
                                                      'go_to': -1,
                                                      'event_jump_to': -1,
                                                      'event_trigger': 'OFF',
                                                      'flag_high': ['A']})
        created_blocks += flag_A_block
        created_ensembles += flag_A_ensemble

        name_pulse_B = 'flag_B'
        flag_B_block, flag_B_ensemble = self._get_basic_wait_ensemble(name_pulse_B, basic_length)
        flag_B_seq_params = self._customize_seq_para({'repetitions': int(length_B / basic_length)-1,
                                                      'go_to': -1,
                                                      'event_jump_to': -1,
                                                      'event_trigger': 'OFF',
                                                      'flag_high': ['B']})
        created_blocks += flag_B_block
        created_ensembles += flag_B_ensemble

        name_pulse_C = 'flag_C'
        flag_C_block, flag_C_ensemble = self._get_basic_wait_ensemble(name_pulse_C, basic_length)
        flag_C_seq_params = self._customize_seq_para({'repetitions': int(length_C / basic_length)-1,
                                                          'go_to': -1,
                                                          'event_jump_to': -1,
                                                          'event_trigger': 'OFF',
                                                          'flag_high': ['C']})
        created_blocks += flag_C_block
        created_ensembles += flag_C_ensemble

        name_trg_D = 'flag_trigger_D'
        flag_D_block, flag_D_ensemble = self._get_basic_wait_ensemble(name_trg_D, basic_length)
        # basic length here = trigger length
        flag_D_trg_seq_params = self._customize_seq_para({'repetitions': 0,
                                                          'go_to': 1,
                                                          'event_jump_to': 1,
                                                          'event_trigger': 'OFF',
                                                          'flag_high': ['D']})
        created_blocks += flag_D_block
        created_ensembles += flag_D_ensemble

        # composing the sequence:
        flags_pulse_params_list.append([name_pulse_A, flag_A_seq_params])
        # make delay A
        flags_pulse_params_list.append([name_wait_AB, wait_AB_seq_params])
        flags_pulse_params_list.append([name_pulse_B, flag_B_seq_params])
        # make delay B
        flags_pulse_params_list.append([name_wait_BC, wait_AB_seq_params])
        flags_pulse_params_list.append([name_pulse_C, flag_C_seq_params])
        # make delay C

        if seq_trig_D:
            flags_pulse_params_list.append([name_trg_D, flag_D_trg_seq_params])
        else:
            # Modify last element in the sequence list. to loop the sequence to the first element
            tmp_dict = dict(flags_pulse_params_list[-1][1])
            tmp_dict['go_to'] = 1
            flags_pulse_params_list[-1][1] = tmp_dict

        # give to PulseSequence the element_list containign serquence names and params. WHere are the correct ensembles and their names with params?
        flags_test_seq = PulseSequence(name=name, ensemble_list=flags_pulse_params_list, rotating_frame=True)

        flags_test_seq.refresh_parameters()
        created_sequences.append(flags_test_seq)

        return created_blocks, created_ensembles, created_sequences

    def generate_flag_A_tau_sweep(self, name='flag_sweep', tau_start=1.0e-6, tau_max=1.0e-4, num_of_points=10, tau_log=False,
                                  length_A=1e-6, delay_A=100e-9,
                                  length_B=1e-6, delay_B=100e-9, wait_AB=2e-6,
                                  length_C=1e-6, delay_C=100e-9, wait_BC=1e-6,
                                  basic_length=1e-6, last_delay=100*1e-9, seq_trig_D=True):
        # Add wait time between init pulse Flag A and the last reaodut pulse Flag A. Otherwise overlapping of two pulses Flag A
        #
        # sequences sweeping tau after picosecond pulse
        # make array of tau steps
        # tau_array = tau_start + np.arange(num_of_points) * tau_step

        # Note that the number of points and the position of the last point can change here.
        if tau_log:
            k_array = np.unique(
                np.rint(np.logspace(0., np.log10(tau_max / tau_start), num_of_points)).astype(int))
        else:
            k_array = np.unique(np.rint(np.linspace(0., tau_max / tau_start, num_of_points)).astype(int))

        # get tau array for measurement ticks
        print(k_array)

        tau_array = k_array * tau_start

        # Create empty lists
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # empty list of the sequence
        flags_pulse_params_list = list()

        # creating elements to add to the sequence

        name_wait_AB = 'wait_AB'
        wait_AB_block, wait_AB_ensemble = self._get_basic_wait_ensemble(name_wait_AB, basic_length)
        wait_AB_seq_params = self._customize_seq_para({'repetitions': int(wait_AB/basic_length)-1,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})

        # the proper list is generated only with += after using this function!
        created_blocks += wait_AB_block
        created_ensembles += wait_AB_ensemble

        name_wait_BC = 'wait_BC'
        wait_BC_block, wait_BC_ensemble = self._get_basic_wait_ensemble(name_wait_BC, basic_length)
        wait_AB_seq_params = self._customize_seq_para({'repetitions': int(wait_BC/basic_length) - 1,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})
        created_blocks += wait_BC_block
        created_ensembles += wait_BC_ensemble

        name_pulse_A = 'flag_A'
        flag_A_block, flag_A_ensemble = self._get_basic_wait_ensemble(name_pulse_A, basic_length)
        flag_A_seq_params = self._customize_seq_para({'repetitions': int(length_A/basic_length)-1,
                                                      'go_to': -1,
                                                      'event_jump_to': -1,
                                                      'event_trigger': 'OFF',
                                                      'flag_high': ['A']})
        created_blocks += flag_A_block
        created_ensembles += flag_A_ensemble

        # delay
        name_delay_A = 'flag_A_delay'
        delay_A_block, delay_A_ensemble = self._get_basic_wait_ensemble(name_delay_A, delay_A)
        delay_A_seq_params = self._customize_seq_para({'repetitions': 0,
                                                      'go_to': -1,
                                                      'event_jump_to': -1,
                                                      'event_trigger': 'OFF'})
        created_blocks += delay_A_block
        created_ensembles += delay_A_ensemble

        name_pulse_B = 'flag_B'
        flag_B_block, flag_B_ensemble = self._get_basic_wait_ensemble(name_pulse_B, basic_length)
        flag_B_seq_params = self._customize_seq_para({'repetitions': int(length_B / basic_length)-1,
                                                      'go_to': -1,
                                                      'event_jump_to': -1,
                                                      'event_trigger': 'OFF',
                                                      'flag_high': ['B']})
        created_blocks += flag_B_block
        created_ensembles += flag_B_ensemble

        name_delay_B = 'flag_B_delay'
        delay_B_block, delay_B_ensemble = self._get_basic_wait_ensemble(name_delay_B, delay_B)
        delay_B_seq_params = self._customize_seq_para({'repetitions': 0,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})
        created_blocks += delay_B_block
        created_ensembles += delay_B_ensemble

        name_pulse_C = 'flag_C'
        flag_C_block, flag_C_ensemble = self._get_basic_wait_ensemble(name_pulse_C, basic_length)
        flag_C_seq_params = self._customize_seq_para({'repetitions': int(length_C / basic_length)-1,
                                                          'go_to': -1,
                                                          'event_jump_to': -1,
                                                          'event_trigger': 'OFF',
                                                          'flag_high': ['C']})
        created_blocks += flag_C_block
        created_ensembles += flag_C_ensemble

        name_delay_C = 'flag_C_delay'
        delay_C_block, delay_C_ensemble = self._get_basic_wait_ensemble(name_delay_C, delay_C)
        delay_C_seq_params = self._customize_seq_para({'repetitions': 0,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})
        created_blocks += delay_C_block
        created_ensembles += delay_C_ensemble

        name_trg_D = 'flag_trigger_D'
        flag_D_block, flag_D_ensemble = self._get_basic_wait_ensemble(name_trg_D, basic_length)
        # basic length here = trigger length
        flag_D_trg_seq_params = self._customize_seq_para({'repetitions': 0,
                                                          'go_to': 1,
                                                          'event_jump_to': 1,
                                                          'event_trigger': 'OFF',
                                                          'flag_high': ['D']})
        created_blocks += flag_D_block
        created_ensembles += flag_D_ensemble

        name_last_delay = 'last_delay'
        last_delay_block, last_delay_ensemble = self._get_basic_wait_ensemble(name_last_delay, last_delay)
        last_delay_seq_params = self._customize_seq_para({'repetitions': 0,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})
        created_blocks += last_delay_block
        created_ensembles += last_delay_ensemble

        # composing the sequence:
        # for num, tau in enumerate(tau_array):
        for k in k_array:
            name_tau_step = name + '_tau_wait_' + str(k)
            if k > 0:
                repetitions = int(k) - 1
            else:
                repetitions = 0

            # first delay element starting from tau_start
            tau_block, tau_ensemble = self._get_basic_wait_ensemble(name_tau_step, tau_start)
            wait_seq_params = self._customize_seq_para({'repetitions': repetitions,
                                                        'go_to': -1,
                                                        'event_jump_to': -1,
                                                        'event_trigger': 'OFF'})
            created_blocks += tau_block
            created_ensembles += tau_ensemble

            # composing the list of the sequence:
            flags_pulse_params_list.append([name_pulse_A, flag_A_seq_params])
            flags_pulse_params_list.append([name_delay_A, delay_A_seq_params])
            flags_pulse_params_list.append([name_wait_AB, wait_AB_seq_params])

            flags_pulse_params_list.append([name_pulse_B, flag_B_seq_params])
            flags_pulse_params_list.append([name_delay_B, delay_B_seq_params])
            flags_pulse_params_list.append([name_wait_BC, wait_AB_seq_params])

            flags_pulse_params_list.append([name_pulse_C, flag_C_seq_params])
            flags_pulse_params_list.append([name_delay_C, delay_C_seq_params])

            # than pulse A again (readout) with swept wait time
            flags_pulse_params_list.append([name_tau_step, wait_seq_params])
            flags_pulse_params_list.append([name_pulse_A, flag_A_seq_params])
            flags_pulse_params_list.append([name_delay_A, delay_A_seq_params])
            flags_pulse_params_list.append([name_last_delay, last_delay_seq_params])

        if seq_trig_D:
            flags_pulse_params_list.append([name_trg_D, flag_D_trg_seq_params])
        else:
            # Modify last element in the sequence list. to loop the sequence to the first element
            tmp_dict = dict(flags_pulse_params_list[-1][1])
            tmp_dict['go_to'] = 1
            flags_pulse_params_list[-1][1] = tmp_dict

        # give to PulseSequence the element_list containign serquence names and params. WHere are the correct ensembles and their names with params?
        flags_test_seq = PulseSequence(name=name, ensemble_list=flags_pulse_params_list, rotating_frame=True)

        flags_test_seq.refresh_parameters()
        created_sequences.append(flags_test_seq)

        return created_blocks, created_ensembles, created_sequences

    def _get_single_mw_pi_ensemble(self, name):

        created_blocks = list()
        created_ensembles = list()

        # adjust sampling rate
        tau_mw_pi = self.rabi_period / 2
        tau_mw_pi = self._adjust_to_samplingrate(tau_mw_pi, 2)

        # Getting necessary pulse block elements
        microwave_element = self._get_mw_element(length=tau_mw_pi,
                                                 increment=0,
                                                 amp=self.microwave_amplitude,
                                                 freq=self.microwave_frequency,
                                                 phase=0)

        mw_pi_block = PulseBlock(name=name)
        mw_pi_block.append(microwave_element)
        created_blocks.append(mw_pi_block)

        mw_pi_ensemble = PulseBlockEnsemble(name=name, rotating_frame=False)
        mw_pi_ensemble.append((mw_pi_ensemble.name, 0))  # (name, num of repetitions)
        created_ensembles.append(mw_pi_ensemble)
        return created_blocks, created_ensembles

    def generate_flag_A_tau_sweep_mw(self, name='flag_sweep', tau_start=1.0e-6, tau_max=1.0e-4, num_of_points=10, tau_log=False,
                                  length_A=1e-6, delay_A=100e-9,
                                  length_B=1e-6, delay_B=100e-9, wait_AB=2e-6,
                                  length_C=1e-6, delay_C=100e-9, wait_BC=1e-6,
                                  basic_length=1e-6, last_delay=100*1e-9, seq_trig_D=True):

        # MW element is introduced after init pulse. Like this the sampling will fail because single mw element is too short for the sequence
        # One can add more waitint tim eint he sequence or make a sequence including init pulse and concecutive mw pulse and shelving in one sequence.
        # Other pulses can be in separate sequences.

        # sequences sweeping tau after picosecond pulse
        # make array of tau steps
        # tau_array = tau_start + np.arange(num_of_points) * tau_step

        # Note that the number of points and the position of the last point can change here.
        if tau_log:
            k_array = np.unique(
                np.rint(np.logspace(0., np.log10(tau_max / tau_start), num_of_points)).astype(int))
        else:
            k_array = np.unique(np.rint(np.linspace(0., tau_max / tau_start, num_of_points)).astype(int))

        # get tau array for measurement ticks
        print(k_array)

        tau_array = k_array * tau_start

        # Create empty lists
        created_blocks = list()
        created_ensembles = list()
        created_sequences = list()

        # empty list of the sequence
        flags_pulse_params_list = list()

        # creating elements to add to the sequence

        # Microwave pi pulse from self params
        name_mw_pi = 'mw_pi_pulse'
        mw_pi_block, mw_pi_ensemble = self._get_single_mw_pi_ensemble(name_mw_pi)
        mw_pi_seq_params = self._customize_seq_para({'repetitions': 0,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})
        created_blocks += mw_pi_block
        created_ensembles += mw_pi_ensemble

        name_wait_AB = 'wait_AB'
        wait_AB_block, wait_AB_ensemble = self._get_basic_wait_ensemble(name_wait_AB, basic_length)
        wait_AB_seq_params = self._customize_seq_para({'repetitions': int(wait_AB/basic_length)-1,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})

        # the proper list is generated only with += after using this function!
        created_blocks += wait_AB_block
        created_ensembles += wait_AB_ensemble

        name_wait_BC = 'wait_BC'
        wait_BC_block, wait_BC_ensemble = self._get_basic_wait_ensemble(name_wait_BC, basic_length)
        wait_AB_seq_params = self._customize_seq_para({'repetitions': int(wait_BC/basic_length) - 1,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})
        created_blocks += wait_BC_block
        created_ensembles += wait_BC_ensemble

        name_pulse_A = 'flag_A'
        flag_A_block, flag_A_ensemble = self._get_basic_wait_ensemble(name_pulse_A, basic_length)
        flag_A_seq_params = self._customize_seq_para({'repetitions': int(length_A/basic_length)-1,
                                                      'go_to': -1,
                                                      'event_jump_to': -1,
                                                      'event_trigger': 'OFF',
                                                      'flag_high': ['A']})
        created_blocks += flag_A_block
        created_ensembles += flag_A_ensemble

        # delay
        name_delay_A = 'flag_A_delay'
        delay_A_block, delay_A_ensemble = self._get_basic_wait_ensemble(name_delay_A, delay_A)
        delay_A_seq_params = self._customize_seq_para({'repetitions': 0,
                                                      'go_to': -1,
                                                      'event_jump_to': -1,
                                                      'event_trigger': 'OFF'})
        created_blocks += delay_A_block
        created_ensembles += delay_A_ensemble

        name_pulse_B = 'flag_B'
        flag_B_block, flag_B_ensemble = self._get_basic_wait_ensemble(name_pulse_B, basic_length)
        flag_B_seq_params = self._customize_seq_para({'repetitions': int(length_B / basic_length)-1,
                                                      'go_to': -1,
                                                      'event_jump_to': -1,
                                                      'event_trigger': 'OFF',
                                                      'flag_high': ['B']})
        created_blocks += flag_B_block
        created_ensembles += flag_B_ensemble

        name_delay_B = 'flag_B_delay'
        delay_B_block, delay_B_ensemble = self._get_basic_wait_ensemble(name_delay_B, delay_B)
        delay_B_seq_params = self._customize_seq_para({'repetitions': 0,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})
        created_blocks += delay_B_block
        created_ensembles += delay_B_ensemble

        name_pulse_C = 'flag_C'
        flag_C_block, flag_C_ensemble = self._get_basic_wait_ensemble(name_pulse_C, basic_length)
        flag_C_seq_params = self._customize_seq_para({'repetitions': int(length_C / basic_length)-1,
                                                          'go_to': -1,
                                                          'event_jump_to': -1,
                                                          'event_trigger': 'OFF',
                                                          'flag_high': ['C']})
        created_blocks += flag_C_block
        created_ensembles += flag_C_ensemble

        name_delay_C = 'flag_C_delay'
        delay_C_block, delay_C_ensemble = self._get_basic_wait_ensemble(name_delay_C, delay_C)
        delay_C_seq_params = self._customize_seq_para({'repetitions': 0,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})
        created_blocks += delay_C_block
        created_ensembles += delay_C_ensemble

        name_trg_D = 'flag_trigger_D'
        flag_D_block, flag_D_ensemble = self._get_basic_wait_ensemble(name_trg_D, basic_length)
        # basic length here = trigger length
        flag_D_trg_seq_params = self._customize_seq_para({'repetitions': 0,
                                                          'go_to': 1,
                                                          'event_jump_to': 1,
                                                          'event_trigger': 'OFF',
                                                          'flag_high': ['D']})
        created_blocks += flag_D_block
        created_ensembles += flag_D_ensemble

        name_last_delay = 'last_delay'
        last_delay_block, last_delay_ensemble = self._get_basic_wait_ensemble(name_last_delay, last_delay)
        last_delay_seq_params = self._customize_seq_para({'repetitions': 0,
                                                       'go_to': -1,
                                                       'event_jump_to': -1,
                                                       'event_trigger': 'OFF'})
        created_blocks += last_delay_block
        created_ensembles += last_delay_ensemble

        # composing the sequence:
        # for num, tau in enumerate(tau_array):
        for k in k_array:
            name_tau_step = name + '_tau_wait_' + str(k)
            if k > 0:
                repetitions = int(k) - 1
            else:
                repetitions = 0

            # first delay element starting from tau_start
            tau_block, tau_ensemble = self._get_basic_wait_ensemble(name_tau_step, tau_start)
            wait_seq_params = self._customize_seq_para({'repetitions': repetitions,
                                                        'go_to': -1,
                                                        'event_jump_to': -1,
                                                        'event_trigger': 'OFF'})
            created_blocks += tau_block
            created_ensembles += tau_ensemble

            # composing the list of the sequence:
            flags_pulse_params_list.append([name_pulse_A, flag_A_seq_params])
            flags_pulse_params_list.append([name_delay_A, delay_A_seq_params])
            flags_pulse_params_list.append([name_wait_AB, wait_AB_seq_params])

            # MW pi pulse. Decide between which pulses mw_pi_seq_params
            flags_pulse_params_list.append([name_mw_pi, mw_pi_seq_params])

            flags_pulse_params_list.append([name_pulse_B, flag_B_seq_params])
            flags_pulse_params_list.append([name_delay_B, delay_B_seq_params])
            flags_pulse_params_list.append([name_wait_BC, wait_AB_seq_params])

            flags_pulse_params_list.append([name_pulse_C, flag_C_seq_params])
            flags_pulse_params_list.append([name_delay_C, delay_C_seq_params])

            # than pulse A again (readout) with swept wait time
            flags_pulse_params_list.append([name_tau_step, wait_seq_params])
            flags_pulse_params_list.append([name_pulse_A, flag_A_seq_params])
            flags_pulse_params_list.append([name_delay_A, delay_A_seq_params])
            flags_pulse_params_list.append([name_last_delay, last_delay_seq_params])

        if seq_trig_D:
            flags_pulse_params_list.append([name_trg_D, flag_D_trg_seq_params])
        else:
            # Modify last element in the sequence list. to loop the sequence to the first element
            tmp_dict = dict(flags_pulse_params_list[-1][1])
            tmp_dict['go_to'] = 1
            flags_pulse_params_list[-1][1] = tmp_dict

        # give to PulseSequence the element_list containign serquence names and params. WHere are the correct ensembles and their names with params?
        flags_test_seq = PulseSequence(name=name, ensemble_list=flags_pulse_params_list, rotating_frame=True)

        flags_test_seq.refresh_parameters()
        created_sequences.append(flags_test_seq)

        return created_blocks, created_ensembles, created_sequences
