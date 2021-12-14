# -*- coding: utf-8 -*-

"""
Acquire a spectrum using Winspec through the COM interface.
This program gets the data from WinSpec, saves them and
gets the data for plotting.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

This file is part of qudi-iqo-modules.

Qudi is free software: you can redistribute it and/or modify it under the terms of
the GNU Lesser General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version.

Qudi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with qudi.
If not, see <https://www.gnu.org/licenses/>.
"""

from qudi.interface.spectrometer_interface import SpectrometerInterface
import numpy as np
import comtypes.client as ctc
import win32com.client as w32c
from ctypes import c_float
import time

import datetime

ctc.GetModule(('{1A762221-D8BA-11CF-AFC2-508201C10000}', 3, 11))
import comtypes.gen.WINX32Lib as WinSpecLib


class WinSpec32(SpectrometerInterface):
    """ Hardware module for reading spectra from the WinSpec32 spectrometer software.

    Example config for copy-paste:

    spectrometer_dummy:
        module.Class: 'spectrometer.winspec_spectrometer.WinSpec32'

    """

    time_units = {
        1: {'name': 'us', 'factor': 1e-6},
        2: {'name': 'ms', 'factor': 1e-3},
        3: {'name': 's', 'factor': 1.},
        4: {'name': 'min', 'factor': 60}
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._status = 0

        self.query_time = 0.01

    def on_activate(self):
        """ Activate module.
        """
        w32c.pythoncom.CoInitialize()

    def on_deactivate(self):
        """ Deactivate module.
        """
        pass

    def check_status(self, method='unknown'):
        if self.status:
            raise RuntimeError(f'Status while method {method}: {self.status}')

    @property
    def experiment_running(self):
        w32c.pythoncom.CoInitialize()
        experiment_instance = w32c.Dispatch("WinX32.ExpSetup")
        result, self._status = experiment_instance.GetParam(WinSpecLib.EXP_RUNNING)
        self.check_status('experiment_running')
        return result

    @property
    def status(self):
        return self._status

    @property
    def exposure_time(self):
        w32c.pythoncom.CoInitialize()
        experiment_instance = w32c.Dispatch("WinX32.ExpSetup")
        exposure_time, self._status = experiment_instance.GetParam(WinSpecLib.EXP_EXPOSURETIME)
        self.check_status('exposure_time')
        units, self._status = experiment_instance.GetParam(WinSpecLib.EXP_EXPOSURETIME_UNITS)
        self.check_status('exposure_time units')
        exposure_time *= self.time_units[units]['factor']
        return exposure_time

    @exposure_time.setter
    def exposure_time(self, value):
        assert isinstance(value, (float, int)), f'exposure_time needs to be float, but was {value}'
        w32c.pythoncom.CoInitialize()
        experiment_instance = w32c.Dispatch("WinX32.ExpSetup")
        value = abs(float(value))
        if value < 1e-3:
            unit = 1
            value *= 1e6
        elif value < 1:
            unit = 2
            value *= 1e3
        else:
            unit = 3

        experiment_instance.SetParam(WinSpecLib.EXP_EXPOSURETIME_UNITS, unit)
        self.check_status(f'exposure_time setting unit to {unit} ({self.time_units[unit]["name"]})')

        self._status = experiment_instance.SetParam(WinSpecLib.EXP_EXPOSURETIME, value)
        self.check_status(f'exposure_time setting to {value}')

    def record_spectrum(self):
        """ Record spectrum from WinSpec32 software.

            @return []: spectrum data
        """
        w32c.pythoncom.CoInitialize()
        # get some data structures from COM that we need later
        current_spectrum = w32c.Dispatch("WinX32.DocFile")
        spectrum_library_instance = w32c.Dispatch("WinX32.DocFiles")
        experiment_instance = w32c.Dispatch("WinX32.ExpSetup")

        # Close all documents so we do not get any errors or prompts to save the currently opened spectrum in WinSpec32
        spectrum_library_instance.CloseAll()

        if experiment_instance.Start(self.WinspecDoc)[0]:
            # start the experiment
            # Wait for acquisition to finish (and check for errors continually)
            # If we didn't care about errors, we could just run WinspecExpt.WaitForExperiment()

            while self.experiment_running and self.status == 0:
                time.sleep(self.query_time)

            """
                Pass a pointer to Winspec so it can put the spectrum in a place in
                memory where python will be able to find it.
            """

            datapointer = c_float()
            raw_spectrum = current_spectrum.GetFrame(1, datapointer)
            # winspec uses 16 bit unsigned int. Make sure to consider that while converting to numpy arrays
            spectrum = np.array(raw_spectrum, dtype=np.uint16).flatten()
            specdata = np.empty((2, len(spectrum)), dtype=np.double)
            specdata[1] = spectrum
            calibration = current_spectrum.GetCalibration()

            if calibration.Order != 2:
                raise ValueError('Cannot handle current WinSpec wavelength calibration.')
            """
                WinSpec doesn't actually store the wavelength information as an array but
                instead calculates it every time you plot using the calibration information
                stored with the spectrum.
            """
            p = np.array([
                calibration.PolyCoeffs(2),
                calibration.PolyCoeffs(1),
                calibration.PolyCoeffs(0)
            ])
            specdata[0] = np.polyval(p, range(1, 1 + len(spectrum))) * 1e-9  # Send to logic in SI units (m)
            return specdata

        else:
            self.log.error("Could not initiate acquisition.")
            return None

    def save_spectrum(self, path, postfix=''):
        """ Save spectrum from WinSpec32 software.

            @param str path: path to save origial spectrum
            @param str postfix: file posfix
        """
        w32c.pythoncom.CoInitialize()
        current_spectrum = w32c.Dispatch("WinX32.DocFile")

        savetime = datetime.datetime.now()
        timestr = savetime.strftime("%Y%m%d-%H%M-%S-%f_")
        current_spectrum.SetParam(
            WinSpecLib.DM_FILENAME,
            str(path) + timestr + str(postfix) + ".spe"
        )
        file_name, self._status = self.WinspecDoc.GetParam(WinSpecLib.DM_FILENAME)
        self.check_status('save_spectrum set filename')
        self.log.debug(file_name)
        current_spectrum.Save()
