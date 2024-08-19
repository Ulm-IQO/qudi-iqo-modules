# -*- coding: utf-8 -*-

import sys
from setuptools import setup, find_namespace_packages


unix_dep = [
    'qudi-core>=1.5.1',
    'entrypoints>=0.4',
    'fysom>=2.1.6',
    'lmfit>=1.0.3',
    'lxml>=4.9.1',
    'matplotlib>=3.6.0',
    'nidaqmx>=0.5.7',
    'numpy>=1.23.3,<2.0',
    'pyqtgraph>=0.13.1',
    'PySide2',  # get fixed version from core
    'PyVisa>=1.12.0',
    'scipy>=1.9.1',
    'zaber_motion>=2.14.6'
]

windows_dep = [
    'qudi-core>=1.5.1',
    'entrypoints>=0.4',
    'fysom>=2.1.6',
    'lmfit>=1.0.3',
    'lxml>=4.9.1',
    'matplotlib>=3.6.0',
    'nidaqmx>=0.5.7',
    'numpy>=1.23.3,<2.0',
    'pyqtgraph>=0.13.1',
    'PySide2',  # get fixed version from core
    'PyVisa>=1.12.0',
    'scipy>=1.9.1',
    'zaber_motion>=2.14.6'
]

with open('VERSION', 'r') as file:
    version = file.read().strip()

with open('README.md', 'r') as file:
    long_description = file.read()

setup(
    name='qudi-iqo-modules',
    version=version,
    packages=find_namespace_packages(where='src'),
    package_dir={'': 'src'},
    package_data={'qudi'    : ['default.cfg'],
                  'qudi.gui': ['*.ui', '*/*.ui'],
                  },
    description='IQO measurement modules collection for qudi',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/Ulm-IQO/qudi-iqo-modules',
    project_urls={'Documentation': 'https://ulm-iqo.github.io/qudi-iqo-modules/',
                  'Source Code': 'https://github.com/Ulm-IQO/qudi-iqo-modules/',
                  'Bug Tracker': 'https://github.com/Ulm-IQO/qudi-iqo-modules/issues/',
                  },
    keywords=['qudi',
              'diamond',
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
    classifiers=['Development Status :: 5 - Production/Stable',

                 'Environment :: Win32 (MS Windows)',
                 'Environment :: X11 Applications',
                 'Environment :: MacOS X',

                 'Intended Audience :: Science/Research',
                 'Intended Audience :: End Users/Desktop',

                 'License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)',

                 'Natural Language :: English',

                 'Operating System :: Microsoft :: Windows :: Windows 8',
                 'Operating System :: Microsoft :: Windows :: Windows 8.1',
                 'Operating System :: Microsoft :: Windows :: Windows 10',
                 'Operating System :: MacOS :: MacOS X',
                 'Operating System :: Unix',
                 'Operating System :: POSIX :: Linux',

                 'Programming Language :: Python :: 3.8',
                 'Programming Language :: Python :: 3.9',
                 'Programming Language :: Python :: 3.10',

                 'Topic :: Scientific/Engineering',
                 ],
    license='LGPLv3',
    install_requires=windows_dep if sys.platform == 'win32' else unix_dep,
    python_requires='>=3.8, <3.11',
    zip_safe=False
)
