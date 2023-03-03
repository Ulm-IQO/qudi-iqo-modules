# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware interface for syringe pumps.

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

from qudi.core import Base
from abc import abstractmethod
from enum import Enum
from qudi.util.constraints import ScalarConstraint

class SyringePumpInterface(Base):
    """
    Interface class to define the abstract controls and communication with syringe pump devices.
    
    A syringe pump device can infuse or withdraw a liquid or gas to or from an experiment
    by retracting or extending the plunger of the syringe.
    
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @abstractmethod
    def start_pump(self):
        """
        Method to activate the pump with its currently set settings.
        
        @return int: error code (0: OK, -1: error)
        """
        pass

    @abstractmethod
    def stop_pump(self):
        """
        Method to stop the pump from its current operation.
        
        @return int: error code (0: OK, -1: error)
        """
        pass
    
    @abstractmethod
    def get_limits(self):
        """
        Method that returns the limits fo transfer rate and transfer volume,
        which is set by the inner diameter of the used syringe.
        @return tuple: ((min_rate, max_rate),(min_volume, max_volume))
        """
        pass

    @abstractmethod
    def get_constraints(self):
        """
        Method to get constraints of pump <pump_number>.
        The constraints give information on the min/max allowed transfer_volume, rate and inner_diameter
        @return PumpConstraints object: contains the different hardware specific constraints
        """
        pass

    @property
    @abstractmethod
    def inner_diameter(self):
        """
        Method to get the currently set value for the inner diameter of the syringe.
        @return float: currently set inner diameter
        """
        return self._inner_diameter

    @inner_diameter.setter
    @abstractmethod
    def inner_diameter(self):
        """
        Method to set the inner diameter of the used syringe.
        @return int: error code (0: Ok, -1: error)
        """
        pass

    @property
    @abstractmethod
    def transfer_time(self):
        """
        Method to get the currently set transfer time.
        @return float: current transfer time
        """
        pass

    @transfer_time.setter
    @abstractmethod
    def transfer_time(self):
        """
        Method to set the transfer time of the pump.
        @return int: error code (0: Ok, -1: error)
        """
        pass

    @property
    @abstractmethod
    def transfer_volume(self):
        """
        Method that returns the currently set transfer volume.
        @return float: current transfer volume
        """
        pass

    @transfer_volume.setter
    @abstractmethod
    def transfer_volume(self):
        """
        Method to set the transfer volume of the pump.
        @return int: error code (0: Ok, -1: error)
        """
        pass
   
    @property
    @abstractmethod
    def status(self):
        """
        Method to get the current status of the pump (e.g. idle, running, etc.).
        @return Enum object: carries the current status of the pump 
        """
        pass
    
class PumpStatus(Enum):
    """
    Enum class that catches all the different stati of a syringe pump.
    """
    # Pump is stopped and idle
    STOPPED = 0
    # pump is pumping
    RUNNING = 1
    # pump is paused, able to continue pumping the remaining set volume
    PAUSED = 2
    # pump is waiting for delay timer to expire to start pumping
    DELAYED = 3
    # pump has reached is maximum allowed movement range and has stalled
    STALLED = 4

class SyringePumpConstraints:
    def __init__(self):
        # declare the units of the constraints given
        self.units = None
        # Inner diameter constraints for the syringes that can be used
        self.inner_diameter = ScalarConstraint(default=1, bounds=(0, 1e6))
        # transfer volume that can fit into the pump
        self.transfer_volume = ScalarConstraint(default=1, bounds=(0, 1e6))
        # allowed transfer rates
        self.transfer_rate = ScalarConstraint(default=1, bounds=(0,1e6))

