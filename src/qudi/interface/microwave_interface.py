# -*- coding: utf-8 -*-

"""
This file contains the Qudi Interface file to control microwave devices.

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
from typing import Union, Tuple, FrozenSet

import numpy as np

from qudi.core.module import Base
from qudi.util.constraints import ScalarConstraint
from qudi.util.enums import SamplingOutputMode


class MicrowaveInterface(Base):
    """This class defines the interface to simple microwave generators with or without frequency
    scan capability.

    There are two modes of operation:
        - CW: constant frequency and constant power
        - scan: variable frequency and constant power

    For the scan, the frequencies can be specified in two ways:
        - jump list: explicitly defining all frequency values
        - equidistant sweep: a start and stop frequency, and step count
    The constraints specify which of the two modes are supported by a specific hardware implementation.

    An external hardware trigger must be supplied to actually step through the scan frequencies.

    # ToDo: Think about if the logic should handle trigger settings and expand the interface if so.
    #  But I would argue the trigger config is something static and hard-wired for a specific setup,
    #  so it should be configurable via config and not handled by logic at runtime.
    """

    @property
    @abstractmethod
    def constraints(self) -> 'MicrowaveConstraints':
        """The microwave constraints object for this device.

        @return MicrowaveConstraints:
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def is_scanning(self) -> bool:
        """Read-Only boolean flag indicating if a scan is running at the moment. Can be used
        together with module_state() to determine if the currently running microwave output is a
        scan or CW.
        Should return False if module_state() is 'idle'.

        @return bool: Flag indicating if a scan is running (True) or not (False)
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def cw_power(self) -> float:
        """Read-only property returning the currently configured CW microwave power in dBm.

        @return float: The currently set CW microwave power in dBm.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def cw_frequency(self) -> float:
        """Read-only property returning the currently set CW microwave frequency in Hz.

        @return float: The currently set CW microwave frequency in Hz.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def scan_power(self) -> float:
        """Read-only property returning the currently configured microwave power in dBm used for
        scanning.

        @return float: The currently set scanning microwave power in dBm
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def scan_frequencies(self) -> Union[np.ndarray, Tuple[float, float, float]]:
        """Read-only property returning the currently configured microwave frequencies used for
        scanning.

        In case of self.scan_mode == SamplingOutputMode.JUMP_LIST, this will be a 1D numpy array.
        In case of self.scan_mode == SamplingOutputMode.EQUIDISTANT_SWEEP, this will be a tuple
        containing 3 values (freq_begin, freq_end, number_of_samples).
        If no frequency scan has been configured, return None.

        @return float[]: The currently set scanning frequencies. None if not set.
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def scan_mode(self) -> SamplingOutputMode:
        """Read-only property returning the currently configured scan mode Enum.

        @return SamplingOutputMode: The currently set scan mode Enum
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def scan_sample_rate(self) -> float:
        """Read-only property returning the currently configured scan sample rate in Hz.

        @return float: The currently set scan sample rate in Hz
        """
        raise NotImplementedError

    @abstractmethod
    def off(self) -> None:
        """Switches off any microwave output (both scan and CW).
        Must return AFTER the device has actually stopped.
        """
        raise NotImplementedError

    @abstractmethod
    def set_cw(self, frequency: float, power: float) -> None:
        """Configure the CW microwave output. Does not start physical signal output, see also
        "cw_on".

        @param float frequency: frequency to set in Hz
        @param float power: power to set in dBm
        """
        raise NotImplementedError

    @abstractmethod
    def cw_on(self) -> None:
        """Switches on preconfigured cw microwave output, see also "set_cw".

        Must return AFTER the output is actually active.
        """
        raise NotImplementedError

    @abstractmethod
    def configure_scan(self, power: float, frequencies: Union[np.ndarray, Tuple[float, float, float]],
                       mode: SamplingOutputMode, sample_rate: float) -> None:
        """Configure a frequency scan.

        @param float power: the power in dBm to be used during the scan
        @param float[] frequencies: an array of all frequencies (jump list)
                                    or a tuple of start, stop frequency and number of steps (equidistant sweep)
        @param SamplingOutputMode mode: enum stating the way how the frequencies are defined
        @param float sample_rate: external scan trigger rate
        """
        raise NotImplementedError

    @abstractmethod
    def start_scan(self) -> None:
        """Switches on the preconfigured microwave scanning, see also "configure_scan".

        Must return AFTER the output is actually active (and can receive triggers for example).
        """
        raise NotImplementedError

    @abstractmethod
    def reset_scan(self) -> None:
        """Reset currently running scan and return to start frequency.
        Does not need to stop and restart the microwave output if the device allows soft scan reset.
        """
        raise NotImplementedError

    def _assert_cw_parameters_args(self, frequency: float, power: float) -> None:
        """ Helper method to unify argument type and value checking against hardware constraints.
        Useful in implementation of "set_cw()".
        """
        # Check power
        assert self.constraints.power_in_range(power)[0], \
            f'CW power to set ({power} dBm) is out of bounds for allowed range ' \
            f'{self.constraints.power_limits}'
        # Check frequency
        assert self.constraints.frequency_in_range(frequency)[0], \
            f'CW frequency to set ({frequency:.9e} Hz) is out of bounds for allowed range ' \
            f'{self.constraints.frequency_limits}'

    def _assert_scan_configuration_args(self, power: float, frequencies: Union[np.ndarray, Tuple[float, float, float]],
                                        mode: SamplingOutputMode, sample_rate: float) -> None:
        """ Helper method to unify argument type and value checking against hardware constraints.
        Useful in implementation of "configure_scan()".
        """
        # Check power
        assert self.constraints.power_in_range(power)[0], \
            f'Scan power to set ({power} dBm) is out of bounds for allowed range ' \
            f'{self.constraints.power_limits}'
        # Check mode
        assert isinstance(mode, SamplingOutputMode), \
            'Scan mode must be Enum type qudi.util.enums.SamplingOutputMode'
        assert self.constraints.mode_supported(mode), \
            f'Unsupported scan mode "{mode}" encountered'
        # Check sample rate
        assert self.constraints.sample_rate_in_range(sample_rate)[0], \
            f'Sample rate to set ({sample_rate:.9e} Hz) is out of bounds for allowed range ' \
            f'{self.constraints.sample_rate_limits}'
        # Check frequencies
        if mode == SamplingOutputMode.JUMP_LIST:
            samples = len(frequencies)
            min_freq, max_freq = min(frequencies), max(frequencies)
        elif mode == SamplingOutputMode.EQUIDISTANT_SWEEP:
            assert len(frequencies) == 3, \
                'Setting scan frequencies for "EQUIDISTANT_SWEEP" mode requires iterable of 3 ' \
                'values: (start, stop, number_of_points)'
            samples = frequencies[-1]
            min_freq, max_freq = frequencies[:2]
        else:
            raise AssertionError(f'Unknown mode {mode} encountered.')
        assert self.constraints.scan_size_in_range(samples)[0], \
            f'Number of samples for frequency scan ({samples}) is out of bounds for ' \
            f'allowed scan size limits {self.constraints.scan_size_limits}'
        assert self.constraints.frequency_in_range(min_freq)[0] and \
               self.constraints.frequency_in_range(max_freq)[0], \
               f'Frequency samples to scan out of bounds.'


