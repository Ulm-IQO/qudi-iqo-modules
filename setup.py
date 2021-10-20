# -*- coding: utf-8 -*-

import os
import sys
from setuptools import setup, find_namespace_packages
from setuptools.command.develop import develop
from setuptools.command.install import install

with open('README.md', 'r') as file:
    long_description = file.read()

# with open(os.path.join('.', 'qudi', 'core', 'VERSION.txt'), 'r') as file:
#     version = file.read().strip()
# ToDo: Fix version import
version = '0.1.0'

unix_dep = ['wheel',
            'qudi',
            'cycler',
            'entrypoints',
            'fysom',
            'GitPython',
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

windows_dep = ['wheel',
               'qudi',
               'cycler',
               'entrypoints',
               'fysom',
               'GitPython',
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


setup(name='qudi-iqo-modules',
      version=version,
      packages=find_namespace_packages(),
      package_data={'': ['LICENSE', 'LICENSE.LESSER', 'AUTHORS.md'],
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
                'modular'
                ],
      license='LGPLv3',
      install_requires=windows_dep if sys.platform == 'win32' else unix_dep,
      python_requires='~=3.8',
      cmdclass={'develop': PrePostDevelopCommands, 'install': PrePostInstallCommands},
      zip_safe=False
      )
