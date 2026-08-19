# -*- coding: utf-8 -*-
"""
Lab-specific extensions to the global pulse generation parameters.

THIS IS THE FILE TO EDIT if your lab needs an extra global generation setting (an AOM delay, a
second microwave channel, a calibration factor, ...). Declare one frozen dataclass inheriting from
`BaseGenerationParameters` and its fields become first-class generation parameters everywhere: a
widget appears on the Predefined Methods tab, the value is saved to and restored from the status
file, and predefined generate methods can read it.

Nothing else needs to change. sequence_generator_logic_data.py imports this module and merges every
subclass declared here with the built-in `CoreGenerationParameters` into the single
`GenerationParameters` class the rest of the pulsed toolchain uses.

For a parameter only one predefined generator needs, declare it on that generator class instead - see
`PredefinedGeneratorBase.generation_parameter_contributors` in pulse_objects.py. That route also
reaches generators loaded through the additional_predefined_methods_path config option, which cannot
edit this file.

`BaseGenerationParameters` lives here rather than in sequence_generator_logic_data.py purely so
this file has no import back into it - a cycle would make the merge silently skip extensions
depending on which module got imported first.

Worked example
--------------
    @dataclass(frozen=True)
    class NVCentreParameters(BaseGenerationParameters):
        '''Extra generation parameters for the NV setup.'''

        green_aom_delay: float = 700e-9
        readout_channel: str = 'd_ch4'

That is the whole class. A predefined generate method then reads it as
`self.generation_parameters['green_aom_delay']`, or directly off the settings object as
`self.generator_settings.generation_parameters.green_aom_delay`.

How a saved value gets back into a field
----------------------------------------
A status file holds text, so a value read back out of it may not have the type its field declares -
a float can arrive as '700e-9', a bool as 'False'. `_coerce_fields()` converts it, choosing how from
the declared type alone:

    declared type      conversion applied to the saved value
    -----------------  ------------------------------------------------------------------
    int, float, str    int(value), float(value), str(value)
    bool               as_bool(value)   - 'False'/'no'/'off'/'0' are False, not True
    an Enum subclass   looked up by member name, else by member value
    any other class    an instance passes through as-is; a dict goes through that class's
                       from_dict() if it has one - this is how PulseEnvelope round-trips

You write none of that; it follows from the annotation you already wrote, which is also why a
field's default can no longer drift out of sync with its restore path - there is only one of each.

If a value cannot be converted (a corrupted status file), the failure is logged and that one field
falls back to its current value, else its default. It is deliberately not raised: a StatusVar
constructor swallows an exception and returns the whole default object, so one bad key would
otherwise silently reset every generation parameter.

Custom handling for one awkward field
-------------------------------------
Type detection cannot know that a fraction must stay within [0, 1]. Override `_coerce_fields()`, let
`super()` do the automatic work for everything, and patch only the field that needs it:

    @dataclass(frozen=True)
    class NVCentreParameters(BaseGenerationParameters):
        green_aom_delay: float = 700e-9        # still fully automatic
        laser_power_fraction: float = 0.5

        @classmethod
        def _coerce_fields(cls, data, current=None):
            coerced = super()._coerce_fields(data, current)
            coerced['laser_power_fraction'] = min(1.0, max(0.0, coerced['laser_power_fraction']))
            return coerced

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
    """Converts a value to a boolean. If the value is a string, it checks for common truthy values.

    Needed because `bool('False')` is True, so a bool round-tripped through a status file as text
    would come back inverted under a plain `bool()`.

    Parameters
    ----------
    value : object
        The value to interpret as a boolean.

    Returns
    -------
    bool
        For a string, whether it is one of '1', 'true', 'yes' or 'on', case-insensitively.
        Otherwise `bool(value)`.
    """
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _coerce_value(value, declared_type):
    """Convert one saved value to the type its generation parameter field declares.

    Parameters
    ----------
    value : object
        The raw value, as read from a status file or handed over by a GUI widget.
    declared_type : type
        The field's declared type.

    Returns
    -------
    object
        `value` converted to `declared_type`, or `value` unchanged where `declared_type` carries no
        usable conversion rule - a parameterised generic, a Union, or an annotation that could not
        be resolved to a real type object.

    Raises
    ------
    TypeError, ValueError, KeyError
        If the conversion itself fails. `_coerce_fields()` handles these.
    """
    if get_origin(declared_type) is not None or not isinstance(declared_type, type):
        return value
    if declared_type is bool:
        # Must precede int: bool is a subclass of int, and bool('False') is True.
        return as_bool(value)
    if declared_type in (int, float, str):
        return declared_type(value)
    if isinstance(value, declared_type):
        return value
    if issubclass(declared_type, Enum) and isinstance(value, str):
        return declared_type[value] if value in declared_type.__members__ else declared_type(value)
    if isinstance(value, dict) and hasattr(declared_type, 'from_dict'):
        # The convention every persisted class in this package follows, so a nested settings object
        # round-trips through its own from_dict() without this layer knowing anything about it.
        return declared_type.from_dict(value)
    return declared_type(value)


@dataclass(frozen=True)
class BaseGenerationParameters:
    """Marker base for everything that contributes fields to `GenerationParameters`.

    Every subclass of this class is collected by sequence_generator_logic_data.py and merged into
    the single `GenerationParameters` dataclass the rest of the toolchain annotates against.

    Rules for a subclass:
      * decorate it with `@dataclass(frozen=True)` - without the decorator its annotations never
        become fields and the class contributes nothing at all, which the merge rejects by name;
      * every field needs a default - Python forbids a no-default field following a defaulted one
        in the merged class;
      * field names must not collide with another contributor's - checked at merge time, which
        raises naming both classes rather than letting one silently win;
      * field types should be str / int / float / bool / Enum / PulseEnvelope, the set the GUI can
        build a widget for (see pulsed_maingui._create_pm_global_params);
      * nothing else. How each field is read back from a saved dict follows from its declared type -
        see `_coerce_fields()`, and override it only for a field whose type cannot express the
        handling it needs.

    Do not instantiate a subclass on its own and hand it to set_generation_parameters(): a
    contributor describes a *fragment* of the schema, not a complete settings object. It is
    deliberately not an instance of the merged class, so coerce_settings() rejects it instead of
    treating it as a full replacement and resetting every other field to its default.
    """

    @classmethod
    def _coerce_fields(cls, data, current=None):
        """Return {field_name: coerced value} for this contributor's own fields only.

        Each value is taken from `data` when present, otherwise from `current` (an existing merged
        instance) when one is given, otherwise from the field's declared default. Being tolerant of
        missing keys is what lets a status file written before a field existed still load, instead
        of raising and losing every saved parameter.

        This single hook backs both from_dict() (current=None, so absent keys fall back to
        defaults) and update_from_dict() (current=self, so absent keys keep their current value).

        The conversion applied to each value follows from that field's declared type, so a
        contributor normally does not override this at all. One that must should call
        `super()._coerce_fields(data, current)` for everything else and patch only the key
        concerned. A value that cannot be converted is logged and replaced by that field's current
        value, else its default, rather than raising - the StatusVar constructor swallows an
        exception and returns the whole default object, so one bad key would otherwise silently
        reset every parameter.

        Parameters
        ----------
        data : dict
            The saved/incoming values. May be missing any or all of this class's fields.
        current : GenerationParameters or None
            The settings currently in effect, or None to fall back to defaults.

        Returns
        -------
        dict
            Keyword arguments for this contributor's fields only.
        """
        try:
            hints = get_type_hints(cls)
        except (NameError, TypeError):
            hints = {}
        # Every contributor field must have a default, so a bare instance is simply all of them.
        defaults = cls()
        coerced = {}
        for name in cls._own_field_names():
            default = getattr(defaults, name)
            raw = cls._pick(data, current, name, default)
            if raw is default:
                # Nothing was saved for this field, so `raw` is the declared default object itself
                # - already the intended value, and converting it would only invent a complaint
                # about a field the user never touched (a `dict` default of None, say).
                coerced[name] = raw
                continue
            try:
                coerced[name] = _coerce_value(raw, hints.get(name, object))
            except (TypeError, ValueError, KeyError) as err:
                live = default if current is None else getattr(current, name, default)
                # `raw` may itself have come off `current` (the key was absent from `data`), in
                # which case reusing it would fail identically and only the default is sound.
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
        """Names of the fields this contributor itself declares, excluding inherited ones.

        `fields()` reports base-first then own, each in declaration order, so subtracting the bases
        preserves this class's own order - which `to_dict()` relies on for the Predefined Methods
        grid layout. `ClassVar`/`InitVar` never appear, since `fields()` does not report them.

        Returns
        -------
        tuple of str
            Field names, in declaration order.
        """
        inherited = {
            f.name for base in cls.__mro__[1:] if is_dataclass(base) for f in fields(base)
        }
        return tuple(f.name for f in fields(cls) if f.name not in inherited)

    @staticmethod
    def _pick(data, current, name, default):
        """Raw value for `name`: from `data`, else from `current`, else `default`.

        Parameters
        ----------
        data : dict
            The saved/incoming values.
        current : GenerationParameters or None
            The settings currently in effect, or None to fall back to `default`.
        name : str
            Field name to look up.
        default : object
            Value to use when `name` appears in neither source.

        Returns
        -------
        object
            The value, uncoerced.
        """
        if name in data:
            return data[name]
        if current is not None:
            return getattr(current, name, default)
        return default


##############################################################################
#                                                                            #
#             Add your lab's generation parameter classes below.             #
#                See the module docstring for a worked example.              #
#                                                                            #
##############################################################################

#Test
@dataclass(frozen=True)
class TestParameters(BaseGenerationParameters):
    time_delay: float = 4.0

    @classmethod
    def _coerce_fields(cls, data, current=None):
        pick = cls._pick
        return {
            'time_delay': float(pick(data, current, 'time_delay', 4.0)),
        }
    #Plan to remove this

    #GenerationParameters
