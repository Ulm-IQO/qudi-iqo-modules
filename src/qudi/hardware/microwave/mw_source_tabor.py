# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware file to control tabor(new microwave device in labor) microwave device.
In our lab, Tabor only works with usb, not visa

"""

# try:
#     import pyvisa as visa
# except ImportError:
#     import visa
import pywinusb.hid as hid
import time
import numpy as np

from qudi.util.mutex import Mutex
from qudi.core.configoption import ConfigOption
from qudi.interface.microwave_interface import MicrowaveInterface, MicrowaveConstraints
from qudi.util.enums import SamplingOutputMode

class MicrowaveTabor(MicrowaveInterface):
    """  Hardware file for Tabor. Tested for the model LS3082B. """

    # teLucid_1292B = 0x1202
    # teLucid_3082B = 0x3002

    teVendorId  = ConfigOption('teVendorId', default = 0x168C)
    teLucidDesktopId  = ConfigOption('teLucidDesktopId', default = 0x6002)
    teLucidPortableId = ConfigOption('teLucidPortableId', default = 0x6081)
    teLucidBenchtopId = ConfigOption('teLucidBenchtopId', default = 0x3002)
    channel = ConfigOption('channel', default= 1)
    BUFFER_SIZE = ConfigOption('BUFFER_SIZE', default = 256)
    _rising_edge_trigger = ConfigOption('rising_edge_trigger', default=True)
    _config_freq_min = ConfigOption('frequency_min', default=None)
    _config_freq_max = ConfigOption('frequency_max', default=None)
    _config_power_min = ConfigOption('power_min', default=None)
    _config_power_max = ConfigOption('power_max', default=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._thread_lock = Mutex()
        self._rm = None
        self._device = None
        self._model = 'LS3082B'
        self._constraints = None
        self._cw_power = -20
        self._cw_frequency = 2.0e9
        self._scan_power = -10
        self._scan_frequencies = None
        self._scan_mode = None
        self._scan_sample_rate = 0.
        self._temp_read = None # temp variable, use to store the readed information
                               # used in func readData
        self._list_state = 0   #tabor LS3082B is broken to show the real state of list
    def on_activate(self):
        # print('now is in mode on_activate')
        """ Initialisation performed during activation of the module."""
        # Connect to hardware
        self._device = hid.HidDeviceFilter(
            vendor_id = self.teVendorId, 
            product_id = self.teLucidBenchtopId
            ).get_devices()[0]
        
        # Open device
        # self._device.open() not necessary in usb mode

        # Reset device
        self._command_wait('*CLS\n')
        self._command_wait('*RST\n') # reset the whole Tabor
        self._command_wait('*IDN?\n')
        # set the default states
        command_channel = ':INST ' + str(self.channel)
        self._command_wait(command_channel) # choose channel
        self._command_wait(':INIT:GATE OFF')
        self._command_wait(':TRIG:SOUR EXTernal')
        self._command_wait(':TRIG:EDG POS')
        self._command_wait(':TRIG:ADV STEP')  # only running with trigger
        
        # Generate constraints
        # self._model == 'LS3082B':
        freq_limits = (1e8, 3e9) # real range should be (9e3, 12e9)
                                  # but this is for this hard to achieve, stupid
        power_limits = (-100, 20)

        # Apply configured soft-boundaries
        if self._config_power_min is not None:
            assert self._config_power_min < power_limits[1]
            power_limits = (max(power_limits[0], self._config_power_min), power_limits[1])
        if self._config_power_max is not None:
            assert self._config_power_max > power_limits[0]
            power_limits = (power_limits[0], min(power_limits[1], self._config_power_max))
        if self._config_freq_min is not None:
            assert self._config_freq_min < freq_limits[1]
            freq_limits = (max(freq_limits[0], self._config_freq_min), freq_limits[1])
        if self._config_freq_max is not None:
            assert self._config_freq_max > freq_limits[0]
            freq_limits = (freq_limits[0], min(freq_limits[1], self._config_freq_max))
        self._constraints = MicrowaveConstraints(
            power_limits=power_limits,
            frequency_limits=freq_limits,           
            scan_size_limits=(2, 4096),
            sample_rate_limits=(0.1, 100), 
            # sample_rate_limits=(100e-6, 4295), wirrten in document !!!!
            scan_modes=(SamplingOutputMode.JUMP_LIST, SamplingOutputMode.EQUIDISTANT_SWEEP)
        )

        self._scan_frequencies = self._constraints.min_frequency
        self._scan_power = self._constraints.min_power
        self._cw_power = self._constraints.min_power
        self._cw_frequency = 2870.0e6
        self._scan_mode = SamplingOutputMode.JUMP_LIST
        self._scan_sample_rate = self._constraints.max_sample_rate

    def on_deactivate(self):
        # print('now is in mode on_deactivate')
        """ Cleanup performed during deactivation of the module. """
        # Close device
        self._device.close()
    
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
            return (self.module_state() != 'idle') and not self._in_cw_mode()
        
    @property
    def cw_power(self):
        """The CW microwave power in dBm. Must implement setter as well.

        @return float: The currently set CW microwave power in dBm.
        """
        with self._thread_lock:
            return self._cw_power

    @property
    def cw_frequency(self):
        """The CW microwave frequency in Hz. Must implement setter as well.

        @return float: The currently set CW microwave frequency in Hz.
        """
        with self._thread_lock:
            return self._cw_frequency

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
            return self._scan_mode

    @property
    def scan_sample_rate(self):
        """Read-only property returning the currently configured scan sample rate in Hz.

        @return float: The currently set scan sample rate in Hz
        """
        with self._thread_lock:
            return self._scan_sample_rate

# -----------------------------up to here---------------------------------------------------
        
    def set_cw(self, frequency, power):
        # print('now is in mode set_cw')
        """Configure the CW microwave output. Does not start physical signal output, see also
        "cw_on".

        @param float frequency: frequency to set in Hz
        @param float power: power to set in dBm
        """
        with self._thread_lock:
            if self.module_state() != 'idle':
                raise RuntimeError('Unable to set CW parameters. Microwave output active.')
            self._assert_cw_parameters_args(frequency, power)

            if not self._in_cw_mode():
                self._command_wait(':LIST OFF')
                self._list_state = 0
                self._command_wait(':FRSW OFF')
                
            self._command_wait(':INIT:CONT ON')
            self._command_wait('FREQuency {}\n'.format(frequency))
            self._command_wait('POWer {}\n'.format(power))
            self._cw_frequency = float(self._command_wait('*FREQuency?\n'))
            self._cw_power = float(self._command_wait( '*POWer?\n'))
  

    def configure_scan(self, power, frequencies, mode, sample_rate):
        """
        """
        # print('now is in mode configure_scan')
        with self._thread_lock:
            # Sanity checks
            if self.module_state() != 'idle':
                raise RuntimeError('Unable to configure frequency scan. Microwave output active.')
            self._assert_scan_configuration_args(power, frequencies, mode, sample_rate)

            # configure scan according to scan mode
            self._scan_mode = mode
            self._scan_sample_rate = sample_rate
            self._scan_power = power
            if mode == SamplingOutputMode.JUMP_LIST:
                self._scan_frequencies = np.asarray(frequencies, dtype=np.float64)
                # self._write_list()
                # self._write_sweep()
            elif mode == SamplingOutputMode.EQUIDISTANT_SWEEP:
                self._scan_frequencies = tuple(frequencies)
                self._write_sweep()

            self._set_trigger_edge() 

    def off(self):
        # print('now is in mode off')
        """Switches off any microwave output (both scan and CW).
        Must return AFTER the device has actually stopped.
        """
        with self._thread_lock:
            if self.module_state() != 'idle':
                list_mode = self._in_list_mode() or self._in_sweep_mode
                self._command_wait(':OUTP OFF')
                if list_mode:
                    self._command_wait(':LIST OFF')
                    self._list_state = 0
                    self._command_wait(':FRSW OFF')
                time.sleep(0.3)
                while self._command_wait(':OUTP?') == 'ON':
                    time.sleep(0.2)
                self.module_state.unlock()

    def cw_on(self):
        # print('now is in mode cw_on')
        """ Switches on cw microwave output.

        Must return AFTER the output is actually active.
        """
        with self._thread_lock:
            if self.module_state() != 'idle':
                if self._in_cw_mode():
                    return
                raise RuntimeError(
                    'Unable to start CW microwave output. Microwave output is currently active.'
                )

            if not self._in_cw_mode():
                self._command_wait(':LIST OFF')
                self._list_state = 0
                self._command_wait(':FRSW OFF')
                self._command_wait('FREQuency {}\n'.format(self._cw_frequency))
                self._command_wait('POWer {}\n'.format(self._cw_power))

            self._command_wait(':OUTP ON')
            time.sleep(0.3)
            while self._command_wait(':OUTP?') != 'ON':
                time.sleep(0.2)
            self.module_state.lock()

    def start_scan(self):
        # print('now is in mode start_scan')
        """Switches on the microwave scanning.

        Must return AFTER the output is actually active (and can receive triggers for example).
        """
        with self._thread_lock:
            if self.module_state() != 'idle':
                if not self._in_cw_mode():
                    return
                raise RuntimeError('Unable to start frequency scan. CW microwave output is active.')
            assert self._scan_frequencies is not None, \
                'No scan_frequencies set. Unable to start scan.'

            # LIST mode
            if self._scan_mode == SamplingOutputMode.JUMP_LIST:
                if not self._in_list_mode():
                    # self._write_list()
                    self._write_sweep()
                                   
                self._command_wait(':OUTP ON')
                while self._command_wait(':OUTP?') != 'ON':
                    time.sleep(0.2)
            # SWEEP mode
            elif self._scan_mode == SamplingOutputMode.EQUIDISTANT_SWEEP:
                if not self._in_sweep_mode():
                    self._write_sweep()

                self._command_wait(':OUTP ON')
                while self._command_wait(':OUTP?') != 'ON':
                    time.sleep(0.2)
            else:
                raise RuntimeError(
                    f'Invalid scan mode encountered ({self._scan_mode}). Please set scan_mode '
                    f'property before configuring or starting a frequency scan.'
                )
            self.module_state.lock()

    def reset_scan(self):
        # print('now is in mode reset_scan')
        """Reset currently running scan and return to start frequency.
        Does not need to stop and restart the microwave output if the device allows soft scan reset.
        """

        """
        ATTENTION: Tabor seems really stupid, during each reset we much set the parameters agagin.
        """
        with self._thread_lock:
            if self.module_state() == 'idle':
                return
            if self._in_cw_mode():
                raise RuntimeError('Can not reset frequency scan. CW microwave output active.')
            """ the command above can lead to big bug.
            Because tabor cannot correctly output thereal state of list mode"""

            if self._scan_mode == SamplingOutputMode.JUMP_LIST:
                # self._command_wait(':OUTP OFF')
                # self._write_sweep()
                # self._command_wait(':OUTP ON')
                temp = 0
            elif self._scan_mode == SamplingOutputMode.EQUIDISTANT_SWEEP:
                self._command_wait(':OUTP OFF')
                self._command_wait(':OUTP ON')


    def readData(self, data):
        # remove '\n'
        strData = ''.join([str(elem) for i, elem in enumerate(data)])
        text = ''
        if len(data) == 0:
            print("Data is empty")
        elif strData: #and not strData.isspace() and strData.isprintable():
            for c in data:
                if c != 0:
                    text+= chr(c)
            print (text)
            self._temp_read = text
        return None

    def _command_wait(self, command_str):
        """ Writes the command in command_str via Pywinusb and waits until the device has finished
        processing it.

        @param str command_str: The command to be written
        """
        if not self._device:
            print ("No device provided")
            return
        
        self._device.open()
        buffer=[0x00] * self.BUFFER_SIZE # USB packet size
        sendData = bytearray(command_str, 'utf-8')
        sendData_len = len(command_str)

        for  i in range(sendData_len):
            buffer[i+3] = sendData[i]

        self._device.send_output_report(buffer)
        time.sleep(0.3)
        self._device.set_raw_data_handler(self.readData)
        self._device.close()

        return self._temp_read
    
    
    def _in_list_mode(self):
        # print('now is in mode in_list')
        # print(self._list_state)
        return self._list_state == 1

    def _in_sweep_mode(self):
        # print('now is in mode in_sweep')
        return self._command_wait(":FRSW?") == 'ON'

    def _in_cw_mode(self):
        # print('now is in mode in_cw')
        return not (self._in_list_mode() or self._in_sweep_mode())

    def _write_list(self):
        # print('now is in mode write_list')
        # print('before writing',self._list_state)
        # print('the func output', self._in_list_mode())
        if not self._in_list_mode():
            self._command_wait(':FRSW OFF') 
            self._command_wait(':LIST ON')
            self._list_state = 1
            # print('after writing',self._list_state)
        self._command_wait('LIST:DELete:ALL') # previous list deleted
        time.sleep(0.3)

        # set list parameters
        self._command_wait('POWer {}\n'.format(self._scan_power))
        temp_len = len(self._scan_frequencies)
        end_symbol = 0
        for i in range(temp_len):
            if i == temp_len - 1:
                end_symbol = 1
            self._command_wait(
                'LIST:DEFine {},{},{},{},{},{}'.format(
                    i + 1,
                    self._scan_frequencies[i],
                    self._scan_power,
                    end_symbol,
                    0,
                    self._scan_sample_rate
                )
            )
            # print("still in for")
        self._command_wait(':INIT:CONT OFF')  # running with trigger
        self._command_wait(':TRIG:ADV STEP')  # running with trigger 

    def _write_sweep(self):
        # print('now is in mode write_sweep')
        if not self._in_sweep_mode():
            self._command_wait(':LIST OFF')
            self._list_state = 0
            self._command_wait(':FRSW ON')

        # start, stop, points = self._scan_frequencies
        # step = (stop - start) / (points - 1) 
        start, stop, step = self._scan_frequencies[0], self._scan_frequencies[-1], len(self._scan_frequencies) + 1
        # is this input also suitable for Tabor ???
        self._command_wait('POWer {}\n'.format(self._scan_power))
        self._command_wait(':FRSW:STAR {}\n '.format(start))
        self._command_wait(':FRSW:STEPs {}'.format(step))
        self._command_wait(':FRSW:STOP {}\n '.format(stop))
        # self._command_wait(':FRSW:TIME {}\n'.format(self._scan_sample_rate)) 
        self._command_wait(':FRSW:DIR NORMal') # set the direction
        time.sleep(0.3)
        self._command_wait(':INIT:CONT OFF')  # running with trigger
        self._command_wait(':TRIG:ADV STEP')  # running with trigger 

    def _set_trigger_edge(self):
        # print('now is in mode set_trigger')
        edge = 'POS' if self._rising_edge_trigger else 'NEG'
        self._command_wait(':TRIG:EDG {}\n'.format(edge))
    