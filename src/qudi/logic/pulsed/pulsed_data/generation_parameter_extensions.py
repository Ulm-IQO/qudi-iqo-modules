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

        @classmethod
        def _coerce_fields(cls, data, current=None):
            pick = cls._pick
            return {
                'green_aom_delay': float(pick(data, current, 'green_aom_delay', 700e-9)),
                'readout_channel': str(pick(data, current, 'readout_channel', 'd_ch4')),
            }

A predefined generate method then reads it as
`self.generation_parameters['green_aom_delay']`, or directly off the settings object as
`self.generator_settings.generation_parameters.green_aom_delay`.

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
from dataclasses import dataclass

__all__ = ['BaseGenerationParameters']


@dataclass(frozen=True)
class BaseGenerationParameters:
    """Marker base for everything that contributes fields to `GenerationParameters`.

    Every subclass of this class is collected by sequence_generator_logic_data.py and merged into
    the single `GenerationParameters` dataclass the rest of the toolchain annotates against.

    Rules for a subclass:
      * every field needs a default - Python forbids a no-default field following a defaulted one
        in the merged class;
      * field names must not collide with another contributor's - checked at merge time, which
        raises naming both classes rather than letting one silently win;
      * field types should be str / int / float / bool / Enum / PulseEnvelope, the set the GUI can
        build a widget for (see pulsed_maingui._create_pm_global_params);
      * override `_coerce_fields()` to declare how each field is read back from a saved dict.

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
        defaults) and update_from_dict() (current=self, so absent keys keep their current value),
        which is why a contributor writes its explicit per-field coercion exactly once.

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
        raise NotImplementedError(f'{cls.__name__} must implement _coerce_fields()')

    @classmethod
    def _own_field_names(cls):
        """Names of the fields this contributor itself declares, excluding inherited ones."""
        return tuple(cls.__dict__.get('__annotations__', {}))

    @staticmethod
    def _pick(data, current, name, default):
        """Raw value for `name`: from `data`, else from `current`, else `default`."""
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
