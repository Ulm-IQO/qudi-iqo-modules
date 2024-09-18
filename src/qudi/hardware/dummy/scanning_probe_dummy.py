# -*- coding: utf-8 -*-
"""
This file contains the Qudi dummy module for the confocal scanner.

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

import time
from typing import Optional, Dict, Tuple, Any, List
import numpy as np
from PySide2 import QtCore
from fysom import FysomError
from qudi.core.configoption import ConfigOption
from qudi.util.mutex import RecursiveMutex
from qudi.util.constraints import ScalarConstraint
from qudi.interface.scanning_probe_interface import (
    ScanningProbeInterface, ScanData, ScanConstraints, ScannerAxis, ScannerChannel,
    ScanSettings, BackScanCapability, CoordinateTransformMixin
)


class ImageGenerator:
    """Generate 1D and 2D images with random Gaussian spots."""
    def __init__(self,
                 position_ranges: Dict[str, List[float]],
                 spot_density: float,
                 spot_size_dist: List[float],
                 spot_amplitude_dist: List[float],
                 spot_depth_range: List[float],  # currently unused
                 ) -> None:
        self.position_ranges = position_ranges
        self.spot_density = spot_density
        self.spot_size_dist = tuple(spot_size_dist)
        self.spot_amplitude_dist = tuple(spot_amplitude_dist)

        # random spots for each 2D axes pair
        self._spots: Dict[Tuple[str, str], Any] = {}
        self.randomize_new_spots()

    def randomize_new_spots(self):
        """Create a random set of Gaussian 2D peaks."""
        self._spots = dict()
        for x_axis, x_range in self.position_ranges.items():
            for y_axis, y_range in self.position_ranges.items():
                if x_axis == y_axis:
                    continue
                x_min, x_max = min(x_range), max(x_range)
                y_min, y_max = min(y_range), max(y_range)
                spot_count = int(round((x_max - x_min) * (y_max - y_min) * self.spot_density))

                # Fill in random spot information
                spot_dict = dict()
                # total number of spots
                spot_dict['count'] = spot_count
                # spot positions as (x, y) tuples
                spot_dict['pos'] = np.empty((spot_count, 2))
                spot_dict['pos'][:, 0] = np.random.uniform(x_min, x_max, spot_count)
                spot_dict['pos'][:, 1] = np.random.uniform(y_min, y_max, spot_count)
                # spot sizes as (sigma_x, sigma_y) tuples
                spot_dict['sigma'] = np.random.normal(
                    self.spot_size_dist[0], self.spot_size_dist[1], (spot_count, 2))
                # spot amplitudes
                spot_dict['amp'] = np.random.normal(
                    self.spot_amplitude_dist[0], self.spot_amplitude_dist[1], spot_count)
                # spot angle
                spot_dict['theta'] = np.random.uniform(0, np.pi, spot_count)

                # Add information to _spots dict
                self._spots[(x_axis, y_axis)] = spot_dict

    def generate_2d_image(self, position_vectors: Dict[str, np.ndarray]) -> np.ndarray:
        scan_axes = tuple(position_vectors.keys())
        sim_data = self._spots[scan_axes]
        number_of_spots = sim_data['count']
        positions = sim_data['pos']
        amplitudes = sim_data['amp']
        sigmas = sim_data['sigma']
        thetas = sim_data['theta']

        x_values = position_vectors[scan_axes[0]]
        y_values = position_vectors[scan_axes[1]]
        xy_grid = np.meshgrid(x_values, y_values, indexing='ij')

        include_dist = self.spot_size_dist[0] + 5 * self.spot_size_dist[1]
        scan_image = np.random.uniform(0, 2e4, (x_values.size, y_values.size))
        for i in range(number_of_spots):
            if positions[i][0] < x_values.min() - include_dist:
                continue
            if positions[i][0] > x_values.max() + include_dist:
                continue
            if positions[i][1] < y_values.min() - include_dist:
                continue
            if positions[i][1] > y_values.max() + include_dist:
                continue
            gauss = self._gaussian_2d(xy_grid,
                                      amp=amplitudes[i],
                                      pos=positions[i],
                                      sigma=sigmas[i],
                                      theta=thetas[i])

            scan_image += gauss
        return scan_image

    @staticmethod
    def _gaussian_2d(xy, amp, pos, sigma, theta=0, offset=0):
        x, y = xy
        sigx, sigy = sigma
        x0, y0 = pos
        a = np.cos(-theta) ** 2 / (2 * sigx ** 2) + np.sin(-theta) ** 2 / (2 * sigy ** 2)
        b = np.sin(2 * -theta) / (4 * sigy ** 2) - np.sin(2 * -theta) / (4 * sigx ** 2)
        c = np.sin(-theta) ** 2 / (2 * sigx ** 2) + np.cos(-theta) ** 2 / (2 * sigy ** 2)
        x_prime = x - x0
        y_prime = y - y0
        return offset + amp * np.exp(
            -(a * x_prime ** 2 + 2 * b * x_prime * y_prime + c * y_prime ** 2))


class ScanningProbeDummyBare(ScanningProbeInterface):
    """
    Dummy scanning probe microscope. Produces a picture with several gaussian spots.

    Example config for copy-paste:

    scanning_probe_dummy:
        module.Class: 'dummy.scanning_probe_dummy.ScanningProbeDummy'
        options:
            spot_density: 4e6           # in 1/m², optional
            position_ranges:
                x: [0, 200e-6]
                y: [0, 200e-6]
                z: [-100e-6, 100e-6]
            frequency_ranges:
                x: [1, 5000]
                y: [1, 5000]
                z: [1, 1000]
            resolution_ranges:
                x: [1, 10000]
                y: [1, 10000]
                z: [2, 1000]
            position_accuracy:
                x: 10e-9
                y: 10e-9
                z: 50e-9
            # back scan capability
            back_scan_available: True
            back_scan_frequency_configurable: True
            back_scan_resolution_configurable: True
    """
    _threaded = True

    # config options
    _position_ranges: Dict[str, List[float]] = ConfigOption(name='position_ranges', missing='error')
    _frequency_ranges: Dict[str, List[float]] = ConfigOption(name='frequency_ranges', missing='error')
    _resolution_ranges: Dict[str, List[float]] = ConfigOption(name='resolution_ranges', missing='error')
    _position_accuracy: Dict[str, float] = ConfigOption(name='position_accuracy', missing='error')
    _spot_density: float = ConfigOption(name='spot_density', default=1e12/8)  # in 1/m²
    _spot_depth_range: List[float] = ConfigOption(name='spot_depth_range', default=(-500e-9, 500e-9))
    _spot_size_dist: List[float] = ConfigOption(name='spot_size_dist', default=(100e-9, 15e-9))
    _spot_amplitude_dist: List[float] = ConfigOption(name='spot_amplitude_dist', default=(2e5, 4e4))
    _require_square_pixels: bool = ConfigOption(name='require_square_pixels', default=False)
    _back_scan_available: bool = ConfigOption(name='back_scan_available', default=True)
    _back_scan_frequency_configurable: bool = ConfigOption(name='back_scan_frequency_configurable', default=True)
    _back_scan_resolution_configurable: bool = ConfigOption(name='back_scan_resolution_configurable', default=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Scan process parameters
        self._scan_settings: Optional[ScanSettings] = None
        self._back_scan_settings: Optional[ScanSettings] = None
        self._current_position = dict()
        self._scan_image = None
        self._back_scan_image = None
        self._scan_data = None
        self._back_scan_data = None

        self._image_generator: Optional[ImageGenerator] = None
        # "Hardware" constraints
        self._constraints: Optional[ScanConstraints] = None
        # Mutex for access serialization
        self._thread_lock = RecursiveMutex()

        self.__scan_start = 0
        self.__last_forward_pixel = 0
        self.__last_backward_pixel = 0
        self.__update_timer = None

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        # Generate static constraints
        axes = list()
        for axis, ax_range in self._position_ranges.items():
            dist = max(ax_range) - min(ax_range)
            resolution_range = tuple(self._resolution_ranges[axis])
            res_default = min(resolution_range[1], 100)
            frequency_range = tuple(self._frequency_ranges[axis])
            f_default = min(frequency_range[1], 1e3)

            position = ScalarConstraint(default=min(ax_range), bounds=tuple(ax_range))
            resolution = ScalarConstraint(default=res_default, bounds=resolution_range, enforce_int=True)
            frequency = ScalarConstraint(default=f_default, bounds=frequency_range)
            step = ScalarConstraint(default=0, bounds=(0, dist))
            axes.append(ScannerAxis(name=axis,
                                    unit='m',
                                    position=position,
                                    step=step,
                                    resolution=resolution,
                                    frequency=frequency, ))
        channels = [ScannerChannel(name='fluorescence', unit='c/s', dtype='float64'),
                    ScannerChannel(name='APD events', unit='count', dtype='float64')]

        if not self._back_scan_available:
            back_scan_capability = BackScanCapability(0)
        else:
            back_scan_capability = BackScanCapability.AVAILABLE
            if self._back_scan_resolution_configurable:
                back_scan_capability = back_scan_capability | BackScanCapability.RESOLUTION_CONFIGURABLE
            if self._back_scan_frequency_configurable:
                back_scan_capability = back_scan_capability | BackScanCapability.FREQUENCY_CONFIGURABLE

        self._constraints = ScanConstraints(
            axis_objects=tuple(axes),
            channel_objects=tuple(channels),
            back_scan_capability=back_scan_capability,
            has_position_feedback=False,
            square_px_only=False
        )

        # Set default process values
        self._current_position = {ax.name: np.mean(ax.position.bounds) for ax in self.constraints.axes.values()}
        self._scan_image = None
        self._back_scan_image = None
        self._scan_data = None
        self._back_scan_data = None

        # Create fixed maps of spots for each scan axes configuration
        self._image_generator = ImageGenerator(
            self._position_ranges,
            self._spot_density,
            self._spot_size_dist,
            self._spot_amplitude_dist,
            self._spot_depth_range,
        )

        self.__scan_start = 0
        self.__last_forward_pixel = 0
        self.__last_backward_pixel = 0
        self.__update_timer = QtCore.QTimer()
        self.__update_timer.setSingleShot(True)
        self.__update_timer.timeout.connect(self._handle_timer, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        """ Deactivate properly the confocal scanner dummy.
        """
        self.reset()
        # free memory
        del self._image_generator
        self._scan_image = None
        self._back_scan_image = None
        try:
            self.__update_timer.stop()
        except:
            pass
        self.__update_timer.timeout.disconnect()

    @property
    def scan_settings(self) -> Optional[ScanSettings]:
        """ Property returning all parameters needed for a 1D or 2D scan. Returns None if not configured.
        """
        with self._thread_lock:
            return self._scan_settings

    @property
    def back_scan_settings(self) -> Optional[ScanSettings]:
        with self._thread_lock:
            return self._back_scan_settings

    def reset(self):
        """ Hard reset of the hardware.
        """
        with self._thread_lock:
            if self.module_state() == 'locked':
                self.module_state.unlock()
            self.log.debug('Scanning probe dummy has been reset.')

    @property
    def constraints(self) -> ScanConstraints:
        """ Read-only property returning the constraints of this scanning probe hardware.
        """
        # self.log.debug('Scanning probe dummy "get_constraints" called.')
        return self._constraints

    def configure_scan(self, settings: ScanSettings) -> None:
        """ Configure the hardware with all parameters needed for a 1D or 2D scan.
        Raise an exception if the settings are invalid and do not comply with the hardware constraints.

        @param ScanSettings settings: ScanSettings instance holding all parameters
        """
        with self._thread_lock:
            self.log.debug('Scanning probe dummy "configure_scan" called.')
            # Sanity checking
            if self.module_state() != 'idle':
                raise RuntimeError('Unable to configure scan parameters while scan is running. '
                                   'Stop scanning and try again.')

            # check settings - will raise appropriate exceptions if something is not right
            self.constraints.check_settings(settings)

            self._scan_settings = settings
            # reset back scan configuration
            self._back_scan_settings = None

    def configure_back_scan(self, settings: ScanSettings) -> None:
        """ Configure the hardware with all parameters of the backwards scan.
        Raise an exception if the settings are invalid and do not comply with the hardware constraints.

        @param ScanSettings settings: ScanSettings instance holding all parameters for the back scan
        """
        with self._thread_lock:
            if self.module_state() != 'idle':
                raise RuntimeError('Unable to configure scan parameters while scan is running. '
                                   'Stop scanning and try again.')
            if self._scan_settings is None:
                raise RuntimeError('Configure forward scan settings first.')

            # check settings - will raise appropriate exceptions if something is not right
            self.constraints.check_back_scan_settings(
                backward_settings=settings,
                forward_settings=self._scan_settings
            )
            self._back_scan_settings = settings

    def move_absolute(self, position, velocity=None, blocking=False):
        """ Move the scanning probe to an absolute position as fast as possible or with a defined
        velocity.

        Log error and return current target position if something fails or a 1D/2D scan is in
        progress.
        """
        with self._thread_lock:
            # self.log.debug('Scanning probe dummy "move_absolute" called.')
            if self.module_state() != 'idle':
                raise RuntimeError('Scanning in progress. Unable to move to position.')
            elif not set(position).issubset(self._position_ranges):
                raise ValueError(f'Invalid axes encountered in position dict. '
                                 f'Valid axes are: {set(self._position_ranges)}')
            else:
                move_distance = {ax: np.abs(pos - self._current_position[ax]) for ax, pos in
                                 position.items()}
                if velocity is None:
                    move_time = 0.01
                else:
                    move_time = max(0.01, np.sqrt(
                        np.sum(dist ** 2 for dist in move_distance.values())) / velocity)
                time.sleep(move_time)
                self._current_position.update(position)
            return self._current_position

    def move_relative(self, distance, velocity=None, blocking=False):
        """ Move the scanning probe by a relative distance from the current target position as fast
        as possible or with a defined velocity.

        Log error and return current target position if something fails or a 1D/2D scan is in
        progress.
        """
        with self._thread_lock:
            self.log.debug('Scanning probe dummy "move_relative" called.')
            if self.module_state() != 'idle':
                raise RuntimeError('Scanning in progress. Unable to move relative.')
            elif not set(distance).issubset(self._position_ranges):
                raise ValueError('Invalid axes encountered in distance dict. '
                                 f'Valid axes are: {set(self._position_ranges)}')
            else:
                new_pos = {ax: self._current_position[ax] + dist for ax, dist in distance.items()}
                if velocity is None:
                    move_time = 0.01
                else:
                    move_time = max(0.01, np.sqrt(
                        np.sum(dist ** 2 for dist in distance.values())) / velocity)
                time.sleep(move_time)
                self._current_position.update(new_pos)
            return self._current_position

    def get_target(self):
        """ Get the current target position of the scanner hardware.

        @return dict: current target position per axis.
        """
        with self._thread_lock:
            self.log.debug('Scanning probe dummy "get_target" called.')
            return self._current_position.copy()

    def get_position(self):
        """ Get a snapshot of the actual scanner position (i.e. from position feedback sensors).

        @return dict: current target position per axis.
        """
        with self._thread_lock:
            self.log.debug('Scanning probe dummy "get_position" called.')
            position = {ax: pos + np.random.normal(0, self._position_accuracy[ax]) for ax, pos in
                        self._current_position.items()}
            return position

    def start_scan(self):
        """Start a scan as configured beforehand.
        Log an error if something fails or a 1D/2D scan is in progress.
        """
        with self._thread_lock:
            self.log.debug('Scanning probe dummy "start_scan" called.')
            if self.module_state() != 'idle':
                raise RuntimeError('Cannot start scan. Scan already in progress.')
            if not self.scan_settings:
                raise RuntimeError('No scan settings configured. Cannot start scan.')
            self.module_state.lock()

            position_vectors_all_axes = self._init_position_vectors()
            position_vectors = {ax: position_vectors_all_axes[ax] for ax in self.scan_settings.axes}
            if self.scan_settings.scan_dimension == 1:
                # ImageGenerator only supports 2D scans at this point
                axis = self.scan_settings.axes[0]
                if axis == 'x':
                    second_axis = 'y'
                elif axis == 'y':
                    second_axis = 'z'
                elif axis == 'z':
                    second_axis = 'x'
                position_vectors[second_axis] = position_vectors_all_axes[second_axis]

            self._scan_image = self._image_generator.generate_2d_image(position_vectors)
            self._scan_data = ScanData.from_constraints(
                settings=self.scan_settings,
                constraints=self.constraints,
                scanner_target_at_start=self.get_target(),
            )
            self._scan_data.new_scan()

            if self._back_scan_settings is not None:
                self._back_scan_image = self._image_generator.generate_2d_image(position_vectors)
                self._back_scan_data = ScanData.from_constraints(
                    settings=self.back_scan_settings,
                    constraints=self.constraints,
                    scanner_target_at_start=self.get_target(),
                )
                self._back_scan_data.new_scan()

            self.__scan_start = time.time()
            self.__last_forward_pixel = 0
            self.__last_backward_pixel = 0
            line_time = self.scan_settings.resolution[0] / self.scan_settings.frequency
            timer_interval_ms = int(0.5 * line_time * 1000)  # update twice every line
            self.__update_timer.setInterval(timer_interval_ms)
            self.__start_timer()

    def stop_scan(self):
        """Stop the currently running scan.
        Log an error if something fails or no 1D/2D scan is in progress.
        """
        with self._thread_lock:
            self.log.debug('Scanning probe dummy "stop_scan" called.')
            if self.module_state() == 'locked':
                self._scan_image = None
                self._back_scan_image = None
                self.__stop_timer()
                self.module_state.unlock()
            else:
                raise RuntimeError('No scan in progress. Cannot stop scan.')

    def emergency_stop(self):
        """
        """
        try:
            self.module_state.unlock()
        except FysomError:
            pass
        self._scan_image = None
        self._back_scan_image = None
        self.log.warning('Scanner has been emergency stopped.')

    def _handle_timer(self):
        """Update during a running scan."""
        try:
            with self._thread_lock:
                self.__update_scan_data()
        except Exception as e:
            self.log.error("Could not update scan data.", exc_info=e)

    def __update_scan_data(self) -> None:
        """Update scan data."""
        if self.module_state() == 'idle':
            raise RuntimeError("Scan is not running.")

        t_elapsed = time.time() - self.__scan_start
        t_forward = self.scan_settings.resolution[0] / self.scan_settings.frequency
        if self.back_scan_settings is not None:
            back_resolution = self.back_scan_settings.resolution[0]
            t_backward = back_resolution / self.back_scan_settings.frequency
        else:
            back_resolution = 0
            t_backward = 0
        t_complete_line = t_forward + t_backward

        aq_lines = int(t_elapsed / t_complete_line)
        t_current_line = t_elapsed % t_complete_line
        if t_current_line < t_forward:
            # currently in forwards scan
            aq_px_backward = back_resolution * aq_lines
            aq_lines_forward = aq_lines + (t_current_line / t_forward)
            aq_px_forward = int(self.scan_settings.resolution[0] * aq_lines_forward)
        else:
            # currently in backwards scan
            aq_px_forward = self.scan_settings.resolution[0] * (aq_lines + 1)
            aq_lines_backward = aq_lines + (t_current_line - t_forward) / t_backward
            aq_px_backward = int(back_resolution * aq_lines_backward)

        # transposing the arrays is necessary to fill along the fast axis first
        new_forward_data = self._scan_image.T.flat[self.__last_forward_pixel:aq_px_forward]
        for ch in self.constraints.channels:
            self._scan_data.data[ch].T.flat[self.__last_forward_pixel:aq_px_forward] = new_forward_data
        self.__last_forward_pixel = aq_px_forward

        # back scan image is not fully accurate: last line is filled the same direction as the forward axis
        if self._back_scan_settings is not None:
            new_backward_data = self._back_scan_image.T.flat[self.__last_backward_pixel:aq_px_backward]
            for ch in self.constraints.channels:
                self._back_scan_data.data[ch].T.flat[self.__last_backward_pixel:aq_px_backward] = new_backward_data
            self.__last_backward_pixel = aq_px_backward

        if self.scan_settings.scan_dimension == 1:
            is_finished = aq_lines > 1
        else:
            is_finished = aq_lines > self.scan_settings.resolution[1]
        if is_finished:
            self.module_state.unlock()
            self.log.debug("Scan finished.")
        else:
            self.__start_timer()

    def get_scan_data(self) -> Optional[ScanData]:
        """ Retrieve the ScanData instance used in the scan.
        """
        with self._thread_lock:
            if self._scan_data is None:
                return None
            else:
                return self._scan_data.copy()

    def get_back_scan_data(self) -> Optional[ScanData]:
        """ Retrieve the ScanData instance used in the backwards scan.
        """
        with self._thread_lock:
            if self._back_scan_data is None:
                return None
            return self._back_scan_data.copy()

    def __start_timer(self):
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__update_timer,
                                            'start',
                                            QtCore.Qt.BlockingQueuedConnection)
        else:
            self.__update_timer.start()

    def __stop_timer(self):
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__update_timer,
                                            'stop',
                                            QtCore.Qt.BlockingQueuedConnection)
        else:
            self.__update_timer.stop()

    def _init_position_vectors(self) -> Dict[str, np.ndarray]:
        position_vectors = {}
        x_axis = self.scan_settings.axes[0]
        x_values = np.linspace(self.scan_settings.range[0][0],
                               self.scan_settings.range[0][1],
                               self.scan_settings.resolution[0])
        position_vectors[x_axis] = x_values
        if self.scan_settings.scan_dimension == 2:
            y_axis = self.scan_settings.axes[1]
            y_values = np.linspace(self.scan_settings.range[1][0],
                                   self.scan_settings.range[1][1],
                                   self.scan_settings.resolution[1])
            position_vectors[y_axis] = y_values
        return self._expand_coordinate(position_vectors)


class ScanningProbeDummy(CoordinateTransformMixin, ScanningProbeDummyBare):
    def _init_position_vectors(self) -> Dict[str, np.ndarray]:
        position_vectors = super()._init_position_vectors()
        position_vectors_tilted = self.coordinate_transform(position_vectors)
        return position_vectors_tilted
