# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware module for AWG7000 Series.

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


import io
import os
import time
try:
    import pyvisa as visa
except ImportError:
    import visa
import numpy as np
from ftplib import FTP, error_perm, all_errors as ftp_errors

from qudi.util.paths import get_appdata_dir
from qudi.util.helpers import natural_sort
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.interface.pulser_interface import PulserInterface, PulserConstraints, SequenceOption


class AWG7k(PulserInterface):
    """ A hardware module for the Tektronix AWG7000 series for generating
        waveforms and sequences thereof.

    Example config for copy-paste:

    pulser_awg7000:
        module.Class: 'awg.tektronix_awg7k.AWG7k'
        options:
            awg_visa_address: 'TCPIP::10.42.0.211::INSTR'
            awg_ip_address: '10.42.0.211'
            timeout: 60
            # tmp_work_dir: 'C:\\Software\\qudi_pulsed_files' # optional
            # ftp_root_dir: 'C:\\inetpub\\ftproot' # optional
            # ftp_login: 'anonymous' # optional
            # ftp_passwd: 'anonymous@' # optional
            #
            # run_mode: 'CONT'   # default — free-running, starts immediately on pulser_on()
            # run_mode: 'TRIG'   # single shot — one trigger edge fires waveform once
            # run_mode: 'GAT'    # gated — output runs only while gate signal is high
            #
            # Only needed when run_mode is 'TRIG' or 'GAT':
            # trigger_level: 0.5       # detection threshold in Volts
            # trigger_slope: 'POS'     # 'POS' rising edge  |  'NEG' falling edge
            # trigger_impedance: '50OHM'  # '50OHM'  |  '1KOHM'

            clear_device_before_upload: False  # if True, wipe all waveforms
            # and sequences from AWG memory before uploading a new
            # waveform/sequence batch (once per upload batch, re-armed after
            # load_waveform()/load_sequence()).
    """

    # config options
    _tmp_work_dir = ConfigOption(name='tmp_work_dir',
                                 default=os.path.join(get_appdata_dir(True), 'pulsed_files'),
                                 missing='warn')
    _visa_address = ConfigOption(name='awg_visa_address', missing='error')
    _ip_address = ConfigOption(name='awg_ip_address', missing='error')
    _ftp_dir = ConfigOption(name='ftp_root_dir', default='C:\\inetpub\\ftproot', missing='warn')
    _username = ConfigOption(name='ftp_login', default='anonymous', missing='warn')
    _password = ConfigOption(name='ftp_passwd', default='anonymous@', missing='warn')
    _visa_timeout = ConfigOption(name='timeout', default=30, missing='nothing')
    _run_mode_config = ConfigOption(name='run_mode', default='CONT', missing='nothing')
    _trigger_level = ConfigOption(name='trigger_level', default=0.5, missing='nothing')
    _trigger_slope = ConfigOption(name='trigger_slope', default='POS', missing='nothing')
    _trigger_impedance = ConfigOption(name='trigger_impedance', default='50OHM', missing='nothing')
    _clear_device_before_upload = ConfigOption(name='clear_device_before_upload', default=False, missing='nothing')

    # The AWG7000 series has no SCPI query to list or identify sequences
    # (unlike waveforms, which support WLIS:NAME?/WLIS:SIZE? -- a genuine
    # hardware query that survives restarts on its own). Without this
    # persistence, the module "forgets" that a sequence was written/loaded
    # every time qudi restarts, even though the actual sequence data still
    # exists in AWG hardware memory (assuming no power cycle occurred).
    _written_sequences = StatusVar(name='written_sequences', default=list())
    _loaded_sequences = StatusVar(name='loaded_sequences', default=list())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._rm = visa.ResourceManager()
        self.awg = None
        self.ftp_working_dir = 'waves'
        self.installed_options = list()
        self._internal_ch_state = {
            'a_ch1': False,
            'a_ch2': False,
        }
        self._pending_clear_before_upload = True
        #self._written_sequences = []
        #self._loaded_sequences = []
        self._marker_byte_dict = {0: b'\x00', 1: b'\x01', 2: b'\x02', 3: b'\x03'}
        self._event_triggers = {'OFF': 'OFF', 'ON': 'ON'}

        # Upload speed-ups (see write_waveform / write_sequence):
        # Persistent FTP session, reconnected on demand by _ftp_run().
        self._ftp = None
        # Local mirror of the AWG waveform list. None = unknown, re-read from the device on
        # the next get_waveform_names() call. Kept in sync on import/delete/clear/reset.
        self._waveform_names_cache = None
        # Sample rate used in the .wfm file footer, so it is not queried for every waveform.
        self._sample_rate_cache = None
        # In-memory .wfm file contents per waveform, while a chunked write is in progress.
        self._wfm_buffers = dict()
        # True once the sequencer element defaults have been confirmed on this device.
        self._sequence_defaults_verified = False
        # Timing statistics of the current upload batch, logged by _log_upload_stats().
        self._upload_stats = None

    def on_activate(self):
        """ Initialisation performed during activation of the module. """
        if not os.path.exists(self._tmp_work_dir):
            os.makedirs(os.path.abspath(self._tmp_work_dir))

        try:
            self.awg = self._rm.open_resource(self._visa_address)
            self.awg.timeout = self._visa_timeout * 1000
        except:
            self.awg = None
            self.log.error(
                'VISA address "{0}" not found by the pyVISA resource manager.\nCheck '
                'the connection by using for example "Agilent Connection Expert".'
                ''.format(self._visa_address))

        ftp = self._ftp_connect()
        self.log.debug('FTP working dir: {0}'.format(ftp.pwd()))

        self._waveform_names_cache = None
        self._sample_rate_cache = None
        self._wfm_buffers = dict()
        self._sequence_defaults_verified = False
        self._upload_stats = None

        idn = self.query('*IDN?').split(',')
        self.mfg, self.model, self.ser, self.fw_ver = idn

        self.installed_options = self.query('*OPT?').split(',')

        self.log.info('Found {} {} Serial: {} FW: {} options: {}'.format(
            self.mfg, self.model, self.ser, self.fw_ver, self.installed_options
        ))

        self.write('MMEM:CDIR "{0}"'.format(os.path.join(self._ftp_dir, self.ftp_working_dir)))
        return

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module. """
        self._ftp_close()
        try:
            self.awg.close()
        except:
            self.log.debug('Closing AWG connection using pyvisa failed.')
        self.log.info('Closed connection to AWG')
        return

    # =========================================================================
    # Internal helper methods for OPC waiting and status polling
    # =========================================================================

    def _wait_opc(self, timeout=10.0, context=''):
        """
        Poll *OPC? until it returns 1, or timeout.

        This is the single shared implementation used everywhere in this
        module that needs to wait for a pending AWG operation to complete.
        Replaces the previously duplicated and UNBOUNDED
        'while int(self.query('*OPC?')) != 1: time.sleep(0.1)' pattern that
        could hang forever with zero log output if the AWG stalled
        internally (e.g. out of memory, communication glitch).

        @param float timeout: seconds to wait before giving up
        @param str context: description used in log messages on failure

        @return bool: True if OPC completed within timeout, False otherwise
        """
        elapsed = 0.0
        while elapsed < timeout:
            try:
                if int(self.query('*OPC?')) == 1:
                    return True
            except Exception as exc:
                self.log.error(
                    '_wait_opc: *OPC? query failed{0}: {1}'
                    ''.format(' ({0})'.format(context) if context else '', exc)
                )
                return False
            time.sleep(0.1)
            elapsed += 0.1

        self.log.error(
            '_wait_opc: timed out after {0}s{1}.'
            ''.format(timeout, ' ({0})'.format(context) if context else '')
        )
        return False

    def _wait_for_armed_or_running(self, timeout=5.0, context=''):
        """
        Poll get_status() until it reports 1 (running) or 2 (armed/waiting
        for trigger), or timeout.

        Extracted from pulser_on() where this exact polling loop was
        duplicated once for SEQ mode and once for TRIG/GAT mode.

        @param float timeout: seconds to wait before giving up
        @param str context: description used in the timeout error message

        @return bool: True if AWG reached running/armed state, False on timeout
        """
        elapsed = 0.0
        while self.get_status()[0] not in (1, 2):
            time.sleep(0.2)
            elapsed += 0.2
            if elapsed >= timeout:
                self.log.error(
                    'AWG failed to reach running/armed state after {0}s{1}.'
                    ''.format(timeout, ' ({0})'.format(context) if context else '')
                )
                return False
        return True

    # =========================================================================
    # Below all the Pulser Interface routines.
    # =========================================================================

    def get_constraints(self):
        """
        Retrieve the hardware constrains from the Pulsing device.

        @return constraints object: object with pulser constraints as attributes.
        """
        constraints = PulserConstraints()

        if self.model == 'AWG7122C':
            if self.get_interleave():
                constraints.sample_rate.min = 12.0e9
                constraints.sample_rate.max = 24.0e9
                constraints.sample_rate.step = 5.0e2
                constraints.sample_rate.default = 24.0e9
            else:
                constraints.sample_rate.min = 10.0e6
                constraints.sample_rate.max = 12.0e9
                constraints.sample_rate.step = 10.0e6
                constraints.sample_rate.default = 12.0e9

        elif self.model == 'AWG7082C':
            if self.get_interleave():
                constraints.sample_rate.min = 8.0e9
                constraints.sample_rate.max = 16.0e9
                constraints.sample_rate.step = 5.0e2
                constraints.sample_rate.default = 16.0e9
            else:
                constraints.sample_rate.min = 10.0e6
                constraints.sample_rate.max = 8.0e9
                constraints.sample_rate.step = 10.0e6
                constraints.sample_rate.default = 8.0e9

        elif self.model == 'AWG7052':
            constraints.sample_rate.min = 10.0e6
            constraints.sample_rate.max = 5.0e9
            constraints.sample_rate.step = 10.0e6
            constraints.sample_rate.default = 5.0e9

        if '02' in self.installed_options or self._has_interleave():
            constraints.a_ch_amplitude.max = 1.0
            constraints.a_ch_amplitude.step = 0.001
            constraints.a_ch_amplitude.default = 1.0
        else:
            constraints.a_ch_amplitude.max = 2.0
            constraints.a_ch_amplitude.step = 0.001
            constraints.a_ch_amplitude.default = 2.0

        if self._zeroing_enabled():
            constraints.a_ch_amplitude.min = 0.25
        else:
            constraints.a_ch_amplitude.min = 0.5

        constraints.d_ch_low.min = -1.4
        constraints.d_ch_low.max = 0.9
        constraints.d_ch_low.step = 0.01
        constraints.d_ch_low.default = 0.0

        constraints.d_ch_high.min = -0.9
        constraints.d_ch_high.max = 1.4
        constraints.d_ch_high.step = 0.01
        constraints.d_ch_high.default = 1.4

        if self.model == 'AWG7052':
            constraints.waveform_length.min = 960
            constraints.waveform_length.step = 64
            constraints.waveform_length.default = 960
        else:
            if self.get_interleave():
                constraints.waveform_length.min = 1920
                constraints.waveform_length.step = 8
            else:
                constraints.waveform_length.min = 960
                constraints.waveform_length.step = 4
            constraints.waveform_length.default = 1920

        if '01' in self.installed_options:
            constraints.waveform_length.max = 64800000
        else:
            constraints.waveform_length.max = 32400000

        if self.model == 'AWG7052':
            constraints.waveform_num.min = 1
            constraints.waveform_num.max = 16000
            constraints.waveform_num.step = 1
            constraints.waveform_num.default = 1
        else:
            constraints.waveform_num.min = 1
            constraints.waveform_num.max = 32000
            constraints.waveform_num.step = 1
            constraints.waveform_num.default = 1

        constraints.sequence_num.min = 1
        constraints.sequence_num.max = 16000
        constraints.sequence_num.step = 1
        constraints.sequence_num.default = 1

        constraints.subsequence_num.min = 1
        constraints.subsequence_num.max = 8000
        constraints.subsequence_num.step = 1
        constraints.subsequence_num.default = 1

        constraints.repetitions.min = 0
        constraints.repetitions.max = 65536
        constraints.repetitions.step = 1
        constraints.repetitions.default = 0

        constraints.event_triggers = ['ON']
        constraints.flags = list()

        if self.model == 'AWG7052':
            constraints.sequence_steps.min = 0
            constraints.sequence_steps.max = 4000
            constraints.sequence_steps.step = 1
            constraints.sequence_steps.default = 0
        else:
            constraints.sequence_steps.min = 0
            constraints.sequence_steps.max = 8000
            constraints.sequence_steps.step = 1
            constraints.sequence_steps.default = 0

        activation_config = dict()
        activation_config['all'] = frozenset({'a_ch1', 'd_ch1', 'd_ch2', 'a_ch2', 'd_ch3', 'd_ch4'})
        activation_config['A1_M1_M2'] = frozenset({'a_ch1', 'd_ch1', 'd_ch2'})
        activation_config['A2_M3_M4'] = frozenset({'a_ch2', 'd_ch3', 'd_ch4'})
        activation_config['Two_Analog'] = frozenset({'a_ch1', 'a_ch2'})
        activation_config['Analog1'] = frozenset({'a_ch1'})
        activation_config['Analog2'] = frozenset({'a_ch2'})
        constraints.activation_config = activation_config

        if self._has_sequence_mode():
            constraints.sequence_option = SequenceOption.OPTIONAL
        else:
            constraints.sequence_option = SequenceOption.NON

        return constraints

    def pulser_on(self):
        """
        Switches the pulsing device on.

        Behaviour depends on both the 'run_mode' config option AND the current
        hardware state:
          - If the AWG hardware is already in SEQ mode (a sequence was loaded),
            the config run_mode is ignored and SEQ mode is preserved.
            The trigger input hardware is configured so that TWAIT on step 1
            uses the same external BNC as in TRIG/GAT waveform mode.
            This makes sequence mode equivalent to waveform mode from the
            user's perspective: draw the AWG trigger in your pulse block,
            PB fires it, AWG sequence starts.
          - Otherwise, the config run_mode governs (CONT / TRIG / GAT).

        @return int: error code
        """
        config_mode = str(self._run_mode_config).upper()
        if config_mode not in ('CONT', 'TRIG', 'GAT'):
            self.log.error(
                'Invalid run_mode "{0}" in config. '
                'Must be CONT, TRIG or GAT. Falling back to CONT.'.format(config_mode)
            )
            config_mode = 'CONT'

        chnl_activation = self.get_active_channels()
        channel_numbers = sorted(
            int(chnl.split('_ch')[1]) for chnl in chnl_activation
            if chnl.startswith('a') and chnl_activation[chnl]
        )

        if not self._is_output_on():
            for ch in channel_numbers:
                self.write('OUTPUT{0}:STATE ON'.format(ch))

            # Check hardware run mode — if a sequence is loaded,
            # load_sequence() already set AWGC:RMOD SEQ.
            # Do NOT override this with TRIG/GAT/CONT from config.
            current_hw_mode = self.query('AWGC:RMOD?')

            if current_hw_mode == 'SEQ':
                if config_mode == 'CONT':
                    # No trigger wait — sequence loops freely via go_to.
                    self.write('AWGC:RUN')
                else:
                    # Sequence mode: configure the trigger INPUT hardware so that
                    # TWAIT on step 1 responds to the same external BNC trigger
                    # the user already draws in their pulse block.
                    # _configure_trigger_input_only() sets TRIG:SOUR EXT, TRIG:LEV,
                    # TRIG:SLOP and TRIG:IMP without touching AWGC:RMOD.
                    self._configure_trigger_input_only(context='SEQ mode')
                    self.write('AWGC:RUN')

                # FIX (dup #7): shared helper replaces duplicated polling loop
                self._wait_for_armed_or_running(timeout=5.0, context='SEQ mode')

                final_status = self.get_status()[0]
                if final_status == 2:
                    self.log.info(
                        'AWG in SEQ mode, step 1 TWAIT armed -- '
                        'waiting for external trigger '
                        '(level={0}V, slope={1}, impedance={2}).'
                        ''.format(self._trigger_level,
                                  self._trigger_slope,
                                  self._trigger_impedance)
                    )
                elif final_status == 1:
                    self.log.info('AWG running in SEQ mode.')
                else:
                    self.log.error(
                        'AWG in unexpected state {0} after SEQ start.'.format(final_status)
                    )

            elif config_mode == 'CONT':
                self.write('AWGC:RUN')
                while not self._is_output_on():
                    time.sleep(0.2)

            else:
                # TRIG or GAT — configure hardware then arm
                self._configure_trigger_mode(config_mode)
                self.write('AWGC:RUN')

                # FIX (dup #7): shared helper replaces duplicated polling loop
                self._wait_for_armed_or_running(timeout=5.0, context=config_mode)

                final_status = self.get_status()[0]
                if final_status == 2:
                    self.log.info(
                        'AWG armed in {0} mode (level={1}V, slope={2}, '
                        'impedance={3}).'.format(
                            config_mode, self._trigger_level,
                            self._trigger_slope, self._trigger_impedance
                        )
                    )
                elif final_status == 1:
                    self.log.warning(
                        'AWG started immediately after AWGC:RUN in {0} mode. '
                        'TRIG:SOUR may not have been accepted as EXT.'.format(config_mode)
                    )

        return self.get_status()[0]

    def pulser_off(self):
        """ Switches the pulsing device off.

        @return int: error code (0:OK, -1:error)
        """
        if self._is_output_on():
            self.write('AWGC:STOP')
            while self._is_output_on():
                time.sleep(0.2)
        return self.get_status()[0]

    def load_waveform(self, load_dict):
        """ Loads a waveform to the specified channel of the pulsing device.
        For devices that have a workspace (i.e. AWG) this will load the waveform from the device
        workspace into the channel.

        @param load_dict: dict|list

        @return dict: Dictionary containing the actually loaded waveforms per channel.
        """
        self._log_upload_stats('waveform')

        if isinstance(load_dict, list):
            new_dict = dict()
            for waveform in load_dict:
                channel = int(waveform.rsplit('_ch', 1)[1])
                new_dict[channel] = waveform
            load_dict = new_dict

        # FIX (#3): use _internal_ch_state instead of get_active_channels().
        # get_active_channels() queries live hardware OUTPUT:STATE? when the
        # AWG is armed/running, which may reflect a stale configuration from
        # a PREVIOUS operation rather than the current intended activation.
        # _internal_ch_state is always set correctly by set_active_channels()
        # and is the single source of truth used consistently throughout
        # write_waveform(), write_sequence(), and now here too.
        analog_channels = natural_sort(
            chnl for chnl, active in self._internal_ch_state.items() if active
        )

        channels_to_set = {'a_ch{0:d}'.format(chnl_num) for chnl_num in load_dict}
        if not channels_to_set.issubset(analog_channels):
            self.log.error('Unable to load all waveforms into channels.\n'
                           'One or more channels to set are not active.')
            return self.get_loaded_assets()[0]

        if not set(load_dict.values()).issubset(self.get_waveform_names()):
            self.log.error('Unable to load waveforms into channels.\n'
                           'One or more waveforms to load are missing on device memory.')
            return self.get_loaded_assets()[0]

        for chnl_num, waveform in load_dict.items():
            self.write('SOUR{0:d}:WAV "{1}"'.format(chnl_num, waveform))

            # Timeout-bounded wait — prevents silent infinite hang if the
            # AWG fails to load the waveform onto this channel.
            load_timeout = 15.0
            load_elapsed = 0.0
            loaded_ok = False
            while load_elapsed < load_timeout:
                if self.query('SOUR{0:d}:WAV?'.format(chnl_num)) == waveform:
                    loaded_ok = True
                    break
                time.sleep(0.1)
                load_elapsed += 0.1

            if not loaded_ok:
                self.log.error(
                    'load_waveform: channel {0} did not load "{1}" within '
                    '{2}s.'.format(chnl_num, waveform, load_timeout)
                )
                self.get_errors()
                return self.get_loaded_assets()[0]

        # FIX (#6 minor, consistency): warn on invalid run_mode instead of
        # silently defaulting, matching pulser_on()'s behaviour.
        mode_map = {'CONT': 'C', 'TRIG': 'T', 'GAT': 'G'}
        mode = str(self._run_mode_config).upper()
        if mode not in mode_map:
            self.log.warning(
                'load_waveform: invalid run_mode "{0}" in config. '
                'Falling back to CONT.'.format(mode)
            )
            mode = 'CONT'
        self.set_mode(mode_map[mode])

        # Re-arm the clear-before-upload flag
        self._pending_clear_before_upload = True

        return self.get_loaded_assets()[0]

    def load_sequence(self, sequence_name):
        """ Loads a sequence to the channels of the device. """
        if sequence_name not in self.get_sequence_names():
            self.log.error('Unable to load sequence.\n'
                           'Sequence to load is missing on device memory.')
            return self.get_loaded_assets()[0]

        # NOTE: AWGC:EVENT:JMODE EJUMP intentionally NOT sent here.
        # This command does not exist on the AWG7000 series (AWG7122C) and
        # generates error -113 "Undefined header". Per-step event jump
        # targets are configured during write_sequence() via
        # sequence_set_event_jump() -> SEQ:ELEM{n}:JTAR:TYPE / INDEX.

        self.set_mode('S')
        self._loaded_sequences = [sequence_name]

        # Re-arm the clear-before-upload flag
        self._pending_clear_before_upload = True

        return self.get_loaded_assets()[0]

    def get_loaded_assets(self):
        """
        Retrieve the currently loaded asset names for each active channel of the device.

        @return (dict, str): Dictionary with keys being the channel number and values being the
                             respective asset loaded into the channel,
                             string describing the asset type ('waveform' or 'sequence')
        """
        # FIX (#3): use _internal_ch_state instead of get_active_channels()
        # for the same stale-hardware-query reason as load_waveform() above.
        channel_numbers = sorted(
            int(chnl.rsplit('_ch', 1)[1])
            for chnl, active in self._internal_ch_state.items() if active
        )

        loaded_assets = dict()
        current_type = None

        run_mode = self.query('AWGC:RMOD?')
        if run_mode in ('CONT', 'TRIG', 'GAT'):
            current_type = 'waveform'
            for chnl_num in channel_numbers:
                loaded_assets[chnl_num] = self.query('SOUR{0}:WAV?'.format(chnl_num))

        elif run_mode == 'SEQ':
            current_type = 'sequence'
            for chnl_num in channel_numbers:
                if len(self._loaded_sequences) > 0:
                    loaded_assets[chnl_num] = self._loaded_sequences[0]

        return loaded_assets, current_type

    def clear_all(self):
        """ Clears all loaded waveforms from the pulse generators RAM/workspace.

        @return int: error code (0:OK, -1:error)
        """
        self.write('WLIS:WAV:DEL ALL')
        if '09' in self.installed_options:
            self.write('SLIS:SUBS:DEL ALL')
        self.write('SEQUENCE:LENGTH 0')
        # Only the predefined ('*'-prefixed) waveforms survive DEL ALL. If the list is not
        # known yet, read it now while it is short, so the waveforms imported next are tracked.
        if self._waveform_names_cache is not None:
            self._waveform_names_cache = {
                wfm for wfm in self._waveform_names_cache if wfm.startswith('*')
            }
        else:
            self.get_waveform_names(refresh=True)
        self._written_sequences = []
        self._loaded_sequences = []
        return 0

    def get_status(self):
        """ Retrieves the status of the pulsing hardware

        @return (int, dict): integer value of the current status with the corresponding dictionary
        """
        status_dic = {-1: 'Failed Request or Communication',
                      0: 'Device has stopped, but can receive commands',
                      1: 'Device is active and running',
                      2: 'Device is waiting for trigger.'}
        current_status = -1 if self.awg is None else int(self.query('AWGC:RST?'))
        return current_status, status_dic

    def get_sample_rate(self):
        """ Get the sample rate of the pulse generator hardware

        @return float: The current sample rate of the device (in Hz)
        """
        return float(self.query('SOUR1:FREQ?'))

    def set_sample_rate(self, sample_rate):
        """ Set the sample rate of the pulse generator hardware.

        @param float sample_rate: The sampling rate to be set (in Hz)

        @return float: the sample rate returned from the device (in Hz).
        """
        self.write('SOUR1:FREQ {0:.4G}MHz\n'.format(sample_rate / 1e6))

        # FIX (#2): bounded wait instead of unbounded 'while ... != 1: sleep'
        if not self._wait_opc(timeout=10.0, context='set_sample_rate'):
            self.log.warning('set_sample_rate: proceeding despite OPC timeout.')

        # Here we need to wait, because when the sampling rate is changed AWG is busy
        # and therefore the ask in get_sample_rate will return an empty string.
        time.sleep(1)
        self._sample_rate_cache = self.get_sample_rate()
        return self._sample_rate_cache

    def get_analog_level(self, amplitude=None, offset=None):
        """ Retrieve the analog amplitude and offset of the provided channels.

        @param list amplitude: optional
        @param list offset: optional

        @return: (dict, dict): tuple of two dicts
        """
        amp = dict()
        off = dict()

        chnl_list = self._get_all_analog_channels()

        if amplitude is None:
            for ch_num, chnl in enumerate(chnl_list):
                amp[chnl] = float(self.query('SOUR{0:d}:VOLT:AMPL?'.format(ch_num + 1)))
        else:
            for chnl in amplitude:
                if chnl in chnl_list:
                    ch_num = int(chnl.rsplit('_ch', 1)[1])
                    amp[chnl] = float(self.query('SOUR{0:d}:VOLT:AMPL?'.format(ch_num)))
                else:
                    self.log.warning('Get analog amplitude from AWG7122c channel "{0}" failed. '
                                     'Channel non-existent.'.format(chnl))

        no_offset = '02' in self.installed_options or '06' in self.installed_options
        if offset is None:
            for ch_num, chnl in enumerate(chnl_list):
                off[chnl] = 0.0 if no_offset else float(
                    self.query('SOUR{0:d}:VOLT:OFFS?'.format(ch_num + 1)))
        else:
            for chnl in offset:
                if chnl in chnl_list:
                    ch_num = int(chnl.rsplit('_ch', 1)[1])
                    off[chnl] = 0.0 if no_offset else float(
                        self.query('SOUR{0:d}:VOLT:OFFS?'.format(ch_num)))
                else:
                    self.log.warning('Get analog offset from AWG7122c channel "{0}" failed. '
                                     'Channel non-existent.'.format(chnl))
        return amp, off

    def set_analog_level(self, amplitude=None, offset=None):
        """ Set amplitude and/or offset value of the provided analog channel(s).

        @param dict amplitude: optional
        @param dict offset: optional

        @return (dict, dict): tuple of two dicts with the actual set values
        """
        constraints = self.get_constraints()
        analog_channels = self._get_all_analog_channels()

        # FIX (#1 CRITICAL): The original code called 'del amplitude[chnl]'
        # while iterating directly over 'amplitude' in the same for-loop.
        # Python raises 'RuntimeError: dictionary changed size during
        # iteration' the moment this line executes for an invalid channel.
        # Fixed by collecting invalid keys during iteration and deleting
        # them only AFTER the loop has finished.
        if amplitude is not None:
            invalid_amp_channels = []
            for chnl in amplitude:
                if chnl not in analog_channels:
                    self.log.warning('Channel to set (a_ch{0}) not available in AWG.\nSetting '
                                     'analogue voltage for this channel ignored.'.format(chnl))
                    invalid_amp_channels.append(chnl)
                    continue
                if amplitude[chnl] < constraints.a_ch_amplitude.min:
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

            for chnl in invalid_amp_channels:
                del amplitude[chnl]

        # FIX (#1 CRITICAL): same fix applied to the offset block, which had
        # the identical bug ('del offset[chnl]' during iteration over offset).
        if offset is not None:
            invalid_off_channels = []
            for chnl in offset:
                if chnl not in analog_channels:
                    self.log.warning('Channel to set (a_ch{0}) not available in AWG.\nSetting '
                                     'offset voltage for this channel ignored.'.format(chnl))
                    invalid_off_channels.append(chnl)
                    continue
                if offset[chnl] < constraints.a_ch_offset.min:
                    self.log.warning('Minimum offset for channel "{0}" is {1}. Requested offset of '
                                     '{2}V was ignored and instead set to min value.'
                                     ''.format(chnl, constraints.a_ch_offset.min, offset[chnl]))
                    offset[chnl] = constraints.a_ch_offset.min
                elif offset[chnl] > constraints.a_ch_offset.max:
                    self.log.warning('Maximum offset for channel "{0}" is {1}. Requested offset of '
                                     '{2}V was ignored and instead set to max value.'
                                     ''.format(chnl, constraints.a_ch_offset.max, offset[chnl]))
                    offset[chnl] = constraints.a_ch_offset.max

            for chnl in invalid_off_channels:
                del offset[chnl]

        if amplitude is not None:
            for a_ch in amplitude:
                ch_num = int(a_ch.rsplit('_ch', 1)[1])
                self.write('SOUR{0:d}:VOLT:AMPL {1}'.format(ch_num, amplitude[a_ch]))
                # FIX (#2): bounded wait instead of unbounded loop
                self._wait_opc(timeout=5.0, context='set_analog_level:amplitude')

        no_offset = '02' in self.installed_options or '06' in self.installed_options
        if offset is not None and not no_offset:
            for a_ch in offset:
                ch_num = int(a_ch.rsplit('_ch', 1)[1])
                self.write('SOUR{0:d}:VOLT:OFFSET {1}'.format(ch_num, offset[a_ch]))
                # FIX (#2): bounded wait instead of unbounded loop
                self._wait_opc(timeout=5.0, context='set_analog_level:offset')

        return self.get_analog_level()

    def get_digital_level(self, low=None, high=None):
        """ Retrieve the digital low and high level of the provided/all channels.

        @param list low: optional
        @param list high: optional

        @return: (dict, dict): tuple of two dicts
        """
        low_val = {}
        high_val = {}

        digital_channels = self._get_all_digital_channels()

        if low is None:
            low = digital_channels
        if high is None:
            high = digital_channels

        for chnl in low:
            if chnl not in digital_channels:
                continue
            d_ch_number = int(chnl.rsplit('_ch', 1)[1])
            a_ch_number = (1 + d_ch_number) // 2
            marker_index = 2 - (d_ch_number % 2)
            low_val[chnl] = float(
                self.query('SOUR{0:d}:MARK{1:d}:VOLT:LOW?'.format(a_ch_number, marker_index)))

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

        @param dict low: optional
        @param dict high: optional

        @return (dict, dict): tuple of two dicts
        """
        ret_low = {}
        ret_high = {}

        if low is None:
            low = {}
        if high is None:
            high = {}

        digital_channels = self._get_all_digital_channels()

        for ch, level in low.items():
            if ch not in digital_channels:
                continue
            d_ch_number = int(ch.rsplit('_ch', 1)[1])
            a_ch_number = (1 + d_ch_number) // 2
            marker_index = 2 - (d_ch_number % 2)
            self.write('SOUR{0:d}:MARK{1:d}:VOLT:LOW {2}'.format(a_ch_number, marker_index, level))
            ret_low[ch] = float(
                self.query('SOUR{0:d}:MARK{1:d}:VOLT:LOW?'.format(a_ch_number, marker_index)))

        for ch, level in high.items():
            if ch not in digital_channels:
                continue
            d_ch_number = int(ch.rsplit('_ch', 1)[1])
            a_ch_number = (1 + d_ch_number) // 2
            marker_index = 2 - (d_ch_number % 2)
            self.write('SOUR{0:d}:MARK{1:d}:VOLT:HIGH {2}'.format(a_ch_number, marker_index, level))
            ret_high[ch] = float(
                self.query('SOUR{0:d}:MARK{1:d}:VOLT:HIGH?'.format(a_ch_number, marker_index)))

        return ret_low, ret_high

    def get_active_channels(self, ch=None):
        """ Get the active channels of the pulse generator hardware.

        @param list ch: optional

        @return dict: channel activation states
        """
        analog_channels = self._get_all_analog_channels()

        active_ch = dict()
        for ch_num, a_ch in enumerate(analog_channels):
            ch_num = ch_num + 1
            if self._is_output_on():
                active_ch[a_ch] = bool(int(self.query('OUTPUT{0:d}:STATE?'.format(ch_num))))
            else:
                active_ch[a_ch] = self._internal_ch_state[a_ch]
            if active_ch[a_ch]:
                digital_mrk = 10 - int(self.query('SOUR{0:d}:DAC:RES?'.format(ch_num)))
                if digital_mrk == 2:
                    active_ch['d_ch{0:d}'.format(ch_num * 2)] = True
                    active_ch['d_ch{0:d}'.format(ch_num * 2 - 1)] = True
                else:
                    active_ch['d_ch{0:d}'.format(ch_num * 2)] = False
                    active_ch['d_ch{0:d}'.format(ch_num * 2 - 1)] = False
            else:
                active_ch['d_ch{0:d}'.format(ch_num * 2)] = False
                active_ch['d_ch{0:d}'.format(ch_num * 2 - 1)] = False

        if ch is not None:
            chnl_to_delete = [chnl for chnl in active_ch if chnl not in ch]
            for chnl in chnl_to_delete:
                del active_ch[chnl]
        return active_ch

    def set_active_channels(self, ch=None):
        """
        Set the active/inactive channels for the pulse generator hardware.

        @param dict ch: optional

        @return dict: with the actual set values for ALL active analog and digital channels
        """
        current_channel_state = self.get_active_channels()

        if ch is None:
            return current_channel_state

        if not set(current_channel_state).issuperset(ch):
            self.log.error('Trying to (de)activate channels that are not present in AWG.\n'
                           'Setting of channel activation aborted.')
            return current_channel_state

        new_channels_state = current_channel_state.copy()
        for chnl in ch:
            new_channels_state[chnl] = ch[chnl]

        constraints = self.get_constraints()
        new_active_channels = {chnl for chnl in new_channels_state if new_channels_state[chnl]}
        if new_active_channels not in constraints.activation_config.values():
            self.log.error('activation_config to set ({0}) is not allowed according to constraints.'
                           ''.format(new_active_channels))
            return current_channel_state

        analog_channels = self._get_all_analog_channels()

        for a_ch in analog_channels:
            ach_num = int(a_ch.rsplit('_ch', 1)[1])
            if new_channels_state['d_ch{0:d}'.format(2 * ach_num)]:
                marker_num = 2
            else:
                marker_num = 0
            dac_res = 10 - marker_num
            self.write('SOUR{0:d}:DAC:RES {1:d}'.format(ach_num, dac_res))

            # Never turn OUTPUT ON here. Turning ON before a waveform is
            # loaded generates E11506. pulser_on() handles OUTPUT:STATE ON
            # after waveforms are loaded. Always turn OFF here so the state
            # is clean and predictable.
            self.write('OUTPUT{0:d}:STATE OFF'.format(ach_num))

            # _internal_ch_state records the DESIRED state (True/False).
            # This is the source of truth used by write_waveform,
            # write_sequence, load_waveform, and get_loaded_assets.
            self._internal_ch_state[a_ch] = new_channels_state[a_ch]

        return self.get_active_channels()

    def write_waveform(self, name, analog_samples, digital_samples, is_first_chunk, is_last_chunk,
                       total_number_of_samples):
        """
        Write a new waveform or append samples to an already existing waveform on the device memory.

        @return (int, list): Number of samples written (-1 indicates failed process) and list of
                             created waveform names
        """
        waveforms = list()
        constraints = self.get_constraints()

        if len(analog_samples) == 0:
            self.log.error('No analog samples passed to write_waveform method in awg7k.')
            return -1, waveforms

        # Config option: clear_device_before_upload.
        # Wipe all waveforms/sequences on the AWG exactly once at the start
        # of a new upload batch (see _pending_clear_before_upload docstring
        # in __init__). Gated on is_first_chunk so a chunked upload of a
        # single large waveform never gets wiped mid-way through.
        if self._clear_device_before_upload and is_first_chunk and self._pending_clear_before_upload:
            self.log.info(
                'clear_device_before_upload enabled: clearing all waveforms '
                'and sequences on AWG before uploading "{0}".'.format(name)
            )
            self.clear_all()
            self._pending_clear_before_upload = False

        if total_number_of_samples < constraints.waveform_length.min:
            self.log.error('Unable to write waveform.\n'
                           'Number of samples to write ({0:d}) is '
                           'smaller than the allowed minimum waveform length ({1:d}).'
                           ''.format(total_number_of_samples, constraints.waveform_length.min))
            return -1, waveforms
        if total_number_of_samples > constraints.waveform_length.max:
            self.log.error('Unable to write waveform.\n'
                           'Number of samples to write ({0:d}) is '
                           'greater than the allowed maximum waveform length ({1:d}).'
                           ''.format(total_number_of_samples, constraints.waveform_length.max))
            return -1, waveforms

        active_analog = natural_sort(
            chnl for chnl in analog_samples if chnl.startswith('a')
        )
        expected_analog = natural_sort(
            chnl for chnl, active in self._internal_ch_state.items() if active
        )
        if set(active_analog) != set(expected_analog):
            self.log.warning(
                'write_waveform channel mismatch: samples have {0} but '
                '_internal_ch_state has {1}. Using sample channels.'
                ''.format(active_analog, expected_analog)
            )

        for a_ch in active_analog:
            # FIX (#9 minor): standardized on '_ch' split (was 'ch' here,
            # inconsistent with every other channel-number extraction in
            # this module, e.g. write_sequence uses '_ch').
            a_ch_num = int(a_ch.rsplit('_ch', 1)[1])
            mrk_ch_1 = 'd_ch{0:d}'.format(a_ch_num * 2 - 1)
            mrk_ch_2 = 'd_ch{0:d}'.format(a_ch_num * 2)

            start = time.time()
            if mrk_ch_1 in digital_samples and mrk_ch_2 in digital_samples:
                mrk_bytes = digital_samples[mrk_ch_2].view('uint8')
                tmp_bytes = digital_samples[mrk_ch_1].view('uint8')
                np.left_shift(mrk_bytes, 1, out=mrk_bytes)
                np.left_shift(tmp_bytes, 0, out=tmp_bytes)
                np.add(mrk_bytes, tmp_bytes, out=mrk_bytes)
            else:
                mrk_bytes = None
            self.log.debug('Prepare digital channel data: {0}'.format(time.time() - start))

            wfm_name = '{0}_ch{1:d}'.format(name, a_ch_num)

            start = time.time()
            wfm_data = self._write_wfm(filename=wfm_name + '.wfm',
                                       analog_samples=analog_samples[a_ch],
                                       marker_bytes=mrk_bytes,
                                       is_first_chunk=is_first_chunk,
                                       is_last_chunk=is_last_chunk,
                                       total_number_of_samples=total_number_of_samples)
            self._add_upload_time('build', time.time() - start)

            # Chunked upload: the .wfm file is only complete (and sent) on the last chunk
            if wfm_data is None:
                waveforms.append(wfm_name)
                continue

            start = time.time()
            try:
                self._send_bytes(wfm_name + '.wfm', wfm_data)
            except ftp_errors as exc:
                self.log.error('write_waveform: FTP upload of "{0}" failed: {1}'.format(wfm_name, exc))
                return -1, waveforms
            self._add_upload_time('ftp', time.time() - start, n_bytes=len(wfm_data))

            start = time.time()
            self.write('MMEM:IMP "{0}","{1}",WFM'.format(wfm_name, wfm_name + '.wfm'))
            if not self._wait_opc(timeout=30.0, context='importing "{0}"'.format(wfm_name)):
                self.log.error(
                    'write_waveform: MMEM:IMP for "{0}" did not complete.\n'
                    'This commonly indicates the AWG has run out of waveform memory and '
                    'silently rejected the import. Checking error queue...'.format(wfm_name))
                self.get_errors()
                return -1, waveforms
            self._add_upload_time('import', time.time() - start)

            # Check the error queue and confirm the import with ONE length query, instead of
            # re-reading the whole waveform list (one query per waveform on the device).
            start = time.time()
            if self.get_errors():
                self.log.error(
                    'write_waveform: AWG reported error(s) while importing "{0}". See messages '
                    'above. Likely cause: AWG waveform memory exhausted after uploading '
                    'multiple large unique waveforms.'.format(wfm_name))
                return -1, waveforms
            try:
                imported_length = int(self.query('WLIS:WAV:LENG? "{0}"'.format(wfm_name)))
            except Exception as exc:
                imported_length = None
                self.log.error('write_waveform: could not query length of imported waveform '
                               '"{0}": {1}'.format(wfm_name, exc))
            if imported_length != total_number_of_samples:
                self.log.error(
                    'write_waveform: "{0}" is not in the AWG workspace with the expected length '
                    'after import (expected {1} samples, AWG reports {2}).\n'
                    'This strongly suggests AWG waveform memory is exhausted. Consider: reducing '
                    'total waveform count (reuse waveforms via sequence repetitions where '
                    'possible), reducing per-waveform sample count, or enabling AWG memory '
                    'expansion option 01 if not already installed.'
                    ''.format(wfm_name, total_number_of_samples, imported_length))
                self._waveform_names_cache = None
                return -1, waveforms
            if self._waveform_names_cache is not None:
                self._waveform_names_cache.add(wfm_name)
            self._add_upload_time('verify', time.time() - start, n_waveforms=1)

            waveforms.append(wfm_name)
        return total_number_of_samples, waveforms

    def write_sequence(self, name, sequence_parameter_list):
        """
        Write a new sequence on the device memory.

        @return int: number of sequence steps written (-1 indicates failed process)
        """
        if not self._has_sequence_mode():
            self.log.error(
                'Direct sequence generation in AWG not possible. '
                'Sequencer option not installed.'
            )
            return -1

        # Config option: clear_device_before_upload.
        # Only fires if no write_waveform() call already cleared the device
        # for this batch (see _pending_clear_before_upload docstring).
        if self._clear_device_before_upload and self._pending_clear_before_upload:
            self.log.info(
                'clear_device_before_upload enabled: clearing all waveforms '
                'and sequences on AWG before uploading sequence "{0}".'
                ''.format(name)
            )
            self.clear_all()
            self._pending_clear_before_upload = False

        num_steps = len(sequence_parameter_list)
        max_steps = self.get_constraints().sequence_steps.max

        if num_steps > max_steps:
            self.log.error(
                'Unable to write sequence "{0}".\n'
                'Requested {1} sequence steps exceeds the hardware maximum '
                'of {2} steps for model {3}.'
                ''.format(name, num_steps, max_steps, self.model)
            )
            return -1

        avail_waveforms = set(self.get_waveform_names())
        for waveform_tuple, param_dict in sequence_parameter_list:
            if not avail_waveforms.issuperset(waveform_tuple):
                self.log.error(
                    'Failed to create sequence "{0}" due to waveforms "{1}" '
                    'not present in device memory.'.format(name, waveform_tuple)
                )
                return -1

        active_analog = natural_sort(
            chnl for chnl, active in self._internal_ch_state.items() if active
        )
        num_tracks = len(active_analog)

        if num_tracks == 0:
            self.log.error(
                'write_sequence: no active analog channels found in '
                '_internal_ch_state = {0}.'.format(self._internal_ch_state)
            )
            return -1

        self.log.debug(
            'write_sequence: _internal_ch_state={0}, '
            'active_analog={1}, num_tracks={2}, num_steps={3}/{4}'
            ''.format(self._internal_ch_state, active_analog, num_tracks,
                      num_steps, max_steps)
        )

        # Drain any pre-existing errors so the queue is clean before we start
        self.get_errors()

        seq_start = time.time()

        # Write all steps relying on the element defaults the AWG assigns after SEQ:LENG
        # (TWAIT OFF, LOOP:INF OFF, LOOP:COUNT 1, GOTO:STATE OFF). These defaults are confirmed
        # on the device once per session; if they do not hold, the sequence is rewritten with
        # every command sent explicitly.
        skip_defaults = True
        result = self._write_sequence_steps(sequence_parameter_list, skip_defaults=True)
        if result == 0 and not self._sequence_defaults_verified:
            verified = self._verify_sequence_defaults(sequence_parameter_list)
            if verified is None:
                pass  # no step relies on the defaults, nothing to verify
            elif verified:
                self._sequence_defaults_verified = True
            else:
                self.log.warning(
                    'write_sequence: AWG sequencer element defaults differ from the expected '
                    '(TWAIT OFF, LOOP:INF OFF, LOOP:COUNT 1, GOTO:STATE OFF). Rewriting the '
                    'sequence with every element parameter set explicitly.')
                skip_defaults = False
                result = self._write_sequence_steps(sequence_parameter_list, skip_defaults=False)
        if result < 0:
            return -1

        self._add_upload_time('sequence', time.time() - seq_start)

        self._written_sequences = [name]
        self.log.info(
            'write_sequence: successfully wrote {0} steps for sequence "{1}"{2}.'
            ''.format(num_steps, name, '' if skip_defaults else ' (all parameters explicit)')
        )
        self._log_upload_stats('sequence "{0}"'.format(name))
        return num_steps

    def _sequence_step_commands(self, step, wfm_tuple, seq_params, skip_defaults):
        """
        SCPI commands for one sequence element.

        @param bool skip_defaults: omit commands that only restate the element defaults set
            by SEQ:LENG (TWAIT OFF, LOOP:INF OFF, LOOP:COUNT 1, GOTO:STATE OFF).

        @return list of str, or None if the step parameters are invalid.
        """
        commands = list()
        for waveform in wfm_tuple:
            try:
                ch_num = int(waveform.rsplit('_ch', 1)[1])
            except (ValueError, IndexError):
                self.log.error(
                    'write_sequence: cannot extract channel number from waveform name "{0}" at '
                    'step {1}.'.format(waveform, step))
                return None
            commands.append('SEQ:ELEM{0:d}:WAV{1:d} "{2}"'.format(step, ch_num, waveform))

        jumpto = seq_params['event_jump_to']
        if jumpto > 0:
            commands.append('SEQ:ELEM{0:d}:JTAR:TYPE INDEX'.format(step))
            commands.append('SEQ:ELEM{0:d}:JTAR:INDEX {1}'.format(step, jumpto))

        trigger = self._event_triggers.get(seq_params['wait_for'])
        if trigger is None:
            self.log.error('Invalid trigger specifier "{0}" at step {1}.\nPlease choose one of: '
                           '"OFF", "ON"'.format(seq_params['wait_for'], step))
        elif trigger != 'OFF':
            commands.append('SEQ:ELEM{0:d}:TWAIT ON'.format(step))
        elif not skip_defaults:
            commands.append('SEQ:ELEM{0:d}:TWAIT OFF'.format(step))

        repeat = seq_params['repetitions']
        if repeat < 0:
            commands.append('SEQ:ELEM{0:d}:LOOP:INFINITE ON'.format(step))
        else:
            if not skip_defaults:
                commands.append('SEQ:ELEM{0:d}:LOOP:INFINITE OFF'.format(step))
            if repeat != 0 or not skip_defaults:
                commands.append('SEQ:ELEM{0:d}:LOOP:COUNT {1:d}'.format(step, repeat + 1))

        goto = seq_params['go_to']
        if goto > 0:
            commands.append('SEQ:ELEM{0:d}:GOTO:STATE ON'.format(step))
            commands.append('SEQ:ELEM{0:d}:GOTO:INDEX {1:d}'.format(step, int(goto)))
        elif not skip_defaults:
            commands.append('SEQ:ELEM{0:d}:GOTO:STATE OFF'.format(step))
        return commands

    def _write_sequence_steps(self, sequence_parameter_list, skip_defaults):
        """
        (Re-)allocates the sequence and writes all its elements. The commands of up to
        opc_check_interval steps are sent as ONE VISA write (joined with ';:'), followed by an
        *OPC? / error queue checkpoint, so a failure is still reported with its step range.

        @return int: 0 on success, -1 on failure.
        """
        num_steps = len(sequence_parameter_list)

        self.write('SEQ:LENG 0')
        self.write('SEQ:LENG {0:d}'.format(num_steps))
        try:
            current_len = int(self.query('SEQ:LENG?'))
        except Exception as exc:
            self.log.error(
                'write_sequence: could not read back SEQ:LENG after setting it to {0}. '
                'Communication error: {1}'.format(num_steps, exc))
            return -1
        if current_len != num_steps:
            self.log.error(
                'write_sequence: SEQ:LENG readback mismatch. Requested {0} steps, AWG reports {1} '
                'steps allocated.\nThis usually means the sequence memory could not be allocated '
                '(e.g. due to a prior incomplete sequence, or insufficient AWG sequence memory).'
                ''.format(num_steps, current_len))
            return -1

        opc_check_interval = 20
        commands = list()
        first_step = 1
        for step, (wfm_tuple, seq_params) in enumerate(sequence_parameter_list, 1):
            step_commands = self._sequence_step_commands(step, wfm_tuple, seq_params, skip_defaults)
            if step_commands is None:
                return -1
            commands.extend(step_commands)

            if step % opc_check_interval != 0 and step != num_steps:
                continue

            if commands:
                self.write(';:'.join(commands))
            commands = list()

            context = 'sequence steps {0}-{1}'.format(first_step, step)
            if not self._wait_opc(timeout=10.0, context=context):
                self.log.error(
                    'write_sequence: AWG did not complete pending operations after steps '
                    '{0}-{1}/{2}. Upload appears STUCK - aborting.'.format(first_step, step, num_steps))
                return -1
            if self.get_errors():
                self.log.error(
                    'write_sequence: AWG reported error(s) in steps {0}-{1}/{2}. See error '
                    'messages above for details. Aborting upload.'.format(first_step, step, num_steps))
                return -1
            self.log.debug('write_sequence: checkpoint OK at step {0}/{1}.'.format(step, num_steps))
            first_step = step + 1
        return 0

    def _verify_sequence_defaults(self, sequence_parameter_list):
        """
        Checks on the device that a step written without explicit TWAIT/LOOP/GOTO commands
        really has the expected defaults.

        @return bool or None: True/False, or None if no step relies on the defaults.
        """
        for step, (_, seq_params) in enumerate(sequence_parameter_list, 1):
            if (seq_params['repetitions'] == 0 and seq_params['go_to'] <= 0
                    and seq_params['wait_for'] == 'OFF'):
                break
        else:
            return None

        def _is_off(answer):
            return answer.upper() in ('0', 'OFF')

        try:
            ok = (_is_off(self.query('SEQ:ELEM{0:d}:TWAIT?'.format(step)))
                  and _is_off(self.query('SEQ:ELEM{0:d}:LOOP:INFINITE?'.format(step)))
                  and int(self.query('SEQ:ELEM{0:d}:LOOP:COUNT?'.format(step))) == 1
                  and _is_off(self.query('SEQ:ELEM{0:d}:GOTO:STATE?'.format(step))))
        except Exception as exc:
            self.log.warning('write_sequence: could not query defaults of sequence step {0}: {1}'
                             ''.format(step, exc))
            ok = False
        self.log.debug('write_sequence: sequencer element defaults verified on step {0}: {1}'
                       ''.format(step, ok))
        return ok

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

    def get_waveform_names(self, refresh=False):
        """ Retrieve the names of all uploaded waveforms on the device.

        Served from a local mirror of the AWG waveform list, which is kept in sync on every
        import/delete/clear done through this module. The device itself (WLIS:SIZE? plus one
        WLIS:NAME? query per waveform) is only read if the mirror is unknown, e.g. after
        activation or clear_all(), or if refresh=True.

        @param bool refresh: force re-reading the list from the device.

        @return list: List of all uploaded waveform name strings in the device workspace.
        """
        if refresh or self._waveform_names_cache is None:
            wfm_list_len = int(self.query('WLIS:SIZE?'))
            self._waveform_names_cache = {
                self.query('WLIS:NAME? {0:d}'.format(index)) for index in range(wfm_list_len)
            }
        return natural_sort(self._waveform_names_cache)

    def get_sequence_names(self):
        """ Retrieve the names of all uploaded sequences on the device.

        @return list: List of all uploaded sequence name strings in the device workspace.
        """
        return self._written_sequences

    def delete_waveform(self, waveform_name):
        """ Delete the waveform with name "waveform_name" from the device memory.

        @param str waveform_name: The name of the waveform to be deleted

        @return list: a list of deleted waveform names.
        """
        if isinstance(waveform_name, str):
            waveform_name = [waveform_name]

        avail_waveforms = set(self.get_waveform_names())
        deleted_waveforms = list()
        for waveform in waveform_name:
            if waveform in avail_waveforms:
                self.write('WLIS:WAV:DEL "{0}"'.format(waveform))
                self._waveform_names_cache.discard(waveform)
                deleted_waveforms.append(waveform)
        return natural_sort(deleted_waveforms)

    def delete_sequence(self, sequence_name):
        """ Delete the sequence with name "sequence_name" from the device memory.

        @return list: a list of deleted sequence names.
        """
        self.write('SEQUENCE:LENGTH 0')
        return list()

    def get_interleave(self):
        """ Check whether Interleave is ON or OFF in AWG.

        @return bool: True: ON, False: OFF
        """
        if self._has_interleave():
            return bool(int(self.query('AWGC:INT:STAT?')))
        return False

    def set_interleave(self, state=False):
        """ Turns the interleave of an AWG on or off.

        @param bool state: The state the interleave should be set to

        @return bool: actual interleave status
        """
        if not isinstance(state, bool):
            return self.get_interleave()

        if state is self.get_interleave():
            return state

        if self._has_interleave():
            self.write('AWGC:INT:STAT {0:d}'.format(int(state)))
            # FIX (#2): bounded wait instead of unbounded loop
            self._wait_opc(timeout=10.0, context='set_interleave')
        return self.get_interleave()

    def write(self, command):
        """ Sends a command string to the device.

        @param string command: string containing the command

        @return int: error code (0:OK, -1:error)
        """
        try:
            bytes_written = self.awg.write(command)
        except Exception as exc:
            self.log.error(
                'VISA write failed for command "{0}": {1}'.format(command, exc)
            )
            return -1
        return 0

    def query(self, question):
        """ Asks the device a 'question' and receive and return an answer from it.

        @param string question: string containing the command

        @return string: the answer of the device to the 'question' in a string
        """
        answer = self.awg.query(question)
        answer = answer.strip()
        answer = answer.rstrip('\n')
        answer = answer.rstrip()
        answer = answer.strip('"')
        return answer

    def reset(self):
        """ Reset the device.

        @return int: error code (0:OK, -1:error)
        """
        self.write('*RST')
        self.write('*WAI')
        self._waveform_names_cache = None
        self._sample_rate_cache = None
        return 0

    def set_lowpass_filter(self, a_ch, cutoff_freq):
        """ Set a lowpass filter to the analog channels of the AWG.

        @param int a_ch: To which channel to apply, either 1 or 2.
        @param cutoff_freq: Cutoff Frequency of the lowpass filter in Hz.
        """
        if a_ch not in (1, 2):
            return
        self.write('OUTPUT{0:d}:FILTER:LPASS:FREQUENCY {1:f}MHz'.format(a_ch, cutoff_freq / 1e6))

    def set_jump_timing(self, synchronous=False):
        """Sets control of the jump timing in the AWG.

        @param bool synchronous: if True the jump timing will be set to synchronous
        """
        timing = 'SYNC' if synchronous else 'ASYNC'
        self.write('EVEN:JTIM {0}'.format(timing))

    def set_mode(self, mode):
        """Change the output mode of the AWG7000 series.

        @param str mode: Options for mode (case-insensitive):
                            continuous - 'C'
                            triggered  - 'T'
                            gated      - 'G'
                            sequence   - 'S'
        """
        look_up = {'C': 'CONT',
                   'T': 'TRIG',
                   'G': 'GAT',
                   'E': 'ENH',
                   'S': 'SEQ'}
        self.write('AWGC:RMOD {0!s}'.format(look_up[mode.upper()]))

    def get_sequencer_mode(self, output_as_int=False):
        """ Asks the AWG which sequencer mode it is using.

        @return: str or int
        """
        if self._has_sequence_mode():
            message = self.query('AWGC:SEQ:TYPE?')
            if 'HARD' in message:
                return 0 if output_as_int else 'Hardware-Sequencer'
            elif 'SOFT' in message:
                return 1 if output_as_int else 'Software-Sequencer'
        return -1 if output_as_int else 'Request-Error'

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

    def _delete_file(self, filename):
        """ Delete a file from FTP working directory, if present. """
        def _delete(ftp):
            try:
                ftp.delete(filename)
            except error_perm:
                pass  # file does not exist
        self._ftp_run(_delete)

    def _send_bytes(self, filename, data):
        """
        Upload a bytes object as file to the AWG via FTP. STOR overwrites an existing file;
        only if the server refuses that, the file is deleted first and the upload retried.
        """
        def _store(ftp):
            try:
                ftp.storbinary('STOR ' + filename, io.BytesIO(data))
            except error_perm:
                ftp.delete(filename)
                ftp.storbinary('STOR ' + filename, io.BytesIO(data))
        self._ftp_run(_store)
        return 0

    def _send_file(self, filename):
        """ Upload a file from the local tmp work dir to the AWG via FTP. """
        if not filename:
            self.log.error('No filename provided for file upload to awg!\nCommand will be ignored.')
            return -1

        filepath = os.path.join(self._tmp_work_dir, filename)
        if not os.path.isfile(filepath):
            self.log.error('No file "{0}" found in "{1}". Unable to upload!'
                           ''.format(filename, self._tmp_work_dir))
            return -1

        with open(filepath, 'rb') as file:
            return self._send_bytes(filename, file.read())

    def _get_filenames_on_device(self):
        """ Get list of filenames on device FTP directory. """
        log = list()

        def _list(ftp):
            log.clear()
            ftp.retrlines('LIST', callback=log.append)
        self._ftp_run(_list)

        filename_list = list()
        for line in log:
            if '<DIR>' not in line:
                size_filename = line[18:].lstrip()
                filename = size_filename.split(' ', 1)[1].strip()
                filename_list.append(filename)
        return filename_list

    def _get_all_channels(self):
        """ Helper method to return a sorted list of all technically available channel descriptors. """
        avail_channels = ['a_ch1', 'd_ch1', 'd_ch2']
        if not self.get_interleave():
            avail_channels.extend(['a_ch2', 'd_ch3', 'd_ch4'])
        return natural_sort(avail_channels)

    def _get_all_analog_channels(self):
        """ Helper method to return a sorted list of all analog channel descriptors. """
        return natural_sort(chnl for chnl in self._get_all_channels() if chnl.startswith('a'))

    def _get_all_digital_channels(self):
        """ Helper method to return a sorted list of all digital channel descriptors. """
        return natural_sort(chnl for chnl in self._get_all_channels() if chnl.startswith('d'))

    def _is_output_on(self):
        """ Ask the AWG if the output is enabled.

        @return bool: True: output on, False: output off
        """
        return bool(int(self.query('AWGC:RST?')))

    def _zeroing_enabled(self):
        """ Check if the zeroing option is enabled. """
        if self._has_interleave():
            return bool(int(self.query('AWGC:INT:ZER?')))
        return False

    def _has_interleave(self):
        """ Check if the device has the interleave option installed. """
        return '06' in self.installed_options

    # Packed (5 bytes/sample) .wfm sample record: float32 analog value + uint8 marker bits.
    _WFM_SAMPLE_DTYPE = np.dtype([('f0', '<f4'), ('f1', 'u1')])

    def _write_wfm(self, filename, analog_samples, marker_bytes, is_first_chunk, is_last_chunk,
                   total_number_of_samples):
        """
        Appends a sampled chunk of a whole waveform to an in-memory .wfm file.

        @return bytes or None: the complete .wfm file content on the last chunk, else None.
        """
        if is_first_chunk:
            num_bytes = str(int(total_number_of_samples * self._WFM_SAMPLE_DTYPE.itemsize))
            header = 'MAGIC 1000\r\n#{0}{1}'.format(len(num_bytes), num_bytes)
            self._wfm_buffers[filename] = io.BytesIO()
            self._wfm_buffers[filename].write(header.encode())

        records = np.zeros(len(analog_samples), dtype=self._WFM_SAMPLE_DTYPE)
        records['f0'] = analog_samples
        if marker_bytes is not None:
            records['f1'] = marker_bytes
        self._wfm_buffers[filename].write(records.tobytes())

        if not is_last_chunk:
            return None

        footer = 'CLOCK {0:16.10E}\r\n'.format(self._get_cached_sample_rate())
        buffer = self._wfm_buffers.pop(filename)
        buffer.write(footer.encode())
        return buffer.getvalue()

    def _get_cached_sample_rate(self):
        """ Sample rate for the .wfm footer, queried once and refreshed in set_sample_rate. """
        if self._sample_rate_cache is None:
            self._sample_rate_cache = self.get_sample_rate()
        return self._sample_rate_cache

    def _configure_trigger_input_only(self, context='SEQ mode'):
        """
        Configure the trigger input hardware WITHOUT changing AWGC:RMOD.

        Sets TRIG:SOUR EXT, TRIG:LEV, TRIG:SLOP and TRIG:IMP. Used both:
          - directly, when AWGC:RMOD is already SEQ (set by load_sequence())
            so that TWAIT on sequence step 1 responds to the same external
            BNC trigger as in TRIG/GAT waveform mode
          - internally by _configure_trigger_mode(), which additionally sets
            AWGC:RMOD before delegating input configuration here

        FIX (#4): this method and _configure_trigger_mode() previously
        duplicated ~90% of their code (slope/impedance validation, the
        TRIG:* writes, OPC wait, error check, and readback+log). They are
        now merged: _configure_trigger_mode() sets the run mode then calls
        this method for everything else.

        @param str context: label used in the log message, e.g. 'SEQ mode'
                            or 'TRIG mode' / 'GAT mode'
        """
        slope = str(self._trigger_slope).upper()
        if slope not in ('POS', 'NEG'):
            self.log.warning(
                'Invalid trigger_slope "{0}", falling back to "POS".'.format(slope))
            slope = 'POS'

        imp = str(self._trigger_impedance).upper()
        if imp not in ('50OHM', '1KOHM'):
            self.log.warning(
                'Invalid trigger_impedance "{0}", falling back to "50OHM".'.format(imp))
            imp = '50OHM'

        self.write('TRIG:IMP {0}'.format(imp))
        self.write('TRIG:SOUR EXT')
        self.write('TRIG:LEV {0:.4f}'.format(self._trigger_level))
        self.write('TRIG:SLOP {0}'.format(slope))

        # FIX (#2): bounded wait instead of unbounded loop
        self._wait_opc(timeout=5.0, context='_configure_trigger_input_only')

        self.get_errors()

        try:
            src  = self.query('TRIG:SOUR?')
            lev  = self.query('TRIG:LEV?')
            slop = self.query('TRIG:SLOP?')
            imq  = self.query('TRIG:IMP?')
            self.log.info(
                '{0} trigger input configured -- source: {1}  level: {2} V  '
                'slope: {3}  impedance: {4}'.format(context, src, lev, slop, imq)
            )
        except Exception as exc:
            self.log.debug('Could not read back trigger settings: {0}'.format(exc))

    def _configure_trigger_mode(self, mode):
        """
        Configure AWG hardware for TRIG or GAT operation.
        Sets AWGC:RMOD to TRIG or GAT, then delegates trigger input
        configuration to _configure_trigger_input_only() to avoid
        duplicating that logic (see FIX #4 note there).

        @param str mode: 'TRIG' or 'GAT'
        """
        mode_map = {'TRIG': 'T', 'GAT': 'G'}
        self.set_mode(mode_map[mode])

        # FIX (#2): bounded wait instead of unbounded loop
        self._wait_opc(timeout=5.0, context='_configure_trigger_mode:set_mode')

        self._configure_trigger_input_only(context='{0} mode'.format(mode))

    def sequence_set_waveform(self, waveform_name, step, track):
        """
        Set the waveform 'waveform_name' to position 'step' in the sequence.

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        self.write('SEQ:ELEM{0:d}:WAV{1} "{2}"'.format(step, track, waveform_name))
        return 0

    def sequence_set_repetitions(self, step, repeat=1):
        """
        Set the repetition counter at step "step".

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1
        if repeat < 0:
            self.write('SEQ:ELEM{0:d}:LOOP:INFINITE ON'.format(step))
        else:
            self.write('SEQ:ELEM{0:d}:LOOP:INFINITE OFF'.format(step))
            self.write('SEQ:ELEM{0:d}:LOOP:COUNT {1:d}'.format(step, repeat + 1))
        return 0

    def sequence_set_goto(self, step, goto=-1):
        """
        Set the go_to parameter for a sequence step.

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        if goto > 0:
            goto = str(int(goto))
            self.write('SEQ:ELEM{0:d}:GOTO:STATE ON'.format(step))
            self.write('SEQ:ELEM{0:d}:GOTO:INDEX {1}'.format(step, goto))
        else:
            self.write('SEQ:ELEM{0:d}:GOTO:STATE OFF'.format(step))
        return 0

    def sequence_set_event_jump(self, step, jumpto=0):
        """
        Set the event trigger input of the specified sequence step.

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        if jumpto > 0:
            self.write('SEQ:ELEM{0:d}:JTAR:TYPE INDEX'.format(step))
            self.write('SEQ:ELEM{0:d}:JTAR:INDEX {1}'.format(step, jumpto))
        return 0

    def sequence_set_wait_trigger(self, step, trigger='OFF'):
        """
        Make a certain sequence step wait for a trigger to start playing.

        @param int step: Sequence step to be edited
        @param str trigger: 'OFF' or 'ON'

        @return int: error code
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        trigger = self._event_triggers.get(trigger)
        if trigger is None:
            self.log.error('Invalid trigger specifier.\nPlease choose one of: "OFF", "ON"')
            return -1

        if trigger != 'OFF':
            self.write('SEQ:ELEM{0:d}:TWAIT ON'.format(step))
        else:
            self.write('SEQ:ELEM{0:d}:TWAIT OFF'.format(step))

        return 0

    def make_sequence_continuous(self):
        """
        Make the sequence loop infinitely by setting the last element go_to to First.

        @return int: last step number, or -1 on error
        """
        if not self._has_sequence_mode():
            self.log.error('Direct sequence generation in AWG not possible. '
                           'Sequencer option not installed.')
            return -1

        last_step = int(self.query('SEQ:LENG?'))
        err = self.sequence_set_goto(last_step, 1)
        if err < 0:
            last_step = err
        return last_step

    def force_jump_sequence(self, final_step, channel=1):
        """
        Force the sequencer to jump to the specified step.

        @param channel: channel number (default 1)
        @param final_step: step to jump to
        """
        self.write('SOURCE{0:d}:JUMP:FORCE {1}'.format(channel, final_step))
        return

    def get_errors(self):
        """
        Get all errors from the device and log them.

        @return bool: whether any error was found
        """
        next_err = True
        has_error = False
        while next_err:
            err = self.query('SYST:ERR?').split(',')
            if int(err[0]) == 0:
                next_err = False
            else:
                self.log.error('{0} error: {1} {2}'.format(self.model, err[0], err[1]))
                has_error = True
        return has_error

    def _has_sequence_mode(self):
        """ Check if sequence mode is available. """
        if self.model == 'AWG7052':
            return True
        else:
            return '08' in self.installed_options