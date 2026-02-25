# -*- coding: utf-8 -*-

"""
This file contains the Qudi file with all default sampling functions.

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

import numpy as np
from qudi.logic.pulsed.sampling_functions import SamplingBase


class Idle(SamplingBase):
    """
    Object representing an idle element (zero voltage)
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @staticmethod
    def get_samples(time_array):
        samples_arr = np.zeros(len(time_array))
        return samples_arr


class DC(SamplingBase):
    """
    Object representing an DC element (constant voltage)
    """
    params = dict()
    params['voltage'] = {'unit': 'V', 'init': 0.0, 'min': -np.inf, 'max': +np.inf, 'type': float}

    def __init__(self, voltage=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(DC.params)
        if voltage is None:
            self.voltage = self.params['voltage']['init']
        else:
            self.voltage = voltage
        return

    @staticmethod
    def _get_dc(time_array, voltage):
        samples_arr = np.zeros(len(time_array)) + voltage
        return samples_arr

    def get_samples(self, time_array):
        samples_arr = self._get_dc(time_array, self.voltage)
        return samples_arr


class SinBase(SamplingBase):
    """
    Class defining some base sine sampling functions
    """
    @staticmethod
    def _get_sine(time_array, amplitude, frequency, phase):
        samples_arr = amplitude * np.sin(2 * np.pi * frequency * time_array + phase)
        return samples_arr

    def _init_parameter(self, name: str, value: float):
        return value if value is not None else self.params[name]["init"]

    def _init_parameters(self, number_of_sines: int, variables_to_set: dict):
        for i in range(1, number_of_sines + 1):
            for name in ("amplitude", "frequency", "phase"):
                key = f"{name}_{i}" if number_of_sines > 1 else name
                setattr(self, key, self._init_parameter(key, variables_to_set[key]))

class Sin(SinBase):
    """
    Object representing a sine wave element
    """
    params = dict()
    params['amplitude'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase'] = {'unit': '°', 'init': 0.0, 'min': -np.inf, 'max': np.inf, 'type': float}

    def __init__(self, amplitude=None, frequency=None, phase=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(Sin.params)
        self._init_parameters(1, locals())

    def get_samples(self, time_array):
        phase_rad = np.pi * self.phase / 180
        samples_arr = self._get_sine(time_array, self.amplitude, self.frequency, phase_rad)
        return samples_arr


class DoubleSinSum(SinBase):
    """
    Object representing a double sine wave element (Superposition of two sine waves; NOT normalized)
    """
    params = dict()
    params['amplitude_1'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_1'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_1'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_2'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_2'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_2'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}

    def __init__(self,
                 amplitude_1=None, frequency_1=None, phase_1=None,
                 amplitude_2=None, frequency_2=None, phase_2=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(DoubleSinSum.params)
        self._init_parameters(2, locals())

    def get_samples(self, time_array):
        # First sine wave
        phase_rad = np.pi * self.phase_1 / 180
        samples_arr = self._get_sine(time_array, self.amplitude_1, self.frequency_1, phase_rad)

        # Second sine wave (add on first sine)
        phase_rad = np.pi * self.phase_2 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_2, self.frequency_2, phase_rad)
        return samples_arr


class DoubleSinProduct(SinBase):
    """
    Object representing a double sine wave element (Product of two sine waves; NOT normalized)
    """
    params = dict()
    params['amplitude_1'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_1'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_1'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_2'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_2'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_2'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}

    def __init__(self,
                 amplitude_1=None, frequency_1=None, phase_1=None,
                 amplitude_2=None, frequency_2=None, phase_2=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(DoubleSinProduct.params)
        self._init_parameters(2, locals())

    def get_samples(self, time_array):
        # First sine wave
        phase_rad = np.pi * self.phase_1 / 180
        samples_arr = self._get_sine(time_array, self.amplitude_1, self.frequency_1, phase_rad)

        # Second sine wave (add on first sine)
        phase_rad = np.pi * self.phase_2 / 180
        samples_arr *= self._get_sine(time_array, self.amplitude_2, self.frequency_2, phase_rad)
        return samples_arr


class TripleSinSum(SinBase):
    """
    Object representing a linear combination of three sines
    (Superposition of three sine waves; NOT normalized)
    """
    params = dict()
    params['amplitude_1'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_1'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_1'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_2'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_2'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_2'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_3'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_3'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_3'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}

    def __init__(self,
                 amplitude_1=None, frequency_1=None, phase_1=None,
                 amplitude_2=None, frequency_2=None, phase_2=None,
                 amplitude_3=None, frequency_3=None, phase_3=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(TripleSinSum.params)
        self._init_parameters(3, locals())

    def get_samples(self, time_array):
        # First sine wave
        phase_rad = np.pi * self.phase_1 / 180
        samples_arr = self._get_sine(time_array, self.amplitude_1, self.frequency_1, phase_rad)

        # Second sine wave (add on first sine)
        phase_rad = np.pi * self.phase_2 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_2, self.frequency_2, phase_rad)

        # Second sine wave (add on sum of first and second)
        phase_rad = np.pi * self.phase_3 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_3, self.frequency_3, phase_rad)
        return samples_arr


class TripleSinProduct(SinBase):
    """
    Object representing a wave element composed of the product of three sines
    (Product of three sine waves; NOT normalized)
    """
    params = dict()
    params['amplitude_1'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_1'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_1'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_2'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_2'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_2'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_3'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_3'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_3'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}

    def __init__(self,
                 amplitude_1=None, frequency_1=None, phase_1=None,
                 amplitude_2=None, frequency_2=None, phase_2=None,
                 amplitude_3=None, frequency_3=None, phase_3=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(TripleSinProduct.params)
        self._init_parameters(3, locals())

    def get_samples(self, time_array):
        # First sine wave
        phase_rad = np.pi * self.phase_1 / 180
        samples_arr = self._get_sine(time_array, self.amplitude_1, self.frequency_1, phase_rad)

        # Second sine wave (add on first sine)
        phase_rad = np.pi * self.phase_2 / 180
        samples_arr *= self._get_sine(time_array, self.amplitude_2, self.frequency_2, phase_rad)

        # Second sine wave (add on sum of first and second)
        phase_rad = np.pi * self.phase_3 / 180
        samples_arr *= self._get_sine(time_array, self.amplitude_3, self.frequency_3, phase_rad)
        return samples_arr

class QuintupleSinSum(SinBase):
    """
    Object representing a linear combination of five sines
    (Superposition of five sine waves; NOT normalized)
    """
    params = dict()
    params['amplitude_1'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_1'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_1'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_2'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_2'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_2'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_3'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_3'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_3'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_4'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_4'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_4'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_5'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_5'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_5'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}

    def __init__(self,
                 amplitude_1=None, frequency_1=None, phase_1=None,
                 amplitude_2=None, frequency_2=None, phase_2=None,
                 amplitude_3=None, frequency_3=None, phase_3=None,
                 amplitude_4=None, frequency_4=None, phase_4=None,
                 amplitude_5=None, frequency_5=None, phase_5=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(QuintupleSinSum.params)
        self._init_parameters(5, locals())

    def get_samples(self, time_array):
        # First sine wave
        phase_rad = np.pi * self.phase_1 / 180
        samples_arr = self._get_sine(time_array, self.amplitude_1, self.frequency_1, phase_rad)

        # Second sine wave (add on first sine)
        phase_rad = np.pi * self.phase_2 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_2, self.frequency_2, phase_rad)

        # Third sine wave (add on sum of first and second)
        phase_rad = np.pi * self.phase_3 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_3, self.frequency_3, phase_rad)

        # Fourth sine wave (add on sum of first three)
        phase_rad = np.pi * self.phase_4 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_4, self.frequency_4, phase_rad)

        # Fifth sine wave (add on sum of first four)
        phase_rad = np.pi * self.phase_5 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_5, self.frequency_5, phase_rad)
        return samples_arr

class SextupleSinSum(SinBase):
    """
    Object representing a linear combination of six sines
    (Superposition of six sine waves; NOT normalized)
    """
    params = dict()
    params['amplitude_1'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_1'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_1'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_2'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_2'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_2'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_3'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_3'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_3'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_4'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_4'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_4'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_5'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_5'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_5'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['amplitude_6'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['frequency_6'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase_6'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}

    def __init__(self,
                 amplitude_1=None, frequency_1=None, phase_1=None,
                 amplitude_2=None, frequency_2=None, phase_2=None,
                 amplitude_3=None, frequency_3=None, phase_3=None,
                 amplitude_4=None, frequency_4=None, phase_4=None,
                 amplitude_5=None, frequency_5=None, phase_5=None,
                 amplitude_6=None, frequency_6=None, phase_6=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(SextupleSinSum.params)
        self._init_parameters(6, locals())

    def get_samples(self, time_array):
        # First sine wave
        phase_rad = np.pi * self.phase_1 / 180
        samples_arr = self._get_sine(time_array, self.amplitude_1, self.frequency_1, phase_rad)

        # Second sine wave (add on first sine)
        phase_rad = np.pi * self.phase_2 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_2, self.frequency_2, phase_rad)

        # Third sine wave (add on sum of first and second)
        phase_rad = np.pi * self.phase_3 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_3, self.frequency_3, phase_rad)

        # Fourth sine wave (add on sum of first three)
        phase_rad = np.pi * self.phase_4 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_4, self.frequency_4, phase_rad)

        # Fifth sine wave (add on sum of first four)
        phase_rad = np.pi * self.phase_5 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_5, self.frequency_5, phase_rad)

        # Sixth sine wave (add on sum of first five)
        phase_rad = np.pi * self.phase_6 / 180
        samples_arr += self._get_sine(time_array, self.amplitude_6, self.frequency_6, phase_rad)
        return samples_arr

class Chirp(SamplingBase):
    """
    Object representing a chirp element
    Landau-Zener-Stueckelberg-Majorana model with a constant amplitude and a linear chirp
    """
    params = dict()
    params['amplitude'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['start_freq'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf,
                                 'type': float}
    params['stop_freq'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf,
                                'type': float}

    def __init__(self, amplitude=None, phase=None, start_freq=None, stop_freq=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(Chirp.params)
        if amplitude is None:
            self.amplitude = self.params['amplitude']['init']
        else:
            self.amplitude = amplitude
        if phase is None:
            self.phase = self.params['phase']['init']
        else:
            self.phase = phase
        if start_freq is None:
            self.start_freq = self.params['start_freq']['init']
        else:
            self.start_freq = start_freq
        if stop_freq is None:
            self.stop_freq = self.params['stop_freq']['init']
        else:
            self.stop_freq = stop_freq
        return

    def get_samples(self, time_array):
        phase_rad = np.deg2rad(self.phase)
        freq_diff = self.stop_freq - self.start_freq
        time_diff = time_array[-1] - time_array[0]
        samples_arr = self.amplitude * np.sin(2 * np.pi * time_array * (
                    self.start_freq + freq_diff * (
                        time_array - time_array[0]) / time_diff / 2) + phase_rad)
        return samples_arr

class AllenEberlyChirp(SamplingBase):

    """
    The Allen-Eberly model involves a sech amplitude shape and a tanh shaped detuning
    It has very good properties in terms of adiabaticity and is often preferable to the standard
    Landau-Zener-Stueckelberg-Majorana model with a constant amplitude and a linear chirp (see class Chirp)
    More information about the Allen-Eberly model can be found in:
    L. Allen and J. H. Eberly, Optical Resonance and Two-Level Atoms Dover, New York, 1987,
    Analytical solution is given in: F. T. Hioe, Phys. Rev. A 30, 2100 (1984).
    """
    params = dict()
    params['amplitude'] = {'unit': 'V', 'init': 0.0, 'min': 0.0, 'max': np.inf, 'type': float}
    params['phase'] = {'unit': '°', 'init': 0.0, 'min': -360, 'max': 360, 'type': float}
    params['start_freq'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf,
                            'type': float}
    params['stop_freq'] = {'unit': 'Hz', 'init': 2.87e9, 'min': 0.0, 'max': np.inf,
                           'type': float}
    params['tau_pulse'] = {'unit': '', 'init': 0.1e-6, 'min': 0.0, 'max': np.inf,
                           'type': float}

    def __init__(self, amplitude=None, phase=None, start_freq=None, stop_freq=None, tau_pulse=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.params.update(AllenEberlyChirp.params)
        if amplitude is None:
            self.amplitude = self.params['amplitude']['init']
        else:
            self.amplitude = amplitude
        if phase is None:
            self.phase = self.params['phase']['init']
        else:
            self.phase = phase
        if start_freq is None:
            self.start_freq = self.params['start_freq']['init']
        else:
            self.start_freq = start_freq
        if stop_freq is None:
            self.stop_freq = self.params['stop_freq']['init']
        else:
            self.stop_freq = stop_freq
        if tau_pulse is None:
            self.tau_pulse = self.params['tau_pulse']['init']
        else:
            self.tau_pulse = tau_pulse
        return

    def get_samples(self, time_array):
        phase_rad = np.deg2rad(self.phase)  # initial phase
        freq_range_max = self.stop_freq - self.start_freq  # frequency range
        t_start = time_array[0]  # start time of the pulse
        pulse_duration = time_array[-1] - time_array[0]  # pulse duration
        freq_center = (self.stop_freq + self.start_freq) / 2  # central frequency
        tau_run = self.tau_pulse  # tau to use for the sample generation, tau_pulse = truncation_ratio * pulse_duration
        # tau_run characterizes the pulse shape, which is sech((t - mu)/tau_run) when mu is the center of the pulse
        # tau_run also characterizes the detuning shape, which is tanh((t - mu)/tau_run)
        # tau_run / pulse_duration = truncation ratio should ideally be 0.1 or <0.2. Higher values - worse truncation

        # conversion for AWG to actually output the specified voltage
        amp_conv = 2 * self.amplitude  # amplitude, corrected from parabola pulse estimation

        # define a sech function as it is not included in the numpy package
        def sech(current_time):
            return 1 / np.cosh(current_time)

        # define a function to calculate the Rabi frequency as a function of time
        def rabi_sech_envelope(current_time):
            return amp_conv * sech((current_time - t_start - (pulse_duration / 2)) / tau_run)

        # define a function to calculate the phase Phi(t) for the specific pulse parameters
        def phi_tanh_chirp(current_time):
            return (2 * np.pi * freq_range_max / 2) * tau_run * np.log(
                np.cosh((current_time - t_start - (pulse_duration / 2)) / tau_run) *
                sech(pulse_duration / (2 * tau_run)))

        # calculate the samples array
        samples_arr = rabi_sech_envelope(time_array) * \
                      np.cos(phase_rad + 2 * np.pi * freq_center * (time_array - t_start) +
                             phi_tanh_chirp(time_array))
        return samples_arr

# FIXME: Not implemented yet!
# class ImportedSamples(object):
#     """
#     Object representing an element of a pre-sampled waveform from file (as for optimal control).
#     """
#     params = dict()
#     params['import_path'] = {'unit': '', 'init': '', 'min': '', 'max': '', 'type': str}
#
#     def __init__(self, importpath):
#         self.importpath = importpath
#
#     def get_samples(self, time_array):
#         # TODO: Import samples from file
#         imported_samples = np.zeros(len(time_array))
#         samples_arr = imported_samples[:len(time_array)]
