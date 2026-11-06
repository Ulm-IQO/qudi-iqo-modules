# -*- coding: utf-8 -*-
"""
Data containers for PulsedMasterLogic: status flags and fit containers.

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
from dataclasses import dataclass

from qudi.util.datafitting import FitContainer


class PulsedMasterStatus(dict):
    """Busy/running status flags of PulsedMasterLogic. All flags default to False.

    A dict subclass because the pulsed GUI reads and writes the flags by key
    (`status_dict['benchmark_busy'] = True`). Attribute access works as well.
    """

    _FLAG_NAMES = (
        'sampling_ensemble_busy',
        'sampling_sequence_busy',
        'sampload_busy',
        'loading_busy',
        'pulser_running',
        'measurement_running',
        'microwave_running',
        'predefined_generation_busy',
        'fitting_busy',
        'benchmark_busy',
    )

    def __init__(self, *args, **kwargs):
        super().__init__({name: False for name in self._FLAG_NAMES})
        self.update(dict(*args, **kwargs))

    @property
    def sampling_ensemble_busy(self) -> bool:
        """True while a PulseBlockEnsemble is being sampled."""
        return self['sampling_ensemble_busy']

    @sampling_ensemble_busy.setter
    def sampling_ensemble_busy(self, value: bool):
        self['sampling_ensemble_busy'] = bool(value)

    @property
    def sampling_sequence_busy(self) -> bool:
        """True while a PulseSequence is being sampled."""
        return self['sampling_sequence_busy']

    @sampling_sequence_busy.setter
    def sampling_sequence_busy(self, value: bool):
        self['sampling_sequence_busy'] = bool(value)

    @property
    def sampload_busy(self) -> bool:
        """True while a sample-and-load operation is in progress."""
        return self['sampload_busy']

    @sampload_busy.setter
    def sampload_busy(self, value: bool):
        self['sampload_busy'] = bool(value)

    @property
    def loading_busy(self) -> bool:
        """True while a waveform/sequence is being loaded onto the pulse generator."""
        return self['loading_busy']

    @loading_busy.setter
    def loading_busy(self, value: bool):
        self['loading_busy'] = bool(value)

    @property
    def pulser_running(self) -> bool:
        """True while the pulse generator output is switched on."""
        return self['pulser_running']

    @pulser_running.setter
    def pulser_running(self, value: bool):
        self['pulser_running'] = bool(value)

    @property
    def measurement_running(self) -> bool:
        """True while a pulsed measurement is running."""
        return self['measurement_running']

    @measurement_running.setter
    def measurement_running(self, value: bool):
        self['measurement_running'] = bool(value)

    @property
    def microwave_running(self) -> bool:
        """True while the external microwave source is switched on."""
        return self['microwave_running']

    @microwave_running.setter
    def microwave_running(self, value: bool):
        self['microwave_running'] = bool(value)

    @property
    def predefined_generation_busy(self) -> bool:
        """True while a predefined generator method is generating blocks/ensembles/sequences."""
        return self['predefined_generation_busy']

    @predefined_generation_busy.setter
    def predefined_generation_busy(self, value: bool):
        self['predefined_generation_busy'] = bool(value)

    @property
    def fitting_busy(self) -> bool:
        """True while a data fit is being calculated."""
        return self['fitting_busy']

    @fitting_busy.setter
    def fitting_busy(self, value: bool):
        self['fitting_busy'] = bool(value)

    @property
    def benchmark_busy(self) -> bool:
        """True while a pulse generator upload/load speed benchmark is running."""
        return self['benchmark_busy']

    @benchmark_busy.setter
    def benchmark_busy(self, value: bool):
        self['benchmark_busy'] = bool(value)

    def to_dict(self) -> dict:
        return dict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(data) if isinstance(data, dict) else cls()


@dataclass(frozen=True)
class FitContainers:
    """The primary and alternative FitContainer of PulsedMeasurementLogic.

    Also supports tuple-style unpacking and indexing.
    """

    primary: FitContainer
    alternative: FitContainer

    @property
    def as_tuple(self):
        return (self.primary, self.alternative)

    def __iter__(self):
        return iter(self.as_tuple)

    def __len__(self):
        return len(self.as_tuple)

    def __getitem__(self, index):
        return self.as_tuple[index]
