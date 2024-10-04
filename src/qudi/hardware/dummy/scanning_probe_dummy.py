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

from logging import getLogger
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

logger = getLogger(__name__)

class ImageGenerator:
    """Generate 1D and 2D images with random Gaussian spots."""
    def __init__(self,
                 position_ranges: Dict[str, List[float]],
                 spot_density: float,
                 spot_size_dist: List[float],
                 spot_amplitude_dist: List[float],
                 spot_view_distance_factor: float,  # currently unused
                 ) -> None:
        self.position_ranges = position_ranges
        self.spot_density = spot_density
        self.spot_size_dist = tuple(spot_size_dist)
        self.spot_amplitude_dist = tuple(spot_amplitude_dist)
        self.spot_view_distance_factor = spot_view_distance_factor

        # random spots for each 2D axes pair
        self._spots: Dict[Tuple[str, str], Any] = {}
        self.randomize_new_spots()

    def randomize_new_spots(self):
        """Create a random set of Gaussian 2D peaks."""
        self._spots = dict()
        number_of_axes = len(list(self.position_ranges.keys()))
        axis_lengths = [abs(value[1]-value[0]) for value in self.position_ranges.values()]
        volume = 0
        if len(axis_lengths) > 0:
            volume = 1
            for value in axis_lengths:
                volume *= value

        spot_count = int(round(volume * self.spot_density ** number_of_axes))
        # Have at least 1 spot
        if not spot_count:
            spot_count = 1

        spot_amplitudes = np.random.normal(
            self.spot_amplitude_dist[0],
            self.spot_amplitude_dist[1],
            spot_count
        )

        # scan bounds per axis.
        position_ranges = np.array(list(self.position_ranges.values()))
        ax_mins = position_ranges[:, 0]
        ax_maxs = position_ranges[:, 1]

        # vectorized generation of random spot positions and sigmas. Each row is a spot.
        spot_positions = np.random.uniform(ax_mins, ax_maxs, (spot_count, len(self.position_ranges)))
        spot_sigmas = np.random.normal(self.spot_size_dist[0], self.spot_size_dist[1],
                                       (spot_count, len(self.position_ranges)))

        # total number of spots
        self._spots['count'] = spot_count
        # spot positions as array with rows being the number of spot and columns being the position along axis
        self._spots['pos'] = spot_positions
        # spot sizes as array with rows being the number of spot and columns being the position along axis
        self._spots['sigma'] = spot_sigmas
        # spot amplitudes
        self._spots['amp'] = spot_amplitudes
        # spot angle
        self._spots['theta'] = np.random.uniform(0, np.pi, spot_count)
        logger.debug(f"Generated {spot_count} spots.")

    def generate_image(
        self,
        position_vectors: Dict[str, np.ndarray],
        current_position: Dict[str, float],
    ) -> np.ndarray:
        scan_values = tuple(position_vectors.values())
        sim_data = self._spots
        positions = sim_data["pos"]
        amplitudes = sim_data["amp"]
        sigmas = sim_data["sigma"]

        t_start = time.perf_counter()

        # convert axis string dicts to axis index dicts
        current_position_vector = self.convert_position_dict_to_array(current_position)
        position_vectors_indices = self.convert_axis_string_dict_to_axis_index_dict(
            position_vectors
        )
        # get only spot positions in detection volume
        include_dist = max(self.spot_size_dist) * self.spot_view_distance_factor

        scan_image = np.random.uniform(
            0,
            min(self.spot_amplitude_dist) * 0.2,
            tuple(value.size for value in scan_values),
        )

        grid_points = self._create_coordinates_for_calculation(
            position_vectors_indices, current_position_vector
        )

        points_in_detection_volume = positions[
            np.array(
                [
                    self.is_point_in_scan_volume(point, grid_points, include_dist)
                    for point in positions
                ]
            )
        ]

        logger.debug(f"{grid_points.shape=}")

        indices = np.array(
            [np.where(positions == point)[0][0] for point in points_in_detection_volume]
        )

        if len(indices) > 0:
            mus_visible = points_in_detection_volume
            sigmas_visible = sigmas[indices]
            amplitudes_visible = amplitudes[indices]

            gauss_1d_all = self._gaussian_n_dim(
                grid_points,
                mus=mus_visible,
                sigmas=sigmas_visible,
                amplitudes=amplitudes_visible,
            )

            new_dim = [
                len(position_vectors_indices[i])
                for i in sorted(position_vectors_indices.keys())
            ]
            gauss_2d_all = np.sum(gauss_1d_all.reshape((-1, *new_dim)), axis=0)

            scan_image += gauss_2d_all

        logger.debug(
            f"Image took {time.perf_counter()-t_start:.3f} s for {points_in_detection_volume.shape[0]=},\n"
            f" {points_in_detection_volume=}"
        )

        return scan_image

    def convert_position_dict_to_array(
        self, position_dict: Dict[str, float]
    ) -> np.ndarray:
        position_vector = np.zeros(len(tuple(self.position_ranges.keys())))
        for ii, axis in enumerate(self.position_ranges.keys()):
            position_vector[ii] = position_dict[axis]
        return position_vector

    def convert_axis_string_dict_to_axis_index_dict(
        self, position_vectors: Dict[str, np.ndarray]
    ) -> Dict[int, np.ndarray]:
        index_dict = {}
        for axis in position_vectors.keys():
            for index, check_axis in enumerate(self.position_ranges.keys()):
                if axis == check_axis:
                    index_dict[index] = position_vectors[axis]
        return index_dict

    def is_point_in_scan_volume(self, point: np.ndarray, scan_points:np.ndarray, include_dist: float):
        distances = np.linalg.norm(scan_points - point, axis=1)
        return np.any(distances <= include_dist)

    @staticmethod
    def _gaussian_n_dim(grid_points, mus, sigmas, amplitudes=None):
        """
        Calculate the Gaussian values for each point in an n-dimensional grid with a customizable amplitude.

        :param grid_points: A 1D numpy array representing the coordinates at which the gaussian should be evaluated (shape: (m, n).
        :param mu: A 1D numpy array representing the mean vector (shape: (n,)).
        :param sigma: A 1D numpy array representing the variance for each axis (shape: (n,)).
        :param amplitude: A scalar value representing the height of the Gaussian (default is 1.0).

        :return: A numpy array of Gaussian values evaluated at each point in grid_points.
        """
        if amplitudes is None:
            amplitudes = np.ones(mus.shape[0])

        # Reshape grid_points for broadcasting
        grid_points = grid_points[:, np.newaxis, :]  # shape: (m, 1, n)
        mus = mus[np.newaxis, :, :]  # shape: (1, k, n)
        sigmas = sigmas[np.newaxis, :, :]  # shape: (1, k, n)

        # Calculate the exponent for each Gaussian
        diff = grid_points - mus  # shape: (m, k, n)
        exponent = -0.5 * np.sum((diff / sigmas) ** 2, axis=2)  # shape: (m, k)

        # Calculate Gaussian values with amplitude
        gaussians = amplitudes[:, np.newaxis] * np.exp(exponent.T)  # shape: (k, m)

        return gaussians

    @staticmethod
    def _create_coordinates_for_calculation(axes_dict, currentpos) -> np.ndarray:
        """
        Calculate the coordinates for which the gaussian should be calculated, when scanning along axes in axes_dict and having a current position as currentpos.

        :param axes_dict: A dict of 1D arrays for which the gaussian should be calculated. keys: axis index, values: values for this axis.
        :param currentpos: A 1D numpy array representing the current position of the scanner (shape: (n,)).

        :return: A numpy array of coordinates to evaluate the Gaussian at.
        """
        # number of dimensions in space
        m = len(currentpos)

        axes_vectors = []
        # populate the grid with values from the axes.
        # All axes indices that are not in axes should be populated with the value of the current position
        for axis_index in range(m):
            if axis_index in axes_dict.keys():
                axes_vectors.append(axes_dict[axis_index])
                continue
            axes_vectors.append(
                np.array(
                    [
                        currentpos[axis_index],
                    ]
                )
            )

        # create meshgrid for all coordinates that should be probed
        mesh_grid = np.meshgrid(*axes_vectors, indexing="ij")

        # create all coordinates by flattening the grid
        return np.vstack([grid.ravel() for grid in mesh_grid]).T


