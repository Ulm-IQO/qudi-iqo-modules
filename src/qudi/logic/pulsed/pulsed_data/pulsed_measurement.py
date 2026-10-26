# -*- coding: utf-8 -*-
"""
Container bundling the settings, data and pulse objects of a pulsed measurement.

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
from logging import getLogger
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from qudi.util.datafitting import FitContainer
from qudi.logic.pulsed.pulsed_data.pulsed_measurement_logic_data import (
    PulsedMeasurementSettings,
    PulsedMeasurementData,
)
from qudi.logic.pulsed.pulsed_data.sequence_generator_logic_data import SequenceGeneratorSettings

if TYPE_CHECKING:
    # Type hints only: pulse_objects.py imports from this package.
    from qudi.logic.pulsed.pulse_objects import PulseBlock, PulseBlockElement, PulseBlockEnsemble, PulseSequence

_logger = getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    """Settings of PulsedMeasurementLogic and SequenceGeneratorLogic for one measurement.

    generator_settings is None when built without access to SequenceGeneratorLogic.
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

    def to_metadata_dict(self, omit=()):
        """to_dict() without the entries the saved file already writes elsewhere.

        Parameters
        ----------
        omit : iterable of str
            Dotted paths to drop, e.g. 'generator_settings.generation_parameters'. Missing paths
            are ignored.
        """
        data = self.to_dict()
        for path in omit:
            container_key, _, field_key = path.partition('.')
            container = data.get(container_key)
            if isinstance(container, dict):
                container.pop(field_key, None)
        return data

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
    """Measurement data arrays and the fit results computed for them.

    The fit results are lmfit ModelResult objects. to_dict() exports them as plain dicts, which
    from_dict() cannot turn back into ModelResults, so they are lost on a round trip.
    """

    measurement_data: PulsedMeasurementData
    fit_result: Optional[object] = None  # lmfit.model.ModelResult
    fit_result_alt: Optional[object] = None  # lmfit.model.ModelResult

    def to_dict(self):
        return {
            'measurement_data': self.measurement_data.to_dict(),
            'fit_result': FitContainer.dict_result(self.fit_result) if self.fit_result is not None else None,
            'fit_result_alt': (
                FitContainer.dict_result(self.fit_result_alt) if self.fit_result_alt is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data):
        # Fit results can't be restored, see class docstring.
        return cls(measurement_data=PulsedMeasurementData.from_dict(data['measurement_data']))


@dataclass
class PulseObjects:
    """Copies of the loaded asset and all ensembles and blocks it references.

    The copies are independent of SequenceGeneratorLogic, so later changes to its assets don't
    affect this object.
    """

    #: Copy of the loaded PulseSequence or PulseBlockEnsemble, or None if nothing is loaded.
    loaded_asset: Optional[Union['PulseBlockEnsemble', 'PulseSequence']] = None
    #: Ensembles referenced by a loaded sequence, keyed by name. Empty for a loaded ensemble.
    ensembles: Dict[str, 'PulseBlockEnsemble'] = field(default_factory=dict)
    #: Blocks referenced by the loaded asset, keyed by name.
    blocks: Dict[str, 'PulseBlock'] = field(default_factory=dict)

    @property
    def loaded_sequence(self) -> Optional['PulseSequence']:
        """`loaded_asset` if it is a PulseSequence, else None."""
        return self.loaded_asset if getattr(self.loaded_asset, 'is_sequence', None) is True else None

    @property
    def loaded_ensemble(self) -> Optional['PulseBlockEnsemble']:
        """`loaded_asset` if it is a PulseBlockEnsemble, else None."""
        return self.loaded_asset if getattr(self.loaded_asset, 'is_sequence', None) is False else None

    @property
    def elements(self) -> List['PulseBlockElement']:
        """All PulseBlockElements of all blocks as a flat list."""
        return [element for block in self.blocks.values() for element in block.element_list]

    def to_dict(self):
        """Return a dict with the keys 'sequence', 'ensembles' and 'blocks'.

        A loaded sequence goes into 'sequence'. A loaded ensemble goes into 'ensembles' and
        'sequence' stays empty, so `if not data['sequence']` means no sequence was loaded.
        """
        sequence = {}
        ensembles = {name: ens.get_dict_representation() for name, ens in self.ensembles.items()}
        if self.loaded_sequence is not None:
            sequence = self.loaded_sequence.get_dict_representation()
        elif self.loaded_ensemble is not None:
            ensembles[self.loaded_ensemble.name] = self.loaded_ensemble.get_dict_representation()
        return {
            'sequence': sequence,
            'ensembles': ensembles,
            'blocks': {name: blk.get_dict_representation() for name, blk in self.blocks.items()},
        }

    def to_metadata_dict(self, omit_generation_method_parameters=False):
        """to_dict() with 'sampling_information' reduced to its compact fields, for file headers.

        Parameters
        ----------
        omit_generation_method_parameters : bool
            Also drop 'generation_method_parameters' from the loaded asset's entry.
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
        loaded_ensemble_name = (
            self.loaded_ensemble.name if self.loaded_sequence is None
            and self.loaded_ensemble is not None else None
        )

        sequence_dict = data['sequence']
        if sequence_dict:
            sequence_dict['sampling_information'] = trim_sampling_information(
                sequence_dict['sampling_information']
            )
            if omit_generation_method_parameters:
                sequence_dict.pop('generation_method_parameters', None)
        for name, ensemble_dict in data['ensembles'].items():
            ensemble_dict['sampling_information'] = trim_sampling_information(
                ensemble_dict['sampling_information']
            )
            if omit_generation_method_parameters and name == loaded_ensemble_name:
                ensemble_dict.pop('generation_method_parameters', None)
        return data

    @classmethod
    def from_dict(cls, data):
        # Local import to avoid a circular import.
        from qudi.logic.pulsed.pulse_objects import PulseBlock, PulseBlockEnsemble, PulseSequence

        blocks = {
            name: PulseBlock.block_from_dict(block_dict)
            for name, block_dict in (data.get('blocks') or {}).items()
        }
        ensembles = {
            name: PulseBlockEnsemble.ensemble_from_dict(ensemble_dict)
            for name, ensemble_dict in (data.get('ensembles') or {}).items()
        }
        # Supported layouts:
        #   1. current: 'sequence', with a loaded ensemble stored in 'ensembles'
        #   2. older: 'loaded_sequence' / 'loaded_ensemble'
        #   3. oldest: 'sequence' with a 'type' tag
        sequence_dict = data.get('sequence') or None
        ensemble_dict = None
        take_loaded_ensemble_from_ensembles = False
        if sequence_dict is not None and 'type' in sequence_dict:
            if sequence_dict.get('type') == 'PulseBlockEnsemble':
                sequence_dict, ensemble_dict = None, sequence_dict
            elif sequence_dict.get('type') != 'PulseSequence':
                sequence_dict = None
        elif sequence_dict is None:
            sequence_dict = data.get('loaded_sequence')
            ensemble_dict = data.get('loaded_ensemble')
            take_loaded_ensemble_from_ensembles = (
                sequence_dict is None and ensemble_dict is None and bool(ensembles)
            )

        loaded_asset = None
        if sequence_dict is not None:
            loaded_asset = PulseSequence.sequence_from_dict(sequence_dict)
        elif ensemble_dict is not None:
            loaded_asset = PulseBlockEnsemble.ensemble_from_dict(ensemble_dict)
        elif take_loaded_ensemble_from_ensembles:
            # A loaded ensemble is the only entry in 'ensembles'.
            if len(ensembles) == 1:
                _, loaded_asset = ensembles.popitem()
            else:
                _logger.warning(
                    'Cannot tell which of the %d ensembles was the loaded asset: no "sequence" '
                    'entry and more than one candidate. Leaving loaded_asset unset.',
                    len(ensembles),
                )
        return cls(loaded_asset=loaded_asset, ensembles=ensembles, blocks=blocks)

    def __setstate__(self, state):
        """Support unpickling old files that used `sequence` instead of `loaded_asset`."""
        if 'sequence' in state and 'loaded_asset' not in state:
            state = dict(state)
            state['loaded_asset'] = state.pop('sequence')
        self.__dict__.update(state)


@dataclass
class PulsedMeasurement:
    """Snapshot of a pulsed measurement: its settings, data and pulse objects.

    `data` and `objects` hold copies, so the snapshot doesn't change while the measurement keeps
    running.
    """

    settings: Settings
    data: Optional[PulsedData] = None
    objects: PulseObjects = field(default_factory=PulseObjects)

    def to_dict(self):
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
