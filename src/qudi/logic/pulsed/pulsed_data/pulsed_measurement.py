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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from qudi.util.datafitting import FitContainer
from qudi.logic.pulsed.pulsed_data.pulsed_measurement_logic_data import (
    PulsedMeasurementSettings,
    PulsedMeasurementData,
)
from qudi.logic.pulsed.pulsed_data.sequence_generator_logic_data import SequenceGeneratorSettings

if TYPE_CHECKING:
    # Deferred: pulse_objects.py sits above pulsed_data/ in the dependency layering (it already
    # imports several classes from this package), so importing these at runtime here would invert
    # that layering. Only needed for type hints below.
    from qudi.logic.pulsed.pulse_objects import PulseBlock, PulseBlockElement, PulseBlockEnsemble, PulseSequence


@dataclass(frozen=True)
class Settings:
    """Bundles the two independently-persisted settings containers describing a pulsed
    measurement: PulsedMeasurementLogic's own settings (microwave, fast counter, readout,
    extraction/analysis) and SequenceGeneratorLogic's (generation parameters, pulse generator
    hardware mirror).

    Each stays owned/persisted by its own logic module's StatusVar - this only holds references,
    bundled together when a measurement is captured. generator_settings is Optional since
    PulsedMeasurementLogic has no Connector to SequenceGeneratorLogic and can't fetch it alone;
    it's None whenever built by code with only PulsedMeasurementLogic access.
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
class PulseObjects:
    """Bundles the currently loaded PulseSequence/PulseBlockEnsemble with independent copies of
    every PulseBlockEnsemble/PulseBlock it references by name (real objects live in
    SequenceGeneratorLogic's saved-asset registries - untouched by this class). `ensembles`/
    `blocks` are read-only copies of just that subset, resolved via
    SequenceGeneratorLogic.resolve_asset_closure(), so a saved measurement's dict representation
    shows every block/ensemble/sequence definition directly instead of only a name (and with them
    every element, nested inside its block's element_list).

    Frozen-in-time: mutating SequenceGeneratorLogic's live registries after a snapshot was taken
    never affects an already-built PulseObjects instance.
    """

    #: The loaded top-level asset - independent copy (PulseBlockEnsemble.copy()/
    #: PulseSequence.copy()), or None if nothing is loaded. Also carries that asset's own
    #: sampling_information/measurement_information/generation_method_parameters - deliberately
    #: not duplicated as separate fields here.
    sequence: Optional[Union['PulseBlockEnsemble', 'PulseSequence']] = None
    #: Every PulseBlockEnsemble `sequence` references by name, keyed by name - independent copies.
    #: Empty if `sequence` is itself a bare PulseBlockEnsemble (or nothing is loaded): there is
    #: nothing to resolve one level up in that case.
    ensembles: Dict[str, 'PulseBlockEnsemble'] = field(default_factory=dict)
    #: Every PulseBlock referenced by `ensembles` (or, for a bare-ensemble `sequence`, referenced
    #: directly by it), keyed by name - independent copies, same provenance as `ensembles`.
    blocks: Dict[str, 'PulseBlock'] = field(default_factory=dict)

    @property
    def elements(self) -> List['PulseBlockElement']:
        """Every PulseBlockElement across every PulseBlock in `blocks`, flattened. A convenience
        view for callers wanting a flat list; not a stored field and not part of to_dict(), since
        `blocks` is the single source of truth and already contains every element."""
        return [element for block in self.blocks.values() for element in block.element_list]

    def to_dict(self):
        """`sequence` is tagged with its own class name so from_dict() knows which class to
        reconstruct.

        The flattened `elements` view (see the property above) is deliberately NOT exported: every
        element already appears inside its block's 'element_list', so a second copy would only
        duplicate them. Use the `elements` property on a live instance if you want them flat.
        """
        sequence_dict = None
        if self.sequence is not None:
            sequence_dict = {'type': type(self.sequence).__name__}
            sequence_dict.update(self.sequence.get_dict_representation())
        return {
            'sequence': sequence_dict,
            'ensembles': {name: ens.get_dict_representation() for name, ens in self.ensembles.items()},
            'blocks': {name: blk.get_dict_representation() for name, blk in self.blocks.items()},
        }

    def to_metadata_dict(self):
        """Trimmed variant of to_dict() for saved-measurement metadata: drops fields from each
        sequence/ensemble's 'sampling_information' that are either pure duplication
        ('ensemble_info' duplicates this same PulseObjects' `ensembles`; 'pulse_generator_settings'
        is a single global snapshot shown once elsewhere already) or large per-sample arrays
        (laser_rising_bins/falling_bins, elements_length_bins, and everything in
        SamplingInformation._legacy_data) that would otherwise dominate the header with numbers
        rather than sequence structure. Structural fields (name, block_list/ensemble_list,
        generation_method_parameters, element definitions) are unaffected. One-way/display-only -
        no from_metadata_dict().
        """
        def trim_sampling_information(sampling_info_dict):
            return {
                'waveforms': sampling_info_dict.get('waveforms', []),
                'number_of_samples': sampling_info_dict.get('number_of_samples'),
                'number_of_elements': sampling_info_dict.get('number_of_elements'),
                'ideal_length': sampling_info_dict.get('ideal_length'),
                'step_waveform_list': sampling_info_dict.get('step_waveform_list', []),
            }

        data = self.to_dict()
        if data['sequence'] is not None:
            data['sequence']['sampling_information'] = trim_sampling_information(
                data['sequence']['sampling_information']
            )
        for ensemble_dict in data['ensembles'].values():
            ensemble_dict['sampling_information'] = trim_sampling_information(
                ensemble_dict['sampling_information']
            )
        return data

    @classmethod
    def from_dict(cls, data):
        # Local import: pulse_objects.py sits above pulsed_data/ in the dependency layering - a
        # module-level import here would invert it.
        from qudi.logic.pulsed.pulse_objects import PulseBlock, PulseBlockEnsemble, PulseSequence

        blocks = {
            name: PulseBlock.block_from_dict(block_dict)
            for name, block_dict in (data.get('blocks') or {}).items()
        }
        ensembles = {
            name: PulseBlockEnsemble.ensemble_from_dict(ensemble_dict)
            for name, ensemble_dict in (data.get('ensembles') or {}).items()
        }
        sequence_dict = data.get('sequence')
        sequence = None
        if sequence_dict is not None:
            seq_type = sequence_dict.get('type')
            if seq_type == 'PulseSequence':
                sequence = PulseSequence.sequence_from_dict(sequence_dict)
            elif seq_type == 'PulseBlockEnsemble':
                sequence = PulseBlockEnsemble.ensemble_from_dict(sequence_dict)
        return cls(sequence=sequence, ensembles=ensembles, blocks=blocks)


@dataclass
class PulsedMeasurement:
    """Top-level container for everything that makes up a single pulsed measurement: its
    settings, its acquired/evaluated data, and the pulse objects (sequence/ensembles/blocks) that
    produced it. Built by PulsedMeasurementLogic.get_pulsed_measurement() / PulsedMasterLogic.
    get_pulsed_measurement() as a frozen-in-time snapshot: `data` and `objects` are populated from
    independent copies, not live references, so a returned/stored PulsedMeasurement does not keep
    changing under the caller as the source measurement continues running or a same-named asset
    gets reloaded/edited later.

    Deliberately NOT frozen (unlike the settings dataclasses nested inside it): `data`/`objects`
    are ordinary mutable objects, matching PulsedData/PulseObjects themselves - freezing this
    container wouldn't make them immutable, only misleadingly imply it.
    """

    settings: Settings
    #: Source: PulsedMeasurementLogic.measurement_data.copy() plus the current fit results - see
    #: PulsedData.
    data: Optional[PulsedData] = None
    #: Source: PulsedMeasurementLogic.loaded_asset.copy() plus SequenceGeneratorLogic.
    #: resolve_asset_closure() - see PulseObjects. Always present (never None itself); "nothing
    #: loaded" is represented by objects.sequence is None.
    objects: PulseObjects = field(default_factory=PulseObjects)

    def to_dict(self):
        """Serializes `settings`, `data`, and `objects` (sequence/ensembles/blocks)."""
        return {
            'settings': self.settings.to_dict(),
            'data': self.data.to_dict() if self.data is not None else None,
            'objects': self.objects.to_dict(),
        }

    @classmethod
    def from_dict(cls, data):
        data_dict = data.get('data')
        objects_dict = data.get('objects')
        return cls(
            settings=Settings.from_dict(data['settings']),
            data=PulsedData.from_dict(data_dict) if data_dict is not None else None,
            objects=PulseObjects.from_dict(objects_dict) if objects_dict is not None else PulseObjects(),
        )
