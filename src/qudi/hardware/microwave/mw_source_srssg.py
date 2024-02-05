# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware file to control Stanford Research Systems signal generators.
Both basic (SG382, SG384, SG386) and vector generators (SG392, SG394, SG396) should work with this module.
Manual and technical specifications can be found here:
    - https://www.thinksrs.com/products/sg380.html (basic RF generators)
    - https://www.thinksrs.com/products/sg390.html (vector signal generators)

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


try:
    import pyvisa as visa
    from pyvisa.resources import MessageBasedResource
except ImportError:
    import visa
    from visa.resources import MessageBasedResource
import time
import numpy as np

from qudi.util.mutex import Mutex
from qudi.core.configoption import ConfigOption
from qudi.interface.microwave_interface import MicrowaveInterface, MicrowaveConstraints
from qudi.util.enums import SamplingOutputMode


class MicrowaveSRSSG(MicrowaveInterface):
    """ Hardware control class to controls SRS SG390 devices.

    Example config for copy-paste:

    mw_source_srssg:
        module.Class: 'microwave.mw_source_srssg.MicrowaveSRSSG'
        options:
            visa_address: 'GPIB0::12::INSTR'
            comm_timeout: 10
    """
    _visa_address: str = ConfigOption('visa_address', missing='error')
    _comm_timeout: float = ConfigOption('comm_timeout', default=10, missing='warn')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = Mutex()
        self._rm = None
        self._device = None
        self._is_vector_sg = False
        self._constraints = None
        self._scan_power = -20
        self._scan_frequencies = None
        self._scan_sample_rate = 0.
        self._in_cw_mode = True

    def on_activate(self):
        """ Initialisation performed during activation of the module. """
        # trying to load the visa connection to the module
        self._rm = visa.ResourceManager()
        self._device: MessageBasedResource = self._rm.open_resource(  # type: ignore
            self._visa_address,
            timeout=int(self._comm_timeout * 1000),
            read_termination='\r\n'
        )

        # Reset device
        self._write('*RST')
        self._write('ENBR 0')  # turn off Type N output
        self._write('ENBL 0')  # turn off BNC output

        # model identifiers are of the form SG3XY:
        # X: 8 for signal generator, 9 for vector signal generator
        # Y: specifies the maximum frequency of the N type output
        model = self._device.query('*IDN?').strip().split(',')[1]
        if not model.startswith('SG3'):
            raise ValueError(f'Unknown model identifier "{model}". Is the address correct?')

        max_freq = model[4]
        if max_freq == '2':
            freq_limits = (1e6, 2.025e9)
        elif max_freq == '4':
            freq_limits = (1e6, 4.050e9)
        elif max_freq == '6':
            freq_limits = (1e6, 6.075e9)
        else:
            raise ValueError(f'Unknown model identifier "{model}". Is the address correct?')

        vector = model[3]
        if vector == '8':
            self._is_vector_sg = False
        elif vector == '9':
            self._is_vector_sg = True
        else:
            raise ValueError(f'Unknown model identifier "{model}". Is the address correct?')

        self._constraints = MicrowaveConstraints(
            power_limits=(-110, 16.5),
            frequency_limits=freq_limits,
            scan_size_limits=(2, 2000),
            sample_rate_limits=(1e-6, 50e3),
            scan_modes=(SamplingOutputMode.JUMP_LIST,)
        )

        self._scan_frequencies = None
        self._scan_power = self._constraints.min_power
        self._scan_sample_rate = self._constraints.max_sample_rate
        self._in_cw_mode = True

    def on_deactivate(self):
        """ Cleanup performed during deactivation of the module."""
        self.off()
        self._device.close()
        self._rm.close()
        self._device = None
        self._rm = None

    @property
    def constraints(self):
        return self._constraints

    @property
    def is_scanning(self):
        """Read-Only boolean flag indicating if a scan is running at the moment. Can be used together with
        module_state() to determine if the currently running microwave output is a scan or CW.
        Should return False if module_state() is 'idle'.

        @return bool: Flag indicating if a scan is running (True) or not (False)
        """
        with self._thread_lock:
            return (self.module_state() != 'idle') and not self._in_cw_mode

    @property
    def cw_power(self):
        """The CW microwave power in dBm. Must implement setter as well.

        @return float: The currently set CW microwave power in dBm.
        """
        with self._thread_lock:
            return float(self._device.query('AMPR?'))

    @property
    def cw_frequency(self):
        """The CW microwave frequency in Hz. Must implement setter as well.

        @return float: The currently set CW microwave frequency in Hz.
        """
        with self._thread_lock:
            return float(self._device.query('FREQ?'))

    @property
    def scan_power(self):
        """The microwave power in dBm used for scanning. Must implement setter as well.

        @return float: The currently set scanning microwave power in dBm
        """
        with self._thread_lock:
            return self._scan_power

    @property
    def scan_frequencies(self):
        """The microwave frequencies used for scanning. Must implement setter as well.

        In case of scan_mode == SamplingOutputMode.JUMP_LIST, this will be a 1D numpy array.
        In case of scan_mode == SamplingOutputMode.EQUIDISTANT_SWEEP, this will be a tuple
        containing 3 values (freq_begin, freq_end, number_of_samples).
        If no frequency scan has been specified, return None.

        @return float[]: The currently set scanning frequencies. None if not set.
        """
        with self._thread_lock:
            return self._scan_frequencies

    @property
    def scan_mode(self):
        """Scan mode Enum. Must implement setter as well.

        @return SamplingOutputMode: The currently set scan mode Enum
        """
        with self._thread_lock:
            return SamplingOutputMode.JUMP_LIST

    @property
    def scan_sample_rate(self):
        """Read-only property returning the currently configured scan sample rate in Hz.

        @return float: The currently set scan sample rate in Hz
        """
        with self._thread_lock:
            return self._scan_sample_rate

    def set_cw(self, frequency, power):
        """Configure the CW microwave output. Does not start physical signal output, see also
        "cw_on".

        @param float frequency: frequency to set in Hz
        @param float power: power to set in dBm
        """
        with self._thread_lock:
            if self.module_state() != 'idle':
                raise RuntimeError('Unable to set CW parameters. Microwave output active.')
            self._assert_cw_parameters_args(frequency, power)

            # disable modulation:
            self._write('MODL 0')
            if self._is_vector_sg:
                # set the modulation subtype to analog
                self._write('STYP 0')
            self._write(f'FREQ {frequency:e}')
            self._write(f'AMPR {power:f}')

    def configure_scan(self, power, frequencies, mode, sample_rate):
        """
        """
        with self._thread_lock:
            # Sanity checks
            if self.module_state() != 'idle':
                raise RuntimeError('Unable to configure frequency scan. Microwave output active.')
            self._assert_scan_configuration_args(power, frequencies, mode, sample_rate)

            # configure scan according to scan mode
            self._scan_sample_rate = sample_rate
            self._scan_power = power
            self._scan_frequencies = np.asarray(frequencies, dtype=np.float64)
            self._write_list()

    def off(self):
        """Switches off any microwave output (both scan and CW).
        Must return AFTER the device has actually stopped.
        """
        with self._thread_lock:
            if self.module_state() != 'idle':
                self._write('ENBR 0')
                while self._output_active():
                    time.sleep(0.1)
                self.module_state.unlock()

    def cw_on(self):
        """ Switches on cw microwave output.

        Must return AFTER the output is actually active.
        """
        with self._thread_lock:
            if self.module_state() != 'idle':
                if self._in_cw_mode:
                    return
                raise RuntimeError(
                    'Unable to start CW microwave output. Microwave output is currently active.'
                )

            self._in_cw_mode = True
            self._rf_on()
            self.module_state.lock()

    def start_scan(self):
        """Switches on the microwave scanning.

        Must return AFTER the output is actually active (and can receive triggers for example).
        """
        with self._thread_lock:
            if self.module_state() != 'idle':
                if not self._in_cw_mode:
                    return
                raise RuntimeError('Unable to start frequency scan. CW microwave output is active.')
            assert self._scan_frequencies is not None, \
                'No scan_frequencies set. Unable to start scan.'

            self._in_cw_mode = False
            self._rf_on()
            self.module_state.lock()

    def reset_scan(self):
        """Reset currently running scan and return to start frequency.
        Does not need to stop and restart the microwave output if the device allows soft scan reset.
        """
        with self._thread_lock:
            if self.module_state() == 'idle':
                return
            if self._in_cw_mode:
                raise RuntimeError('Can not reset frequency scan. CW microwave output active.')

            self._write('LSTR')

    def _write(self, command: str) -> None:
        self._device.write(command)
        err = self._device.query('LERR?')
        if err != '0':
            raise RuntimeError(f'Error code {err} received while sending command {command}.')

    def _write_list(self):
        # delete a previously created list:
        self._write('LSTD')

        # ask for a new list
        success = self._device.query(f'LSTC? {len(self._scan_frequencies):d}')
        if success:
            self.log.debug('Successfully created a new list.')
        else:
            raise RuntimeError('List creation was unsuccessful.')

        for ii, freq in enumerate(self._scan_frequencies):
            self._write(
                # cycle the frequency, set the power, display the frequency
                f'LSTP {ii:d},{freq:e},N,N,N,{self._scan_power:f},2,N,N,N,N,N,N,N,N,N'
            )
        # the commands contains 15 entries, which are related to the
        # following commands (in brackets the explanation), if parameter is
        # specified as 'N', then it will be left unchanged.
        #
        #   '1,2,3,4,5,6,7,8,9,10,11,12,13,14,15'
        #
        #   Position explanation:
        #
        #   1 = FREQ (frequency in exponential representation: e.g. 1.45e9)
        #   2 = PHAS (phase in degree as float, e.g.45.0 )
        #   3 = AMPL (Amplitude of LF in dBm as float, BNC output, e.g. -45.0)
        #   4 = OFSL (Offset of LF in Volt as float, BNC output, e.g. 0.02)
        #   5 = AMPR (Amplitude of RF in dBm as float, Type N output, e.g. -45.0)
        #   6 = DISP (set the Front panel display type as integer)
        #           0: Modulation Type
        #           1: Modulation Function
        #           2: Frequency
        #           3: Phase
        #           4: Modulation Rate or Period
        #           5: Modulation Deviation or Duty Cycle
        #           6: RF Type N Amplitude
        #           7: BNC Amplitude
        #           10: BNC Offset
        #           13: I Offset
        #           14: Q Offset
        #   7 = Enable/Disable modulation by an integer number, with the
        #       following bit meaning:
        #           Bit 0: MODL (Enable modulation)
        #           Bit 1: ENBL (Disable LF, BNC output)
        #           Bit 2: ENBR (Disable RF, Type N output)
        #           Bit 3:  -   (Disable Clock output)
        #           Bit 4:  -   (Disable HF, RF doubler output)
        #   8 = TYPE (Modulation type, integer number with the meaning)
        #           0: AM/ASK   (amplitude modulation)
        #           1: FM/FSK   (frequency modulation)
        #           2: ΦM/PSK   (phase modulation)
        #           3: Sweep
        #           4: Pulse
        #           5: Blank
        #           7: QAM (quadrature amplitude modulation)
        #           8: CPM (continuous phase modulation)
        #           9: VSB (vestigial/single sideband modulation)
        #   9 = Modulation function, integer number. Note that not all
        #       values are valid in all modulation modes. In brackets
        #       behind the possible modulation functions are denoted with
        #       the meaning: MFNC = AM/FM/ΦM,  SFNC = Sweep,
        #                    PFNC = Pulse/Blank, QFNC = IQ
        #           0: Sine                 MFNC, SFNC,       QFNC
        #           1: Ramp                 MFNC, SFNC,       QFNC
        #           2: Triangle             MFNC, SFNC,       QFNC
        #           3: Square               MFNC,       PFNC, QFNC
        #           4: Phase noise          MFNC,       PFNC, QFNC
        #           5: External             MFNC, SFNC, PFNC, QFNC
        #           6: Sine/Cosine                            QFNC
        #           7: Cosine/Sine                            QFNC
        #           8: IQ Noise                               QFNC
        #           9: PRBS symbols                           QFNC
        #           10: Pattern (16 bits)                     QFNC
        #           11: User waveform       MFNC, SFNC, PFNC, QFNC
        #  10 = RATE/SRAT/(PPER, RPER)
        #       Modulation rate in frequency as float, e.g. 20.4 (for 20.4kHz)
        #       with the meaning
        #  11 = (ADEP, ANDP)/(FDEV, FNDV)/(PDEV, PNDV)/SDEV/PWID
        #       Modulation deviation in percent as float (e.g. 90.0 for 90%
        #       modulation depth)
        #  12 = Amplitude of clock output
        #  13 = Offset of clock output
        #  14 = Amplitude of HF (RF doubler output)
        #  15 = Offset of rear DC

        # enable the created list:
        self._write('LSTE 1')

    def _rf_on(self):
        """ Switches on any preconfigured microwave output.
        """
        self._write('ENBR 1')
        while not self._output_active():
            time.sleep(0.1)

    def _output_active(self):
        return bool(int(self._device.query('ENBR?').strip()))

    ########################################################################################
    ########################################################################################
    ########################################################################################
    ########################################################################################
    ########################################################################################
    ########################################################################################
    ########################################################################################

    # def sweep_on(self):
    #     """ Switches on the sweep mode.
    #
    #     @return int: error code (0:OK, -1:error)
    #     """
    #     self._internal_mode = 'sweep'
    #     self.log.error('This was never tested!')
    #     return self.on()
    #
    # def set_sweep(self, start, stop, step, power):
    #     """ Sweep from frequency start to frequency sto pin steps of width stop with power.
    #     """
    #     # set the type
    #     self._device.write('MODL 3')
    #     # and the subtype
    #     self._device.write('STYP 0')
    #
    #     sweep_length = stop - start
    #     index = 0
    #
    #     time_per_freq =  2e-3 # in Hz, 2ms per point assumed for the beginning
    #     # time it takes for a whole sweep, which is the rate of the sweep,
    #     # i.e. rate = 1/ time_for_freq_range
    #     rate = (sweep_length/step) * time_per_freq
    #     mod_type = 5 # blank
    #     mod_func = 3 # blank
    #     self._device.write('LSTP {0:d},{1:e},N,N,N,{2:f},N,N,{3},{4},{5:e},{6:e},N,N,N,N'.format(index, start, power, mod_type, mod_func, rate, sweep_length))
    #     self._internal_mode = 'sweep'
    #
    #     self.log.error('This was never tested!')
    #
    #     return start, stop, step, power, self._internal_mode
    #
    # def reset_sweeppos(self):
    #     """ Reset of MW sweep position to start
    #
    #     @return int: error code (0:OK, -1:error)
    #     """
    #     self._internal_mode = 'sweep'
    #     self.log.error('This was never tested!')
    #     return self.reset_listpos()
