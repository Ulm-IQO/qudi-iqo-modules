# -*- coding: utf-8 -*-
"""
Contains a contextmanager that can be used to ensure exceptions thrown in code blocks running in
non-main QThreads are not silently ignored but instead logged using logging.Logger.exception before
being re-raised.

Copyright (c) 2024, the qudi developers. See the AUTHORS.md file at the top-level directory of this
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

__all__ = ['threaded_exception_watchdog']

from PySide2 import QtCore
from logging import Logger
from contextlib import contextmanager


@contextmanager
def threaded_exception_watchdog(logger: Logger) -> None:
    """ Contextmanager that needs a logging.Logger instance and can be wrapped around any code
    block running in a Qt application. If this code block raises any unhandled exceptions, it will
    check if the current thread is the main thread and log the exception before re-raising if this
    is not the case.
    This will ensure unhandled exceptions are not silently ignored in Qt non-main threads.
    """
    try:
        yield
    except Exception:
        qt_app = QtCore.QCoreApplication.instance()
        if (qt_app is not None) and (QtCore.QThread.currentThread() != qt_app.thread()):
            logger.exception('Unhandled exception:')
        raise
