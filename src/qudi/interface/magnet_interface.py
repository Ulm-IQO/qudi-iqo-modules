# -*- coding: utf-8 -*-

"""
This file contains the Qudi Interface file to control magnets.

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

from abc import abstractmethod
from qudi.core.module import Base
from dataclasses import dataclass

class ChannelDataClass:
    test




class MagnetInterface(Base):


    @abstractmethod
    def get_constraints(self):
        pass

    @abstractmethod
    def get_ctrl_value(self, ch):
        """
        @param list ch:

        """
        pass

    @abstractmethod
    def set_ctrl_value(self, ch, value):

        pass

    @property
    @abstractmethod
    def channel_config(self):
        """
        @return dict: Dictionary containing the correspondance between
                      {'x':1, 'y':2}
        """
        pass


class CoilMagnetInterface(MagnetInterface):

    def get_temp(self):
        pass


class MotorMagnetInterface(MagnetInterface):
    @abstractmethod
    def get_velocity(self):
        pass

    @abstractmethod
    def set_velocity(self):
        pass