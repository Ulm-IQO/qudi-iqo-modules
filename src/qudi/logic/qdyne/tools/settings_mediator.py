# -*- coding: utf-8 -*-
"""Holds qdyne settings, organised by method and mode.

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
from typing import Any, Callable, Dict, List, Optional

from qudi.core.logger import get_logger

__all__ = ['SettingsMediator', 'DEFAULT_MODE']

DEFAULT_MODE = 'default'


class SettingsMediator:
    """One settings store: {method: {mode: frozen settings dataclass}} plus the current selection.

    Replaces the DataclassMediator -> SettingsMediator -> MultiSettingsMediator chain, which was
    three QObject levels deep with each level deleting the previous level's state in __init__ and
    overriding its properties with incompatible ones. Most of the load/save bugs lived in there.

    **No Qt.** Change notification is a plain callback list - see `subscribe()`. The GUI wraps this
    in a small QObject bridge that turns those callbacks into signals, so Qt stays in the GUI layer
    where it belongs and this class can be unit-tested without a QApplication.

    The settings themselves are frozen dataclasses, so every mutating method here *replaces* the
    stored object rather than editing it. Nothing outside can change a settings object that another
    part of the toolchain is holding.
    """

    def __init__(self, settings_classes: Optional[Dict[str, type]] = None):
        """
        Parameters
        ----------
        settings_classes : dict
            {method name: settings dataclass}. Defaults are created for each straight away, so the
            mediator is usable immediately rather than only after a separate create_default() call.
        """
        self._log = get_logger(__name__)
        self._settings_classes: Dict[str, type] = dict(settings_classes or {})
        self._method_dict: Dict[str, Dict[str, Any]] = {}
        self._current_method = ''
        self._current_mode = DEFAULT_MODE

        self._on_data: List[Callable] = []
        self._on_mode: List[Callable] = []
        self._on_method: List[Callable] = []
        self._on_renewed: List[Callable] = []

        if self._settings_classes:
            self.create_defaults()

    # ------------------------------------------------------------------ notification

    def subscribe(self, on_data=None, on_mode=None, on_method=None, on_renewed=None) -> None:
        """Register callbacks. Each is optional and may be registered more than once.

        on_data(dict)      the current settings changed value
        on_mode(str)       a different mode became current
        on_method(str)     a different method became current
        on_renewed(object) the current settings OBJECT was swapped, so widgets must be rebuilt
        """
        for callbacks, callback in (
            (self._on_data, on_data),
            (self._on_mode, on_mode),
            (self._on_method, on_method),
            (self._on_renewed, on_renewed),
        ):
            if callback is not None:
                callbacks.append(callback)

    def unsubscribe(self, callback) -> None:
        """Remove one callback from every list it was registered in.

        Needed because an observer that outlives its subscription keeps this mediator holding a
        strong reference to a bound method - which pins the whole owning object graph in memory and,
        worse, means a later settings change calls into an object that has already been torn down.
        `unsubscribe_all()` is the wrong tool for that: one observer going away must not silently
        cancel everyone else's notifications.

        Removing a callback that was never registered is not an error - teardown paths should not
        have to track what they managed to subscribe.
        """
        for callbacks in (self._on_data, self._on_mode, self._on_method, self._on_renewed):
            while callback in callbacks:
                callbacks.remove(callback)

    def unsubscribe_all(self) -> None:
        for callbacks in (self._on_data, self._on_mode, self._on_method, self._on_renewed):
            callbacks.clear()

    def _notify(self, callbacks: List[Callable], payload) -> None:
        """One misbehaving observer must not stop the others, nor the logic that triggered it."""
        for callback in list(callbacks):
            try:
                callback(payload)
            except RuntimeError as err:
                if 'deleted' in str(err):
                    # A Qt observer whose C++ object has been destroyed - a widget or bridge torn
                    # down without unsubscribing, while this mediator lives on in the logic. It can
                    # never recover, so drop it rather than raise the same traceback on every
                    # settings change for the rest of the session. One warning, then silence.
                    self._drop(callback)
                    self._log.warning(
                        'Dropped a settings observer whose Qt object has been deleted - something '
                        'subscribed without unsubscribing on teardown.'
                    )
                else:
                    self._log.exception('Settings observer raised; continuing with the rest.')
            except Exception:
                self._log.exception('Settings observer raised; continuing with the rest.')

    def _drop(self, callback) -> None:
        """Remove one callback by identity.

        Identity, not equality: comparing a bound method of a deleted QObject can itself raise, and
        `callbacks.remove()` would do exactly that comparison.
        """
        for callbacks in (self._on_data, self._on_mode, self._on_method, self._on_renewed):
            callbacks[:] = [cb for cb in callbacks if cb is not callback]

    # ------------------------------------------------------------------ current selection

    @property
    def settings_classes(self) -> Dict[str, type]:
        return dict(self._settings_classes)

    @property
    def method_dict(self) -> Dict[str, Dict[str, Any]]:
        return self._method_dict

    @property
    def method_list(self) -> List[str]:
        return list(self._method_dict)

    @property
    def current_method(self) -> str:
        return self._current_method

    @property
    def mode_dict(self) -> Dict[str, Any]:
        return self._method_dict.get(self._current_method, {})

    @property
    def mode_list(self) -> List[str]:
        return list(self.mode_dict)

    @property
    def current_mode(self) -> str:
        return self._current_mode

    @property
    def current_data(self):
        """The settings object currently selected, or None if nothing is configured.

        Falls back to the default mode rather than raising when the current mode is missing, so a
        half-restored status file still yields something usable.
        """
        modes = self.mode_dict
        if not modes:
            self._log.error(f"Method '{self._current_method}' has no settings.")
            return None
        if self._current_mode in modes:
            return modes[self._current_mode]
        self._log.error(f"Mode '{self._current_mode}' not found. Falling back to '{DEFAULT_MODE}'.")
        return modes.get(DEFAULT_MODE, next(iter(modes.values())))

    @property
    def default_data(self):
        return self.mode_dict.get(DEFAULT_MODE)

    # ------------------------------------------------------------------ construction / persistence

    def create_defaults(self) -> None:
        """Give every known method a default mode."""
        for method, settings_cls in self._settings_classes.items():
            self._method_dict[method] = {DEFAULT_MODE: settings_cls(name=DEFAULT_MODE)}
        if self._settings_classes and not self._current_method:
            self._current_method = next(iter(self._settings_classes))

    def load_from_dict(self, method_map: Any) -> None:
        """Restore from {method: {mode: {field: value}}}.

        Tolerant on both axes: a method known to the code but absent from `method_map` gets
        defaults, and saved keys that are no longer fields are dropped by the dataclass's own
        from_dict(). Either case used to abort the whole load and leave every method unconfigured.
        """
        method_map = method_map if isinstance(method_map, dict) else {}
        for method, settings_cls in self._settings_classes.items():
            saved_modes = method_map.get(method)
            if not isinstance(saved_modes, dict) or not saved_modes:
                self._method_dict[method] = {DEFAULT_MODE: settings_cls(name=DEFAULT_MODE)}
                self._log.info(f"No saved settings for method '{method}'. Created defaults.")
                continue
            modes = {}
            for mode, values in saved_modes.items():
                try:
                    modes[mode] = settings_cls.from_dict(values)
                except ValueError as err:
                    # from_dict() tolerates an unknown *key*, but the settings classes reject an
                    # impossible *value* (a zero bin width, an inverted signal window). One bad mode
                    # in the status file must not stop the module activating, so fall back to
                    # defaults for that mode and say so.
                    self._log.error(
                        f"Saved settings for '{method}/{mode}' are invalid ({err}). "
                        f'Using defaults for that mode.'
                    )
                    modes[mode] = settings_cls(name=mode)
            modes.setdefault(DEFAULT_MODE, settings_cls(name=DEFAULT_MODE))
            self._method_dict[method] = modes

        unknown = set(method_map) - set(self._settings_classes)
        if unknown:
            self._log.warning(
                f'Ignoring saved settings for unknown method(s) {sorted(unknown)}.'
            )
        if not self._current_method:
            self._current_method = next(iter(self._method_dict), '')

    def dump_as_dict(self) -> dict:
        """The persistence form: {method: {mode: {field: value}}}."""
        return {
            method: {mode: settings.to_dict() for mode, settings in modes.items()}
            for method, modes in self._method_dict.items()
        }

    # ------------------------------------------------------------------ method / mode selection

    def update_method(self, new_method: str) -> None:
        """Select a method. Called by the GUI; does not emit the method signal back at it."""
        if new_method not in self._method_dict:
            self._log.error(f"Method '{new_method}' not implemented. Keeping '{self._current_method}'.")
            return
        self._current_method = new_method
        self._current_mode = DEFAULT_MODE
        self._notify(self._on_mode, DEFAULT_MODE)
        self._notify(self._on_renewed, self.current_data)

    def set_method(self, new_method: str) -> None:
        """Select a method from the logic side, telling the GUI to follow."""
        self.update_method(new_method)
        self._notify(self._on_method, self._current_method)

    def update_mode(self, new_mode: str) -> None:
        if new_mode not in self.mode_dict:
            self._log.error(f"Mode '{new_mode}' not found for method '{self._current_method}'.")
            return
        self._current_mode = new_mode
        self._notify(self._on_data, self._display_dict())

    def set_mode(self, new_mode: str) -> None:
        self.update_mode(new_mode)
        self._notify(self._on_mode, self._current_mode)

    def add_mode(self, new_mode_name: str, force_creation: bool = False, setting=None) -> None:
        """Create a mode, seeded from `setting` when given and from the default mode otherwise.

        `setting` used to be accepted and then ignored, so a caller restoring settings from a saved
        file (QdyneLogic.load_data) had them silently replaced by defaults.
        """
        modes = self.mode_dict
        if not modes:
            self._log.error(f"Cannot add a mode: method '{self._current_method}' has no settings.")
            return
        if new_mode_name in modes and not force_creation:
            self._log.error(f"Mode '{new_mode_name}' already exists.")
            return

        source = setting if setting is not None else self.default_data
        if source is None:
            self._log.error(f"Cannot add mode '{new_mode_name}': no template settings available.")
            return
        # copy(name=...) rather than deepcopy: these are frozen, so a copy is a cheap replace() and
        # the caller's object can never be aliased into the store.
        modes[new_mode_name] = source.copy(name=new_mode_name)
        self.set_mode(new_mode_name)

    def delete_mode(self, mode_name: str) -> None:
        modes = self.mode_dict
        if mode_name not in modes:
            self._log.error(f"Mode '{mode_name}' not found.")
            return
        if mode_name == DEFAULT_MODE:
            self._log.error('Cannot delete the default mode.')
            return
        del modes[mode_name]
        self.set_mode(DEFAULT_MODE if self._current_mode == mode_name else self._current_mode)

    # ------------------------------------------------------------------ value updates

    def _display_dict(self) -> dict:
        data = self.current_data
        return data.to_display_dict() if data is not None else {}

    def _store(self, updated) -> None:
        self.mode_dict[self._current_mode] = updated

    def _updated(self, data, new_values: dict):
        """`data` with `new_values` applied, or None if the result would be invalid.

        The settings containers are frozen and validate in __post_init__, so an impossible value -
        a zero bin width, an unknown count mode - raises rather than being stored. Both callers
        below are reachable from Qt slots and from the settings restore at activation, where an
        unguarded raise escapes into the event loop or stops the module starting.
        load_from_dict() already takes exactly this line, for exactly this reason.
        """
        try:
            return data.update_from_dict(new_values)
        except ValueError as err:
            self._log.error(f'Rejected settings update {new_values}: {err}')
            return None

    def sync_values(self, new_values: dict) -> None:
        """Apply values coming FROM the settings widget.

        Deliberately silent: the widget already shows these, and echoing them back invites a
        feedback loop between widget and store.
        """
        data = self.current_data
        if data is None:
            return
        updated = self._updated(data, new_values)
        if updated is not None:
            self._store(updated)

    def set_values(self, new_values: dict) -> None:
        """Apply values from the logic side and tell observers, so the widget catches up."""
        data = self.current_data
        if data is None:
            return
        updated = self._updated(data, new_values)
        if updated is not None:
            self._store(updated)
        # Observers are told either way: on rejection this is what makes a widget still showing the
        # refused value snap back to what is actually stored.
        self._notify(self._on_data, self._display_dict())

    def set_single_value(self, param_name: str, value) -> None:
        data = self.current_data
        if data is None:
            return
        if param_name not in type(data).field_names():
            self._log.error(f"Parameter '{param_name}' not found in {type(data).__name__}.")
            return
        self.set_values({param_name: value})
