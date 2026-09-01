# -*- coding: utf-8 -*-
"""Settings containers for the qdyne state estimators.

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

from qudi.core.logger import get_logger
from qudi.logic.qdyne.qdyne_data.settings_base import QdyneSettingsBase

__all__ = ['StateEstimatorSettings', 'TimeTagStateEstimatorSettings']

_logger = get_logger(__name__)

#: Count windows already reported as empty. __post_init__ runs on every construction, and these are
#: frozen dataclasses - so every settings edit builds a new object and would re-warn. One bad
#: configuration used to produce a dozen identical log lines in a single session.
_warned_empty_windows = set()


@dataclass(frozen=True)
class StateEstimatorSettings(QdyneSettingsBase):
    """Fields every state estimator needs, whatever its method."""

    sequence_length: float = 1e-9
    record_length: float = 1e-9
    bin_width: float = 1e-9

    def __post_init__(self):
        """Reject values that cannot describe a measurement.

        Frozen dataclasses can validate freely - they just cannot mutate - and __post_init__ runs on
        every construction path including from_dict(), so this catches a bad status file as well as
        a bad call. Without it a bin_width of 0 surfaces much later as a ZeroDivisionError inside
        sig_start_int, which says nothing about where the wrong value came from.
        """
        if self.bin_width <= 0:
            raise ValueError(f'bin_width must be > 0, got {self.bin_width}.')
        if self.record_length <= 0:
            raise ValueError(f'record_length must be > 0, got {self.record_length}.')
        if self.sequence_length <= 0:
            raise ValueError(f'sequence_length must be > 0, got {self.sequence_length}.')


@dataclass(frozen=True)
class TimeTagStateEstimatorSettings(StateEstimatorSettings):
    """Settings for the time-tag estimator.

    `weight` carries one factor per time tag for the WeightedAverage count mode. It is marked
    exclude=True because the settings widget has no editor for a list - but it IS persisted, unlike
    before: `to_dict()` covers every field and only `to_display_dict()` honours the marker. Without
    that, a weight set from a script vanished on the next save/load cycle, which is most of why
    WeightedAverage was unusable.
    """

    #: Count modes TimeTagStateEstimator implements. Kept here so the estimator and its settings
    #: cannot disagree about what is selectable. Declared before the fields so `count_mode` can
    #: advertise it as `choices` metadata.
    COUNT_MODES = ('Average', 'WeightedAverage')

    name: str = 'default'
    #: choices= tells the settings widget to offer a drop-down instead of a free-text box. Without
    #: it, count_mode rendered as a QLineEdit, and a typo raised ValueError out of __post_init__ -
    #: through the mediator and into the Qt event loop, since nothing on that path guards it.
    count_mode: str = field(default='Average', metadata={'choices': COUNT_MODES})
    sig_start: float = 0.0
    sig_end: float = 0.0
    weight: list = field(default_factory=list, metadata={'exclude': True})

    def __post_init__(self):
        super().__post_init__()
        if self.count_mode not in self.COUNT_MODES:
            raise ValueError(
                f'count_mode must be one of {self.COUNT_MODES}, got {self.count_mode!r}.'
            )
        if self.sig_start < 0:
            raise ValueError(f'sig_start must be >= 0, got {self.sig_start}.')


        # The window checks WARN rather than raise, deliberately. The settings widget sends one
        # field at a time, so editing sig_start before sig_end legitimately passes through a state
        # where the window looks inverted - raising there would make the GUI unusable. These are
        # "your measurement will count nothing" problems, not "this object cannot exist" problems,
        # and the distinction is what decides between a warning and an exception here.
        # Compared against effective_sig_end, so the "never set" default (0.0, meaning the whole
        # record) is not mistaken for an empty window. `<=`, not `<`: an equal pair counts nothing
        # just as surely as an inverted one, and with a strict `<` neither this branch nor the
        # record_length branch below fired for it. Reported once per distinct window - see
        # _warned_empty_windows.
        if self.effective_sig_end <= self.sig_start:
            window = (self.sig_start, self.effective_sig_end)
            if window not in _warned_empty_windows:
                _warned_empty_windows.add(window)
                _logger.warning(
                    f'The count window [{self.sig_start}, {self.effective_sig_end}) s is empty - '
                    f'every readout will count zero photons, the time trace will be flat and its '
                    f'spectrum all zeros. Set sig_end greater than sig_start.'
                )
        elif self.effective_sig_end > self.record_length:
            _logger.warning(
                f'sig_end ({self.effective_sig_end} s) is beyond record_length '
                f'({self.record_length} s) - counts past the end of the record cannot exist.'
            )

    @property
    def effective_sig_end(self) -> float:
        """`sig_end`, or the whole record when it has never been set.

        `sig_end` defaults to 0.0, which with the default `sig_start` of 0.0 makes an EMPTY count
        window: every readout counts zero photons, so the time trace comes out flat, its spectrum
        is all zeros, and the fit then dies inside lmfit with "Parameter 'offset' has min == max".
        Three baffling symptoms from one silent default, and nothing in the module derived a window
        from the loaded sequence - so clearing the status file left Qdyne unable to produce a
        non-flat trace until someone dragged the sig_end line by hand.

        Resolved here rather than written into the field by __post_init__, which was the obvious fix
        and is wrong: the container is constructed long before the counter's real record_length
        reaches it, so the repair captured the placeholder default (1e-9) and the window stayed
        empty once the real record length (1e-6) arrived. Leaving 0.0 in the field to mean "the
        whole record" keeps it correct however late the real value shows up.
        """
        return self.sig_end if self.sig_end > 0.0 else self.record_length

    @property
    def sig_start_int(self) -> int:
        return int(self.sig_start / self.bin_width)

    @property
    def sig_end_int(self) -> int:
        return int(self.effective_sig_end / self.bin_width)

    @property
    def max_bins(self) -> int:
        return int(self.record_length / self.bin_width)
