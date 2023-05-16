# -*- coding: utf-8 -*-

"""
Interface for a generic input stream of data points with fixed sampling rate and data type.

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

import numpy as np
from typing import Union, Type, Iterable, Mapping, Optional, Dict, List, Tuple
from enum import Enum
from abc import abstractmethod
from qudi.core.module import Base
from qudi.util.constraints import ScalarConstraint


class StreamingMode(Enum):
    INVALID = -1
    CONTINUOUS = 0
    FINITE = 1


class SampleTiming(Enum):
    INVALID = -1
    CONSTANT = 0
    TIMESTAMP = 1
    RANDOM = 2


class DataInStreamConstraints:
    """ Collection of constraints for hardware modules implementing DataInStreamInterface.
    """
    def __init__(self,
                 channel_units: Mapping[str, str],
                 sample_timings: Iterable[Union[SampleTiming, int]],
                 streaming_modes: Iterable[Union[StreamingMode, int]],
                 data_type: Union[Type[int], Type[float], Type[np.integer], Type[np.floating]],
                 channel_buffer_size: Optional[ScalarConstraint],
                 sample_rate: Optional[ScalarConstraint] = None):
        if not isinstance(sample_rate, ScalarConstraint) and sample_rate is not None:
            raise TypeError(
                f'"sample_rate" must be None or'
                f'{ScalarConstraint.__module__}.{ScalarConstraint.__qualname__} instance'
            )
        if not isinstance(channel_buffer_size, ScalarConstraint):
            raise TypeError(
                f'"channel_buffer_size" must be '
                f'{ScalarConstraint.__module__}.{ScalarConstraint.__qualname__} instance'
            )
        self._channel_units = {**channel_units}
        self._sample_timings = [SampleTiming(timing) for timing in sample_timings]
        self._streaming_modes = [StreamingMode(mode) for mode in streaming_modes]
        self._data_type = np.dtype(data_type)
        self._channel_buffer_size = channel_buffer_size
        if sample_rate is None:
            if SampleTiming.CONSTANT in self._sample_timings:
                raise ValueError('"sample_rate" ScalarConstraint must be provided if '
                                 'SampleTiming.CONSTANT is permitted')
            self._sample_rate = ScalarConstraint(default=1, bounds=(1, 1), increment=0)
        else:
            self._sample_rate = sample_rate

    @property
    def channel_units(self) -> Dict[str, str]:
        return self._channel_units.copy()

    @property
    def sample_timings(self) -> List[SampleTiming]:
        return self._sample_timings.copy()

    @property
    def streaming_modes(self) -> List[StreamingMode]:
        return self._streaming_modes.copy()

    @property
    def data_type(self) -> np.dtype:
        return self._data_type

    @property
    def sample_rate(self) -> ScalarConstraint:
        return self._sample_rate

    @property
    def channel_buffer_size(self) -> ScalarConstraint:
        return self._channel_buffer_size


class DataInStreamInterface(Base):
    """
    Interface for a generic input stream of data points with fixed sampling rate and data type.

    You can choose if a preset number of samples is recorded and buffered for read or if samples
    are acquired continuously into a (circular) read buffer.
    """

    @property
    @abstractmethod
    def constraints(self) -> DataInStreamConstraints:
        """ Read-only property returning the constraints on the settings for this data streamer. """
        pass

    @property
    @abstractmethod
    def available_samples(self) -> int:
        """ Read-only property to return the currently available number of samples per channel ready
        to read from buffer.
        """
        pass

    @property
    @abstractmethod
    def sample_rate(self) -> float:
        """ Read-only property returning the currently set sample rate in Hz.

        Ignored for anything but SampleTiming.CONSTANT.
        """
        pass

    @property
    @abstractmethod
    def channel_buffer_size(self) -> int:
        """ Read-only property returning the currently set buffer size in samples per channel.
        The total buffer size in bytes can be estimated by:
            <buffer_size> * <channel_count> * numpy.nbytes[<data_type>]

        For StreamingMode.FINITE this will also be the total number of samples to acquire per
        channel.
        """
        pass

    @property
    @abstractmethod
    def streaming_mode(self) -> StreamingMode:
        """ Read-only property returning the currently configured StreamingMode Enum """
        pass

    @property
    @abstractmethod
    def sample_timing(self) -> SampleTiming:
        """ Read-only property returning the currently configured SampleTiming Enum """
        pass

    @property
    @abstractmethod
    def active_channels(self) -> List[str]:
        """ Read-only property returning the currently configured active channel names """
        pass

    @abstractmethod
    def configure(self,
                  active_channels: Iterable[str],
                  streaming_mode: Union[StreamingMode, int],
                  sample_timing: Union[SampleTiming, int],
                  channel_buffer_size: int,
                  sample_rate: Optional[float] = None) -> None:
        """ Configure a data stream. See read-only properties for information on each parameter. """
        pass

    @abstractmethod
    def start_stream(self) -> None:
        """ Start the data acquisition/streaming """
        pass

    @abstractmethod
    def stop_stream(self) -> None:
        """ Stop the data acquisition/streaming """
        pass

    @abstractmethod
    def read_data_into_buffer(self,
                              data_buffer: np.ndarray,
                              timestamp_buffer: Optional[np.ndarray] = None,
                              number_of_samples: Optional[int] = None) -> None:
        """
        Read data from the stream buffer into a 1D/2D numpy array given as parameter.
        In case of a single data channel the numpy array can be either 1D or 2D. In case of more
        channels the array must be 2D with the first index corresponding to the channel number and
        the second index serving as sample index:
            data_buffer.shape == (<channel_count>, <sample_count>)
        The data_buffer array must have the same data type as self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.timedelta64 timestamp_buffer array has to be
        provided to be filled with timestamps corresponding to the data_buffer array. It must be
        at least <number_of_samples> in size.

        If number_of_samples is omitted it will be derived from buffer.shape[1]
        """
        pass

    @abstractmethod
    def read_available_data_into_buffer(self,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> None:
        """
        Read data from the stream buffer into a 1D/2D numpy array given as parameter.
        In case of a single data channel the numpy array can be either 1D or 2D. In case of more
        channels the array must be 2D with the first index corresponding to the channel number and
        the second index serving as sample index:
            data_buffer.shape == (<channel_count>, <sample_count>)
        The data_buffer array must have the same data type as self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.timedelta64 timestamp_buffer array has to be
        provided to be filled with timestamps corresponding to the data_buffer array. It must be
        at least <number_of_samples> in size.

        This method will read all currently available samples into buffer. If number of available
        samples exceed buffer size, read only as many samples as fit into the buffer.
        """
        pass

    @abstractmethod
    def read_data(self,
                  number_of_samples: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        """
        Read data from the stream buffer into a 2D numpy array and return it.
        The arrays first index corresponds to the channel number and the second index serves as
        sample index:
            return_array.shape == (self.number_of_channels, number_of_samples)
        The numpy arrays data type is the one defined in self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.timedelta64 timestamp_buffer array will be
        returned as well with timestamps corresponding to the data_buffer array.

        If number_of_samples is omitted all currently available samples are read from buffer.
        This method will not return until all requested samples have been read or a timeout occurs.
        If no samples are available, this method will immediately return an empty array.
        """
        pass

    @abstractmethod
    def read_single_point(self) -> Tuple[np.ndarray, Union[None, np.timedelta64]]:
        """
        This method will initiate a single sample read on each configured data channel.
        In general this sample may not be acquired simultaneous for all channels and timing in
        general can not be assured. Us this method if you want to have a non-timing-critical
        snapshot of your current data channel input.
        May not be available for all devices.
        The returned 1D numpy array will contain one sample for each channel.

        In case of SampleTiming.TIMESTAMP a single numpy.timedelta64 timestamp value will be
        returned as well.
        """
        pass
