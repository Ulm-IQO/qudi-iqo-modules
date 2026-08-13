# -*- coding: utf-8 -*-

"""
This file contains the Qudi base state machines for organizing the pulsed toolchain.

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
import logging

from PySide6 import QtCore
from qudi.util.mutex import RecursiveMutex

__all__ = ['StateMachine', 'StateMachineError']

# These machines are plain QObjects owned by a logic module rather than modules themselves, so they
# have no qudi `self.log`. Only recover() writes here, and only when the table is malformed.
_log = logging.getLogger(__name__)


class StateMachineError(RuntimeError):
    """Raised when an event is triggered that the current state does not allow.

    Parameters
    ----------
    state : object
        The state the machine was in when the event was rejected.
    event : str
        The event that was not allowed.

    Attributes
    ----------
    state : object
        As above, kept so a handler can branch on it without parsing the message.
    event : str
        As above.
    """

    def __init__(self, state, event):
        # getattr rather than state.name: Enum members have .name, the plain values used in tests
        # and by any non-Enum caller do not.
        state_name = getattr(state, 'name', state)
        super().__init__(f'Event {event!r} is not allowed in state {state_name!r}')
        self.state = state
        self.event = event


class StateMachine(QtCore.QObject):
    """A finite state machine defined by an explicit table of allowed transitions.

    The invariant this class exists to hold: **`trigger()` is the only way `state` ever changes.**
    `state` has no setter and the transition table is copied at construction, so a state the table
    does not allow simply cannot be reached.

    Parameters
    ----------
    initial : object
        State the machine starts in. Must be the source of at least one transition.
    transitions : mapping
        The rule table, `{(source_state, event_name): destination_state}`. Copied on the way in, so
        the caller cannot rewrite the rules afterwards.
    parent : QtCore.QObject, optional
        Qt parent. Pass the owning logic module so the machine is destroyed along with it.

    Raises
    ------
    ValueError
        If `initial` is the source of no transition - almost always a typo in the table, and
        otherwise a machine that could never move.
    """

    #: Emitted after a transition that actually changed the state. A legal self-transition does not
    #: emit, so receivers are never woken for a no-op.
    sigStateChanged = QtCore.Signal(object, object)  # (old_state, new_state)

    #: Name of the event `recover()` fires. Subclasses whose table already names the same move
    #: something else override this rather than adding a duplicate set of rows - see
    #: SampLoadStateMachine, where 'abort' is that move.
    #:
    #: Recovery deliberately goes through the table like everything else. A `reset()` that assigned
    #: `_state` directly would be simpler and would break the one property the whole design rests
    #: on: that every state the machine has ever been in was reachable by a rule someone wrote down.
    RECOVERY_EVENT = 'fail'

    def __init__(self, initial, transitions, parent=None):
        super().__init__(parent)
        # Copied, not stored by reference: the table is this machine's contract, and a caller
        # holding the original must not be able to rewrite the rules after construction.
        self._transitions = dict(transitions)
        sources = {state for state, _ in self._transitions}
        if initial not in sources:
            raise ValueError(
                f'Initial state {initial!r} is the source of no transition, so the machine could '
                f'never leave it. Check the transition table for a typo.'
            )
        self._initial = initial
        self._state = initial
        # Recursive: a slot connected to sigStateChanged may call back into trigger(), and the
        # owning logic module may already hold its own lock around the call.
        self._lock = RecursiveMutex()

    @property
    def state(self):
        """The current state. Read-only - `trigger()` is the only way it changes.

        Returns
        -------
        object
            The state the machine is currently in.
        """
        return self._state

    @property
    def initial_state(self):
        """The state the machine was constructed in, and the one `recover()` returns it to.

        Returns
        -------
        object
            The initial state.
        """
        return self._initial

    def can(self, event) -> bool:
        """Whether `event` is allowed in the current state.

        The non-raising counterpart to `trigger()`, for asking without acting - GUI button
        enablement, for instance. Note that `can()` followed by `trigger()` is not atomic; where
        that matters, call `trigger()` and catch instead.

        Parameters
        ----------
        event : str
            Event name to test.

        Returns
        -------
        bool
            True if the event would be accepted right now.
        """
        return (self._state, event) in self._transitions

    def trigger(self, event):
        """Perform `event`, moving to whichever state the table maps it to.

        Parameters
        ----------
        event : str
            Event name.

        Returns
        -------
        object
            The new state - unchanged from the previous one for a legal self-transition.

        Raises
        ------
        StateMachineError
            If `event` is not allowed in the current state. The state is left untouched.
        """
        with self._lock:
            try:
                new_state = self._transitions[(self._state, event)]
            except KeyError:
                # from None: the KeyError is an implementation detail of the dict lookup and would
                # only clutter the traceback whoever hits this actually reads.
                raise StateMachineError(self._state, event) from None
            old_state = self._state
            self._state = new_state

        # Emitted outside the lock: a directly connected slot runs synchronously inside emit(), and
        # holding a mutex across arbitrary receiver code is how lock-ordering deadlocks appear.
        if old_state is not new_state:
            self.sigStateChanged.emit(old_state, new_state)
        return new_state

    def recover(self):
        """Return the machine to its initial state after a fault.

        The counterpart to `trigger()` for the paths where something has already gone wrong: an
        operation raised part-way through, or a step the machine was waiting on never reported back.
        Where `trigger()` is strict so that a mistake is loud, `recover()` is deliberately the
        opposite - idempotent, and it never raises. It is called from `except`/`finally` blocks and
        from operator-driven resets, where an exception of its own would mask the failure being
        recovered from and strand the machine anyway.

        The move itself is still an ordinary table lookup of `RECOVERY_EVENT`, so recovery is as
        auditable as every other transition, and `sigStateChanged` fires normally - which is what
        releases `module_state` and lets the GUI catch up.

        Returns
        -------
        object
            The state afterwards - the initial state unless the table had no way back, which is a
            defect in the table rather than something a caller can act on.
        """
        with self._lock:
            if self._state is self._initial:
                # Already home. Not an error: fault paths overlap, and recovering twice is normal.
                return self._state
            recoverable = (self._state, self.RECOVERY_EVENT) in self._transitions
            stuck_in = self._state

        if recoverable:
            try:
                # Outside the lock, so trigger() emits under the same conditions as any other call.
                # Which also means the state can move in between, hence the catch: two threads
                # recovering at once, or a completion signal landing just as a reset goes through,
                # must not turn "never raises" into a StateMachineError thrown from a `finally`.
                return self.trigger(self.RECOVERY_EVENT)
            except StateMachineError:
                return self._state

        # Unreachable with a well-formed table - test_every_state_can_recover pins that every state
        # has a recovery row. Reported rather than raised, per the contract above.
        _log.error(
            'No %r transition out of %r, so %s cannot return to %r. The machine is stuck and the '
            'transition table needs a recovery row for this state.',
            self.RECOVERY_EVENT, getattr(stuck_in, 'name', stuck_in), type(self).__name__,
            getattr(self._initial, 'name', self._initial),
        )
        return stuck_in

