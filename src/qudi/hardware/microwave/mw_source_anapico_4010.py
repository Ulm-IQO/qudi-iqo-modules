try:
    import pyvisa as visa
except ImportError:
    Warning('Pyvisa not found!')
import time
from typing import Any, Callable, Mapping, Optional
import numpy as np

from qudi.util.mutex import Mutex
from qudi.core.configoption import ConfigOption
from qudi.interface.microwave_interface import MicrowaveInterface, MicrowaveConstraints
from qudi.util.enums import SamplingOutputMode

class MicrowaveAnaPicoAPSin(MicrowaveInterface):
    _visa_address = ConfigOption('visa_address', missing='error')
    _comm_timeout = ConfigOption('comm_timeout', default=10, missing='warn')
    _wait = ConfigOption('wait', default=0.5)
    _enable_debug = ConfigOption('enable_debug', default=False)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)

        self._thread_lock = Mutex()
        self._rm = None
        self._device = None
        self._model = ''
        self._constraints = None
        self._cw_frequency = None
        self._cw_power = -30.
        self._scan_frequencies = None
        self._scan_power = -30.
        self._scan_mode = None

    def on_activate(self):
        """ Initialization performed during activation of the module. """
        self._rm = visa.ResourceManager()
        self._device = self._rm.open_resource(self._visa_address, timeout=self._comm_timeout)
        if self._enable_debug: print('comm_timeout = %d' % self._comm_timeout)

        if self._enable_debug: print('Try to ask %s' % '*IDN?')
        answer = self._device.query('*IDN?')
        if self._enable_debug: print('answer: %s' % answer)
        self._model = answer.split(',')[1]
        if self._enable_debug: print('model: %s' % self._model)
        
        # Generate constraints
        if self._model == 'APSIN3000-HC':
            freq_limits = (9e3, 3.3e9)
            power_limits = (-30, 13)
            scan_size_limits = (2,1000) # The pyvisa throws an error if the size is to large.... despite the actual hardware limitation
        elif self._model == 'APSIN6010':
            freq_limits = (9e3, 6.1e9)
            power_limits = (-30, 18)
            scan_size_limits = (2, 20000)
        elif self._model == 'APSIN4010':
            freq_limits = (9e3, 4.1e9)
            power_limits = (-30, 18)
            scan_size_limits = (2, 20000)
        else:
            freq_limits = (9e3, 3.3e9)
            power_limits = (-30, 13)
            scan_size_limits = (2,1000)
            self.log.warning('Model string unknown, hardware constraints might be wrong.')

        self._constraints = MicrowaveConstraints(
            power_limits=power_limits,
            frequency_limits=freq_limits,
            scan_modes=(SamplingOutputMode.JUMP_LIST,SamplingOutputMode.EQUIDISTANT_SWEEP),
            scan_size_limits=scan_size_limits,
            sample_rate_limits=(0.1,100) 
        )

    def on_deactivate(self):
        """ Cleanup performed during deactivation of the module. """
        self._device.close()
        self._rm.close()
        self._device = None
        self._rm = None

