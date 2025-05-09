from qudi.interface.switch_interface import SwitchInterface
from qudi.core.configoption import ConfigOption

import clr
import time

clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.DeviceManagerCLI.dll")
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\Thorlabs.MotionControl.GenericMotorCLI.dll")
clr.AddReference("C:\\Program Files\\Thorlabs\\Kinesis\\ThorLabs.MotionControl.FilterFlipperCLI.dll")

from Thorlabs.MotionControl.DeviceManagerCLI import *
from Thorlabs.MotionControl.GenericMotorCLI import *
from Thorlabs.MotionControl.FilterFlipperCLI import *
from System import Decimal, UInt32

class ThorlabsFlipMount(SwitchInterface):
    """ Hardware that handles several Thorlabs flip mounts at once
    
    Example of configuration:
    flip_mounts:
        module.Class: 'switches.thorlabs_flip_mount.ThorlabsFlipMount'  #Path of this file
        options:
            name: 'Thorlabs flip mounts'
            mounts:                                                     #Dictionary containing the list of moounts
                M1:                                                     #name of each mount
                    serial: 37008125                                    #serial number of the mount (open kinesis  and tak it from there)
                    state1: 'Down'                              #Labels of the states. Default states '1' and '2'                    state2: 'Up'
                M2:
                    serial: 37008126
                    state1: 'Confocal'
                    state2: 'Wide Field'
    """
    mounts = ConfigOption(missing='error')
    _name = ConfigOption(name="name", missing='error')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._devices = dict()

    def on_activate(self):
        """ Prepare module, connect to hardware.
        """
        DeviceManagerCLI.BuildDeviceList()
        for (name, mount) in self.mounts.items():
            self.log.debug(f"Initializing {name}:{mount}")
            self._devices[name] = FilterFlipper.CreateFilterFlipper(str(['serial']))
            self._devices[name].Connect(str(mount['serial']))
            if not self._devices[name].IsSettingsInitialized():
                self._devices[name].WaitForSettingsInitialized(10000)  # 10 second timeout.
                assert self._devices[name].IsSettingsInitialized() is True
            self._devices[name].StartPolling(250)
        self.log.debug(f"Waiting 5sbefore enabling")
        time.sleep(1)
        for (name, device) in self._devices.items():
            self.log.debug(f"Enabling {name}")
            device.EnableDevice()
        self.log.debug(f"Done")

    def on_deactivate(self):
        """ Disconnect from hardware on deactivation.
        """
        for (name, device) in self._devices.items():
            device.StopPolling()
            device.Disconnect()

    @property
    def name(self):
        return self._name
    @property
    def available_states(self):
        return {mount:(info.get('state1','1'),info.get('state2','2')) for (mount, info) in self.mounts.items()}
    def get_state(self, switch):
        st = self._devices[switch].Position
        if st == 2:
            return self.mounts[switch].get('state2','2')
        else:
            return self.mounts[switch].get('state1','1')

    def set_state(self, switch, state):
        if state == self.mounts[switch].get('state2','2'):
            new_pos = UInt32(2)  # Must be a .NET decimal.
        else:
            new_pos = UInt32(1)
        self._devices[switch].SetPosition(new_pos,60000)  # 60 second timeout.
