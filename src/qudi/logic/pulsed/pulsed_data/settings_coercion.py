# -*- coding: utf-8 -*-
"""
This file contains the shared normalization helper used by the settings setters of
PulsedMeasurementLogic, PulsedMasterLogic and SequenceGeneratorLogic.

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
    """Raised when a caller hands a settings setter something it cannot interpret.

    """


def coerce_settings(value, kwargs, current, cls):
    """Normalize a settings argument into an instance of `cls`.

    `cls` is the one concrete settings dataclass the call site accepts (e.g. FastCounterSettings),
    not a generic "is this a class" check - anything that is not an instance of that type (or a
    subclass of it), a dict, or None is rejected.

    A dict means a *partial patch* relative to `current`; a `cls` instance means a *full
    replacement*. Neither `value` nor `kwargs` is mutated.

    Parameters
    ----------
    value : cls or dict or None
        The settings argument as passed by the caller.
    kwargs : dict
        The setter's keyword arguments. Take precedence over conflicting names in `value`.
    current : cls
        The settings currently in effect, used as the base a partial patch is applied to.
    cls : type
        The settings dataclass to normalize to.

    Returns
    -------
    cls
        The new settings instance to apply. Never the same object as `current` unless `value`
        happens to be `current` itself.

    Raises
    ------
    SettingsTypeError
        `value` is neither a `cls`, a dict, nor None.
    TypeError
        `cls`/`current` are inconsistent, or `kwargs` names a field `cls` does not have - both
        are bugs in the calling module rather than bad user input.
    """
    if not (isinstance(cls, type) and is_dataclass(cls)):
        raise TypeError(f'coerce_settings: cls must be a dataclass type, got {cls!r}')
    if not isinstance(current, cls):
        raise TypeError(f'coerce_settings: current must be a {cls.__name__}, got '
                        f'{type(current).__name__}')

    # Check for the dataclass before the dict: AnalysisParameters/ExtractionParameters/
    # GenerationMethodParameters are dict subclasses, so isinstance(x, dict) is True for them.
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

    The counterpart of coerce_settings() for the relay layer, where the value has to end up as a
    dict regardless: a QtCore.Signal(dict) cannot carry a dataclass across a thread boundary, so
    PulsedMasterLogic must flatten before emitting. Unlike coerce_settings() this does not need
    the current settings, since it produces a (possibly partial) dict rather than a complete
    settings object.

    Parameters
    ----------
    value : cls or dict or None
        The settings argument as passed by the caller.
    kwargs : dict
        The setter's keyword arguments. Take precedence over conflicting names in `value`.
    cls : type, optional
        Settings dataclass to accept in addition to dict/None. If None, only dict/None are valid.

    Returns
    -------
    dict
        A new dict; neither `value` nor `kwargs` is mutated.

    Raises
    ------
    SettingsTypeError
        `value` is neither a `cls`, a dict, nor None.
    """
    if cls is not None and isinstance(value, cls):
        return {**value.to_dict(), **kwargs}
    if isinstance(value, dict):
        return {**value, **kwargs}
    if value is None:
        return dict(kwargs)
    expected = f'{cls.__name__}, dict or None' if cls is not None else 'dict or None'
    raise SettingsTypeError(f'Expected {expected}, got {type(value).__name__}')
