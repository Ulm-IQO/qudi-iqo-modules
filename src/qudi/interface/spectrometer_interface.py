# -*- coding: utf-8 -*-
"""
Interface for a spectrometer.

Qudi is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Qudi is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Qudi. If not, see <http://www.gnu.org/licenses/>.

Copyright (c) the Qudi Developers. See the COPYRIGHT.txt file at the
top-level directory of this distribution and at <https://github.com/Ulm-IQO/qudi/>
"""

from abc import abstractmethod
from qudi.core.module import Base


class SpectrometerInterface(Base):
    """ This is the interface class to define the controls for the simple optical spectrometer.

    This is interface is very basic, a more advanced one is in progress.

    Warning: This interface use CamelCase. This is should not be done in future versions. See more info here :
    documentation/programming_style.md

    """
    @abstractmethod
    def record_spectrum(self):
        """ Launch an acquisition a wait for a response

        @return (2, N) float array: The acquired array with the wavelength in meter in the first row and measured value
                                    int the second
        """
        pass

    @property
    @abstractmethod
    def exposure_time(self):
        """ Get the acquisition exposure time

        @return (float): Exposure time in second
        """
        pass

    @exposure_time.setter
    @abstractmethod
    def exposure_time(self, value):
        """ Set the acquisition exposure time

        @param (float) value: Exposure time to set in second
        """
        pass
