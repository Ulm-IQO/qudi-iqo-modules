# -*- coding: utf-8 -*-
"""
This file contains dataclasses used by PulsedMasterLogic to store and manage its busy/running
status flags and the fit containers it exposes to the GUI.

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


##############################################################################
#   The following classes are for the PulsedMasterLogic class and are used  #
#         to store and manage its status flags and fit containers.          #
##############################################################################

class PulsedMasterStatus(dict):
    """dict-based structured container for PulsedMasterLogic's busy/running status flags.

    Kept as a dict subclass (the same pattern as AnalysisParameters/ExtractionParameters/
    GenerationMethodParameters in pulsed_measurement_logic_data.py, and SequenceStep in
    pulse_objects.py) rather than a plain dataclass, because
    qudi.gui.pulsed.pulsed_maingui.PulsedMeasurementGui reads AND writes these flags directly
    via "pulsedmasterlogic().status_dict['flag_name']" (including plain assignment, e.g.
    "status_dict['benchmark_busy'] = True"). Subclassing dict keeps that GUI code working
    unchanged while giving PulsedMasterLogic itself named, typo-proof attribute access
    (self.status_dict.sampload_busy instead of self.status_dict['sampload_busy']).

    All flags default to False.
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
        """True while a sample-and-load operation (sample immediately followed by load) is in
        progress."""
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
    """Bundles the primary and alternative-data FitContainer instances exposed by
    PulsedMeasurementLogic, so callers can use named access (.primary/.alternative) instead of
    an anonymous (fc, alt_fc) tuple.

    Supports iteration/indexing so existing tuple-style call sites (fit_containers[0],
    fit_containers[1]) keep working unchanged.
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
