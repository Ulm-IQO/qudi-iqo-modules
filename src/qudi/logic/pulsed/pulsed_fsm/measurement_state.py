# -*- coding: utf-8 -*-

"""
The state machine describing what PulsedMeasurementLogic is currently doing.

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

__all__ = ['MeasurementState', 'MeasurementStateMachine']


class MeasurementState(Enum):
    """What PulsedMeasurementLogic is currently doing.

    Replaces the pair `module_state()` x `ExecutionState.is_paused`, which spread these three
    states across two variables and left "running" and "paused" indistinguishable to anything
    holding only the first of them.
    """

    IDLE = 'idle'
    RUNNING = 'running'
    PAUSED = 'paused'


#: Every legal move, and nothing else. A pair absent from this table is an event that simply cannot
#: happen from that state - the omissions carry as much meaning as the entries, so read them
#: together: there is deliberately no (PAUSED, 'pause') and no (RUNNING, 'resume').
_TRANSITIONS = {
    (MeasurementState.IDLE, 'start'): MeasurementState.RUNNING,
    (MeasurementState.RUNNING, 'pause'): MeasurementState.PAUSED,
    (MeasurementState.PAUSED, 'resume'): MeasurementState.RUNNING,
    (MeasurementState.RUNNING, 'stop'): MeasurementState.IDLE,
    (MeasurementState.PAUSED, 'stop'): MeasurementState.IDLE,

    # The recovery rows, fired by StateMachine.recover(). Kept apart from 'stop' although they land
    # in the same place: 'stop' is the user ending a measurement and the hardware is switched off in
    # order, 'fail' is a measurement that came apart - most often a device raising between the
    # transition and the calls that were meant to follow it. Naming them differently is what lets
    # the log say which of the two happened.
    (MeasurementState.RUNNING, 'fail'): MeasurementState.IDLE,
    (MeasurementState.PAUSED, 'fail'): MeasurementState.IDLE,
}


class MeasurementStateMachine(StateMachine):
    """The measurement run: idle -> running <-> paused -> idle.

    The four event methods exist so call sites read `fsm.pause()` rather than
    `fsm.trigger('pause')`. They are one line each, but they are the reason a mistyped event is an
    immediate AttributeError your editor flags, instead of a string that survives until the lookup
    fails at runtime. The event names appear only in this file.

    Parameters
    ----------
    parent : QtCore.QObject, optional
        Qt parent. Pass the owning PulsedMeasurementLogic so the machine dies with it.
    """

    def __init__(self, parent=None):
        super().__init__(MeasurementState.IDLE, _TRANSITIONS, parent)

    def start(self):
        """Begin a measurement. Only legal from IDLE.

        Returns
        -------
        MeasurementState
            RUNNING.

        Raises
        ------
        StateMachineError
            If a measurement is already running or paused.
        """
        return self.trigger('start')

    def pause(self):
        """Pause a running measurement. Only legal from RUNNING.

        Returns
        -------
        MeasurementState
            PAUSED.

        Raises
        ------
        StateMachineError
            If nothing is running, or it is already paused - which is what stops the hardware-off
            sequence being run a second time.
        """
        return self.trigger('pause')

    def resume(self):
        """Continue a paused measurement. Only legal from PAUSED.

        Returns
        -------
        MeasurementState
            RUNNING.

        Raises
        ------
        StateMachineError
            If nothing is paused - which is what stops microwave_on()/pulse_generator_on() being
            re-issued to hardware that is already running.
        """
        return self.trigger('resume')

    def stop(self):
        """End the measurement. Legal from either RUNNING or PAUSED.

        Returns
        -------
        MeasurementState
            IDLE.

        Raises
        ------
        StateMachineError
            If no measurement is in progress.
        """
        return self.trigger('stop')
