# -*- coding: utf-8 -*-

"""
Keeping instream buffer instances activated, if deactivated.

Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

from PySide2 import QtCore
from qudi.core.module import Base
from qudi.core.configoption import ConfigOption
from qudi.core.modulemanager import ModuleManager

class BufferKeepAlive(Base):
    """
    Keep specific server-side modules (e.g. DataInStreamBuffer(s)) activated. Intended for remote connections.

    Uses ModuleManager API as defined in qudi-core/modulemanager.py:
      - ModuleManager.instance()
      - ModuleManager.modules (returns a copy of registry dict: name -> ManagedModule)
      - ManagedModule.is_active (True if instance exists and module_state != 'deactivated')
      - ManagedModule.state (string; e.g., 'idle', 'locked', 'deactivated', 'not loaded', 'BROKEN')
      - ModuleManager.activate_module(name) (queued to main thread when needed)

    Configuration:
      instream_keepalive:
        module.Class: 'interfuse.instream_keepalive.BufferKeepAlive'
        options:
          modules:
            - 'wavemeter_buffer_1'
            - 'wavemeter_buffer_2'
          interval_ms: 1500

    Behavior:
      - Polls ModuleManager for each listed module.
      - Detects transition from active -> inactive and queues a re-activation once.
      - Tracks last-known activation state to suppress repeated activations/logs.
      - Avoids racing with live deactivation by using QTimer.singleShot(0, ...) for activation.
      - Pauses during application shutdown.
    """

    _modules = ConfigOption(name='modules', default=[], missing='warn')
    _interval_ms = ConfigOption(name='interval_ms',
                                default=1500,
                                missing='nothing',
                                constructor=lambda x: max(200, int(x)))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mm: ModuleManager | None = None
        self._timer: QtCore.QTimer | None = None
        self._shutting_down: bool = False
        # Track last-known activation state per module name
        self._last_active: dict[str, bool] = {}

    def on_activate(self):
        # Observe app shutdown to avoid fighting real exit
        app = QtCore.QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._on_app_quit)

        self._mm = ModuleManager.instance()
        if self._mm is None:
            raise RuntimeError('BufferKeepAlive: ModuleManager.instance() is None')

        # Initialize last-known states to current activation snapshot to avoid initial spam
        self._snapshot_states()

        # Immediate ensure, then periodic checks
        self._ensure_active()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._ensure_active)
        self._timer.start()
        self.log.info(f'BufferKeepAlive watching: {self._modules}')

    def on_deactivate(self):
        if self._timer:
            self._timer.stop()
            self._timer = None

    def _on_app_quit(self):
        self._shutting_down = True

    # ---------- ModuleManager integration ----------

    def _get_entry(self, name: str):
        """
        Fetch ManagedModule entry by name using ModuleManager.modules dict (copy).
        ModuleManager.modules returns a shallow copy, safe to read.
        """
        try:
            modules_dict = self._mm.modules  # copy of registry
            return modules_dict.get(name, None)
        except Exception:
            # As a fallback, try internal attribute if available
            try:
                internal = getattr(self._mm, '_modules', None)
                if isinstance(internal, dict):
                    return internal.get(name, None)
            except Exception:
                pass
        return None

    def _is_active_now(self, entry) -> bool:
        """
        Decide activation based on ManagedModule.is_active/property in qudi-core.

        Per modulemanager.py:
          ManagedModule.is_active: instance exists and instance.module_state() != 'deactivated'
          ManagedModule.state: string; can be 'idle', 'locked', 'deactivated', etc.
        """
        if entry is None:
            return False
        try:
            # Preferred: is_active boolean
            return bool(entry.is_active)
        except Exception:
            # Fallback: use state string
            try:
                st = str(entry.state).strip().lower()
                # Consider anything except 'deactivated' and 'not loaded' as active
                return st not in ('deactivated', 'not loaded', 'broken')
            except Exception:
                return False

    def _snapshot_states(self):
        self._last_active.clear()
        for name in self._modules:
            entry = self._get_entry(name)
            active = self._is_active_now(entry)
            # Initialize with current state
            self._last_active[name] = active

    # ---------- Keep-alive core ----------

    def _ensure_active(self):
        if self._shutting_down:
            return
        for name in self._modules:
            try:
                entry = self._get_entry(name)
                active = self._is_active_now(entry)
                was_active = self._last_active.get(name, False)

                # If we previously saw it active and it is now inactive, reactivate once.
                # We also reactivate if it was unknown and is inactive now.
                if (not active) and was_active:
                    # Queue activation to avoid racing current deactivate
                    QtCore.QTimer.singleShot(0, lambda n=name: self._activate_once(n))
                # Update snapshot
                self._last_active[name] = active
            except Exception:
                # Log but keep the loop running
                self.log.exception(f'BufferKeepAlive: error while checking "{name}"')

    def _activate_once(self, name: str):
        if self._shutting_down:
            return
        try:
            # ModuleManager.activate_module handles thread affinity internally (BlockingQueuedConnection)
            self._mm.activate_module(name)
            # Update last-known state to "active" to avoid repeated attempts next tick
            self._last_active[name] = True
            self.log.info(f'BufferKeepAlive: reactivated "{name}"')
        except Exception:
            # Log activation failure once; next tick will try again if still inactive
            self.log.exception(f'BufferKeepAlive: failed to reactivate "{name}"')