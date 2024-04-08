
from qudi.core.configoption import ConfigOption
from qudi.interface.simple_laser_interface import SimpleLaserInterface
from qudi.interface.simple_laser_interface import ControlMode, ShutterState, LaserState

from enum import Enum
from typing import Union
import time

import clr   # Requires package 'pythonnet', documentation here
clr.AddReference(r'mscorlib')
clr.AddReference('System.Reflection')
from System.Text import StringBuilder
from System.Reflection import Assembly

class Models(Enum):
    """ Model numbers for Millennia lasers
    """
    NEWFOCUS6700 = 0


class NewfocusDiodeLaser(SimpleLaserInterface):
    """ Newfocus laser.

    Example config for copy-paste:

    newfocus_laser:
        module.Class: 'laser.newfocus_laser.NewfocusDiodeLaser'
        options:
            laserid: 4106
            devicekey: '6700 SN60615'
            dllpath: 'path'
                # laser control requires the Newport USB driver, download and set up the software at
                # https://www.newport.com/f/velocity-wide-&-fine-tunable-lasers, extract and input path to the
                # directory containing 'USBWrap.dll' as the dllpath option
    """
    _DeviceKey = ConfigOption('devicekey', None)
    _Laserid = ConfigOption('laserid', None)
    _Buff = StringBuilder(64)
    _control_mode = ControlMode.UNKNOWN.value

    _idn = ''
    dll_path = ConfigOption(name='dllpath', default='',  missing='warn')

    def Query(self, word):
        self._Buff.Clear()
        self._dev.Query(self._DeviceKey, word, self._Buff)
        return self._Buff.ToString()

    def on_activate(self):
        """ Activate Module.
        """
        self.model = Models
        self.connect_laser()

    def on_deactivate(self):
        """ Deactivate module
        """
        self.disconnect_laser()

    def connect_laser(self):
        """ Connect to Instrument.

            @return bool: connection success
        """
        try:
            Assembly.LoadFile(self.dll_path + 'UsbDllWrap.dll')
            clr.AddReference(r'UsbDllWrap')
            import Newport
            self._dev = Newport.USBComm.USB()
            self._dev.OpenDevices(self._Laserid, True)
            out = self._dev.Read(self._DeviceKey, self._Buff)
            timer = 0
            while timer <= 100:
                while not out == -1:
                    out = self._dev.Read(self._DeviceKey, self._Buff)
                    print('Empyting the buffer: {}'.format(out))
                    time.sleep(0.5)
                self._idn = self.Query('*IDN?')
                if not self._idn == '':
                    self.log.info("\nLaser connected: {}".format(self._idn))
                    self._control_mode = self.Query('SOURce:CPOWer?')
                    self._output_state = self.Query('OUTPut:STATe?')
                    self.get_power_range()
                    self.get_current_range()
                    timer = 101
                    pass
                else:
                    self.log.info('reconnecting try:'+str(timer))
                    self._dev.CloseDevices()
                    time.sleep(0.2)
                    timer+=1
            if not self._idn == '':
                return True
            else:
                self.log.info('Time out')
                self.log.exception('time out')
                return False
        except:
            self._dev = None
            self.log.exception('Communication Failure:')
            return False

    def disconnect_laser(self):
        """ Close the connection to the instrument.
        """
        self._dev.CloseDevices()
        
    def get_power_range(self):
        """ Return laser power range

        @return float[2]: power range (min, max)
        """
        output=0
        if self._output_state == 1:
            print('Disabling output!')
            output = 1
            self.output_disable()
        power = self.get_power_setpoint()
        self.set_power('MAX')
        self.maxpower = self.get_power_setpoint()
        self.set_power(power)
        if output == 1:
            print('power range detection finished, re-enabling output')
            self.output_enable()
        return [0, self.maxpower]

    def get_power(self):
        """ Return actual laser power

        @return float: Laser power in milli watts
        """
        return float(self.Query('SENSe:POWer:DIODe'))

    def set_power(self, power):
        """ Set power setpoint.

        @param Union[float,str] power: power to set in mW, can be 'MAX' to set power to maximum rated power
        """
        if type(power) in [int, float]:
            if float(power) <= self.maxpower:
                self.Query('SOURce:POWer:DIODe {}'.format(power))
                #return self.get_power_setpoint()
            else:
                self.log.exception('Set value exceeding max power! Power must be <= {}mW'.format(self.maxpower))
        elif power == 'MAX':
            self.Query('SOURce:POWer:DIODe MAX')
        else:
            self.log.exception('Power input not valid')

    def get_power_setpoint(self):
        """ Return laser power setpoint.

        @return float: power setpoint in milli watts
        """
        #if self.get_control_mode() != ControlMode.POWER.name:
            #self.log.warning('Not in Constant Power mode! Use get_current_setpoint()')
        return float(self.Query('SOURce:POWer:DIODe?'))

    def get_current_unit(self):
        """ Get unit for laser current.

        @return str: unit
        """
        return 'mA'

    def get_current(self):
        """ Get actual laser current

        @return float: laser current in current units
        """
        return float(self.Query('SENSe:CURRent:DIODe'))

    def get_current_range(self):
        """ Get laser current range.

        @return float[2]: laser current range
        """
        output = 0
        if self._output_state == 1:
            output = 1
            print('Disabling output!')
            self.output_disable()
        current = self.get_current_setpoint()
        self.set_current('MAX')
        self.maxcurrent = self.get_current_setpoint()
        self.set_current(current)
        if output == 1:
            print('current range detection finished, re-enabling output')
            self.output_enable()
        return [0, self.maxcurrent]

    def get_current_setpoint(self):
        """ Get laser current setpoint

        @return float: laser current setpoint
        """
        #if self.get_control_mode() != ControlMode.CURRENT.name:
            #self.log.warning('Not in Constant Current mode! Use get_power_setpoint()')
        return float(self.Query('SOURce:CURRent:DIODe?'))

    def set_current(self, current):
        """ Set laser current setpoint

        @param Union[float,str] current: desired laser current setpoint in mA, can be 'MAX' for the max rated current
        """
        if type(current) in [int, float]:
            if float(current) <= self.maxcurrent:
                self.Query('SOURce:CURRent:DIODe {}'.format(current))
                #return self.get_current_setpoint()
            else:
                self.log.exception('Set value exceeding max current! Current must be <= {}mA'.format(self.maxcurrent))
        elif current == 'MAX':
            self.Query('SOURce:CURRent:DIODe MAX')
        else:
            self.log.exception('Current input not valid')

    def allowed_control_modes(self):
        """ Get supported control modes

        @return frozenset: set of supported ControlMode enums
        """
        return frozenset([ControlMode.POWER, ControlMode.CURRENT])

    def get_control_mode(self):
        """ Get the currently active control mode

        @return ControlMode: active control mode enum
        """
        if self.Query('SOURce:CPOWer?') == '0':
            self._control_mode = ControlMode.CURRENT.value
            return str(ControlMode.CURRENT)
        elif self.Query('SOURce:CPOWer?') == '1':
            self._control_mode = ControlMode.POWER.value
            return str(ControlMode.POWER)
        else:
            self.log.exception('Error while calling function:')

    def set_control_mode(self, control_mode):
        """ Set the active control mode

        @param ControlMode control_mode: desired control mode enum name
        """
        if control_mode == ControlMode.CURRENT:
            self.Query('SOURce:CPOWer 0')
            #return self.get_control_mode()
        elif control_mode == ControlMode.POWER:
            self.Query('SOURce:CPOWer 1')
            #return self.get_control_mode()
        else:
            self.log.exception('Mode not available: please use ControlMode.CURRENT or ControlMode.POWER')

    def get_laser_state(self):
        """ Get laser state

        @return LaserState: current laser state enum
        """
        if int(self.Query('OUTPut:STATe?')) == 0:
            self._output_state = LaserState.OFF.value
            return str(LaserState.OFF)
        elif int(self.Query('OUTPut:STATe?')) == 1:
            self._output_state = LaserState.ON.value
            return str(LaserState.ON)
        else:
            self.log.exception('Error while calling function:')

    def set_laser_state(self, state):
        """ Set laser state.

        @param LaserState state: desired laser state enum name
        """
        if state == LaserState.OFF:
            self.Query('OUTPut:STATe 0')
            #return self.get_laser_state()
        elif state == LaserState.ON:
            self.Query('OUTPut:STATe 1')
            #return self.get_laser_state()
        else:
            self.log.exception('State not available: please use LaserState.OFF or LaserState.ON')

    def get_shutter_state(self):
        """ Get laser shutter state

        @return ShutterState: actual laser shutter state
        """
        return ShutterState.NO_SHUTTER

    def set_shutter_state(self, state):
        """ Set laser shutter state.

        @param ShutterState state: desired laser shutter state
        """
        return ShutterState.NO_SHUTTER

    def get_temperatures(self):
        """ Get all available temperatures.

        @return dict: dict of temperature names and value in degrees Celsius
        """
        return {'Diode': self.Query('SENSe:TEMPerature:DIODe'),
                'Cavity': self.Query('SENSe:TEMPerature:CAVity')}

    def get_extra_info(self):
        """ Show dianostic information about lasers.

        @return str: diagnostic info as a string
        """
        return ('Laser ID string:'          + self.Query('*IDN?')                 +'\n'
                'Laser model number:'       + self.Query('SYSTem:LASer:MODEL?')   +'\n'     
                'Laser serial number:'      + self.Query('SYSTem:LASer:SN?')      +'\n'                                                                                                   
                'Current errors:'           + self.Query('ERRSTR?')
                )
