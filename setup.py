# -*- coding: utf-8 -*-

import os
import sys
from setuptools import setup, find_namespace_packages
from setuptools.command.develop import develop
from setuptools.command.install import install


class PrePostDevelopCommands(develop):
    """ Pre- and Post-installation script for development mode.
    """

    def run(self):
        # PUT YOUR PRE-INSTALL SCRIPT HERE or CALL A FUNCTION
        develop.run(self)
        # PUT YOUR POST-INSTALL SCRIPT HERE or CALL A FUNCTION


class PrePostInstallCommands(install):
    """ Pre- and Post-installation for installation mode.
    """

    def run(self):
        # PUT YOUR PRE-INSTALL SCRIPT HERE or CALL A FUNCTION
        install.run(self)
        # PUT YOUR POST-INSTALL SCRIPT HERE or CALL A FUNCTION


unix_dep = [
    'qudi-core',
    'cycler',
    'entrypoints',
    'fysom',
    'jupyter',
    'jupytext',
    'lmfit',
    'lxml',
    'matplotlib',
    'nidaqmx',
    'numpy',
    'pyqtgraph',
    'PySide2',
    'PyVisa',
    'rpyc',
    'ruamel.yaml',
    'scipy',
]

windows_dep = [
    'qudi-core',
    'cycler',
    'entrypoints',
    'fysom',
    'jupyter',
    'jupytext',
    'lmfit',
    'lxml',
    'matplotlib',
    'nidaqmx',
    'numpy',
    'pyqtgraph',
    'PySide2',
    'PyVisa',
    'rpyc',
    'ruamel.yaml',
    'scipy',
]

with open('VERSION', 'r') as file:
    version = file.read().strip()

with open('README.md', 'r') as file:
    long_description = file.read()

setup(
    name='qudi-iqo-modules',
    version=version,
    packages=find_namespace_packages(where='src', exclude=['qudi.artwork']),
    package_dir={'': 'src'},
    package_data={''        : ['LICENSE', 'LICENSE.LESSER', 'AUTHORS.md', 'README.md', 'VERSION'],
                  'qudi'    : ['artwork/icons/*', 'artwork/icons/**/*', 'artwork/icons/**/**/*'],
                  'qudi.gui': ['*.ui', '*/*.ui'],
                  },
    description='IQO measurement modules collection for qudi',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/Ulm-IQO/qudi-iqo-modules',
    keywords=['diamond',
              'quantum',
              'confocal',
              'experiment',
              'lab',
              'laboratory',
              'instrumentation',
              'instrument',
              'modular',
              'measurement',
              ],
    license='LGPLv3',
    install_requires=windows_dep if sys.platform == 'win32' else unix_dep,
    python_requires='~=3.8',
    cmdclass={'develop': PrePostDevelopCommands, 'install': PrePostInstallCommands},
    zip_safe=False
)
