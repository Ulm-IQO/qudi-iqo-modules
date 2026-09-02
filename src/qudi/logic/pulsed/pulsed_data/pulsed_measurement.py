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
from logging import getLogger
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

_logger = getLogger(__name__)


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

    def to_metadata_dict(self, omit=()):
        """Trimmed variant of to_dict() for saved-measurement metadata: drops any nested settings
        container that the calling header already writes out in full at its own top level, so no
        container is ever written twice into one file and the two copies cannot drift apart.

        `omit` holds dotted paths into the dict to_dict() returns, e.g.
        'measurement_settings.fast_counter_settings' or 'generator_settings.generation_parameters'.
        A path naming something absent is ignored, so a caller may list a key that only exists when
        generator_settings is not None.

        Which paths each saved file omits is decided by its metadata builder - see the
        _*_METADATA_OMIT constants in pulsed_measurement_logic.py. One-way/display-only, mirroring
        PulseObjects.to_metadata_dict() below - no from_metadata_dict(); to_dict() stays lossless
        for the .pulsedmeasurement snapshot.
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
    """Bundles this measurement's raw/laser/signal arrays with any fit results computed for it.

    fit_result/fit_result_alt are lmfit.model.ModelResult objects (or None) - the output of
    PulsedMeasurementLogic.do_fit(). Like `loaded_asset` on PulseObjects, these have only a
    one-way, lossy export path: FitContainer.dict_result() (model name + parameter values/
    stderr, already used in saved metadata today via _get_signal_metadata()) - there is no
    reconstruction path anywhere in this codebase, and lmfit does not offer a simple one either,
    so from_dict() cannot restore live ModelResult objects; round-tripping through to_dict()/
    from_dict() loses them, same as `loaded_asset` on PulseObjects.

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

    One slot holds the loaded asset (`loaded_asset`) whichever kind it is; `loaded_sequence` and
    `loaded_ensemble` are Optional views over it that read as None for the other kind. to_dict()
    flattens that back out into a fixed three-key shape - see its docstring.
    """

    #: The loaded top-level asset, whichever kind it is - independent copy
    #: (PulseBlockEnsemble.copy()/PulseSequence.copy()), or None if nothing is loaded. Named for
    #: the concept rather than for one of the two types it can hold, matching
    #: PulsedMeasurementLogic/PulsedMasterLogic/SequenceGeneratorLogic, which all call it
    #: `loaded_asset` too. Read it through `loaded_sequence`/`loaded_ensemble` below when you care
    #: which kind it is. Also carries that asset's own sampling_information/
    #: measurement_information/generation_method_parameters - deliberately not duplicated as
    #: separate fields here.
    loaded_asset: Optional[Union['PulseBlockEnsemble', 'PulseSequence']] = None
    #: Every PulseBlockEnsemble `loaded_asset` references by name, keyed by name - independent
    #: copies. Empty *on the instance* if `loaded_asset` is itself a bare PulseBlockEnsemble (or
    #: nothing is loaded): there is nothing to resolve one level up in that case. Note this differs
    #: from the saved form, where to_dict() files a bare loaded ensemble into 'ensembles' so the
    #: 'sequence' key can stay empty - see to_dict().
    ensembles: Dict[str, 'PulseBlockEnsemble'] = field(default_factory=dict)
    #: Every PulseBlock referenced by `ensembles` (or, for a bare-ensemble `loaded_asset`,
    #: referenced directly by it), keyed by name - independent copies, same provenance as
    #: `ensembles`.
    blocks: Dict[str, 'PulseBlock'] = field(default_factory=dict)

    @property
    def loaded_sequence(self) -> Optional['PulseSequence']:
        """`loaded_asset` when a PulseSequence is loaded, else None.

        A view over the single `loaded_asset` slot rather than a field of its own, so this and
        `loaded_ensemble` can never disagree with each other or with `loaded_asset`, and there is
        no "at most one of them is set" invariant for anyone to break.

        The tri-state getattr mirrors _resolve_asset_closure() in sequence_generator_logic.py:
        None means "not a pulse asset at all", so an unrelated object reads as None from both
        properties instead of raising AttributeError.
        """
        return self.loaded_asset if getattr(self.loaded_asset, 'is_sequence', None) is True else None

    @property
    def loaded_ensemble(self) -> Optional['PulseBlockEnsemble']:
        """`loaded_asset` when a bare PulseBlockEnsemble is loaded, else None - see
        `loaded_sequence` for why both are properties over one slot."""
        return self.loaded_asset if getattr(self.loaded_asset, 'is_sequence', None) is False else None

    @property
    def elements(self) -> List['PulseBlockElement']:
        """Every PulseBlockElement across every PulseBlock in `blocks`, flattened. A convenience
        view for callers wanting a flat list; not a stored field and not part of to_dict(), since
        `blocks` is the single source of truth and already contains every element."""
        return [element for block in self.blocks.values() for element in block.element_list]

    def to_dict(self):
        """Always writes exactly three keys - 'sequence', 'ensembles', 'blocks' - whichever kind of
        asset is loaded, so evaluation code reads one fixed shape and never has to test which keys
        are present before it can start.

        The kind is not encoded anywhere; it is *reconstructed* from the structure. A loaded
        PulseSequence goes in 'sequence'. A loaded bare PulseBlockEnsemble goes into 'ensembles'
        instead and 'sequence' stays empty, so:

            if not data['sequence']:   # -> a PulseBlockEnsemble was loaded (or nothing was)

        Test the whole 'sequence' value, never its 'ensemble_list': PulseSequence(name='x') has an
        empty ensemble_list and is still a sequence, whereas a real sequence's representation always
        carries at least its 'name' and so is never falsy.

        This replaces an older layout that named the key after the kind ('loaded_sequence' /
        'loaded_ensemble') and tagged the value with its class name. from_dict() still reads that
        one, and the pre-split single-'sequence'-plus-'type'-tag one before it.

        The flattened `elements` view (see the property above) is deliberately NOT exported: every
        element already appears inside its block's 'element_list', so a second copy would only
        duplicate them. Use the `elements` property on a live instance if you want them flat.
        """
        sequence = {}
        ensembles = {name: ens.get_dict_representation() for name, ens in self.ensembles.items()}
        if self.loaded_sequence is not None:
            sequence = self.loaded_sequence.get_dict_representation()
        elif self.loaded_ensemble is not None:
            # A bare ensemble resolves nothing one level up, so `ensembles` is empty on the
            # instance and filing the asset here costs nothing - and it is what lets 'sequence'
            # stay empty as the kind marker.
            ensembles[self.loaded_ensemble.name] = self.loaded_ensemble.get_dict_representation()
        return {
            'sequence': sequence,
            'ensembles': ensembles,
            'blocks': {name: blk.get_dict_representation() for name, blk in self.blocks.items()},
        }

    def to_metadata_dict(self, omit_generation_method_parameters=False):
        """Trimmed variant of to_dict() for saved-measurement metadata: drops fields from each
        sequence/ensemble's 'sampling_information' that are either pure duplication
        ('ensemble_info' duplicates this same PulseObjects' `ensembles`; 'pulse_generator_settings'
        is a single global snapshot shown once elsewhere already) or large per-sample arrays
        (laser_rising_bins/falling_bins, elements_length_bins, and everything in
        SamplingInformation._legacy_data) that would otherwise dominate the header with numbers
        rather than sequence structure. Structural fields (name, block_list/ensemble_list,
        generation_method_parameters, element definitions) are unaffected. One-way/display-only -
        no from_metadata_dict().

        Parameters
        ----------
        omit_generation_method_parameters : bool
            Optional, also drop 'generation_method_parameters' from the loaded asset's own entry,
            for a header that already writes it out at its top level (only the signal file does -
            see _get_signal_metadata()). Deliberately applies to the loaded asset alone: every
            *other* ensemble under 'ensembles' has its own, which is not duplicated anywhere. Note
            a loaded bare ensemble now lives under 'ensembles' too (see to_dict()), so the flag has
            to follow it there rather than keying off a separate top-level entry.
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
        # Which entry is the loaded asset: 'sequence' when non-empty, else the bare ensemble
        # to_dict() filed under its own name. `None` when nothing is loaded.
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
        # Three layouts are readable here, newest first:
        #   1. current  - 'sequence' (no 'type' tag); a bare ensemble sits in 'ensembles'
        #   2. split    - 'loaded_sequence' / 'loaded_ensemble', named after the kind
        #   3. pre-split- one 'sequence' key holding either kind, told apart by a 'type' tag
        # (1) and (3) share the key name, so the 'type' tag is what separates them: the current
        # format never writes one.
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
            # Current format, ensemble case: to_dict() filed the loaded bare ensemble as the sole
            # entry in 'ensembles'. Pop it back out, because on the instance `ensembles` holds only
            # what the asset resolves one level up - empty for a bare ensemble.
            if len(ensembles) == 1:
                _, loaded_asset = ensembles.popitem()
            else:
                # Cannot come from to_dict(); with an empty 'sequence' and several candidates the
                # loaded asset is genuinely ambiguous, so refuse to guess rather than pick one.
                _logger.warning(
                    'Cannot tell which of the %d ensembles was the loaded asset: no "sequence" '
                    'entry and more than one candidate. Leaving loaded_asset unset.',
                    len(ensembles),
                )
        return cls(loaded_asset=loaded_asset, ensembles=ensembles, blocks=blocks)

    def __setstate__(self, state):
        """Unpickles a '.pulsedmeasurement' snapshot written before `sequence` was renamed to
        `loaded_asset`. Pickle restores instance state directly, bypassing from_dict()'s legacy
        branch, so such a file would otherwise arrive with no `loaded_asset` at all."""
        if 'sequence' in state and 'loaded_asset' not in state:
            state = dict(state)
            state['loaded_asset'] = state.pop('sequence')
        self.__dict__.update(state)


@dataclass
class PulsedMeasurement:
    """Top-level container for everything that makes up a single pulsed measurement: its
    settings, its acquired/evaluated data, and the pulse objects (loaded asset/ensembles/blocks)
    that produced it. Built by PulsedMeasurementLogic.get_pulsed_measurement() / PulsedMasterLogic.
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
    #: loaded" is represented by objects.loaded_asset is None.
    objects: PulseObjects = field(default_factory=PulseObjects)

    def to_dict(self):
        """Serializes `settings`, `data`, and `objects` (loaded asset/ensembles/blocks)."""
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
