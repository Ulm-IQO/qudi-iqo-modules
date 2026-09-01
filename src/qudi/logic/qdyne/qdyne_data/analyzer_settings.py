# -*- coding: utf-8 -*-
"""Settings containers for the qdyne time trace analyzers.

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

from qudi.logic.qdyne.qdyne_data.settings_base import QdyneSettingsBase

__all__ = ['AnalyzerSettings', 'FourierAnalyzerSettings']


@dataclass(frozen=True)
class AnalyzerSettings(QdyneSettingsBase):
    """Fields every time trace analyzer needs.

    sequence_length defaults to 1e-9 s to match StateEstimatorSettings. It used to default to a bare
    `1`, i.e. one second - nine orders of magnitude away from the estimator's default for the same
    physical quantity, which put the frequency axis in the wrong place until a measurement happened
    to overwrite it.
    """

    sequence_length: float = 1e-9

    def __post_init__(self):
        super().__post_init__()
        if self.sequence_length <= 0:
            # Feeds np.fft.rfftfreq as the sample spacing, so a zero or negative value puts the
            # whole frequency axis somewhere meaningless rather than failing outright.
            raise ValueError(f'sequence_length must be > 0, got {self.sequence_length}.')


@dataclass(frozen=True)
class FourierAnalyzerSettings(AnalyzerSettings):
    """Settings for the Fourier analyzer."""

    #: Spectrum types FourierAnalyzer implements, so the two cannot drift apart. Declared before the
    #: fields so `spectrum_type` can advertise it as `choices` metadata.
    SPECTRUM_TYPES = ('amp', 'power')

    name: str = 'default'
    padding_parameter: int = 1
    #: choices= makes the settings widget offer a drop-down - see TimeTagStateEstimatorSettings.
    spectrum_type: str = field(default='amp', metadata={'choices': SPECTRUM_TYPES})

    def __post_init__(self):
        super().__post_init__()
        if self.spectrum_type not in self.SPECTRUM_TYPES:
            # Caught here rather than deep inside get_freq_domain_signal(), which used to print to
            # stdout and then fall through to an UnboundLocalError.
            raise ValueError(
                f'spectrum_type must be one of {self.SPECTRUM_TYPES}, got {self.spectrum_type!r}.'
            )
