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
from PySide6 import QtCore
from qudi.util.mutex import RecursiveMutex

__all__ = ['StateMachine', 'StateMachineError']


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