# ---------------------- Outputs --------------------------
    @property
    def constraints(self):
        """The microwave constraints object for this device.

        @return MicrowaveConstraints:
        """
        return self._constraints
    
    @property
    def is_scanning(self):
        """Read-Only boolean flag indicating if a scan is running at the moment. 
        Can be used together with module_state() to determine if the currently 
        running microwave output is a scan or CW. 
        Should return False if module_state() is 'idle'.

        @return bool: Flag indicating if a scan is running (True) or not (False)
        """
        with self._thread_lock:
            return (self.module_state() != 'idle') and not self._in_cw_mode()

    @property
    def cw_power(self):
        """The CW microwave power in dBm.

        @return float: The currently set CW microwave power in dBm.
        """
        with self._thread_lock:
            if self._enable_debug: print('Try to ask %s' % ':POW:LEV?')
            answer = self._device.query(':POW:LEV?').strip('\n')
            if self._enable_debug: print('answer: %s' % answer)
            return float(answer)

    @property
    def cw_frequency(self):
        """The CW microwave frequency in Hz. Must implement setter as well.

        @return float: The currently set CW microwave frequency in Hz.
        """
        with self._thread_lock:
            return float(self._device.query(':FREQ:CW?'))

    @property
    def scan_power(self):
        """The input microwave power in dBm used for scanning.

        @return float: The currently set scanning microwave power in dBm
        """
        with self._thread_lock:
            return self._scan_power

    @property
    def scan_frequencies(self):
        """The input microwave frequencies used for scanning. 
         Must implement setter as well.
        In case of scan_mode == SamplingOutputMode.JUMP_LIST, this will be a 1D 
        numpy array.
        In case of scan_mode == SamplingOutputMode.EQUIDISTANT_SWEEP, this will 
        be a tuple containing 3 values (freq_begin, freq_end, number_of_samples).
        If no frequency scan has been specified, return None.

        @return float[]: The currently set scanning frequencies. None if not set.
        """
        with self._thread_lock:
            return self._scan_frequencies

    @property
    def scan_sample_rate(self):
        """Read-only property returning the currently configured scan sample rate in Hz.

        @return float: The currently set scan sample rate in Hz
        """
        with self._thread_lock:
            return Warning('NO sample rate available')

    @property
    def scan_mode(self):
        """Scan mode Enum. Must implement setter as well.

        @return SamplingOutputMode: The currently set scan mode Enum
        """
        with self._thread_lock:
            return self._scan_mode
        
