# -*- coding: utf-8 -*-

"""
The state machine for the one multi-step workflow PulsedMasterLogic genuinely orchestrates.

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
from enum import Enum

from qudi.logic.pulsed.pulsed_fsm.state_machines import StateMachine

__all__ = ['SampLoadState', 'SampLoadStateMachine']


class SampLoadState(Enum):
    """Where PulsedMasterLogic is in the generate -> sample -> load chain.

    Master is a relay: seven of its ten status flags are mirrors of SequenceGeneratorLogic and
    PulsedMeasurementLogic, and those stay mirrors. This machine covers only the workflow master
    actually drives itself - the "GenSampLo" button - which today is half-encoded in the
    `sampload_busy` bool plus branching spread across four slots.

    Not every run visits every state: a plain "Generate" stops after GENERATING, a plain "Sample"
    starts at SAMPLING, and a plain "Load" starts at LOADING.
    """

    IDLE = 'idle'
    GENERATING = 'generating'
    SAMPLING = 'sampling'
    LOADING = 'loading'


#: Every legal move.
#:
#: Note there are two ways out of GENERATING and two out of SAMPLING, and they are separate EVENTS
#: rather than one event with two outcomes. A transition table maps one (state, event) pair to
#: exactly one destination, so a runtime condition - here `sample_and_load` - cannot be expressed as
#: a branch inside a row. It is expressed by the caller choosing which event to fire, which also
#: makes the two paths visible at the call site instead of buried in an `if`.
_TRANSITIONS = {
    # Entering the chain. Which one depends on which button was pressed.
    (SampLoadState.IDLE, 'begin_generate'): SampLoadState.GENERATING,
    (SampLoadState.IDLE, 'begin_sample'): SampLoadState.SAMPLING,
    (SampLoadState.IDLE, 'begin_load'): SampLoadState.LOADING,

    # Carrying on to the next step - only fired when the caller asked for sample-and-load.
    (SampLoadState.GENERATING, 'continue_to_sample'): SampLoadState.SAMPLING,
    (SampLoadState.SAMPLING, 'continue_to_load'): SampLoadState.LOADING,

    # Normal completion of whatever step was last.
    (SampLoadState.GENERATING, 'finish'): SampLoadState.IDLE,
    (SampLoadState.SAMPLING, 'finish'): SampLoadState.IDLE,
    (SampLoadState.LOADING, 'finish'): SampLoadState.IDLE,

    # The failure branches - every one of master's `if asset is None:` paths.
    (SampLoadState.GENERATING, 'abort'): SampLoadState.IDLE,
    (SampLoadState.SAMPLING, 'abort'): SampLoadState.IDLE,
    (SampLoadState.LOADING, 'abort'): SampLoadState.IDLE,
}


class SampLoadStateMachine(StateMachine):
    """The generate -> sample -> load chain.

    Unlike GeneratorStateMachine there is no nesting here, so a single shared `finish` event is safe
    - no step can be running inside another one.

    `finish` and `abort` both land on IDLE and differ only in name. They are kept apart so the call
    sites read as what they are: master's slots already branch on `if asset_name is None:`, and
    naming the failure path makes that branch self-describing rather than a second bare `finish`.

    Parameters
    ----------
    parent : QtCore.QObject, optional
        Qt parent. Pass the owning PulsedMasterLogic so the machine dies with it.
    """

    #: `abort` already is the move recover() needs, out of every state and into IDLE, so this
    #: machine points RECOVERY_EVENT at it instead of carrying a second identical set of rows under
    #: another name. The two callers differ only in what they know: a slot that was told the step
    #: produced nothing calls abort(), while reset_toolchain() - which knows only that the user has
    #: given up on whatever the chain was doing - calls recover().
    RECOVERY_EVENT = 'abort'

    def __init__(self, parent=None):
        super().__init__(SampLoadState.IDLE, _TRANSITIONS, parent)

    def begin_generate(self):
        """A predefined generate method was requested. Only legal from IDLE."""
        return self.trigger('begin_generate')

    def begin_sample(self):
        """Sampling was requested directly, without a preceding generate. Only legal from IDLE."""
        return self.trigger('begin_sample')

    def begin_load(self):
        """Loading was requested directly, without a preceding sample. Only legal from IDLE."""
        return self.trigger('begin_load')

    def continue_to_sample(self):
        """Generation finished and sample-and-load was requested, so carry on into sampling."""
        return self.trigger('continue_to_sample')

    def continue_to_load(self):
        """Sampling finished and sample-and-load was requested, so carry on into loading."""
        return self.trigger('continue_to_load')

    def finish(self):
        """The chain completed normally at whatever step it had reached."""
        return self.trigger('finish')

    def abort(self):
        """The chain failed - the step produced nothing to carry forward."""
        return self.trigger('abort')
