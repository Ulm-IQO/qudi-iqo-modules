# -*- coding: utf-8 -*-
"""Qt adapter for the Qt-free settings mediator.

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
from PySide6.QtCore import QObject, Signal, Slot

__all__ = ['MediatorBridge']


class MediatorBridge(QObject):
    """Turns a `SettingsMediator`'s plain callbacks into Qt signals.

    The mediator lives in the logic layer and has no Qt at all, which keeps it unit-testable without
    a QApplication and stops Qt leaking into the settings containers. The widgets, however, are Qt
    all the way down and want signals - so exactly one adapter sits between them, here in the GUI
    layer rather than in the logic.

    Everything that is not a signal is forwarded to the wrapped mediator by __getattr__, so the
    widgets keep using `current_data`, `method_list`, `update_method`, `add_mode` and friends
    unchanged. Only the construction site had to learn about this class.
    """

    data_updated_sig = Signal(dict)
    mode_updated_sig = Signal(str)
    method_updated_sig = Signal(str)
    data_renewed_sig = Signal(object)

    def __init__(self, mediator, parent=None):
        super().__init__(parent)
        self._mediator = mediator
        # Kept so teardown() can hand back exactly the objects that were registered. Bound methods
        # are created fresh on every attribute access, so `self.data_updated_sig.emit` here and
        # `self.data_updated_sig.emit` later are different objects.
        self._subscriptions = {
            'on_data': self.data_updated_sig.emit,
            'on_mode': self.mode_updated_sig.emit,
            'on_method': self.method_updated_sig.emit,
            'on_renewed': self.data_renewed_sig.emit,
        }
        mediator.subscribe(**self._subscriptions)

    def teardown(self):
        """Unsubscribe from the mediator. Call this when the owning widget goes away.

        The mediator lives in the *logic*, which outlives the GUI - so a bridge that is destroyed
        without unsubscribing leaves four bound signal emits registered against a QObject whose C++
        half is gone. Every later settings change then raises
        `RuntimeError: Signal source has been deleted` out of SettingsMediator._notify(), once per
        orphaned bridge, for the rest of the session. Restarting the GUI added another one each
        time.

        Safe to call more than once.
        """
        for callback in self._subscriptions.values():
            try:
                self._mediator.unsubscribe(callback)
            except Exception:
                pass    # the mediator is already gone, or never had this callback
        self._subscriptions = {}

    @property
    def mediator(self):
        """The wrapped, Qt-free mediator."""
        return self._mediator

    def __getattr__(self, name):
        # Only reached for names this QObject does not define itself, so the signals above always
        # win. Guard on the private attribute to avoid recursing during __init__.
        if name == '_mediator':
            raise AttributeError(name)
        return getattr(self._mediator, name)

    # Explicit slots for the two the widgets connect signals TO - a Qt signal connection needs a
    # real bound method, not something conjured by __getattr__.

    @Slot(dict)
    def sync_values(self, new_values):
        self._mediator.sync_values(new_values)

    @Slot(dict)
    def set_values(self, new_values):
        self._mediator.set_values(new_values)

    @Slot(str)
    def update_mode(self, new_mode):
        self._mediator.update_mode(new_mode)

    @Slot(str)
    def update_method(self, new_method):
        self._mediator.update_method(new_method)