# ------------- Inputs -----------------------------------

    def set_cw(self, frequency, power):
        """Configure the CW microwave output. Does not start physical signal 
        output, see also "cw_on".

        @param float frequency: frequency to set in Hz
        @param float power: power to set in dBm
        """
        if self._enable_debug: print('=====================Running set_cw()=====================')
        with self._thread_lock:
            if self.module_state() != 'idle':
                raise RuntimeError('Unable to set CW parameters. Microwave output active.')
            self._assert_cw_parameters_args(frequency, power)
            self._command_wait(f':FREQ:FIX {frequency:e}')
            self._command_wait(f':POW:LEV {power:f}')
        if self._enable_debug: print('=====================Completed set_cw()=====================')

    def configure_scan(self, power, frequencies, mode,sample_rate):
        """"Configure the Sweep microwave output. Does not start physical signal
        output, see also "scan_on". 
        TODO check if really not turning on auto
        @param list frequencies: frequencies to set in Hz, 
            For sweep mode the size must be three with freq_start, freq_stop and points
            For List mode the list should be 1d array with all freqs
        @param float power: power to set in dbm; 
            Note power can also be sweeped but not implemented so far
        @param mode: mode to performe, either SamplingOutputMode.JUMP_LIST or 
            SamplingOutputMode.EQUIDISTANT_SWEEP
        @sample_rate: No sample input possible, needs to be given anyway because of programming
        """
        if self._enable_debug: print('=====================Running configure_scan()=====================')        
        with self._thread_lock:
            if self.module_state() != 'idle':
                raise RuntimeError('Unable to set CW parameters. Microwave output active.')
            self._assert_scan_configuration_args(power,frequencies,mode,sample_rate=1) # Dummy sampling rate is used, as the devices do not have this feature
            self._scan_mode = mode
            self._scan_power = power
            self._scan_sample_rate = sample_rate

            if mode == SamplingOutputMode.JUMP_LIST:
                self._scan_frequencies = np.asarray(frequencies, dtype=np.float64)
                self._write_list()
            elif mode == SamplingOutputMode.EQUIDISTANT_SWEEP:
                self._scan_frequencies = tuple(frequencies)
                self._write_sweep()
        if self._enable_debug: print('=====================Completed configure_scan()=====================')     

    def cw_on(self):
        """ Switches on cw microwave output.

        Must return AFTER the output is actually active.
        """
        if self._enable_debug: print('=====================Running cw_on()=====================')
        with self._thread_lock:
            if self.module_state() != 'idle':
                if self._in_cw_mode():
                    return
                raise RuntimeError(
                    'Unable to start CW microwave output. Frequency scanning in progress.'
                )

            self._command_wait(':OUTP ON')
            
            complete = 0
            t = 0
            while complete == 0:
                time.sleep(self._wait)
                t += 1
                if self._enable_debug: print('Try to ask %s' % ':OUTP:STAT?')
                answer = self._device.query(':OUTP:STAT?').strip('\n')
                if self._enable_debug: print('answer: %s' % answer)
                complete = int(float(answer))
                if t > 5:
                    if self._enable_debug: print('Timeout!')
                    break
            self.module_state.lock()
        if self._enable_debug: print('=====================Completed cw_on()=====================')

    def start_scan(self):
        """Switches on the microwave scanning.

        Must return AFTER the output is actually active (and can receive triggers for example).
        """
        if self._enable_debug: print('=====================Running start_scan()=====================')
        with self._thread_lock:
            if self.module_state() != 'idle':
                if not self._in_cw_mode():
                    return
                raise RuntimeError('Unable to start frequency scan. CW microwave output is active.')
            assert self._scan_frequencies is not None, \
                'No scan_frequencies set. Unable to start scan.'
            
            # if self._scan_mode == SamplingOutputMode.JUMP_LIST:
            #     if not self._in_list_mode():
            #         self._write_list()
            #     self._command_wait(':OUTP:STAT ON')
            # elif self._scan_mode == SamplingOutputMode.EQUIDISTANT_SWEEP:
            #     if not self._in_sweep_mode():
            #         self._write_sweep()
            #     self._command_wait(':OUTP:STAT ON')
            self._command_wait(':OUTP:STAT ON')
            self.module_state.lock()
        if self._enable_debug: print('=====================Completed start_scan()=====================')

    def reset_scan(self):
        """Reset currently running scan and return to start frequency.
        Does not need to stop and restart the microwave output if the device allows soft scan reset.
        """
        if self._enable_debug: print('=====================Running reset_scan()=====================')
        with self._thread_lock:
            if self.module_state() == 'idle':
                return
            if self._in_cw_mode():
                raise RuntimeError('Can not reset frequency scan. CW microwave output active.')
            #self._command_wait(':ABOR')
            #self._command_wait(':INIT')
            #self._command_wait(':FREQ:MODE FIX')
            #self._command_wait(':FREQ:MODE LIST')
            # print('abort ommited')
        if self._enable_debug: print('=====================Completed reset_scan()=====================')

    def off(self):
        """Switches off any microwave output (both scan and CW).
        Must return AFTER the device has actually stopped.
        """
        if self._enable_debug: print('=====================Running off()=====================')
        with self._thread_lock:
            if self.module_state() != 'idle':
                # Switch to CW mode to turn of scanning
                mode = self._in_cw_mode()
                if not mode:
                    self._command_wait(':FREQ:MODE FIX')
                self._command_wait(':OUTP OFF')

                complete = 1
                t = 0
                while complete != 0:
                    time.sleep(self._wait)
                    t += 1
                    if self._enable_debug: print('Try to ask %s' % ':OUTP:STAT?')
                    answer = self._device.query(':OUTP:STAT?')
                    if self._enable_debug: print('answer: %s' % answer)
                    complete = int(float(answer))
                    if t > 5:
                        if self._enable_debug: print('Timeout!')
                        break
                
                self.module_state.unlock()
        if self._enable_debug: print('=====================Completed off()=====================')

    def _command_wait(self, command_str):
        """ Writes the command in command_str via PyVisa and waits until the 
        device has finished processing it.

        @param str command_str: The command to be written
        """
        if self._enable_debug: print('Try to write %s' % command_str)
        self._device.write(command_str)
        if self._enable_debug: print('Try to write *WAI')
        self._device.write('*WAI')
        
        complete = 0
        t = 0
        while complete != 1:
            time.sleep(self._wait)
            t += 1
            if self._enable_debug: print('Try to ask %s' % '*OPC?')
            answer = self._device.query('*OPC?').strip('\n')
            if self._enable_debug: print('answer: %s' % answer)
            complete = int(float(answer))
            if t > 5:
                if self._enable_debug: print('Timeout!')
                break

