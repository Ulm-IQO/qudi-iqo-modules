# -*- coding: utf-8 -*-
"""
This file contains the dataclass container used to store and manage all the
settings, data and sequence for pulsed measurements.

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
from typing import TYPE_CHECKING, Optional, Union

from qudi.util.datafitting import FitContainer
from qudi.logic.pulsed.pulsed_data.pulsed_measurement_logic_data import (
    PulsedMeasurementSettings,
    PulsedMeasurementData,
)
from qudi.logic.pulsed.pulsed_data.sequence_generator_logic_data import SequenceGeneratorSettings

if TYPE_CHECKING:
    # Deferred: pulse_objects.py sits above pulsed_data/ in the dependency layering (it already
    # imports several classes from this package), so importing PulseBlockEnsemble/PulseSequence
    # at runtime here would invert that layering. Only needed for the `sequence` type hint below.
    from qudi.logic.pulsed.pulse_objects import PulseBlockEnsemble, PulseSequence


@dataclass(frozen=True)
class Settings:
    """Bundles the two independently-persisted settings containers that together fully describe
    a pulsed measurement: PulsedMeasurementLogic's own settings (microwave, fast counter,
    readout, alternative signal processing, extraction/analysis parameters, timer interval) and
    SequenceGeneratorLogic's settings (generation parameters, pulse generator hardware mirror,
    upload speed benchmarks).

    These stay two nested, independently-owned sub-objects rather than being flattened into one
    - each is still owned, mutated and persisted by its own logic module's StatusVar
    (PulsedMeasurementLogic._settings / SequenceGeneratorLogic._generator_settings). This
    container only ever holds references to those, for the purpose of bundling a matched pair
    together at the point a full pulsed measurement is captured (e.g.
    PulsedMeasurementLogic.get_pulsed_measurement()/save_measurement_data(), which accept
    generator_settings as a parameter handed down from PulsedMasterLogic - PulsedMeasurementLogic
    has no Connector to SequenceGeneratorLogic and cannot fetch generator_settings on its own).
    generator_settings is therefore Optional: it is None whenever a Settings/PulsedMeasurement is
    built by code that only has access to PulsedMeasurementLogic.

    No fields overlap between the two nested settings objects: PulsedMeasurementSettings covers
    the *measurement/readout* side (external CW microwave source, fast counter, extraction/
    analysis of already-acquired data) while SequenceGeneratorSettings covers the *pulse
    generation* side (AWG channel/timing setup, pulse generator hardware mirror, upload speed
    benchmarks). One close pair is worth flagging explicitly so it isn't mistaken for a
    duplicate: PulsedMeasurementSettings.microwave_settings (power/frequency of the external CW
    microwave source, applied when use_ext_microwave is enabled) versus
    SequenceGeneratorSettings.generation_parameters.microwave_frequency/microwave_amplitude (the
    frequency/amplitude value baked into a generated, AWG-played pulse waveform). These describe
    two different microwave signal paths - external CW hardware vs. values encoded into a
    sampled waveform - that are routinely calibrated to different values, so they are correctly
    kept as separate fields rather than merged.
    """

    measurement_settings: PulsedMeasurementSettings
    generator_settings: Optional[SequenceGeneratorSettings] = None

    def to_dict(self):
        return {
            'measurement_settings': self.measurement_settings.to_dict(),
            'generator_settings': (
                self.generator_settings.to_dict() if self.generator_settings is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data):
        generator_data = data.get('generator_settings')
        return cls(
            measurement_settings=PulsedMeasurementSettings.from_dict(data['measurement_settings']),
            generator_settings=(
                SequenceGeneratorSettings.from_dict(generator_data) if generator_data is not None else None
            ),
        )


@dataclass
class PulsedData:
    """Bundles this measurement's raw/laser/signal arrays with any fit results computed for it.

    fit_result/fit_result_alt are lmfit.model.ModelResult objects (or None) - the output of
    PulsedMeasurementLogic.do_fit(). Like `sequence` on PulsedMeasurement, these have only a
    one-way, lossy export path: FitContainer.dict_result() (model name + parameter values/
    stderr, already used in saved metadata today via _get_signal_metadata()) - there is no
    reconstruction path anywhere in this codebase, and lmfit does not offer a simple one either,
    so from_dict() cannot restore live ModelResult objects; round-tripping through to_dict()/
    from_dict() loses them, same as `sequence` on PulsedMeasurement.

    Not copied on construction: PulsedMeasurementLogic reassigns self._fit_result/
    self._fit_result_alt wholesale on every new fit rather than mutating an existing ModelResult
    in place, so holding a bare reference is already safe for a frozen snapshot's purposes -
    unlike measurement_data, which is actively mutated in place while a measurement runs and
    therefore does need PulsedMeasurementData.copy().
    """

    measurement_data: PulsedMeasurementData
    fit_result: Optional[object] = None  # lmfit.model.ModelResult - see class docstring
    fit_result_alt: Optional[object] = None  # lmfit.model.ModelResult - see class docstring

    def to_dict(self):
        """fit_result/fit_result_alt are exported one-way via FitContainer.dict_result() for
        display/save purposes - from_dict() cannot restore them, see class docstring."""
        return {
            'measurement_data': self.measurement_data.to_dict(),
            'fit_result': FitContainer.dict_result(self.fit_result) if self.fit_result is not None else None,
            'fit_result_alt': (
                FitContainer.dict_result(self.fit_result_alt) if self.fit_result_alt is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data):
        # fit_result/fit_result_alt intentionally not restored - see class docstring.
        return cls(measurement_data=PulsedMeasurementData.from_dict(data['measurement_data']))


@dataclass
class PulsedMeasurement:
    """Top-level container for everything that makes up a single pulsed measurement: its
    settings, its acquired/evaluated data, and the pulse sequence/ensemble that produced it.
    Built by PulsedMeasurementLogic.get_pulsed_measurement() / PulsedMasterLogic.
    get_pulsed_measurement() as a frozen-in-time snapshot: `data` and `sequence` are populated
    from independent copies (PulsedMeasurementData.copy() / PulseBlockEnsemble.copy() /
    PulseSequence.copy()), not live references, so a returned/stored PulsedMeasurement does not
    keep changing under the caller as the source measurement continues running or a same-named
    asset gets reloaded/edited later.

    `sequence` (and `data.fit_result`/`data.fit_result_alt` - see PulsedData) is intentionally
    NOT fully included in to_dict()/from_dict(): PulseBlockEnsemble/PulseSequence are not
    actually persisted via dict serialization anywhere in this codebase - the real persistence
    path (SequenceGeneratorLogic._save_ensemble_to_file() et al.) pickles the live object
    directly to a .block/.ensemble/.sequence file. get_dict_representation()/
    ensemble_from_dict()/sequence_from_dict() do still exist on those classes but have no live
    callers - their only caller is a fully commented-out block of abandoned StatusVar hooks in
    sequence_generator_logic.py. If a whole PulsedMeasurement ever needs full serialization, it
    should be pickled to match how the object it wraps is already persisted, rather than given a
    bespoke dict-safe format this codebase already tried and dropped for this exact type.

    Deliberately NOT frozen (unlike the settings dataclasses nested inside it): `data`/`sequence`
    are ordinary mutable objects, matching PulsedData/PulseBlockEnsemble/PulseSequence themselves
    - freezing this container wouldn't make them immutable, only misleadingly imply it.
    """

    settings: Settings
    #: Source: PulsedMeasurementLogic.measurement_data.copy() plus the current fit results - see
    #: PulsedData.
    data: Optional[PulsedData] = None
    #: Source: PulsedMeasurementLogic.loaded_asset.copy() (whichever PulseBlockEnsemble/
    #: PulseSequence is currently loaded) - an independent copy, not a live reference. Also
    #: carries that asset's own sampling_information/measurement_information/
    #: generation_method_parameters - deliberately not duplicated as separate fields here.
    sequence: Optional[Union['PulseBlockEnsemble', 'PulseSequence']] = None

    def to_dict(self):
        """Serializes `settings` and `data`. `sequence` is intentionally excluded - see class
        docstring."""
        return {
            'settings': self.settings.to_dict(),
            'data': self.data.to_dict() if self.data is not None else None,
        }

    @classmethod
    def from_dict(cls, data):
        data_dict = data.get('data')
        return cls(
            settings=Settings.from_dict(data['settings']),
            data=PulsedData.from_dict(data_dict) if data_dict is not None else None,
        )
