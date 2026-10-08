# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware module for AWG70000 Series, with configurable run modes
(continuous / triggered / triggered-continuous) and a faster upload path.

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


import os
import shutil
import tempfile
import time
try:
    import pyvisa as visa
except ImportError:
    import visa
import numpy as np
from ftplib import FTP, error_perm, all_errors as ftp_errors
from lxml import etree as ET

from qudi.core.configoption import ConfigOption
from qudi.util.helpers import natural_sort
from qudi.interface.pulser_interface import PulserInterface, PulserConstraints, SequenceOption


class AWG70K(PulserInterface):
    """ A hardware module for the Tektronix AWG70000 series for generating
        waveforms and sequences thereof.

    Compared to tektronix_awg70k.py this module
      - sets the channel run mode itself (config option run_mode), so the AWG can be armed and
        triggered externally, e.g. by a PulseBlaster through the AWG/PulseBlaster interfuse,
      - uploads considerably faster: one persistent FTP session, .wfmx files built in memory,
        no waveform list polling, cached channel state / sample rate, and sequence steps sent in
        batches instead of one VISA write per step parameter.

    Run modes (run_mode config option, applied to all channels a waveform/sequence is loaded to):
        'CONT'  continuous: playback starts on pulser_on() and loops freely (default).
        'TRIG'  triggered: pulser_on() arms the AWG (AWGC:RST? = 1), every trigger edge plays the
                loaded waveform once. For a loaded sequence, step 1 is set to wait for the trigger
                instead, so every trigger plays the sequence once (same as the AWG7000 SEQ mode).
        'TCON'  triggered continuous: pulser_on() arms the AWG, the first trigger edge starts
                continuous playback.
    The AWG70000 series has no gated run mode.

    Example config for copy-paste:

    pulser_awg70000:
        module.Class: 'awg.tektronix_awg70k_seq.AWG70K'
        options:
            awg_visa_address: 'TCPIP::10.42.0.211::INSTR'
            awg_ip_address: '10.42.0.211'
            timeout: 60
            # ftp_root_dir: 'C:\\inetpub\\ftproot' # optional, root directory on AWG device
            # ftp_login: 'anonymous' # optional, the username for ftp login
            # ftp_passwd: 'anonymous@' # optional, the password for ftp login
            #
            # run_mode: 'CONT'   # default, free-running, starts immediately on pulser_on()
            # run_mode: 'TRIG'   # armed by pulser_on(), one trigger edge plays the waveform once
            # run_mode: 'TCON'   # armed by pulser_on(), first trigger edge starts continuous play
            #
            # Only used when run_mode is 'TRIG' or 'TCON'. Options left out keep the setting
            # currently stored on the AWG.
            # trigger_input: 'A'         # 'A', 'B' (trigger BNCs) or 'INT' (internal trigger)
            # trigger_level: 1.4         # detection threshold in Volts
            # trigger_slope: 'POS'       # 'POS' rising edge  |  'NEG' falling edge
            # trigger_impedance: '50OHM' # '50OHM'  |  '1KOHM'
            # trigger_timing: 'ASYNC'    # 'ASYNC' (lowest latency)  |  'SYNC' (lowest jitter)
            # internal_trigger_interval: 1e-3  # seconds, only for trigger_input 'INT'
            #
            # clear_device_before_upload: False  # if True, wipe all waveforms and sequences
            # # from AWG memory once before each upload batch (re-armed after load_waveform()/
            # # load_sequence()).
            # import_timeout: 300  # seconds to wait for the AWG to import one .wfmx file
    """

    # config options
    _visa_address = ConfigOption(name='awg_visa_address', missing='error')
    _ip_address = ConfigOption(name='awg_ip_address', missing='error')
    _visa_timeout = ConfigOption(name='timeout', default=30, missing='nothing')
    _ftp_dir = ConfigOption(name='ftp_root_dir', default='C:\\inetpub\\ftproot', missing='warn')
    _username = ConfigOption(name='ftp_login', default='anonymous', missing='warn')
    _password = ConfigOption(name='ftp_passwd', default='anonymous@', missing='warn')
    _run_mode_config = ConfigOption(name='run_mode', default='CONT', missing='nothing')
    _trigger_input = ConfigOption(name='trigger_input', default='A', missing='nothing')
    _trigger_level = ConfigOption(name='trigger_level', default=None, missing='nothing')
    _trigger_slope = ConfigOption(name='trigger_slope', default=None, missing='nothing')
    _trigger_impedance = ConfigOption(name='trigger_impedance', default=None, missing='nothing')
    _trigger_timing = ConfigOption(name='trigger_timing', default=None, missing='nothing')
    _internal_trigger_interval = ConfigOption(name='internal_trigger_interval', default=None,
                                              missing='nothing')
    _clear_device_before_upload = ConfigOption(name='clear_device_before_upload', default=False,
                                               missing='nothing')
    _import_timeout = ConfigOption(name='import_timeout', default=300, missing='nothing')

    # translation dict from qudi trigger descriptor to device command. 'ON' is accepted for
    # compatibility with the AWG7000 module (used by the AWG/PulseBlaster interfuse) and resolves
    # to the configured trigger_input.
    __event_triggers = {'OFF': 'OFF', 'A': 'ATR', 'B': 'BTR', 'INT': 'ITR'}

    # Sequence steps sent per VISA write, followed by an *OPC? / error queue checkpoint.
    _SEQ_STEPS_PER_WRITE = 50

    # .wfmx data larger than this is spooled to a temporary file instead of being kept in RAM.
    _WFMX_SPOOL_BYTES = 64 * 2**20

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Get an instance of the visa resource manager
        self._rm = visa.ResourceManager()

        self.awg = None  # This variable will hold a reference to the awg visa resource
        self.awg_model = ''  # String describing the model

        self.ftp_working_dir = 'waves'  # subfolder of FTP root dir on AWG disk to work in

        self.__max_seq_steps = 0
        self.__max_seq_repetitions = 0
        self.__min_waveform_length = 0
        self.__max_waveform_length = 0
        self.__installed_options = list()

        self._pending_clear_before_upload = True
        # Name of the sequence written last. Target of the AWG7000-style
        # sequence_set_wait_trigger(step, trigger) call used by the interfuse.
        self._last_written_sequence = None

        # Upload speed-ups (see write_waveform / write_sequence):
        # Persistent FTP session, reconnected on demand by _ftp_run().
        self._ftp = None
        # Local mirror of the AWG waveform list. None = unknown, re-read from the device on
        # the next get_waveform_names() call. Kept in sync on import/delete/clear/reset.
        self._waveform_names_cache = None
        # Sample rate used in the .wfmx header, so it is not queried for every waveform.
        self._sample_rate_cache = None
        # Channel activation as last read from / written to the device. None = unknown.
        self._active_channels_cache = None
        # .wfmx file parts per waveform, while a chunked write is in progress.
        self._wfmx_buffers = dict()
        # True once the sequencer step defaults have been confirmed on this device.
        self._sequence_defaults_verified = False
        # Timing statistics of the current upload batch, logged by _log_upload_stats().
        self._upload_stats = None
        # Trigger settings last logged, so pulser_on() only logs them when they change.
        self._last_trigger_log = None
        return

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        try:
            self.awg = self._rm.open_resource(self._visa_address)
            self.awg.timeout = self._visa_timeout * 1000
        except Exception:
            self.awg = None
            self.log.error('VISA address "{0}" not found by the pyVISA resource manager.\nCheck '
                           'the connection by using for example "Agilent Connection Expert".'
                           ''.format(self._visa_address))

        # try connecting to AWG using FTP protocol
        try:
            ftp = self._ftp_connect()
            self.log.debug('FTP working dir: {0}'.format(ftp.pwd()))
        except ftp_errors as exc:
            self.log.error('FTP connection to AWG at "{0}" failed: {1}\nUploading waveforms will '
                           'not work until this is fixed.'.format(self._ip_address, exc))

        self._waveform_names_cache = None
        self._sample_rate_cache = None
        self._active_channels_cache = None
        self._wfmx_buffers = dict()
        self._sequence_defaults_verified = False
        self._upload_stats = None
        self._last_trigger_log = None
        self._last_written_sequence = None

        if self.awg is not None:
            self.awg_model = self.query('*IDN?').split(',')[1]
        else:
            self.awg_model = ''

        # Query some constraints from the device and stash them in order to avoid redundant queries.
        self.__max_seq_steps = int(self.query('SLIS:SEQ:STEP:MAX?'))
        self.__max_seq_repetitions = int(self.query('SLIS:SEQ:STEP:RCO:MAX?'))
        self.__min_waveform_length = int(self.query('WLIS:WAV:LMIN?'))
        self.__max_waveform_length = int(self.query('WLIS:WAV:LMAX?'))

        self.__installed_options = self.query('*OPT?').split(',')
        self._drain_stale_errors('at module activation')

        mode = self._get_run_mode()
        self.log.info('Found {0}, options: {1}, run_mode: {2}{3}'.format(
            self.awg_model, self.__installed_options, mode,
            '' if mode == 'CONT' else ', trigger_input: {0}'.format(self._trigger_input)))
        return

    def on_deactivate(self):
        """ Required tasks to be performed during deactivation of the module.
        """
        self._ftp_close()
        for buffers in self._wfmx_buffers.values():
            self._close_wfmx_buffers(buffers)
        self._wfmx_buffers = dict()
        # Closes the connection to the AWG
        try:
            self.awg.close()
        except:
            self.log.debug('Closing AWG connection using pyvisa failed.')
        self.log.info('Closed connection to AWG')
        return

    def get_constraints(self):
        """
        Retrieve the hardware constrains from the Pulsing device.

        @return constraints object: object with pulser constraints as attributes.

        Provides all the constraints (e.g. sample_rate, amplitude, total_length_bins,
        channel_config, ...) related to the pulse generator hardware to the caller.

            SEE PulserConstraints CLASS IN pulser_interface.py FOR AVAILABLE CONSTRAINTS!!!

        If you are not sure about the meaning, look in other hardware files to get an impression.
        If still additional constraints are needed, then they have to be added to the
        PulserConstraints class.

        Each scalar parameter is an ScalarConstraints object defined in cor.util.interfaces.
        Essentially it contains min/max values as well as min step size, default value and unit of
        the parameter.

        PulserConstraints.activation_config differs, since it contain the channel
        configuration/activation information of the form:
            {<descriptor_str>: <channel_set>,
             <descriptor_str>: <channel_set>,
             ...}

        If the constraints cannot be set in the pulsing hardware (e.g. because it might have no
        sequence mode) just leave it out so that the default is used (only zeros).
        """
        constraints = PulserConstraints()

        if self.awg_model in ['AWG70002A', 'AWG70002B']:
            constraints.sample_rate.min = 1.5e3
            constraints.sample_rate.max = 25.0e9
            constraints.sample_rate.step = 5.0e2
            constraints.sample_rate.default = 25.0e9
        elif self.awg_model in ['AWG70001A', 'AWG70001B']:
            constraints.sample_rate.min = 1.49e3
            constraints.sample_rate.max = 50.0e9
            constraints.sample_rate.step = 10
            constraints.sample_rate.default = 50.0e9

        constraints.a_ch_amplitude.min = 0.25
        constraints.a_ch_amplitude.max = 0.5
        constraints.a_ch_amplitude.step = 0.0001
        constraints.a_ch_amplitude.default = 0.5

        constraints.d_ch_low.min = -1.4
        constraints.d_ch_low.max = 0.9
        constraints.d_ch_low.step = 0.1e-3
        constraints.d_ch_low.default = 0.0

        constraints.d_ch_high.min = -0.9
        constraints.d_ch_high.max = 1.4
        constraints.d_ch_high.step = 0.1e-3
        constraints.d_ch_high.default = 1.4

        constraints.waveform_length.min = self.__min_waveform_length
        constraints.waveform_length.max = self.__max_waveform_length
        if self.awg_model in ['AWG70002A', 'AWG70002B']:
            constraints.waveform_length.step = 1
            constraints.waveform_length.default = self.__min_waveform_length
        elif self.awg_model in ['AWG70001A', 'AWG70001B']:
            constraints.waveform_length.step = 2
            constraints.waveform_length.default = self.__min_waveform_length

        # FIXME: Check the proper number for your device
        constraints.waveform_num.min = 1
        constraints.waveform_num.max = 32000
        constraints.waveform_num.step = 1
        constraints.waveform_num.default = 1
        # FIXME: Check the proper number for your device
        constraints.sequence_num.min = 1
        constraints.sequence_num.max = 4000
        constraints.sequence_num.step = 1
        constraints.sequence_num.default = 1
        # FIXME: Check the proper number for your device
        constraints.subsequence_num.min = 1
        constraints.subsequence_num.max = 8000
        constraints.subsequence_num.step = 1
        constraints.subsequence_num.default = 1

        # If sequencer mode is available then these should be specified
        constraints.repetitions.min = 0
        constraints.repetitions.max = self.__max_seq_repetitions
        constraints.repetitions.step = 1
        constraints.repetitions.default = 0
        constraints.event_triggers = ['OFF', 'A', 'B', 'INT']
        constraints.flags = ['A', 'B', 'C', 'D']

        constraints.sequence_steps.min = 0
        constraints.sequence_steps.max = self.__max_seq_steps
        constraints.sequence_steps.step = 1
        constraints.sequence_steps.default = 0

        # the name a_ch<num> and d_ch<num> are generic names, which describe UNAMBIGUOUSLY the
        # channels. Here all possible channel configurations are stated, where only the generic
        # names should be used. The names for the different configurations can be customary chosen.
        activation_config = dict()
        if self.awg_model in ['AWG70002A', 'AWG70002B']:
            activation_config['all'] = frozenset(
                {'a_ch1', 'd_ch1', 'd_ch2', 'a_ch2', 'd_ch3', 'd_ch4'})
            # Usage of both channels but reduced markers (higher analog resolution)
            activation_config['ch1_2mrk_ch2_1mrk'] = frozenset(
                {'a_ch1', 'd_ch1', 'd_ch2', 'a_ch2', 'd_ch3'})
            activation_config['ch1_2mrk_ch2_0mrk'] = frozenset({'a_ch1', 'd_ch1', 'd_ch2', 'a_ch2'})
            activation_config['ch1_1mrk_ch2_2mrk'] = frozenset(
                {'a_ch1', 'd_ch1', 'a_ch2', 'd_ch3', 'd_ch4'})
            activation_config['ch1_0mrk_ch2_2mrk'] = frozenset({'a_ch1', 'a_ch2', 'd_ch3', 'd_ch4'})
            activation_config['ch1_1mrk_ch2_1mrk'] = frozenset({'a_ch1', 'd_ch1', 'a_ch2', 'd_ch3'})
            activation_config['ch1_0mrk_ch2_1mrk'] = frozenset({'a_ch1', 'a_ch2', 'd_ch3'})
            activation_config['ch1_1mrk_ch2_0mrk'] = frozenset({'a_ch1', 'd_ch1', 'a_ch2'})
            # Usage of channel 1 only:
            activation_config['ch1_2mrk'] = frozenset({'a_ch1', 'd_ch1', 'd_ch2'})
            # Usage of channel 2 only:
            activation_config['ch2_2mrk'] = frozenset({'a_ch2', 'd_ch3', 'd_ch4'})
            # Usage of only channel 1 with one marker:
            activation_config['ch1_1mrk'] = frozenset({'a_ch1', 'd_ch1'})
            # Usage of only channel 2 with one marker:
            activation_config['ch2_1mrk'] = frozenset({'a_ch2', 'd_ch3'})
            # Usage of only channel 1 with no marker:
            activation_config['ch1_0mrk'] = frozenset({'a_ch1'})
            # Usage of only channel 2 with no marker:
            activation_config['ch2_0mrk'] = frozenset({'a_ch2'})
        elif self.awg_model in ['AWG70001A', 'AWG70001B']:
            activation_config['all'] = frozenset({'a_ch1', 'd_ch1', 'd_ch2'})
            # Usage of only channel 1 with one marker:
            activation_config['ch1_1mrk'] = frozenset({'a_ch1', 'd_ch1'})
            # Usage of only channel 1 with no marker:
            activation_config['ch1_0mrk'] = frozenset({'a_ch1'})

        constraints.activation_config = activation_config
        if self._has_sequence_mode():
            constraints.sequence_option = SequenceOption.OPTIONAL
        else:
            constraints.sequence_option = SequenceOption.NON

        # FIXME: additional constraint really necessary?
        constraints.dac_resolution = {'min': 8, 'max': 10, 'step': 1, 'unit': 'bit'}
        return constraints

    def pulser_on(self):
        """ Switches the pulsing device on.

        Applies the configured run mode to all channels with a loaded asset, then starts the AWG.
        In 'TRIG'/'TCON' mode the AWG ends up armed and waiting for a trigger (status 1); in
        'CONT' mode it is running (status 2).

        @return int: error code (-1: error, otherwise the status of the device, see get_status)
        """
        # do nothing if AWG is already running or armed
        if self._is_output_on():
            return self.get_status()[0]

        mode = self._get_run_mode()
        loaded_assets, asset_type = self.get_loaded_assets()
        channel_numbers = sorted(loaded_assets)
        if not channel_numbers:
            self.log.warning('pulser_on: no waveform or sequence loaded on any active channel.')

        if mode != 'CONT':
            self._configure_trigger_input(channel_numbers)
        for ch_num in channel_numbers:
            self.write('SOUR{0:d}:RMOD {1}'.format(ch_num, self._channel_run_mode(mode, asset_type)))

        self.write('AWGC:RUN')
        status = self._wait_for_status((1, 2), timeout=10.0)
        if status not in (1, 2):
            self.log.error('pulser_on: AWG did not start within 10 s (status {0}).'.format(status))
            self.get_errors()
            return -1

        if mode != 'CONT' and status == 2 and asset_type != 'sequence':
            self.log.warning(
                'pulser_on: AWG started playing right away although run_mode is "{0}". It should '
                'be armed and waiting for a trigger. Check the AWG trigger settings.'.format(mode))
        self.log.debug('pulser_on: AWG {0} (run_mode {1}, {2}).'.format(
            'armed, waiting for trigger' if status == 1 else 'running', mode, asset_type))
        return status

    def pulser_off(self):
        """ Switches the pulsing device off.

        @return int: error code (0:OK, -1:error, higher number corresponds to
                                 current status of the device. Check then the
                                 class variable status_dic.)
        """
        # do nothing if AWG is already idle
        if self._is_output_on():
            self.write('AWGC:STOP')
            if self._wait_for_status((0,), timeout=10.0) != 0:
                self.log.error('pulser_off: AWG did not stop within 10 s.')
                return -1
        return self.get_status()[0]

    def write_waveform(self, name, analog_samples, digital_samples, is_first_chunk, is_last_chunk,
                       total_number_of_samples):
        """
        Write a new waveform or append samples to an already existing waveform on the device memory.
        The flags is_first_chunk and is_last_chunk can be used as indicator if a new waveform should
        be created or if the write process to a waveform should be terminated.

        NOTE: All sample arrays in analog_samples and digital_samples must be of equal length!

        @param str name: the name of the waveform to be created/append to
        @param dict analog_samples: keys are the generic analog channel names (i.e. 'a_ch1') and
                                    values are 1D numpy arrays of type float32 containing the
                                    voltage samples.
        @param dict digital_samples: keys are the generic digital channel names (i.e. 'd_ch1') and
                                     values are 1D numpy arrays of type bool containing the marker
                                     states.
        @param bool is_first_chunk: Flag indicating if it is the first chunk to write.
                                    If True this method will create a new empty wavveform.
                                    If False the samples are appended to the existing waveform.
        @param bool is_last_chunk:  Flag indicating if it is the last chunk to write.
                                    Some devices may need to know when to close the appending wfm.
        @param int total_number_of_samples: The number of sample points for the entire waveform
                                            (not only the currently written chunk)

        @return (int, list): Number of samples written (-1 indicates failed process) and list of
                             created waveform names
        """
        waveforms = list()

        # Sanity checks
        if len(analog_samples) == 0:
            self.log.error('No analog samples passed to write_waveform method in awg70k.')
            return -1, waveforms

        # Config option: clear_device_before_upload. Wipe the AWG exactly once at the start of a
        # new upload batch, never in the middle of a chunked upload.
        if (self._clear_device_before_upload and is_first_chunk
                and self._pending_clear_before_upload):
            self.log.info('clear_device_before_upload enabled: clearing all waveforms and '
                          'sequences on AWG before uploading "{0}".'.format(name))
            self.clear_all()
            self._pending_clear_before_upload = False

        if total_number_of_samples < self.__min_waveform_length:
            self.log.error('Unable to write waveform.\nNumber of samples to write ({0:d}) is '
                           'smaller than the allowed minimum waveform length ({1:d}).'
                           ''.format(total_number_of_samples, self.__min_waveform_length))
            return -1, waveforms
        if total_number_of_samples > self.__max_waveform_length:
            self.log.error('Unable to write waveform.\nNumber of samples to write ({0:d}) is '
                           'greater than the allowed maximum waveform length ({1:d}).'
                           ''.format(total_number_of_samples, self.__max_waveform_length))
            return -1, waveforms

        # determine active channels
        activation_dict = self.get_active_channels()
        active_channels = {chnl for chnl in activation_dict if activation_dict[chnl]}
        active_analog = natural_sort(chnl for chnl in active_channels if chnl.startswith('a'))

        # Sanity check of channel numbers
        sample_channels = set(analog_samples).union(digital_samples)
        if active_channels != sample_channels:
            self.log.error('Mismatch of channel activation and sample array dimensions for '
                           'waveform creation.\nChannel activation is: {0}\nSample arrays have: {1}'
                           ''.format(active_channels, sample_channels))
            return -1, waveforms

        # Write waveforms. One for each analog channel.
        for a_ch in active_analog:
            # Get the integer analog channel number
            a_ch_num = int(a_ch.rsplit('_ch', 1)[1])
            # Get the digital channel specifiers belonging to this analog channel markers
            mrk_ch_1 = 'd_ch{0:d}'.format(a_ch_num * 2 - 1)
            mrk_ch_2 = 'd_ch{0:d}'.format(a_ch_num * 2)

            # Encode marker information in an array of bytes (uint8): bit 0 = marker 1,
            # bit 1 = marker 2. The caller's sample arrays are left untouched.
            if mrk_ch_1 in digital_samples and mrk_ch_2 in digital_samples:
                mrk_bytes = np.left_shift(digital_samples[mrk_ch_2].view('uint8'), 1)
                np.bitwise_or(mrk_bytes, digital_samples[mrk_ch_1].view('uint8'), out=mrk_bytes)
            elif mrk_ch_1 in digital_samples:
                mrk_bytes = digital_samples[mrk_ch_1].view('uint8')
            else:
                mrk_bytes = None

            # Create waveform name string
            wfm_name = '{0}_ch{1:d}'.format(name, a_ch_num)

            # Append this chunk to the in-memory .wfmx file
            start = time.time()
            wfmx_file = self._write_wfmx(filename=wfm_name + '.wfmx',
                                         analog_samples=analog_samples[a_ch],
                                         marker_bytes=mrk_bytes,
                                         is_first_chunk=is_first_chunk,
                                         is_last_chunk=is_last_chunk,
                                         total_number_of_samples=total_number_of_samples)
            self._add_upload_time('build', time.time() - start)

            # Chunked upload: the .wfmx file is only complete (and sent) on the last chunk
            if wfmx_file is None:
                waveforms.append(wfm_name)
                continue

            try:
                if not self._upload_and_import(wfm_name, wfmx_file, total_number_of_samples):
                    return -1, waveforms
            finally:
                wfmx_file.close()

            # Append created waveform name to waveform list
            waveforms.append(wfm_name)
        return total_number_of_samples, waveforms

    def _upload_and_import(self, wfm_name, wfmx_file, total_number_of_samples):
        """ Send a finished .wfmx file to the AWG, import it into the waveform list and check it.

        @return bool: True on success.
        """
        # A waveform of the same name must be removed before the new file is imported.
        if wfm_name in self._get_waveform_name_set():
            self.write('WLIS:WAV:DEL "{0}"'.format(wfm_name))
            self._waveform_names_cache.discard(wfm_name)

        start = time.time()
        n_bytes = wfmx_file.tell()
        try:
            self._send_fileobj(wfm_name + '.wfmx', wfmx_file)
        except ftp_errors as exc:
            self.log.error('write_waveform: FTP upload of "{0}" failed: {1}'.format(wfm_name, exc))
            return False
        self._add_upload_time('ftp', time.time() - start, n_bytes=n_bytes)

        # Errors still queued from earlier commands must not be blamed on this import.
        self._drain_stale_errors('before importing "{0}"'.format(wfm_name))

        start = time.time()
        self.write('MMEM:OPEN "{0}"'.format(
            os.path.join(self._ftp_dir, self.ftp_working_dir, wfm_name + '.wfmx')))
        if not self._wait_opc(timeout=self._import_timeout,
                              context='importing "{0}"'.format(wfm_name)):
            self.log.error('write_waveform: MMEM:OPEN of "{0}" did not complete. Checking error '
                           'queue...'.format(wfm_name))
            self.get_errors()
            self._waveform_names_cache = None
            return False
        self._add_upload_time('import', time.time() - start)

        # Check the error queue and confirm the import with ONE length query, instead of
        # polling the whole waveform list.
        start = time.time()
        if self.get_errors():
            self.log.error('write_waveform: AWG reported error(s) while importing "{0}". See '
                           'messages above.'.format(wfm_name))
            self._waveform_names_cache = None
            return False
        try:
            imported_length = int(self.query('WLIS:WAV:LENG? "{0}"'.format(wfm_name)))
        except Exception as exc:
            imported_length = None
            self.log.error('write_waveform: could not query length of imported waveform "{0}": '
                           '{1}'.format(wfm_name, exc))
        if imported_length != total_number_of_samples:
            self.log.error('write_waveform: "{0}" is not in the AWG waveform list with the '
                           'expected length after import (expected {1} samples, AWG reports {2}).'
                           ''.format(wfm_name, total_number_of_samples, imported_length))
            self._waveform_names_cache = None
            return False
        if self._waveform_names_cache is not None:
            self._waveform_names_cache.add(wfm_name)
        self._add_upload_time('verify', time.time() - start, n_waveforms=1)
        return True

    def write_sequence(self, name, sequence_parameter_list):
        """
        Write a new sequence on the device memory.

        @param name: str, the name of the waveform to be created/append to
        @param sequence_parameter_list: list, contains the parameters for each sequence step and
                                        the according waveform names.

        @return: int, number of sequence steps written (-1 indicates failed process)
        """
        # Check if device has sequencer option installed
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. Sequencer option not '
                           'installed.')
            return -1

        # Config option: clear_device_before_upload. Only fires if no write_waveform() call
        # already cleared the device for this batch.
        if self._clear_device_before_upload and self._pending_clear_before_upload:
            self.log.info('clear_device_before_upload enabled: clearing all waveforms and '
                          'sequences on AWG before uploading sequence "{0}".'.format(name))
            self.clear_all()
            self._pending_clear_before_upload = False

        num_steps = len(sequence_parameter_list)
        if num_steps > self.__max_seq_steps:
            self.log.error('Unable to write sequence "{0}".\nRequested {1} sequence steps exceeds '
                           'the hardware maximum of {2} steps.'
                           ''.format(name, num_steps, self.__max_seq_steps))
            return -1

        # Check if all waveforms are present on device memory
        avail_waveforms = self._get_waveform_name_set()
        for waveform_tuple, param_dict in sequence_parameter_list:
            if not avail_waveforms.issuperset(waveform_tuple):
                self.log.error('Failed to create sequence "{0}" due to waveforms "{1}" not '
                               'present in device memory.'.format(name, waveform_tuple))
                return -1

        activation = self.get_active_channels()
        channel_numbers = sorted(int(chnl.rsplit('_ch', 1)[1]) for chnl in activation
                                 if chnl.startswith('a') and activation[chnl])
        if not channel_numbers:
            self.log.error('write_sequence: no active analog channels.')
            return -1

        # Drain any pre-existing errors so the queue is clean before we start
        self.get_errors()
        start = time.time()

        # Write all steps relying on the step defaults the AWG assigns in SLIS:SEQ:NEW
        # (WINP OFF, RCO 1, GOTO NEXT, EJIN OFF). These defaults are confirmed on the device once
        # per session; if they do not hold, the sequence is rewritten with every parameter set.
        skip_defaults = True
        result = self._write_sequence_steps(name, sequence_parameter_list, channel_numbers,
                                            skip_defaults=True)
        if result == 0 and not self._sequence_defaults_verified:
            verified = self._verify_sequence_defaults(name, sequence_parameter_list)
            if verified is None:
                pass  # no step relies on the defaults, nothing to verify
            elif verified:
                self._sequence_defaults_verified = True
            else:
                self.log.warning('write_sequence: AWG sequence step defaults differ from the '
                                 'expected (WINP OFF, RCO 1, GOTO NEXT, EJIN OFF). Rewriting the '
                                 'sequence with every step parameter set explicitly.')
                skip_defaults = False
                result = self._write_sequence_steps(name, sequence_parameter_list,
                                                    channel_numbers, skip_defaults=False)
        if result < 0:
            return -1

        self._add_upload_time('sequence', time.time() - start)
        self._last_written_sequence = name
        self.log.info('write_sequence: wrote {0} steps for sequence "{1}"{2}.'.format(
            num_steps, name, '' if skip_defaults else ' (all parameters explicit)'))
        self._log_upload_stats('sequence "{0}"'.format(name))
        return num_steps

    def _sequence_step_commands(self, name, step, num_steps, wfm_tuple, seq_params,
                                channel_numbers, use_flags, skip_defaults):
        """
        SCPI commands for one sequence step.

        @param list channel_numbers: active analog channel numbers; track n plays on
                                     channel_numbers[n-1] (see load_sequence).
        @param bool use_flags: write the four flags of this step. If no step of the sequence uses
                               flags, they are left at the AWG default.
        @param bool skip_defaults: omit commands that only restate the step defaults set by
                                   SLIS:SEQ:NEW (WINP OFF, RCO 1, GOTO NEXT, EJIN OFF).

        @return list of str, or None if the step parameters are invalid.
        """
        commands = list()

        # Waveform per track. Match waveforms to tracks by their '_ch<n>' suffix, falling back to
        # the tuple order if the suffixes do not match the active channels.
        if len(wfm_tuple) != len(channel_numbers):
            self.log.error('Unable to write sequence.\nLength of waveform tuple "{0}" at step {1} '
                           'does not match the number of active analog channels {2}.'
                           ''.format(wfm_tuple, step, channel_numbers))
            return None
        by_channel = dict()
        for waveform in wfm_tuple:
            try:
                by_channel[int(waveform.rsplit('_ch', 1)[1])] = waveform
            except (ValueError, IndexError):
                break
        if set(by_channel) == set(channel_numbers):
            track_waveforms = [by_channel[ch_num] for ch_num in channel_numbers]
        else:
            track_waveforms = list(wfm_tuple)
        for track, waveform in enumerate(track_waveforms, 1):
            commands.append('SLIS:SEQ:STEP{0:d}:TASS{1:d}:WAV "{2}","{3}"'.format(
                step, track, name, waveform))

        # Wait trigger
        trigger = self._trigger_command(seq_params['wait_for'])
        if trigger is None:
            self.log.error('Invalid wait trigger specifier "{0}" at step {1}.\nPlease choose one '
                           'of: "OFF", "A", "B", "INT"'.format(seq_params['wait_for'], step))
            return None
        if trigger != 'OFF' or not skip_defaults:
            commands.append('SLIS:SEQ:STEP{0:d}:WINP "{1}",{2}'.format(step, name, trigger))

        # Repetitions
        repeat = seq_params['repetitions']
        if repeat < 0:
            commands.append('SLIS:SEQ:STEP{0:d}:RCO "{1}",INF'.format(step, name))
        elif repeat != 0 or not skip_defaults:
            commands.append('SLIS:SEQ:STEP{0:d}:RCO "{1}",{2:d}'.format(step, name,
                                                                         int(repeat) + 1))

        # Go to
        goto = seq_params['go_to']
        if goto > num_steps:
            self.log.error('Assigned "go_to = {0}" at step {1} is larger than the number of steps '
                           '"{2}".'.format(goto, step, num_steps))
            return None
        if goto > 0:
            commands.append('SLIS:SEQ:STEP{0:d}:GOTO "{1}",{2:d}'.format(step, name, int(goto)))
        elif not skip_defaults:
            commands.append('SLIS:SEQ:STEP{0:d}:GOTO "{1}",NEXT'.format(step, name))

        # Event jump
        event = self._trigger_command(seq_params['event_trigger'])
        if event is None:
            self.log.error('Invalid event trigger specifier "{0}" at step {1}.\nPlease choose one '
                           'of: "OFF", "A", "B", "INT"'.format(seq_params['event_trigger'], step))
            return None
        if event != 'OFF':
            jumpto = seq_params['event_jump_to']
            jumpto = 'NEXT' if jumpto <= 0 else str(int(jumpto))
            commands.append('SLIS:SEQ:STEP{0:d}:EJIN "{1}",{2}'.format(step, name, event))
            commands.append('SLIS:SEQ:STEP{0:d}:EJUM "{1}",{2}'.format(step, name, jumpto))
        elif not skip_defaults:
            commands.append('SLIS:SEQ:STEP{0:d}:EJIN "{1}",OFF'.format(step, name))

        # Flags (track 1). Set explicitly for every step once any step uses them, since the AWG
        # default is "no change".
        if use_flags:
            flags_t = seq_params['flag_trigger'] or list()
            flags_h = seq_params['flag_high'] or list()
            for flag in ('A', 'B', 'C', 'D'):
                if flag in flags_t:
                    state = 'PULS'
                elif flag in flags_h:
                    state = 'HIGH'
                else:
                    state = 'LOW'
                commands.append('SLIS:SEQ:STEP{0:d}:TFL1:{1}FL "{2}",{3}'.format(
                    step, flag, name, state))
        return commands

    def _write_sequence_steps(self, name, sequence_parameter_list, channel_numbers, skip_defaults):
        """
        (Re-)creates the sequence and writes all its steps. The commands of up to
        _SEQ_STEPS_PER_WRITE steps are sent as ONE VISA write (joined with ';:'), followed by an
        *OPC? / error queue checkpoint, so a failure is still reported with its step range.

        @return int: 0 on success, -1 on failure.
        """
        num_steps = len(sequence_parameter_list)
        num_tracks = len(channel_numbers)
        use_flags = any(params['flag_trigger'] or params['flag_high']
                        for _, params in sequence_parameter_list)

        # Create new sequence and set jump timing to immediate.
        # Delete old sequence by the same name if present.
        if self.new_sequence(name=name, steps=num_steps, tracks=num_tracks) < 0:
            return -1
        try:
            current_len = int(self.query('SLIS:SEQ:LENG? "{0}"'.format(name)))
        except Exception as exc:
            self.log.error('write_sequence: could not read back the length of sequence "{0}": {1}'
                           ''.format(name, exc))
            return -1
        if current_len != num_steps:
            self.log.error('write_sequence: requested {0} steps for sequence "{1}", AWG reports '
                           '{2} steps.'.format(num_steps, name, current_len))
            self.get_errors()
            return -1

        commands = list()
        first_step = 1
        for step, (wfm_tuple, seq_params) in enumerate(sequence_parameter_list, 1):
            step_commands = self._sequence_step_commands(name, step, num_steps, wfm_tuple,
                                                         seq_params, channel_numbers, use_flags,
                                                         skip_defaults)
            if step_commands is None:
                return -1
            commands.extend(step_commands)

            if step % self._SEQ_STEPS_PER_WRITE != 0 and step != num_steps:
                continue

            if commands:
                self.write(';:'.join(commands))
            commands = list()

            context = 'sequence steps {0}-{1}'.format(first_step, step)
            if not self._wait_opc(timeout=30.0, context=context):
                self.log.error('write_sequence: AWG did not complete steps {0}-{1}/{2}. Aborting.'
                               ''.format(first_step, step, num_steps))
                return -1
            if self.get_errors():
                self.log.error('write_sequence: AWG reported error(s) in steps {0}-{1}/{2}. See '
                               'error messages above. Aborting upload.'
                               ''.format(first_step, step, num_steps))
                return -1
            first_step = step + 1
        return 0

    def _verify_sequence_defaults(self, name, sequence_parameter_list):
        """
        Checks on the device that a step written without explicit WINP/RCO/GOTO/EJIN commands
        really has the expected defaults.

        @return bool or None: True/False, or None if no step relies on the defaults.
        """
        for step, (_, seq_params) in enumerate(sequence_parameter_list, 1):
            if (seq_params['repetitions'] == 0 and seq_params['go_to'] <= 0
                    and seq_params['wait_for'] == 'OFF' and seq_params['event_trigger'] == 'OFF'):
                break
        else:
            return None

        try:
            answers = [self.query('SLIS:SEQ:STEP{0:d}:{1}? "{2}"'.format(step, param, name)).upper()
                       for param in ('WINP', 'RCO', 'GOTO', 'EJIN')]
            ok = answers == ['OFF', '1', 'NEXT', 'OFF']
        except Exception as exc:
            self.log.warning('write_sequence: could not query defaults of sequence step {0}: {1}'
                             ''.format(step, exc))
            answers = None
            ok = False
        self.log.debug('write_sequence: step defaults on step {0}: {1} -> {2}'.format(
            step, answers, ok))
        return ok

    def get_waveform_names(self, refresh=False):
        """ Retrieve the names of all uploaded waveforms on the device.

        Served from a local mirror of the AWG waveform list, which is kept in sync on every
        import/delete/clear done through this module. The device itself is only read if the
        mirror is unknown, e.g. after activation or clear_all(), or if refresh=True.

        @param bool refresh: force re-reading the list from the device.

        @return list: List of all uploaded waveform name strings in the device workspace.
        """
        return natural_sort(self._get_waveform_name_set(refresh))

    def _get_waveform_name_set(self, refresh=False):
        """ The waveform list mirror as a set (see get_waveform_names). """
        if refresh or self._waveform_names_cache is None:
            try:
                query_return = self.query('WLIS:LIST?')
            except visa.VisaIOError:
                self.log.error('Unable to read waveform list from device. VisaIOError occured.')
                return set()
            self._waveform_names_cache = set(query_return.split(',')) if query_return else set()
        return self._waveform_names_cache

    def get_sequence_names(self):
        """ Retrieve the names of all uploaded sequence on the device.

        @return list: List of all uploaded sequence name strings in the device workspace.
        """
        sequence_list = list()

        if not self._has_sequence_mode():
            return sequence_list

        try:
            number_of_seq = int(self.query('SLIS:SIZE?'))
            for ii in range(number_of_seq):
                sequence_list.append(self.query('SLIS:NAME? {0:d}'.format(ii + 1)))
        except visa.VisaIOError:
            self.log.error('Unable to read sequence list from device. VisaIOError occurred.')
        return sequence_list

    def delete_waveform(self, waveform_name):
        """ Delete the waveform with name "waveform_name" from the device memory.

        @param str waveform_name: The name of the waveform to be deleted
                                  Optionally a list of waveform names can be passed.

        @return list: a list of deleted waveform names.
        """
        if isinstance(waveform_name, str):
            waveform_name = [waveform_name]

        avail_waveforms = self._get_waveform_name_set()
        deleted_waveforms = list()
        for waveform in waveform_name:
            if waveform in avail_waveforms:
                self.write('WLIS:WAV:DEL "{0}"'.format(waveform))
                deleted_waveforms.append(waveform)
        avail_waveforms.difference_update(deleted_waveforms)
        return natural_sort(deleted_waveforms)

    def delete_sequence(self, sequence_name):
        """ Delete the sequence with name "sequence_name" from the device memory.

        @param str sequence_name: The name of the sequence to be deleted
                                  Optionally a list of sequence names can be passed.

        @return list: a list of deleted sequence names.
        """
        if isinstance(sequence_name, str):
            sequence_name = [sequence_name]

        avail_sequences = self.get_sequence_names()
        deleted_sequences = list()
        for sequence in sequence_name:
            if sequence in avail_sequences:
                self.write('SLIS:SEQ:DEL "{0}"'.format(sequence))
                deleted_sequences.append(sequence)
        return deleted_sequences

    def load_waveform(self, load_dict):
        """ Loads a waveform to the specified channel of the pulsing device.
        @param dict|list load_dict: a dictionary with keys being one of the available channel
                                    index and values being the name of the already written
                                    waveform to load into the channel.
                                    Examples:   {1: rabi_ch1, 2: rabi_ch2} or
                                                {1: rabi_ch2, 2: rabi_ch1}
                                    If just a list of waveform names if given, the channel
                                    association will be invoked from the channel
                                    suffix '_ch1', '_ch2' etc. A
                                    possible configuration can be e.g.
                                        ['rabi_ch1', 'rabi_ch2', 'rabi_ch3']
        @return dict: Dictionary containing the actually loaded waveforms per
                      channel.
        For devices that have a workspace (i.e. AWG) this will load the waveform
        from the device workspace into the channel.
        Please note that the channel index used here is not to be confused with the number suffix
        in the generic channel descriptors (i.e. 'd_ch1', 'a_ch1'). The channel index used here is
        highly hardware specific and corresponds to a collection of digital and analog channels
        being associated to a SINGLE wavfeorm asset.
        """
        self._log_upload_stats('waveform')

        if isinstance(load_dict, list):
            new_dict = dict()
            for waveform in load_dict:
                channel = int(waveform.rsplit('_ch', 1)[1])
                new_dict[channel] = waveform
            load_dict = new_dict

        # Get all active channels
        chnl_activation = self.get_active_channels()
        analog_channels = natural_sort(
            chnl for chnl in chnl_activation if chnl.startswith('a') and chnl_activation[chnl])

        # Check if all channels to load to are active
        channels_to_set = {'a_ch{0:d}'.format(chnl_num) for chnl_num in load_dict}
        if not channels_to_set.issubset(analog_channels):
            self.log.error('Unable to load waveforms into channels.\n'
                           'One or more channels to set are not active.')
            return self.get_loaded_assets()[0]

        # Check if all waveforms to load are present on device memory
        if not set(load_dict.values()).issubset(self._get_waveform_name_set()):
            self.log.error('Unable to load waveforms into channels.\n'
                           'One or more waveforms to load are missing on device memory.')
            return self.get_loaded_assets()[0]

        # Load waveforms into channels
        for chnl_num, waveform in load_dict.items():
            self.write('SOUR{0:d}:CASS:WAV "{1}"'.format(chnl_num, waveform))
            if not self._wait_for_asset(chnl_num, waveform, timeout=15.0):
                self.log.error('load_waveform: channel {0} did not load "{1}" within 15 s.'
                               ''.format(chnl_num, waveform))
                self.get_errors()
                return self.get_loaded_assets()[0]

        # Re-arm the clear-before-upload flag
        self._pending_clear_before_upload = True
        return self.get_loaded_assets()[0]

    def load_sequence(self, sequence_name):
        """ Loads a sequence to the channels of the device in order to be ready for playback.
        For devices that have a workspace (i.e. AWG) this will load the sequence from the device
        workspace into the channels.
        Track n of the sequence is loaded into the n-th active analog channel.

        In run_mode 'TRIG', step 1 of the sequence is set to wait for the configured trigger
        input, so every trigger plays the sequence once.

        @param str sequence_name: name of the sequence to load
        @return dict: Dictionary containing the actually loaded waveforms per channel.
        """
        if sequence_name not in self.get_sequence_names():
            self.log.error('Unable to load sequence.\n'
                           'Sequence to load is missing on device memory.')
            return self.get_loaded_assets()[0]

        # Get all active channels
        chnl_activation = self.get_active_channels()
        channel_numbers = sorted(int(chnl.rsplit('_ch', 1)[1]) for chnl in chnl_activation
                                 if chnl.startswith('a') and chnl_activation[chnl])

        # Check if number of sequence tracks matches the number of analog channels
        trac_num = int(self.query('SLIS:SEQ:TRAC? "{0}"'.format(sequence_name)))
        if trac_num != len(channel_numbers):
            self.log.error('Unable to load sequence.\nNumber of tracks in sequence to load does '
                           'not match the number of active analog channels.')
            return self.get_loaded_assets()[0]

        if self._get_run_mode() == 'TRIG':
            self.sequence_set_wait_trigger(sequence_name, 1, 'ON')

        # Load sequence
        for track, chnl in enumerate(channel_numbers, 1):
            self.write('SOUR{0:d}:CASS:SEQ "{1}", {2:d}'.format(chnl, sequence_name, track))
            expected = '{0},{1:d}'.format(sequence_name, track)
            if not self._wait_for_asset(chnl, expected, timeout=30.0):
                self.log.error('load_sequence: channel {0} did not load track {1} of "{2}" within '
                               '30 s.'.format(chnl, track, sequence_name))
                self.get_errors()
                return self.get_loaded_assets()[0]

        # Re-arm the clear-before-upload flag
        self._pending_clear_before_upload = True
        return self.get_loaded_assets()[0]

    def get_loaded_assets(self):
        """
        Retrieve the currently loaded asset names for each active channel of the device.
        The returned dictionary will have the channel numbers as keys.
        In case of loaded waveforms the dictionary values will be the waveform names.
        In case of a loaded sequence the values will be the sequence name.

        @return (dict, str): Dictionary with keys being the channel number and values being the
                             respective asset loaded into the channel,
                             string describing the asset type ('waveform' or 'sequence')
        """
        # Get all active channels
        chnl_activation = self.get_active_channels()
        channel_numbers = sorted(int(chnl.split('_ch')[1]) for chnl in chnl_activation if
                                 chnl.startswith('a') and chnl_activation[chnl])

        # Get assets per channel
        loaded_assets = dict()
        current_type = None
        for chnl_num in channel_numbers:
            # Ask AWG for currently loaded waveform or sequence. A waveform looks like
            # 'waveformname' and a sequence like 'sequencename,1' (with the loaded track).
            splitted = self._query_loaded_asset(chnl_num).rsplit(',', 1)
            asset_name = splitted[0]
            if not asset_name:
                continue
            asset_type = 'sequence' if len(splitted) > 1 else 'waveform'
            if current_type is not None and current_type != asset_type:
                self.log.error('Unable to determine loaded assets.')
                return dict(), ''
            current_type = asset_type
            loaded_assets[chnl_num] = asset_name

        return loaded_assets, current_type

    def clear_all(self):
        """ Clears all loaded waveform from the pulse generators RAM.

        @return int: error code (0:OK, -1:error)
        """
        self.write('WLIS:WAV:DEL ALL')
        self._wait_opc(timeout=30.0, context='clear_all: waveforms')
        if self._has_sequence_mode():
            self.write('SLIS:SEQ:DEL ALL')
            self._wait_opc(timeout=30.0, context='clear_all: sequences')
        self._waveform_names_cache = None
        self._last_written_sequence = None
        return 0

    def get_status(self):
        """ Retrieves the status of the pulsing hardware

        @return (int, dict): integer value of the current status with the
                             corresponding dictionary containing status
                             description for all the possible status variables
                             of the pulse generator hardware
        """
        status_dic = {-1: 'Failed Request or Communication',
                      0: 'Device has stopped, but can receive commands',
                      1: 'Device is armed and waiting for a trigger',
                      2: 'Device is active and running'}
        if self.awg is None:
            return -1, status_dic
        try:
            current_status = int(self.query('AWGC:RST?'))
        except Exception as exc:
            self.log.error('Could not read AWG run state: {0}'.format(exc))
            current_status = -1
        return current_status, status_dic

    def set_sample_rate(self, sample_rate):
        """ Set the sample rate of the pulse generator hardware

        @param float sample_rate: The sample rate to be set (in Hz)

        @return foat: the sample rate returned from the device (-1:error)
        """
        self.write('CLOCK:SRATE %.4G' % sample_rate)
        self._wait_opc(timeout=30.0, context='set_sample_rate')
        time.sleep(1)
        self._sample_rate_cache = self.get_sample_rate()
        return self._sample_rate_cache

    def get_sample_rate(self):
        """ Set the sample rate of the pulse generator hardware

        @return float: The current sample rate of the device (in Hz)
        """
        return_rate = float(self.query('CLOCK:SRATE?'))
        return return_rate

    def get_analog_level(self, amplitude=None, offset=None):
        """ Retrieve the analog amplitude and offset of the provided channels.

        @param list amplitude: optional, if a specific amplitude value (in Volt
                               peak to peak, i.e. the full amplitude) of a
                               channel is desired.
        @param list offset: optional, if a specific high value (in Volt) of a
                            channel is desired.

        @return dict: with keys being the generic string channel names and items
                      being the values for those channels. Amplitude is always
                      denoted in Volt-peak-to-peak and Offset in (absolute)
                      Voltage.

        If no entries provided then the levels of all channels where simply
        returned. If no analog channels provided, return just an empty dict.
        """
        amp = dict()
        off = dict()

        chnl_list = self._get_all_analog_channels()

        # get pp amplitudes
        if amplitude is None:
            for ch_num, chnl in enumerate(chnl_list, 1):
                amp[chnl] = float(self.query('SOUR{0:d}:VOLT:AMPL?'.format(ch_num)))
        else:
            for chnl in amplitude:
                if chnl in chnl_list:
                    ch_num = int(chnl.rsplit('_ch', 1)[1])
                    amp[chnl] = float(self.query('SOUR{0:d}:VOLT:AMPL?'.format(ch_num)))
                else:
                    self.log.warning('Get analog amplitude from AWG70k channel "{0}" failed. '
                                     'Channel non-existent.'.format(chnl))

        # get voltage offsets
        if offset is None:
            for chnl in chnl_list:
                off[chnl] = 0.0
        else:
            for chnl in offset:
                if chnl in chnl_list:
                    ch_num = int(chnl.rsplit('_ch', 1)[1])
                    off[chnl] = float(self.query('SOUR{0:d}:VOLT:OFFS?'.format(ch_num)))
                else:
                    self.log.warning('Get analog offset from AWG70k channel "{0}" failed. '
                                     'Channel non-existent.'.format(chnl))
        return amp, off

    def set_analog_level(self, amplitude=None, offset=None):
        """ Set amplitude and/or offset value of the provided analog channel.

        @param dict amplitude: dictionary, with key being the channel and items
                               being the amplitude values (in Volt peak to peak,
                               i.e. the full amplitude) for the desired channel.
        @param dict offset: dictionary, with key being the channel and items
                            being the offset values (in absolute volt) for the
                            desired channel.

        @return (dict, dict): tuple of two dicts with the actual set values for
                              amplitude and offset.

        If nothing is passed then the command will return two empty dicts.
        """
        # Check the inputs by using the constraints...
        constraints = self.get_constraints()
        # ...and the available analog channels
        analog_channels = self._get_all_analog_channels()

        # amplitude sanity check. Invalid channels are removed after the loop, as a dict must not
        # change size while being iterated.
        if amplitude is not None:
            invalid_channels = list()
            for chnl in amplitude:
                if chnl not in analog_channels:
                    self.log.warning('Channel to set ({0}) not available in AWG.\nSetting '
                                     'analogue voltage for this channel ignored.'.format(chnl))
                    invalid_channels.append(chnl)
                elif amplitude[chnl] < constraints.a_ch_amplitude.min:
                    self.log.warning('Minimum Vpp for channel "{0}" is {1}. Requested Vpp of {2}V '
                                     'was ignored and instead set to min value.'
                                     ''.format(chnl, constraints.a_ch_amplitude.min,
                                               amplitude[chnl]))
                    amplitude[chnl] = constraints.a_ch_amplitude.min
                elif amplitude[chnl] > constraints.a_ch_amplitude.max:
                    self.log.warning('Maximum Vpp for channel "{0}" is {1}. Requested Vpp of {2}V '
                                     'was ignored and instead set to max value.'
                                     ''.format(chnl, constraints.a_ch_amplitude.max,
                                               amplitude[chnl]))
                    amplitude[chnl] = constraints.a_ch_amplitude.max
            for chnl in invalid_channels:
                del amplitude[chnl]
        # offset sanity check
        if offset is not None:
            invalid_channels = list()
            for chnl in offset:
                if chnl not in analog_channels:
                    self.log.warning('Channel to set ({0}) not available in AWG.\nSetting '
                                     'offset voltage for this channel ignored.'.format(chnl))
                    invalid_channels.append(chnl)
                elif offset[chnl] < constraints.a_ch_offset.min:
                    self.log.warning('Minimum offset for channel "{0}" is {1}. Requested offset of '
                                     '{2}V was ignored and instead set to min value.'
                                     ''.format(chnl, constraints.a_ch_offset.min, offset[chnl]))
                    offset[chnl] = constraints.a_ch_offset.min
                elif offset[chnl] > constraints.a_ch_offset.max:
                    self.log.warning('Maximum offset for channel "{0}" is {1}. Requested offset of '
                                     '{2}V was ignored and instead set to max value.'
                                     ''.format(chnl, constraints.a_ch_offset.max,
                                               offset[chnl]))
                    offset[chnl] = constraints.a_ch_offset.max
            for chnl in invalid_channels:
                del offset[chnl]

        if amplitude is not None:
            for chnl, amp in amplitude.items():
                ch_num = int(chnl.rsplit('_ch', 1)[1])
                self.write('SOUR{0:d}:VOLT:AMPL {1}'.format(ch_num, amp))
                self._wait_opc(timeout=10.0, context='set_analog_level: amplitude')

        if offset is not None:
            for chnl, off in offset.items():
                ch_num = int(chnl.rsplit('_ch', 1)[1])
                self.write('SOUR{0:d}:VOLT:OFFSET {1}'.format(ch_num, off))
                self._wait_opc(timeout=10.0, context='set_analog_level: offset')
        return self.get_analog_level()

    def get_digital_level(self, low=None, high=None):
        """ Retrieve the digital low and high level of the provided channels.

        @param list low: optional, if a specific low value (in Volt) of a
                         channel is desired.
        @param list high: optional, if a specific high value (in Volt) of a
                          channel is desired.

        @return: (dict, dict): tuple of two dicts, with keys being the channel
                               number and items being the values for those
                               channels. Both low and high value of a channel is
                               denoted in (absolute) Voltage.

        If no entries provided then the levels of all channels where simply
        returned. If no digital channels provided, return just an empty dict.
        """
        low_val = {}
        high_val = {}

        digital_channels = self._get_all_digital_channels()

        if low is None:
            low = digital_channels
        if high is None:
            high = digital_channels

        # get low marker levels
        for chnl in low:
            if chnl not in digital_channels:
                continue
            d_ch_number = int(chnl.rsplit('_ch', 1)[1])
            a_ch_number = (1 + d_ch_number) // 2
            marker_index = 2 - (d_ch_number % 2)
            low_val[chnl] = float(
                self.query('SOUR{0:d}:MARK{1:d}:VOLT:LOW?'.format(a_ch_number, marker_index)))
        # get high marker levels
        for chnl in high:
            if chnl not in digital_channels:
                continue
            d_ch_number = int(chnl.rsplit('_ch', 1)[1])
            a_ch_number = (1 + d_ch_number) // 2
            marker_index = 2 - (d_ch_number % 2)
            high_val[chnl] = float(
                self.query('SOUR{0:d}:MARK{1:d}:VOLT:HIGH?'.format(a_ch_number, marker_index)))

        return low_val, high_val

    def set_digital_level(self, low=None, high=None):
        """ Set low and/or high value of the provided digital channel.

        @param dict low: dictionary, with key being the channel and items being
                         the low values (in volt) for the desired channel.
        @param dict high: dictionary, with key being the channel and items being
                         the high values (in volt) for the desired channel.

        @return (dict, dict): tuple of two dicts where first dict denotes the
                              current low value and the second dict the high
                              value.

        If nothing is passed then the command will return two empty dicts.
        """
        if low is None:
            low = self.get_digital_level()[0]
        if high is None:
            high = self.get_digital_level()[1]

        #If you want to check the input use the constraints:
        constraints = self.get_constraints()
        digital_channels = self._get_all_digital_channels()

        # Check the constraints for marker high level
        for key in high:
            if high[key] < constraints.d_ch_high.min:
                self.log.warning('Voltages for digital values are too small for high. Setting to minimum value')
                high[key] = constraints.d_ch_high.min
            elif high[key] > constraints.d_ch_high.max:
                self.log.warning('Voltages for digital values are too high for high. Setting to maximum value')
                high[key] = constraints.d_ch_high.max

        # Check the constraints for marker low level
        for key in low:
            if low[key] < constraints.d_ch_low.min:
                self.log.warning('Voltages for digital values are too small for low. Setting to minimum value')
                low[key] = constraints.d_ch_low.min
            elif low[key] > constraints.d_ch_low.max:
                self.log.warning('Voltages for digital values are too high for low. Setting to maximum value')
                low[key] = constraints.d_ch_low.max

        # Check the difference between marker high and low
        for key in high:
            if key not in low:
                continue
            if high[key] - low[key] < 0.5:
                self.log.warning('Voltage difference is too small. Reducing low voltage level.')
                low[key] = high[key] - 0.5
            elif high[key] - low[key] > 1.4:
                self.log.warning('Voltage difference is too large. Increasing low voltage level.')
                low[key] = high[key] - 1.4

        # set high marker levels
        for chnl in high:
            if chnl not in digital_channels:
                continue
            d_ch_number = int(chnl.rsplit('_ch', 1)[1])
            a_ch_number = (1 + d_ch_number) // 2
            marker_index = 2 - (d_ch_number % 2)
            self.write('SOUR{0:d}:MARK{1:d}:VOLT:HIGH {2}'.format(a_ch_number, marker_index, high[chnl]))
        # set low marker levels
        for chnl in low:
            if chnl not in digital_channels:
                continue
            d_ch_number = int(chnl.rsplit('_ch', 1)[1])
            a_ch_number = (1 + d_ch_number) // 2
            marker_index = 2 - (d_ch_number % 2)
            self.write('SOUR{0:d}:MARK{1:d}:VOLT:LOW {2}'.format(a_ch_number, marker_index, low[chnl]))

        return self.get_digital_level()

    def get_active_channels(self, ch=None):
        """ Get the active channels of the pulse generator hardware.

        The activation is read from the device (output state and DAC resolution) once and then
        served from a cache that set_active_channels() keeps up to date.

        @param list ch: optional, if specific analog or digital channels are
                        needed to be asked without obtaining all the channels.

        @return dict:  where keys denoting the channel number and items boolean
                       expressions whether channel are active or not.
        """
        if self._active_channels_cache is None:
            self._active_channels_cache = self._read_active_channels()
        active_ch = self._active_channels_cache.copy()

        # return either all channel information or just the one asked for.
        if ch is not None:
            active_ch = {chnl: state for chnl, state in active_ch.items() if chnl in ch}
        return active_ch

    def _read_active_channels(self):
        """ Read the channel activation from the device. """
        active_ch = dict()
        for ch_num, a_ch in enumerate(self._get_all_analog_channels(), 1):
            # check what analog channels are active
            active_ch[a_ch] = bool(int(self.query('OUTPUT{0:d}:STATE?'.format(ch_num))))
            # check how many markers are active on each channel, i.e. the DAC resolution
            digital_mrk = 0
            if active_ch[a_ch]:
                digital_mrk = 10 - int(self.query('SOUR{0:d}:DAC:RES?'.format(ch_num)))
            active_ch['d_ch{0:d}'.format(ch_num * 2 - 1)] = digital_mrk >= 1
            active_ch['d_ch{0:d}'.format(ch_num * 2)] = digital_mrk >= 2
        return active_ch

    def set_active_channels(self, ch=None):
        """
        Set the active/inactive channels for the pulse generator hardware.
        The state of ALL available analog and digital channels will be returned
        (True: active, False: inactive).
        The actually set and returned channel activation must be part of the available
        activation_configs in the constraints.
        You can also activate/deactivate subsets of available channels but the resulting
        activation_config must still be valid according to the constraints.
        If the resulting set of active channels can not be found in the available
        activation_configs, the channel states must remain unchanged.

        @param dict ch: dictionary with keys being the analog or digital string generic names for
                        the channels (i.e. 'd_ch1', 'a_ch2') with items being a boolean value.
                        True: Activate channel, False: Deactivate channel

        @return dict: with the actual set values for ALL active analog and digital channels

        If nothing is passed then the command will simply return the unchanged current state.
        """
        current_channel_state = self.get_active_channels()

        if ch is None:
            return current_channel_state

        if not set(current_channel_state).issuperset(ch):
            self.log.error('Trying to (de)activate channels that are not present in AWG70k.\n'
                           'Setting of channel activation aborted.')
            return current_channel_state

        # Determine new channel activation states
        new_channels_state = current_channel_state.copy()
        for chnl in ch:
            new_channels_state[chnl] = ch[chnl]

        # check if the channels to set are part of the activation_config constraints
        constraints = self.get_constraints()
        new_active_channels = {chnl for chnl in new_channels_state if new_channels_state[chnl]}
        if new_active_channels not in constraints.activation_config.values():
            self.log.error('activation_config to set ({0}) is not allowed according to constraints.'
                           ''.format(new_active_channels))
            return current_channel_state

        # calculate dac resolution for each analog channel and set it in hardware.
        # Also (de)activate the analog channels accordingly
        max_res = constraints.dac_resolution['max']
        self._active_channels_cache = None
        for a_ch in self._get_all_analog_channels():
            ach_num = int(a_ch.rsplit('_ch', 1)[1])
            # determine number of markers for current a_ch
            if new_channels_state['d_ch{0:d}'.format(2 * ach_num - 1)]:
                marker_num = 2 if new_channels_state['d_ch{0:d}'.format(2 * ach_num)] else 1
            else:
                marker_num = 0
            # set DAC resolution for this channel
            dac_res = max_res - marker_num
            self.write('SOUR{0:d}:DAC:RES {1:d}'.format(ach_num, dac_res))
            # (de)activate the analog channel
            if new_channels_state[a_ch]:
                self.write('OUTPUT{0:d}:STATE ON'.format(ach_num))
            else:
                self.write('OUTPUT{0:d}:STATE OFF'.format(ach_num))

        return self.get_active_channels()

    def get_interleave(self):
        """ Check whether Interleave is ON or OFF in AWG.

        @return bool: True: ON, False: OFF

        Unused for pulse generator hardware other than an AWG.
        """
        return False

    def set_interleave(self, state=False):
        """ Turns the interleave of an AWG on or off.

        @param bool state: The state the interleave should be set to
                           (True: ON, False: OFF)

        @return bool: actual interleave status (True: ON, False: OFF)
        """
        if state:
            self.log.warning('Interleave mode not available for the AWG 70000 Series!\n'
                             'Method call will be ignored.')
        return False

    def reset(self):
        """Reset the device.

        @return int: error code (0:OK, -1:error)
        """
        self.write('*RST')
        self.write('*WAI')
        self._waveform_names_cache = None
        self._sample_rate_cache = None
        self._active_channels_cache = None
        self._last_trigger_log = None
        return 0

    def query(self, question):
        """ Asks the device a 'question' and receive and return an answer from it.

        @param string question: string containing the command

        @return string: the answer of the device to the 'question' in a string
        """
        return self.awg.query(question).strip().rstrip('\n').rstrip().strip('"')

    def write(self, command):
        """ Sends a command string to the device.

        @param string command: string containing the command

        @return int: error code (0:OK, -1:error)
        """
        try:
            self.awg.write(command)
        except Exception as exc:
            self.log.error('VISA write failed for command "{0}": {1}'.format(command[:200], exc))
            return -1
        return 0

    def get_errors(self):
        """
        Get all errors from the device and log them.

        @return bool: whether any error was found
        """
        has_error = False
        for _ in range(100):
            err = self.query('SYST:ERR?').split(',', 1)
            if int(err[0]) == 0:
                break
            self.log.error('{0} error: {1} {2}'.format(
                self.awg_model, err[0], err[1] if len(err) > 1 else ''))
            has_error = True
        return has_error

    def _drain_stale_errors(self, context):
        """ Empty the device error queue, logging leftover errors as warnings only.

        @return bool: whether any error was found
        """
        has_error = False
        for _ in range(100):
            err = self.query('SYST:ERR?').split(',', 1)
            if int(err[0]) == 0:
                break
            self.log.warning('{0} error left over from earlier commands (found {1}): {2} {3}'
                             ''.format(self.awg_model, context, err[0],
                                       err[1] if len(err) > 1 else ''))
            has_error = True
        return has_error

    def new_sequence(self, name, steps, tracks=None):
        """
        Generate a new sequence 'name' having 'steps' number of steps and 'tracks' number of tracks
        with immediate (async.) jump timing.

        @param str name: Name of the sequence which should be generated
        @param int steps: Number of steps
        @param int tracks: Number of tracks. Defaults to the number of active analog channels.

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        if tracks is None:
            activation = self.get_active_channels()
            tracks = max(1, sum(1 for chnl in activation
                                if chnl.startswith('a') and activation[chnl]))

        if name in self.get_sequence_names():
            self.delete_sequence(name)
        self.write('SLIS:SEQ:NEW "{0}", {1:d}, {2:d}'.format(name, steps, tracks))
        self.write('SLIS:SEQ:EVEN:JTIM "{0}", IMM'.format(name))
        return 0

    def sequence_set_waveform(self, sequence_name, waveform_name, step, track):
        """
        Set the waveform 'waveform_name' to position 'step' in the sequence 'sequence_name'.

        @param str sequence_name: Name of the sequence which should be editted
        @param str waveform_name: Name of the waveform which should be added
        @param int step: Position of the added waveform
        @param int track: track which should be editted

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        self.write('SLIS:SEQ:STEP{0:d}:TASS{1:d}:WAV "{2}", "{3}"'.format(step,
                                                                          track,
                                                                          sequence_name,
                                                                          waveform_name))
        return 0

    def sequence_set_repetitions(self, sequence_name, step, repeat=0):
        """
        Set the repetition counter of sequence "sequence_name" at step "step" to "repeat".
        A repeat value of -1 denotes infinite repetitions; 0 means the step is played once.

        @param str sequence_name: Name of the sequence to be edited
        @param int step: Sequence step to be edited
        @param int repeat: number of repetitions. (-1: infinite, 0: once, 1: twice, ...)

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1
        repeat = 'INF' if repeat < 0 else str(int(repeat + 1))
        self.write('SLIS:SEQ:STEP{0:d}:RCO "{1}", {2}'.format(step, sequence_name, repeat))
        return 0

    def sequence_set_goto(self, sequence_name, step, goto=-1):
        """
        Set the step to continue with after step "step" of sequence "sequence_name".

        @param str sequence_name: Name of the sequence to be edited
        @param int step: Sequence step to be edited
        @param int goto: step to go to. 0 or -1 is interpreted as next step.

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        goto = str(int(goto)) if goto > 0 else 'NEXT'
        self.write('SLIS:SEQ:STEP{0:d}:GOTO "{1}", {2}'.format(step, sequence_name, goto))
        return 0

    def sequence_set_event_jump(self, sequence_name, step, trigger='OFF', jumpto=0):
        """
        Set the event trigger input of the specified sequence step and the jump_to destination.

        @param str sequence_name: Name of the sequence to be edited
        @param int step: Sequence step to be edited
        @param str trigger: Trigger string specifier. ('OFF', 'A', 'B' or 'INT')
        @param int jumpto: The sequence step to jump to. 0 or -1 is interpreted as next step

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        trigger = self._trigger_command(trigger)
        if trigger is None:
            self.log.error('Invalid trigger specifier.\n'
                           'Please choose one of: "OFF", "A", "B", "INT"')
            return -1

        self.write('SLIS:SEQ:STEP{0:d}:EJIN "{1}", {2}'.format(step, sequence_name, trigger))
        # Set event_jump_to if event trigger is enabled
        if trigger != 'OFF':
            jumpto = 'NEXT' if jumpto <= 0 else str(int(jumpto))
            self.write('SLIS:SEQ:STEP{0:d}:EJUM "{1}", {2}'.format(step, sequence_name, jumpto))
        return 0

    def sequence_set_wait_trigger(self, sequence_name, step=None, trigger='OFF'):
        """
        Make a certain sequence step wait for a trigger to start playing.

        Also accepts the AWG7000 module call form sequence_set_wait_trigger(step, trigger), as
        used by the AWG/PulseBlaster interfuse. It then applies to the sequence written last.

        @param str sequence_name: Name of the sequence to be edited
        @param int step: Sequence step to be edited
        @param str trigger: Trigger string specifier. ('OFF', 'A', 'B', 'INT', or 'ON' for the
                            configured trigger_input)

        @return int: error code
        """
        if not isinstance(sequence_name, str):
            # AWG7000 form: (step, trigger)
            sequence_name, step, trigger = (self._last_written_sequence, sequence_name,
                                            'OFF' if step is None else step)
            if sequence_name is None:
                self.log.error('sequence_set_wait_trigger: no sequence has been written yet.')
                return -1

        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        trigger = self._trigger_command(trigger)
        if trigger is None:
            self.log.error('Invalid trigger specifier.\n'
                           'Please choose one of: "OFF", "A", "B", "INT"')
            return -1

        self.write('SLIS:SEQ:STEP{0:d}:WINP "{1}", {2}'.format(step, sequence_name, trigger))
        return 0

    def sequence_set_flags(self, sequence_name, step, flags_t=None, flags_h=None):
        """
        Set the flags in "flags" to HIGH (trigger=False) during the sequence step or let the flags
        send out a fixed duration trigger pulse (trigger=True). All other flags are set to LOW.

        @param str sequence_name: Name of the sequence to be edited
        @param int step: Sequence step to be edited
        @param list flags_t: List of flag trigger specifiers to be active during this sequence step, if both options are
                             selected, the flag is set to trigger (PULS)
        @param list flags_h: List of flag high specifiers to be active during this sequence step

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        flags_t = flags_t or list()
        flags_h = flags_h or list()
        for flag in ('A', 'B', 'C', 'D'):
            if flag in flags_t:
                state = 'PULS'
            elif flag in flags_h:
                state = 'HIGH'
            else:
                state = 'LOW'

            self.write('SLIS:SEQ:STEP{0:d}:TFL1:{1}FL "{2}",{3}'.format(step,
                                                                        flag,
                                                                        sequence_name,
                                                                        state))
        return 0

    def make_sequence_continuous(self, sequencename=None):
        """
        Usually after a run of a sequence the output stops. Many times it is desired that the full
        sequence is repeated many times. This is achieved here by setting the 'jump to' value of
        the last element to 'First'

        @param sequencename: Name of the sequence which should be made continous. Defaults to the
                             sequence written last.

        @return int last_step: The step number which 'jump to' has to be set to 'First'
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        if sequencename is None:
            sequencename = self._last_written_sequence
            if sequencename is None:
                self.log.error('make_sequence_continuous: no sequence has been written yet.')
                return -1
        last_step = int(self.query('SLIS:SEQ:LENG? "{0}"'.format(sequencename)))
        err = self.sequence_set_goto(sequencename, last_step, 1)
        if err < 0:
            last_step = err
        return last_step

    def force_jump_sequence(self, final_step, channel=1):
        """
        This command forces the sequencer to jump to the specified step per channel. A
        force jump does not require a trigger event to execute the jump.
        For two channel instruments, if both channels are playing the same sequence, then
        both channels jump simultaneously to the same sequence step.

        @param channel: determines the channel number. If omitted, interpreted as 1
        @param final_step: Step to jump to. Possible options are
            FIRSt - This enables the sequencer to jump to first step in the sequence.
            CURRent - This enables the sequencer to jump to the current sequence step,
            essentially starting the current step over.
            LAST - This enables the sequencer to jump to the last step in the sequence.
            END - This enables the sequencer to go to the end and play 0 V until play is
            stopped.
            <NR1> - This enables the sequencer to jump to the specified step, where the
            value is between 1 and 16383.

        """
        self.write('SOURCE{0:d}:JUMP:FORCE {1}'.format(channel, final_step))
        return

    # =========================================================================
    # Run mode / trigger helpers
    # =========================================================================

    def _get_run_mode(self):
        """ The configured run mode, validated. Falls back to 'CONT' if invalid. """
        mode = str(self._run_mode_config).upper()
        if mode not in ('CONT', 'TRIG', 'TCON'):
            self.log.error('Invalid run_mode "{0}" in config. Must be CONT, TRIG or TCON (the '
                           'AWG70000 series has no gated mode). Falling back to CONT.'.format(mode))
            mode = 'CONT'
        return mode

    @staticmethod
    def _channel_run_mode(mode, asset_type):
        """ SOURn:RMOD for the configured run mode and the loaded asset type.

        A loaded sequence in 'TRIG' mode runs continuously and waits for the trigger at step 1
        (set in load_sequence), so every trigger plays the sequence once.
        """
        if asset_type == 'sequence' and mode == 'TRIG':
            return 'CONT'
        return mode

    def _trigger_command(self, trigger):
        """ Device trigger specifier for a qudi trigger descriptor, or None if invalid. """
        trigger = str(trigger).upper()
        if trigger == 'ON':
            trigger = str(self._trigger_input).upper()
        return self.__event_triggers.get(trigger)

    def _configure_trigger_input(self, channel_numbers):
        """
        Route the configured trigger input to the given channels and apply the configured trigger
        level / slope / impedance / timing. Settings that are not configured are left as they are
        on the AWG. Logs the resulting settings whenever they change.
        """
        trigger = self._trigger_command(self._trigger_input)
        if trigger in (None, 'OFF'):
            self.log.error('Invalid trigger_input "{0}" in config. Must be A, B or INT. Using A.'
                           ''.format(self._trigger_input))
            trigger = 'ATR'

        commands = ['SOUR{0:d}:TINP {1}'.format(ch_num, trigger) for ch_num in channel_numbers]
        if trigger in ('ATR', 'BTR'):
            if self._trigger_level is not None:
                commands.append('TRIG:LEV {0:.4f},{1}'.format(float(self._trigger_level), trigger))
            if self._trigger_slope is not None:
                slope = str(self._trigger_slope).upper()[:3]
                if slope in ('POS', 'NEG'):
                    commands.append('TRIG:SLOP {0},{1}'.format(slope, trigger))
                else:
                    self.log.warning('Invalid trigger_slope "{0}", must be POS or NEG. Ignored.'
                                     ''.format(self._trigger_slope))
            if self._trigger_impedance is not None:
                impedance = {'50OHM': 50, '50': 50, '1KOHM': 1000, '1000': 1000}.get(
                    str(self._trigger_impedance).upper().replace(' ', ''))
                if impedance is not None:
                    commands.append('TRIG:IMP {0:d},{1}'.format(impedance, trigger))
                else:
                    self.log.warning('Invalid trigger_impedance "{0}", must be 50OHM or 1KOHM. '
                                     'Ignored.'.format(self._trigger_impedance))
        elif self._internal_trigger_interval is not None:
            commands.append('TRIG:INT {0:.6E}'.format(float(self._internal_trigger_interval)))
        if self._trigger_timing is not None:
            timing = {'ASYNC': 'ASYN', 'ASYN': 'ASYN', 'SYNC': 'SYNC'}.get(
                str(self._trigger_timing).upper())
            if timing is not None:
                commands.append('TRIG:MODE {0}'.format(timing))
            else:
                self.log.warning('Invalid trigger_timing "{0}", must be ASYNC or SYNC. Ignored.'
                                 ''.format(self._trigger_timing))

        if commands:
            self.write(';:'.join(commands))
        self._wait_opc(timeout=10.0, context='configuring trigger input')
        self.get_errors()

        try:
            if trigger in ('ATR', 'BTR'):
                settings = (trigger,
                            self.query('TRIG:LEV? {0}'.format(trigger)),
                            self.query('TRIG:SLOP? {0}'.format(trigger)),
                            self.query('TRIG:IMP? {0}'.format(trigger)),
                            self.query('TRIG:MODE?'))
                message = ('AWG trigger input {0}: level {1} V, slope {2}, impedance {3} Ohm, '
                           'timing {4}'.format(*settings))
            else:
                settings = (trigger, self.query('TRIG:INT?'), self.query('TRIG:MODE?'))
                message = 'AWG internal trigger: interval {1} s, timing {2}'.format(*settings)
        except Exception as exc:
            self.log.debug('Could not read back trigger settings: {0}'.format(exc))
            return
        if settings != self._last_trigger_log:
            self._last_trigger_log = settings
            self.log.info(message)

    def _wait_for_status(self, states, timeout=10.0, poll_interval=0.05):
        """ Poll AWGC:RST? until it is one of 'states' or until timeout.

        @return int: the last status read.
        """
        deadline = time.time() + timeout
        while True:
            status = self.get_status()[0]
            if status in states or status < 0 or time.time() >= deadline:
                return status
            time.sleep(poll_interval)

    def _query_loaded_asset(self, ch_num):
        """ Asset loaded into a channel, e.g. 'wfm_ch1' or 'seqname,1', with quotes removed. """
        return self.query('SOUR{0:d}:CASS?'.format(ch_num)).replace('"', '').strip()

    def _wait_for_asset(self, ch_num, expected, timeout=15.0):
        """ Poll SOURn:CASS? until it reports 'expected' or until timeout. @return bool """
        deadline = time.time() + timeout
        while self._query_loaded_asset(ch_num).replace(' ', '') != expected:
            if time.time() >= deadline:
                return False
            time.sleep(0.05)
        return True

    def _wait_opc(self, timeout=10.0, context=''):
        """
        Wait for all pending AWG operations with ONE blocking *OPC? query, using the given
        timeout as VISA timeout. No polling, so it returns as soon as the AWG is done.

        @return bool: True if the AWG reported completion within timeout.
        """
        old_timeout = self.awg.timeout
        try:
            self.awg.timeout = max(old_timeout, timeout * 1000)
            return int(self.query('*OPC?')) == 1
        except Exception as exc:
            self.log.error('*OPC? failed or timed out after {0} s{1}: {2}'.format(
                timeout, ' ({0})'.format(context) if context else '', exc))
            # discard a late *OPC? answer, so it is not read as the answer of the next query
            try:
                self.awg.clear()
            except Exception:
                pass
            return False
        finally:
            self.awg.timeout = old_timeout

    # =========================================================================
    # Upload statistics
    # =========================================================================

    def _add_upload_time(self, key, seconds, n_bytes=0, n_waveforms=0):
        """ Accumulate upload timing statistics of the current batch (see _log_upload_stats). """
        if self._upload_stats is None:
            self._upload_stats = {'t_start': time.time() - seconds, 'times': dict(),
                                  'bytes': 0, 'waveforms': 0}
        stats = self._upload_stats
        stats['times'][key] = stats['times'].get(key, 0.0) + seconds
        stats['bytes'] += n_bytes
        stats['waveforms'] += n_waveforms

    def _log_upload_stats(self, label):
        """
        Logs one summary line for the upload batch since the last summary, then resets it.
        'other' is the wall time not spent in this module, mostly qudi sampling the waveforms.
        """
        stats, self._upload_stats = self._upload_stats, None
        if stats is None:
            return
        wall = time.time() - stats['t_start']
        times = stats['times']
        awg_total = sum(times.values())
        parts = ', '.join('{0} {1:.1f} s'.format(key, times[key])
                          for key in ('build', 'ftp', 'import', 'verify', 'sequence') if key in times)
        self.log.info(
            'AWG upload summary ({0}): {1:d} waveform channel(s), {2:.1f} MB in {3:.1f} s wall '
            'time. AWG module: {4:.1f} s ({5}); other (mostly qudi sampling): {6:.1f} s.'
            ''.format(label, stats['waveforms'], stats['bytes'] / 1e6, wall, awg_total, parts,
                      wall - awg_total))

    # =========================================================================
    # FTP
    # =========================================================================

    def _ftp_connect(self):
        """ (Re-)open the persistent FTP session in the AWG working directory. """
        self._ftp_close()
        ftp = FTP(self._ip_address)
        ftp.login(user=self._username, passwd=self._password)
        ftp.cwd(self.ftp_working_dir)
        self._ftp = ftp
        return ftp

    def _ftp_close(self):
        """ Close the persistent FTP session, if any. Never raises. """
        ftp, self._ftp = self._ftp, None
        if ftp is None:
            return
        try:
            ftp.quit()
        except ftp_errors:
            try:
                ftp.close()
            except ftp_errors:
                pass

    def _ftp_run(self, func):
        """
        Run func(ftp) on the persistent FTP session. The AWG FTP server drops idle sessions
        after a while, so on any FTP/socket error the session is re-opened and func is
        retried once.
        """
        try:
            ftp = self._ftp if self._ftp is not None else self._ftp_connect()
            return func(ftp)
        except ftp_errors as exc:
            self.log.debug('FTP session error ({0}), reconnecting and retrying once.'.format(exc))
            return func(self._ftp_connect())

    def _send_fileobj(self, filename, fileobj):
        """
        Upload a file object as file to the AWG via FTP. STOR overwrites an existing file;
        only if the server refuses that, the file is deleted first and the upload retried.
        """
        def _store(ftp):
            fileobj.seek(0)
            try:
                ftp.storbinary('STOR ' + filename, fileobj, blocksize=2**20)
            except error_perm:
                ftp.delete(filename)
                fileobj.seek(0)
                ftp.storbinary('STOR ' + filename, fileobj, blocksize=2**20)
        self._ftp_run(_store)
        return 0

    def _get_filenames_on_device(self):
        """
        @return list: filenames found in <ftproot>\\waves
        """
        log = list()

        def _list(ftp):
            log.clear()
            ftp.retrlines('LIST', callback=log.append)
        self._ftp_run(_list)

        filename_list = list()
        for line in log:
            if '<DIR>' not in line:
                # that is how a potential line is looking like:
                #   '05-10-16  05:22PM                  292 SSR aom adjusted.seq'
                # Remove the date, then split off the file size. The rest is the file name.
                size_filename = line[18:].lstrip()
                filename_list.append(size_filename.split(' ', 1)[1].strip())
        return filename_list

    def _delete_file(self, filename):
        """ Delete a file from the FTP working directory, if present. """
        def _delete(ftp):
            try:
                ftp.delete(filename)
            except error_perm:
                pass  # file does not exist
        self._ftp_run(_delete)

    # =========================================================================
    # .wfmx file creation
    # =========================================================================

    def _write_wfmx(self, filename, analog_samples, marker_bytes, is_first_chunk, is_last_chunk,
                    total_number_of_samples):
        """
        Appends a sampled chunk of a whole waveform to a .wfmx file built in memory (spooled to a
        temporary file if it grows large). The .wfmx layout is: XML header, all analog samples
        (float32), then all marker bytes.

        @return file object or None: the complete .wfmx file (positioned at its end) on the last
                                     chunk, else None. The caller must close it.
        """
        if is_first_chunk:
            old = self._wfmx_buffers.pop(filename, None)
            if old is not None:
                self._close_wfmx_buffers(old)
            header = self._create_xml_header(total_number_of_samples, marker_bytes is not None)
            main = tempfile.SpooledTemporaryFile(max_size=self._WFMX_SPOOL_BYTES)
            main.write(header.encode('utf8'))
            markers = None
            if marker_bytes is not None:
                markers = tempfile.SpooledTemporaryFile(max_size=self._WFMX_SPOOL_BYTES)
            self._wfmx_buffers[filename] = (main, markers)

        try:
            main, markers = self._wfmx_buffers[filename]
        except KeyError:
            raise RuntimeError('Chunk for "{0}" received without a preceding first chunk.'
                               ''.format(filename))

        # analog samples in binary format. One sample is 4 bytes (little endian float32).
        main.write(np.asarray(analog_samples, dtype='<f4').tobytes())
        if markers is not None and marker_bytes is not None:
            markers.write(np.asarray(marker_bytes, dtype=np.uint8).tobytes())

        if not is_last_chunk:
            return None

        del self._wfmx_buffers[filename]
        if markers is not None:
            markers.seek(0)
            shutil.copyfileobj(markers, main, 2**22)
            markers.close()
        return main

    @staticmethod
    def _close_wfmx_buffers(buffers):
        for buffer in buffers:
            if buffer is not None:
                buffer.close()

    def _create_xml_header(self, number_of_samples, markers_active):
        """
        This function creates an xml file containing the header for the wfmx-file format using
        etree.
        """
        hdr = ET.Element('DataFile', offset='XXXXXXXXX', version='0.1')
        dsc = ET.SubElement(hdr, 'DataSetsCollection', xmlns='http://www.tektronix.com')
        datasets = ET.SubElement(dsc, 'DataSets', version='1', xmlns='http://www.tektronix.com')
        datadesc = ET.SubElement(datasets, 'DataDescription')
        sub_elem = ET.SubElement(datadesc, 'NumberSamples')
        sub_elem.text = str(int(number_of_samples))
        sub_elem = ET.SubElement(datadesc, 'SamplesType')
        sub_elem.text = 'AWGWaveformSample'
        sub_elem = ET.SubElement(datadesc, 'MarkersIncluded')
        sub_elem.text = 'true' if markers_active else 'false'
        sub_elem = ET.SubElement(datadesc, 'NumberFormat')
        sub_elem.text = 'Single'
        sub_elem = ET.SubElement(datadesc, 'Endian')
        sub_elem.text = 'Little'
        sub_elem = ET.SubElement(datadesc, 'Timestamp')
        sub_elem.text = '2014-10-28T12:59:52.9004865-07:00'
        prodspec = ET.SubElement(datasets, 'ProductSpecific', name='')
        sub_elem = ET.SubElement(prodspec, 'ReccSamplingRate', units='Hz')
        sub_elem.text = str(self._get_cached_sample_rate())
        sub_elem = ET.SubElement(prodspec, 'ReccAmplitude', units='Volts')
        sub_elem.text = '0.5'
        sub_elem = ET.SubElement(prodspec, 'ReccOffset', units='Volts')
        sub_elem.text = '0'
        sub_elem = ET.SubElement(prodspec, 'SerialNumber')
        sub_elem = ET.SubElement(prodspec, 'SoftwareVersion')
        sub_elem.text = '4.0.0075'
        sub_elem = ET.SubElement(prodspec, 'UserNotes')
        sub_elem = ET.SubElement(prodspec, 'OriginalBitDepth')
        sub_elem.text = 'Floating'
        sub_elem = ET.SubElement(prodspec, 'Thumbnail')
        sub_elem = ET.SubElement(prodspec, 'CreatorProperties', name='Basic Waveform')
        sub_elem = ET.SubElement(hdr, 'Setup')

        xml_header = ET.tostring(hdr, encoding='unicode')
        xml_header = xml_header.replace('><', '>\r\n<')

        # Calculates the length of the header and replace placeholder with actual number
        xml_header = xml_header.replace('XXXXXXXXX', str(len(xml_header)).zfill(9))
        return xml_header

    def _get_cached_sample_rate(self):
        """ Sample rate for the .wfmx header, queried once and refreshed in set_sample_rate. """
        if self._sample_rate_cache is None:
            self._sample_rate_cache = self.get_sample_rate()
        return self._sample_rate_cache

    # =========================================================================
    # Channel helpers
    # =========================================================================

    def _get_all_channels(self):
        """
        Helper method to return a sorted list of all technically available channel descriptors
        (e.g. ['a_ch1', 'a_ch2', 'd_ch1', 'd_ch2'])

        @return list: Sorted list of channels
        """
        configs = self.get_constraints().activation_config
        if 'all' in configs:
            largest_config = configs['all']
        else:
            largest_config = list(configs.values())[0]
            for config in configs.values():
                if len(largest_config) < len(config):
                    largest_config = config
        return natural_sort(largest_config)

    def _get_all_analog_channels(self):
        """
        Helper method to return a sorted list of all technically available analog channel
        descriptors (e.g. ['a_ch1', 'a_ch2'])

        @return list: Sorted list of analog channels
        """
        return [chnl for chnl in self._get_all_channels() if chnl.startswith('a')]

    def _get_all_digital_channels(self):
        """
        Helper method to return a sorted list of all technically available digital channel
        descriptors (e.g. ['d_ch1', 'd_ch2'])

        @return list: Sorted list of digital channels
        """
        return [chnl for chnl in self._get_all_channels() if chnl.startswith('d')]

    def _is_output_on(self):
        """
        Aks the AWG if the output is enabled, i.e. if the AWG is running or armed

        @return: bool, (True: output on, False: output off)
        """
        return bool(int(self.query('AWGC:RST?')))

    def _has_sequence_mode(self):
        if self.awg_model in ['AWG70001A', 'AWG70002A']:
            return '03' in self.__installed_options
        if self.awg_model in ['AWG70001B', 'AWG70002B']:
            return 'SEQ' in self.__installed_options
        return False