# -------------- Helpers ---------------------------------

    def _in_list_mode(self):
        if self._enable_debug: print('Try to ask %s' % ':FREQ:MODE?')
        answer = self._device.query(':FREQ:MODE?').strip('\n')
        if self._enable_debug: print('answer: %s' % answer)
        return answer.lower() == 'list'

    def _in_sweep_mode(self):
        if self._enable_debug: print('Try to ask %s' % ':FREQ:MODE?')
        answer = self._device.query(':FREQ:MODE?').strip('\n')
        if self._enable_debug: print('answer: %s' % answer)
        return answer.lower() == 'swe'

    def _in_cw_mode(self):
        'Note: For this devices: FIX is equal to CW and usually returned'
        if self._enable_debug: print('Try to ask %s' % ':FREQ:MODE?')
        answer = self._device.query(':FREQ:MODE?').strip('\n')
        if self._enable_debug: print('answer: %s' % answer)
        return answer.lower() == 'fix'
    
    def _write_list(self):
        print('\n\n====================================run _write_list()====================================\n\n')
        # Front panel only updates when in CW mode
        if not self._in_cw_mode():
            self._command_wait(':FREQ:MODE FIX')
        #self._command_wait(':INIT:CONT 1')
        self._command_wait(':TRIG:TYPE POINT')
        self._command_wait(':TRIG:SOUR EXT')
        freq_str = ','.join(f'{freq:f}' for freq in self._scan_frequencies)
        self._command_wait(f':LIST:FREQ {freq_str}')
        self._command_wait(f':POW:LEV {self._scan_power:f}')
        #self._command_wait(f':LIST:COUN 1')
        self._command_wait(':LIST:DWEL 0.00005') # Set to minimal to not interfere with the trigger 
        self._command_wait(':LIST:DEL 0') # 
        
        print(self._in_list_mode())
        if not self._in_list_mode():
            self._command_wait(':FREQ:MODE LIST')
        print(self._in_list_mode())
     
    def _write_sweep(self):
        # Front panel only updates when in CW mode
        if self._model == 'APSIN6010':
            # This device needs to be once in the sweep mode to accept any frequency changes
            self._command_wait(':FREQ:MODE SWE')
        if not self._in_cw_mode():
            self._command_wait(':FREQ:MODE FIX')

        start, stop, points = self._scan_frequencies

        self._device.write(':SWE:SPAC LIN')
        self._device.write(f':FREQ:STAR {start:f}')
        self._device.write(f':FREQ:STOP {stop:f}')
        self._device.write(f':SWE:POIN {points:f}')
        
        self._device.write(f':POW {self._scan_power:f}')

        self._command_wait(':TRIG:SOUR EXT')
        if self._model == 'APSIN6010':
            # This device uses internal trigger aswell even if set to ext 
            dwel = 1/self._scan_sample_rate
            self._command_wait(f':SWE:DWEL {dwel:f}') 
        else:
            self._command_wait(':SWE:DWEL 0.00005') # Set to minimal to not interfere with the trigger 
        self._command_wait(':SWE:DEL 0') 
        self._command_wait(':TRIG:TYPE NORM')
        self._command_wait(':TRIG:SLOP POS')

        self._command_wait(':FREQ:MODE SWE')

