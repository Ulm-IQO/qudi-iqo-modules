# -*- coding: utf-8 -*-
"""
Helpers that normalize the arguments of the settings setters in PulsedMeasurementLogic,
PulsedMasterLogic and SequenceGeneratorLogic.

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
from dataclasses import fields, is_dataclass, replace

__all__ = ['SettingsTypeError', 'coerce_settings', 'as_settings_dict']


class SettingsTypeError(TypeError):
    """Raised when a settings setter receives a value it cannot interpret."""


def coerce_settings(value, kwargs, current, cls):
    """Normalize a settings argument into an instance of `cls`.

    A `cls` instance replaces the settings completely; a dict is applied as a partial update on
    top of `current`. Neither `value` nor `kwargs` is mutated.

    Parameters
    ----------
    value : cls or dict or None
        The settings argument as passed by the caller.
    kwargs : dict
        The setter's keyword arguments. Override matching keys in `value`.
    current : cls
        The settings currently in effect.
    cls : type
        The settings dataclass to normalize to.

    Returns
    -------
    cls
        The new settings instance.

    Raises
    ------
    SettingsTypeError
        If `value` is not a `cls`, a dict or None.
    TypeError
        If `cls` and `current` don't match, or `kwargs` contains an unknown field.
    """
    if not (isinstance(cls, type) and is_dataclass(cls)):
        raise TypeError(f'coerce_settings: cls must be a dataclass type, got {cls!r}')
    if not isinstance(current, cls):
        raise TypeError(f'coerce_settings: current must be a {cls.__name__}, got '
                        f'{type(current).__name__}')

    # Check the dataclass first: some settings classes are dict subclasses.
    if isinstance(value, cls):
        if not kwargs:
            return value
        unknown = set(kwargs) - {f.name for f in fields(cls)}
        if unknown:
            raise TypeError(f'Unknown {cls.__name__} field(s): {sorted(unknown)}')
        return replace(value, **kwargs)
    if isinstance(value, dict):
        return current.update_from_dict({**value, **kwargs} if kwargs else value)
    if value is None:
        return current.update_from_dict(kwargs)
    raise SettingsTypeError(f'Expected {cls.__name__}, dict or None, got {type(value).__name__}')


def as_settings_dict(value, kwargs, cls=None):
    """Normalize a settings argument into a plain dict.

    Used by PulsedMasterLogic, which relays settings through a QtCore.Signal(dict).

    Parameters
    ----------
    value : cls or dict or None
        The settings argument as passed by the caller.
    kwargs : dict
        The setter's keyword arguments. Override matching keys in `value`.
    cls : type, optional
        Settings dataclass to accept in addition to dict and None.

    Returns
    -------
    dict
        A new dict.

    Raises
    ------
    SettingsTypeError
        If `value` is not a `cls`, a dict or None.
    """
    if cls is not None and isinstance(value, cls):
        return {**value.to_dict(), **kwargs}
    if isinstance(value, dict):
        return {**value, **kwargs}
    if value is None:
        return dict(kwargs)
    expected = f'{cls.__name__}, dict or None' if cls is not None else 'dict or None'
    raise SettingsTypeError(f'Expected {expected}, got {type(value).__name__}')
