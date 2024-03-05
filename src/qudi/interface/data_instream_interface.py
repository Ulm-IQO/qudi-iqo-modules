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

__all__ = ['StreamingMode', 'SampleTiming', 'DataInStreamConstraints', 'DataInStreamInterface']

import numpy as np
from typing import Union, Type, Iterable, Mapping, Optional, Dict, List, Tuple, Sequence
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
    """ Collection of constraints for hardware modules implementing DataInStreamInterface """
    def __init__(self,
                 channel_units: Mapping[str, str],
                 sample_timing: Union[SampleTiming, int],
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
        self._sample_timing = SampleTiming(sample_timing)
        self._streaming_modes = [StreamingMode(mode) for mode in streaming_modes]
        self._data_type = np.dtype(data_type).type
        self._channel_buffer_size = channel_buffer_size
        if sample_rate is None:
            if self._sample_timing != SampleTiming.RANDOM:
                raise ValueError('"sample_rate" ScalarConstraint must be provided if '
                                 '"sample_timing" is not SampleTiming.RANDOM')
            self._sample_rate = ScalarConstraint(default=1, bounds=(1, 1), increment=0)
        else:
            self._sample_rate = sample_rate

    @property
    def channel_units(self) -> Dict[str, str]:
        return self._channel_units.copy()

    @property
    def sample_timing(self) -> SampleTiming:
        return self._sample_timing

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
    """ Interface for a generic input stream (finite or infinite) of data points from multiple
    channels with common data type.

    A connecting logic module can choose to manage its own buffer array(s) and let the hardware
    module read samples directly into the provided arrays for maximum data throughput using
    "read_data_into_buffer" and "read_available_data_into_buffer". Alternatively one can call
    "read_data" or "read_single_point" which will return sufficiently large data arrays that are
    newly allocated each time the method is called (less efficient but no buffer handling needed).
    In any case each time a "read_..." method is called, the samples returned are not available
    anymore and will be consumed. So multiple logic modules can not read from the same data stream.

    The sample timing can behave according to 3 different modes (Enum). Check constraints to see
    which mode is used by the hardware.

    SampleTiming.CONSTANT: The sample rate is deterministic and constant. Each sample in the stream
                           has a fixed timing determined by the sample rate.
    SampleTiming.TIMESTAMP: The sample rate is just a hint for the hardware but can not be
                            considered constant. The hardware will provide a numpy.float64 timestamp
                            in seconds from the start of the stream for each sample. This requires
                            an additional timestamp buffer array in addition to the normal channel
                            sample buffer.
    SampleTiming.RANDOM: The sample rate is just a hint for the hardware but can not be
                         considered constant. There is no deterministic time correlation between
                         samples, except that they are acquired one after another.
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
        It must be ensured that each channel can provide at least the number of samples returned
        by this property.
        """
        pass

    @property
    @abstractmethod
    def sample_rate(self) -> float:
        """ Read-only property returning the currently set sample rate in Hz.
        For SampleTiming.CONSTANT this is the sample rate of the hardware, for any other timing mode
        this property represents only a hint to the actual hardware timebase and can not be
        considered accurate.
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
    def active_channels(self) -> List[str]:
        """ Read-only property returning the currently configured active channel names """
        pass

    @abstractmethod
    def configure(self,
                  active_channels: Sequence[str],
                  streaming_mode: Union[StreamingMode, int],
                  channel_buffer_size: int,
                  sample_rate: float) -> None:
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
                              samples_per_channel: int,
                              timestamp_buffer: Optional[np.ndarray] = None) -> None:
        """ Read data from the stream buffer into a 1D numpy array given as parameter.
        Samples of all channels are stored interleaved in contiguous memory.
        In case of a multidimensional buffer array, this buffer will be flattened before written
        into.
        The 1D data_buffer can be unraveled into channel and sample indexing with:

            data_buffer.reshape([<samples_per_channel>, <channel_count>])

        The data_buffer array must have the same data type as self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.float64 timestamp_buffer array has to be
        provided to be filled with timestamps corresponding to the data_buffer array. It must be
        able to hold at least <samples_per_channel> items:

        This function is blocking until the required number of samples has been acquired.
        """
        pass

    @abstractmethod
    def read_available_data_into_buffer(self,
                                        data_buffer: np.ndarray,
                                        timestamp_buffer: Optional[np.ndarray] = None) -> int:
        """ Read data from the stream buffer into a 1D numpy array given as parameter.
        All samples for each channel are stored in consecutive blocks one after the other.
        The number of samples read per channel is returned and can be used to slice out valid data
        from the buffer arrays like:

            valid_data = data_buffer[:<channel_count> * <return_value>]
            valid_timestamps = timestamp_buffer[:<return_value>]

        See "read_data_into_buffer" documentation for more details.

        This method will read all currently available samples into buffer. If number of available
        samples exceeds buffer size, read only as many samples as fit into the buffer.
        """
        pass

    @abstractmethod
    def read_data(self,
                  samples_per_channel: Optional[int] = None
                  ) -> Tuple[np.ndarray, Union[np.ndarray, None]]:
        """ Read data from the stream buffer into a 1D numpy array and return it.
        All samples for each channel are stored in consecutive blocks one after the other.
        The returned data_buffer can be unraveled into channel samples with:

            data_buffer.reshape([<samples_per_channel>, <channel_count>])

        The numpy array data type is the one defined in self.constraints.data_type.

        In case of SampleTiming.TIMESTAMP a 1D numpy.float64 timestamp_buffer array will be
        returned as well with timestamps corresponding to the data_buffer array.

        If samples_per_channel is omitted all currently available samples are read from buffer.
        This method will not return until all requested samples have been read or a timeout occurs.
        """
        pass

    @abstractmethod
    def read_single_point(self) -> Tuple[np.ndarray, Union[None, np.float64]]:
        """ This method will initiate a single sample read on each configured data channel.
        In general this sample may not be acquired simultaneous for all channels and timing in
        general can not be assured. Us this method if you want to have a non-timing-critical
        snapshot of your current data channel input.
        May not be available for all devices.
        The returned 1D numpy array will contain one sample for each channel.

        In case of SampleTiming.TIMESTAMP a single numpy.float64 timestamp value will be returned
        as well.
        """
        pass
