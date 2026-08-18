# -*- coding: utf-8 -*-

"""
This is a utility module for the requirements workflow
It parses the package list for pinned ones

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

import sys
import toml
from packaging.requirements import Requirement

def has_version_constraints(dep):
    req = Requirement(dep)
    return len(req.specifier) > 0

def is_pinned(dep):
    req = Requirement(dep)
    return all(spec.operator == '==' for spec in req.specifier)

def is_upper_bounded(dep):
    req = Requirement(dep)
    return any(spec.operator in ('<', '<=', '~=', '^') for spec in req.specifier)


def get_constrained_dependencies(pyproject_path):
    with open(pyproject_path, 'r') as file:
        pyproject_data = toml.load(file)

    dependencies = pyproject_data.get('project', {}).get('dependencies', [])

    if not dependencies:
        return

    pinned_deps = [dep for dep in dependencies if is_pinned(dep)]
    upper_bounded_deps = [dep for dep in dependencies if is_upper_bounded(dep)]

    return pinned_deps + upper_bounded_deps
if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(1)
    pyproject_path = sys.argv[1]
    get_constrained_dependencies(pyproject_path)