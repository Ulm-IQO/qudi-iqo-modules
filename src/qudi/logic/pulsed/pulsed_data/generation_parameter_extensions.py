# -*- coding: utf-8 -*-
"""
Lab-specific extensions to the global pulse generation parameters.

Edit this file to add a global generation setting for your lab (an AOM delay, a second microwave
channel, ...). Declare a frozen dataclass inheriting from `BaseGenerationParameters`. Its fields
get a widget on the Predefined Methods tab, are saved in the status file and can be read by
predefined generate methods. sequence_generator_logic_data.py merges all subclasses declared here
with `CoreGenerationParameters` into the `GenerationParameters` class.

A parameter needed by only one predefined generator can be declared on the generator class
instead, see `PredefinedGeneratorBase.generation_parameter_contributors` in pulse_objects.py.

Example
-------
    @dataclass(frozen=True)
    class NVCentreParameters(BaseGenerationParameters):
        green_aom_delay: float = 700e-9
        readout_channel: str = 'd_ch4'

A predefined generate method reads it as `self.generation_parameters['green_aom_delay']`.

Loading saved values
--------------------
Values read from the status file are converted based on the field's declared type:

    int, float, str    int(value), float(value), str(value)
    bool               as_bool(value), so 'False'/'no'/'off'/'0' are False
    Enum subclass      looked up by member name, else by member value
    other classes      instances are kept; dicts go through the class's from_dict()

If a value cannot be converted, a warning is logged and the field keeps its current value or
default.

To add custom handling for a field, override `_coerce_fields()`, call `super()` and patch the
result:

    @dataclass(frozen=True)
    class NVCentreParameters(BaseGenerationParameters):
        green_aom_delay: float = 700e-9
        laser_power_fraction: float = 0.5

        @classmethod
        def _coerce_fields(cls, data, current=None):
            coerced = super()._coerce_fields(data, current)
            coerced['laser_power_fraction'] = min(1.0, max(0.0, coerced['laser_power_fraction']))
            return coerced

Mutable defaults
----------------
Fields of a mutable type need `field(default_factory=...)`, otherwise Python raises
"ValueError: mutable default ... is not allowed". This includes `PulseEnvelope`:

    from dataclasses import dataclass, field
    from qudi.logic.pulsed.sampling_functions import PulseEnvelope, PulseEnvelopeType

    @dataclass(frozen=True)
    class NVCentreParameters(BaseGenerationParameters):
        envelope: PulseEnvelope = field(
            default_factory=lambda: PulseEnvelope(PulseEnvelopeType.rectangle)
        )

The same applies to dicts, lists, numpy arrays and instances of classes that define `__eq__`.

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
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from logging import getLogger
from typing import get_origin, get_type_hints

__all__ = ['BaseGenerationParameters', 'as_bool']

_logger = getLogger(__name__)


def as_bool(value):
    """Convert a value to bool, treating only '1', 'true', 'yes' and 'on' as True for strings.

    Plain `bool('False')` would return True.

    Parameters
    ----------
    value : object
        The value to convert.

    Returns
    -------
    bool
    """
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _coerce_value(value, declared_type):
    """Convert a saved value to the declared type of its field.

    Parameters
    ----------
    value : object
        The raw value from the status file or a GUI widget.
    declared_type : type
        The field's declared type.

    Returns
    -------
    object
        The converted value. Returned unchanged for generics, Unions and unresolved annotations.

    Raises
    ------
    TypeError, ValueError, KeyError
        If the conversion fails.
    """
    if get_origin(declared_type) is not None or not isinstance(declared_type, type):
        return value
    if declared_type is bool:
        # Before int, since bool is a subclass of int.
        return as_bool(value)
    if declared_type in (int, float, str):
        return declared_type(value)
    if isinstance(value, declared_type):
        return value
    if issubclass(declared_type, Enum) and isinstance(value, str):
        return declared_type[value] if value in declared_type.__members__ else declared_type(value)
    if isinstance(value, dict) and hasattr(declared_type, 'from_dict'):
        return declared_type.from_dict(value)
    return declared_type(value)


@dataclass(frozen=True)
class BaseGenerationParameters:
    """Base class for classes contributing fields to `GenerationParameters`.

    Subclasses must:
      * be decorated with `@dataclass(frozen=True)`;
      * give every field a default (`field(default_factory=...)` for mutable types);
      * use field names not used by any other contributor;
      * use str, int, float, bool, Enum or PulseEnvelope fields to get a GUI widget.

    A subclass instance holds only part of the parameters, so it can't be passed to
    set_generation_parameters().
    """

    @classmethod
    def _coerce_fields(cls, data, current=None):
        """Return the converted values of this contributor's own fields.

        Each value is taken from `data`, else from `current`, else from the field default.
        Values that fail to convert are logged and replaced instead of raising, since a raised
        error would reset all generation parameters when loading the status file.

        Parameters
        ----------
        data : dict
            The incoming values. Keys may be missing.
        current : GenerationParameters or None
            The settings currently in effect, or None to use defaults.

        Returns
        -------
        dict
            Keyword arguments for this contributor's fields.
        """
        try:
            hints = get_type_hints(cls)
        except (NameError, TypeError):
            hints = {}
        defaults = cls()
        coerced = {}
        for name in cls._own_field_names():
            default = getattr(defaults, name)
            raw = cls._pick(data, current, name, default)
            if raw is default:
                # Defaults are used as-is.
                coerced[name] = raw
                continue
            try:
                coerced[name] = _coerce_value(raw, hints.get(name, object))
            except (TypeError, ValueError, KeyError) as err:
                live = default if current is None else getattr(current, name, default)
                # If `raw` came from `current`, fall back to the default instead.
                fallback = default if live is raw else live
                _logger.warning(
                    '%s.%s: cannot coerce %r to %s (%s). Ignoring that saved value and using %r '
                    'instead; every other generation parameter is unaffected.',
                    cls.__name__, name, raw, getattr(hints.get(name), '__name__', '?'), err,
                    fallback
                )
                coerced[name] = fallback
        return coerced

    @classmethod
    def _own_field_names(cls):
        """Names of the fields declared by this class itself, in declaration order.

        Returns
        -------
        tuple of str
        """
        inherited = {
            f.name for base in cls.__mro__[1:] if is_dataclass(base) for f in fields(base)
        }
        return tuple(f.name for f in fields(cls) if f.name not in inherited)

    @staticmethod
    def _pick(data, current, name, default):
        """Return the value for `name` from `data`, else from `current`, else `default`."""
        if name in data:
            return data[name]
        if current is not None:
            return getattr(current, name, default)
        return default


##############################################################################
#                                                                            #
#             Add your lab's generation parameter classes below.             #
#                  See the module docstring for an example.                  #
#                                                                            #
##############################################################################

