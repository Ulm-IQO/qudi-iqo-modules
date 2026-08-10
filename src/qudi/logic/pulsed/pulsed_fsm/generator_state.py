# -*- coding: utf-8 -*-

"""
The state machine describing what SequenceGeneratorLogic is currently doing.

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

__all__ = ['GeneratorState', 'GeneratorStateMachine']


class GeneratorState(Enum):
    """What SequenceGeneratorLogic is currently doing.

    Replaces `module_state()` x `__sequence_generation_in_progress` x
    `SequenceSamplingState.in_progress`. The middle one existed solely to answer "am I inside an
    outer sequence-sampling run?", which SAMPLING_SEQUENCE now answers directly.
    """

    IDLE = 'idle'
    GENERATING = 'generating'
    SAMPLING_ENSEMBLE = 'sampling_ensemble'
    SAMPLING_SEQUENCE = 'sampling_sequence'
    BENCHMARKING = 'benchmarking'


#: Every legal move. Each activity has its own paired start_/end_ event rather than one shared
#: 'finish': sampling an ensemble happens both standalone AND nested inside sampling a sequence, so
#: a shared end event fired by the inner run would tear down the outer run's state.
#:
#: The two SAMPLING_SEQUENCE self-transitions are the point of this table. They let
#: sample_pulse_block_ensemble() call start/end unconditionally: standalone it moves
#: IDLE -> SAMPLING_ENSEMBLE -> IDLE, nested it lands on the self-transitions and nothing changes,
#: so the outer sequence run keeps ownership. That is what `__sequence_generation_in_progress` and
#: its eleven guarded module_state.unlock() call sites were doing by hand.
_TRANSITIONS = {
    # Predefined generate methods.
    (GeneratorState.IDLE, 'start_generating'): GeneratorState.GENERATING,
    (GeneratorState.GENERATING, 'end_generating'): GeneratorState.IDLE,

    # Sampling one PulseBlockEnsemble on its own.
    (GeneratorState.IDLE, 'start_ensemble_sampling'): GeneratorState.SAMPLING_ENSEMBLE,
    (GeneratorState.SAMPLING_ENSEMBLE, 'end_ensemble_sampling'): GeneratorState.IDLE,

    # Sampling a whole PulseSequence.
    (GeneratorState.IDLE, 'start_sequence_sampling'): GeneratorState.SAMPLING_SEQUENCE,
    (GeneratorState.SAMPLING_SEQUENCE, 'end_sequence_sampling'): GeneratorState.IDLE,

    # ...and the ensembles sampled inside it: legal, but the state does not move.
    (GeneratorState.SAMPLING_SEQUENCE, 'start_ensemble_sampling'): GeneratorState.SAMPLING_SEQUENCE,
    (GeneratorState.SAMPLING_SEQUENCE, 'end_ensemble_sampling'): GeneratorState.SAMPLING_SEQUENCE,

    # Pulse generator upload-speed benchmark.
    (GeneratorState.IDLE, 'start_benchmark'): GeneratorState.BENCHMARKING,
    (GeneratorState.BENCHMARKING, 'end_benchmark'): GeneratorState.IDLE,
}


class GeneratorStateMachine(StateMachine):
    """Asset generation, sampling and benchmarking.

    Every activity is bracketed by a matching pair of event methods. Call sites should use
    try/finally so the end event fires on every exit path - that single guarantee replaces the
    three-different-cleanup-sequences-per-method arrangement in sequence_generator_logic.py, where
    two of the three exits from sample_pulse_sequence() forget to reset SequenceSamplingState.

    Every event method below raises StateMachineError when the current state does not allow it, and
    leaves the state untouched.

    Parameters
    ----------
    parent : QtCore.QObject, optional
        Qt parent. Pass the owning SequenceGeneratorLogic so the machine dies with it.
    """

    def __init__(self, parent=None):
        super().__init__(GeneratorState.IDLE, _TRANSITIONS, parent)

    def start_generating(self):
        """Begin running a predefined generate method. Only legal from IDLE."""
        return self.trigger('start_generating')

    def end_generating(self):
        """Finish running a predefined generate method."""
        return self.trigger('end_generating')

    def start_ensemble_sampling(self):
        """Begin sampling one PulseBlockEnsemble.

        Legal from IDLE (moves to SAMPLING_ENSEMBLE) and from SAMPLING_SEQUENCE (a nested run - the
        state does not change and no signal is emitted).
        """
        return self.trigger('start_ensemble_sampling')

    def end_ensemble_sampling(self):
        """Finish sampling one PulseBlockEnsemble. A no-op when nested inside a sequence run."""
        return self.trigger('end_ensemble_sampling')

    def start_sequence_sampling(self):
        """Begin sampling a PulseSequence. Only legal from IDLE."""
        return self.trigger('start_sequence_sampling')

    def end_sequence_sampling(self):
        """Finish sampling a PulseSequence."""
        return self.trigger('end_sequence_sampling')

    def start_benchmark(self):
        """Begin the pulse generator upload-speed benchmark. Only legal from IDLE."""
        return self.trigger('start_benchmark')

    def end_benchmark(self):
        """Finish the pulse generator benchmark."""
        return self.trigger('end_benchmark')
