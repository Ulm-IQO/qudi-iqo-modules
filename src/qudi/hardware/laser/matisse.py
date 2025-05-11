import time

from PySide2 import QtCore
from fysom import Fysom
import numpy as np

from sirah_matisse_commander import SirahMatisseCommanderDevice, MatisseControlStatus

from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.mutex import Mutex
from qudi.interface.process_control_interface import ProcessControlConstraints, ProcessControlInterface
from qudi.interface.switch_interface import SwitchInterface

class MatisseCommander(ProcessControlInterface, SwitchInterface):
    """
    A Light proxy to talk to the MatisseCommander program.

    Copy and paste example configuration:
    ```yaml
    matisse:
        config:
            address: 'localhost' # default
            port: 30000 # default
    ```
    """

    _address = ConfigOption(name="address", default="localhost")
    """Configuration option for the address at which the MatisseCommander program lives. most likely it is on the same computer, so the default is "localhost"""
    _port = ConfigOption(name="port", default=3000)
    """Configuration option for the port at which the MatisseCommander program listens"""

    conversionfactor = StatusVar(default=1)
    "Internal status variable to remember the conversion factor between tension and frequency."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._device = None
        self._constraints_process = None
        self._idle_activated = False

    # Qudi base 
    def on_activate(self):
        self._constraints_process = ProcessControlConstraints(
            setpoint_channels=["piezo ref cell", "piezo slow", "scan rising speed", "scan falling speed", "scan lower limit", "scan upper limit", "conversion factor", "scan mode", "scan value"],
            process_channels=["diode power dc"],
            units={"scan rising speed":"1/s", "scan falling speed":"1/s", "conversion factor":"MHz"},
            limits={"scan lower limit":(0.0,0.7), "scan upper limit":(0.0,0.7), "scan mode":(0,7), "scan value":(0.0,0.7)},
            dtypes={"scan mode":int}
        )
        self._device = SirahMatisseCommanderDevice(self._address, self._port)
        self._device.connect()
        self._idle_activated = False

    def on_deactivate(self):
        if self._device is not None:
            self._device.disconnect()

    # ProcessControlInterface
    def get_process_value(self, channel):
        if channel == "diode power dc":
            return self._device.diode_power_dc
        else:
            raise ValueError(f'Invalid process channel specifier "{channel}".')
    def set_setpoint(self, channel, value):
        if channel == "piezo ref cell":
            self._device.piezo_ref_cell = value
        elif channel == "piezo slow":
            self._device.piezo_slow = value
        elif channel == "scan rising speed":
            if self._device.set('SCAN:RISINGSPEED', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "scan falling speed": 
            if self._device.set('SCAN:FALLINGSPEED', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "scan lower limit": 
            if self._device.set('SCAN:LOWERLIMIT', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "scan upper limit":
            if self._device.set('SCAN:UPPERLIMIT', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "scan mode":
            if self._device.set('SCAN:MODE', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "scan value":
            if not self._idle_activated:
                return
            if self._device.set('SCAN:NOW', value) is False:
                raise RuntimeError(
                    'Setting slow piezo did not complete successfully.')
        elif channel == "conversion factor":
            self.conversionfactor = value
        else:
            raise ValueError(f'Invalid process channel specifier "{channel}".')
    def get_setpoint(self, channel):
        if channel == "piezo ref cell":
            return self._device.piezo_ref_cell
        elif channel == "piezo slow":
            return self._device.piezo_slow
        elif channel == "scan rising speed":
            return self._device.query('SCAN:RISINGSPEED')
        elif channel == "scan falling speed": 
            return self._device.query('SCAN:FALLINGSPEED')
        elif channel == "scan lower limit": 
            return self._device.query('SCAN:LOWERLIMIT')
        elif channel == "scan upper limit":
            return self._device.query('SCAN:UPPERLIMIT')
        elif channel == "scan mode":
            return self._device.query('SCAN:MODE')
        elif channel == "scan value":
            return self._device.query('SCAN:NOW')
        elif channel == "conversion factor":
            return self.conversionfactor
        else:
            raise ValueError(f'Invalid process channel specifier "{channel}".')
    def get_activity_state(self, channel):
        if channel == "scan value":
            return self._idle_activated
        return True
    def set_activity_state(self, channel, active):
        if channel == "scan value":
            self._idle_activated = active
    @property
    def constraints(self):
        return self._constraints_process

    # SwitchInterface
    def name(self):
        return "Matisse laser"
    def available_states(self):
        return {
            "Scan Status": ("STOP", "RUN")
        }
    def get_state(self, switch):
        if switch == "Scan Status":
            v = self._device.query('SCAN:STATUS')
            if v == MatisseControlStatus.RUN:
                return "RUN"
            else:
                return "STOP"
        else:
            raise ValueError(f'Invalid switch specifier "{switch}".')
    def set_state(self, switch, state):
        if switch == "Scan Status":
            if state == "RUN":
                v = "RUN"
            else:
                v = "STOP"
            self._device.set('SCAN:STATUS', v)
        else:
            raise ValueError(f'Invalid switch specifier "{switch}".') 

