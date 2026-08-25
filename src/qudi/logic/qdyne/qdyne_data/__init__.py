# -*- coding: utf-8 -*-
"""Typed containers for the qdyne toolchain.

This package holds the settings and data dataclasses qdyne persists and passes around. It follows
the same conventions as `qudi.logic.pulsed.pulsed_data` - see README.md next to this file.

Nothing in here imports Qt. The settings are plain frozen dataclasses; change notification is the
job of whatever owns them (a mediator, and above it QdyneLogic).
"""

from qudi.logic.qdyne.qdyne_data.settings_base import QdyneSettingsBase

__all__ = ['QdyneSettingsBase']