class ScanningProbeDummyBare(ScanningProbeInterface):
    """
    Dummy scanning probe microscope. Produces a picture with several gaussian spots.

    Example config for copy-paste:

    scanning_probe_dummy:
        module.Class: 'dummy.scanning_probe_dummy.ScanningProbeDummy'
        options:
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
            # spot_density: 5e4 # optional
            # spot_view_distance_factor: 2 # optional
            # spot_size_dist: [400e-9, 100e-9] # optional
            # spot_amplitude_dist: [2e5, 4e4] # optional
            # require_square_pixels: False # optional
            # back_scan_available: True # optional
            # back_scan_frequency_configurable: True # optional
            # back_scan_resolution_configurable: True # optional
    """
    _threaded = True

    # config options
    _position_ranges: Dict[str, List[float]] = ConfigOption(name='position_ranges', missing='error')
    _frequency_ranges: Dict[str, List[float]] = ConfigOption(name='frequency_ranges', missing='error')
    _resolution_ranges: Dict[str, List[float]] = ConfigOption(name='resolution_ranges', missing='error')
    _position_accuracy: Dict[str, float] = ConfigOption(name='position_accuracy', missing='error')
    _spot_density: float = ConfigOption(name='spot_density', default=1e5)  # in 1/m
    _spot_view_distance_factor: List[float] = ConfigOption(
        name='spot_view_distance_factor',
        default=2
    ) # spots are visible by this factor times the maximum spot size from each scan point away
    _spot_size_dist: List[float] = ConfigOption(name='spot_size_dist', default=(400e-9, 100e-9))
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
            self._spot_view_distance_factor,
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

            position_vectors = self._init_position_vectors()

            self._scan_image = self._image_generator.generate_image(position_vectors, self.get_target())
            self._scan_data = ScanData.from_constraints(
                settings=self.scan_settings,
                constraints=self.constraints,
                scanner_target_at_start=self.get_target(),
            )
            self._scan_data.new_scan()

            if self._back_scan_settings is not None:
                self._back_scan_image = self._image_generator.generate_image(position_vectors, self.get_target())
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
            is_finished = aq_lines >= 1
        else:
            is_finished = aq_lines >= self.scan_settings.resolution[1]
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

    def _init_position_vectors_from_scan_settings(self) -> Dict[str, np.ndarray]:
        position_vectors = {axis: np.linspace(
            self.scan_settings.range[ii][0],
            self.scan_settings.range[ii][1],
            self.scan_settings.resolution[ii]) for ii, axis in enumerate(self.scan_settings.axes)}
        return position_vectors

    def _init_position_vectors(self) -> Dict[str, np.ndarray]:
        position_vectors = self._init_position_vectors_from_scan_settings()
        return position_vectors

    @_spot_density.constructor
    def spot_density_constructor(self, spot_density: float) -> float:
        volume_edges = [abs(pos_range[1] - pos_range[0]) for pos_range in self._position_ranges.values()]
        volume = 1
        for edge in volume_edges:
            volume *= edge
        if volume * spot_density ** len(self._position_ranges.keys()) > 80e3:
            spot_density = (80e3 / volume) ** (1 / len(self._position_ranges.keys()))
            self.log.warning(f'Specified spot density results in more than 80k spots. To keep performance, reducing spot density to {spot_density} 1/m^{len(self._position_ranges.keys())}')
        return spot_density


class ScanningProbeDummy(CoordinateTransformMixin, ScanningProbeDummyBare):
    def _init_position_vectors(self) -> Dict[str, np.ndarray]:
        position_vectors = super()._init_position_vectors()

        if self.coordinate_transform_enabled:
            position_vectors = self._expand_coordinate(position_vectors)
            return self.coordinate_transform(position_vectors)

        return position_vectors