class MicrowaveConstraints:
    """A container to hold all constraints for microwave sources.
    """
    def __init__(self, power_limits: Tuple[float, float], frequency_limits: Tuple[float, float],
                 scan_size_limits: Tuple[int, int], sample_rate_limits: Tuple[float, float],
                 scan_modes: Tuple[SamplingOutputMode, ...]) -> None:
        """
        @param float[2] power_limits: Allowed min and max power
        @param float[2] frequency_limits: Allowed min and max frequency
        @param int[2] scan_size_limits: Allowed min and max number of samples for scanning
        @param float[2] sample_rate_limits: Allowed min and max scan sample rate (in Hz)
        @param SamplingOutputMode[] scan_modes: Allowed scan mode Enums
        """
        assert len(power_limits) == 2, 'power_limits must be iterable of length 2 (min, max)'
        assert len(frequency_limits) == 2, \
            'frequency_limits must be iterable of length 2 (min, max)'
        assert len(scan_size_limits) == 2, \
            'scan_size_limits must be iterable of length 2 (min, max)'
        assert len(sample_rate_limits) == 2, \
            'sample_rate_limits must be iterable of length 2 (min, max)'
        assert all(isinstance(mode, SamplingOutputMode) for mode in scan_modes), \
            'scan_modes must be iterable containing only qudi.util.enums.SamplingOutputMode Enums'

        self._scan_modes = frozenset(scan_modes)
        self._power = ScalarConstraint(power_limits[0], power_limits)
        self._frequency = ScalarConstraint(frequency_limits[0], frequency_limits)
        self._scan_size = ScalarConstraint(scan_size_limits[0], scan_size_limits, enforce_int=True)
        self._sample_rate = ScalarConstraint(sample_rate_limits[0], sample_rate_limits)

    @property
    def power(self) -> ScalarConstraint:
        return self._power

    @property
    def frequency(self) -> ScalarConstraint:
        return self._frequency

    @property
    def scan_size(self) -> ScalarConstraint:
        return self._scan_size

    @property
    def sample_rate(self) -> ScalarConstraint:
        return self._sample_rate

    # legacy functions

    @property
    def scan_size_limits(self) -> Tuple[int, int]:
        return self.scan_size.bounds

    @property
    def min_scan_size(self) -> int:
        return self.scan_size.minimum

    @property
    def max_scan_size(self) -> int:
        return self.scan_size.maximum

    @property
    def sample_rate_limits(self) -> Tuple[float, float]:
        return self.sample_rate.bounds

    @property
    def min_sample_rate(self) -> float:
        return self.sample_rate.minimum

    @property
    def max_sample_rate(self) -> float:
        return self.sample_rate.maximum

    @property
    def power_limits(self) -> Tuple[float, float]:
        return self.power.bounds

    @property
    def min_power(self) -> float:
        return self.power.minimum

    @property
    def max_power(self) -> float:
        return self.power.maximum

    @property
    def frequency_limits(self) -> Tuple[float, float]:
        return self.frequency.bounds

    @property
    def min_frequency(self) -> float:
        return self.frequency.minimum

    @property
    def max_frequency(self) -> float:
        return self.frequency.maximum

    @property
    def scan_modes(self) -> FrozenSet[SamplingOutputMode]:
        return self._scan_modes

    def frequency_in_range(self, value: float) -> Tuple[bool, float]:
        return self.frequency.is_valid(value), self.frequency.clip(value)

    def power_in_range(self, value: float) -> Tuple[bool, float]:
        return self.power.is_valid(value), self.power.clip(value)

    def scan_size_in_range(self, value: int) -> Tuple[bool, int]:
        return self.scan_size.is_valid(value), self.scan_size.clip(value)

    def sample_rate_in_range(self, value: float) -> Tuple[bool, float]:
        return self.sample_rate.is_valid(value), self.sample_rate.clip(value)

    def mode_supported(self, mode: SamplingOutputMode) -> bool:
        return mode in self._scan_modes
