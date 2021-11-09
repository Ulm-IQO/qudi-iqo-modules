# -*- coding: utf-8 -*-

import sys
from setuptools import setup, find_namespace_packages


unix_dep = [
    'qudi-core',
    'entrypoints',
    'fysom',
    'lmfit',
    'lxml',
    'matplotlib',
    'nidaqmx',
    'numpy',
    'pyqtgraph',
    'PySide2',
    'PyVisa',
    'scipy',
]

windows_dep = [
    'qudi-core',
    'entrypoints>=0.3',
    'fysom>=2.1.6',
    'lmfit>=1.0.3',
    'lxml>=4.6.3',
    'matplotlib>=3.4.3',
    'nidaqmx>=0.5.7',
    'numpy>=1.21.3',
    'pyqtgraph>=0.12.3',
    'PySide2>=5.15.2',
    'PyVisa>=1.11.3',
    'scipy>=1.7.1',
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
    zip_safe=False
)
