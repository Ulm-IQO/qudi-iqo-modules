# -*- coding: utf-8 -*-
"""Base class for every qdyne settings dataclass.

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
from dataclasses import dataclass, fields, replace
from enum import Enum
from typing import Any, Dict, Type, TypeVar, get_type_hints

from qudi.core.logger import get_logger

_logger = get_logger(__name__)

_T = TypeVar('_T', bound='QdyneSettingsBase')


def as_bool(value: Any) -> bool:
    """bool('False') is True, which is never what a saved settings file means."""
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _coerce(value: Any, target: Type, field_name: str, fallback: Any) -> Any:
    """Best-effort conversion of `value` to `target`.

    A value that cannot be converted is logged and replaced by `fallback` rather than raising: these
    objects are built from status files and saved measurements, where one bad entry must not take
    the whole settings object down with it.
    """
    if target is Any or target is None:
        return value
    try:
        if target is bool:
            return as_bool(value)
        if isinstance(target, type) and issubclass(target, Enum):
            return value if isinstance(value, target) else target(value)
        if target in (int, float, str):
            return target(value)
        if target is list:
            return list(value)
        if target is dict:
            return dict(value)
        if target is tuple:
            return tuple(value)
    except (TypeError, ValueError) as err:
        _logger.warning(
            f'Could not coerce {field_name}={value!r} to {getattr(target, "__name__", target)} '
            f'({err}). Using {fallback!r} instead.'
        )
        return fallback
    return value


@dataclass(frozen=True)
class QdyneSettingsBase:
    """Frozen base for qdyne settings containers.

    Frozen on purpose, following pulsed_data: a settings object handed to an estimator, a widget and
    a saved-measurement snapshot is the *same* object, and anything able to mutate it in place can
    change all three at once. Use `update_from_dict()` (or `dataclasses.replace`) to get a modified
    copy instead.

    The three-method contract every persisted class in this package implements:

    * `to_dict()`      - every field, the lossless persistence form
    * `from_dict()`    - tolerant constructor: unknown keys ignored, missing keys defaulted
    * `update_from_dict()` - partial update returning a NEW instance

    `to_dict()` deliberately includes fields marked `metadata={'exclude': True}`. That marker means
    "do not show this in the settings widget", which is a display concern - it previously also meant
    "never save this", so a hidden field silently failed to persist. The display subset now has its
    own accessor, `to_display_dict()`.
    """

    name: str = ''

    # ---------------------------------------------------------------- introspection helpers

    @classmethod
    def _field_types(cls) -> Dict[str, Any]:
        """Field name -> resolved type.

        Uses get_type_hints() rather than `field.type` so this keeps working if a module ever adds
        `from __future__ import annotations` (or a future Python defers annotations by default), at
        which point `field.type` becomes the string 'float' and every type comparison silently
        fails. Falls back to the raw annotations if a hint cannot be resolved.
        """
        try:
            hints = get_type_hints(cls)
        except (NameError, TypeError):
            hints = {f.name: f.type for f in fields(cls)}
        return {f.name: hints.get(f.name, f.type) for f in fields(cls)}

    @classmethod
    def field_names(cls) -> set:
        return {f.name for f in fields(cls)}

    # ---------------------------------------------------------------- the three-method contract

    def to_dict(self) -> dict:
        """Every field, including ones hidden from the settings widget."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def to_display_dict(self) -> dict:
        """The subset a settings widget should show.

        Drops private fields and anything marked `metadata={'exclude': True}`.
        """
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if not f.name.startswith('_') and not f.metadata.get('exclude', False)
        }

    @classmethod
    def from_dict(cls: Type[_T], data: Any) -> _T:
        """Build from a dict, tolerating a schema that has moved on.

        Keys that are not fields are dropped with a warning; fields absent from `data` take their
        declared default. This is what lets a status file written by an older version load instead
        of raising TypeError out of __init__.
        """
        if not isinstance(data, dict):
            return cls()
        types = cls._field_types()
        defaults = cls()
        unknown = set(data) - cls.field_names()
        if unknown:
            _logger.warning(
                f'Ignoring saved key(s) {sorted(unknown)} - not field(s) of {cls.__name__}.'
            )
        kwargs = {
            name: _coerce(data[name], types[name], name, getattr(defaults, name))
            for name in cls.field_names()
            if name in data
        }
        return cls(**kwargs)

    def update_from_dict(self: _T, data: Any) -> _T:
        """Partial update. Returns a NEW instance; `self` is unchanged."""
        if not isinstance(data, dict):
            return self
        types = type(self)._field_types()
        known = {k: v for k, v in data.items() if k in type(self).field_names()}
        unknown = set(data) - type(self).field_names()
        if unknown:
            _logger.warning(
                f'Ignoring key(s) {sorted(unknown)} - not field(s) of {type(self).__name__}.'
            )
        if not known:
            return self
        coerced = {
            name: _coerce(value, types[name], name, getattr(self, name))
            for name, value in known.items()
        }
        return replace(self, **coerced)

    def copy(self: _T, **changes) -> _T:
        """A modified copy. Frozen dataclasses have no in-place edit by design."""
        return replace(self, **changes) if changes else replace(self)
