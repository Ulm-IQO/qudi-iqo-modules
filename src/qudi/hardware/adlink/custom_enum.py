# -*- coding: utf-8 -*-

"""
This file contains helper classes.

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

from enum import Enum
from qudi.hardware.adlink.config_options import AdlinkCardType, AdlinkReadCount


class EnumSearchName(Enum):
    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    def get_value_from_name(enum_member: AdlinkCardType):
        for member in AdlinkReadCount:
            if member.name == enum_member.name:
                return member.value
        member = AdlinkReadCount.DEFAULT
        print(
            f"Error {enum_member} not found in AdlinkReadCount. Returning value of {member}."
        )
        return member.value
