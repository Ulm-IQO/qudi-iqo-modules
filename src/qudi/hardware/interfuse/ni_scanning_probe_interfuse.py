# -*- coding: utf-8 -*-

"""
Interfuse of Ni Finite Sampling IO and NI AO HardwareFiles to make a confocal scanner.


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
import time
from typing import Optional, Dict, List
from dataclasses import asdict

from PySide2 import QtCore
from PySide2.QtGui import QGuiApplication

from qudi.interface.scanning_probe_interface import ScanningProbeInterface, ScanConstraints, \
    ScannerAxis, ScannerChannel, ScanData, ScanSettings, CoordinateTransformMixin, BackScanCapability
from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector
from qudi.util.mutex import Mutex
from qudi.util.enums import SamplingOutputMode
from qudi.util.helpers import in_range
from qudi.util.constraints import ScalarConstraint


class NiScanningProbeInterfuseBare(ScanningProbeInterface):
    """
    This interfuse combines modules of a National Instrument device to make up a scanning probe hardware.
    One module for software timed analog output (NIXSeriesAnalogOutput) to position e.g. a scanner to a specific
    position and a hardware timed module for in and output (NIXSeriesFiniteSamplingIO) to realize 1D/2D scans.

    Example config for copy-paste:

    ni_scanning_probe:
        module.Class: 'interfuse.ni_scanning_probe_interfuse.NiScanningProbeInterfuse'
        # to use without tilt correction
        # module.Class: 'interfuse.ni_scanning_probe_interfuse.NiScanningProbeInterfuseBare'
        connect:
            scan_hardware: 'ni_finite_sampling_io'
            analog_output: 'ni_ao'
        options:
            ni_channel_mapping:
                x: 'ao0'
                y: 'ao1'
                z: 'ao2'
                APD1: 'PFI8'
                APD2: 'PFI9'
                AI0: 'ai0'
            position_ranges: # in m
                x: [-100e-6, 100e-6]
                y: [0, 200e-6]
                z: [-100e-6, 100e-6]
            frequency_ranges: #Aka values written/retrieved per second; Check with connected HW for sensible constraints.
                x: [1, 5000]
                y: [1, 5000]
                z: [1, 1000]
            resolution_ranges:
                x: [1, 10000]
                y: [1, 10000]
                z: [2, 1000]
            input_channel_units:
                APD1: 'c/s'
                APD2: 'c/s'
                AI0: 'V'
            move_velocity: 400e-6 #m/s; This speed is used for scanner movements and avoids jumps from position to position.
            default_backward_resolution: 50
    """
    _ni_finite_sampling_io = Connector(name='scan_hardware', interface='FiniteSamplingIOInterface')
    _ni_ao = Connector(name='analog_output', interface='ProcessSetpointInterface')

    _ni_channel_mapping: Dict[str, str] = ConfigOption(name='ni_channel_mapping', missing='error')
    _position_ranges: Dict[str, List[float]] = ConfigOption(name='position_ranges', missing='error')
    _frequency_ranges: Dict[str, List[float]] = ConfigOption(name='frequency_ranges', missing='error')
    _resolution_ranges: Dict[str, List[float]] = ConfigOption(name='resolution_ranges', missing='error')
    _input_channel_units: Dict[str, str] = ConfigOption(name='input_channel_units', missing='error')

    __max_move_velocity: float = ConfigOption(name='maximum_move_velocity', default=400e-6)
    __default_backward_resolution: int = ConfigOption(name='default_backward_resolution', default=50)

    _threaded = True  # Interfuse is by default not threaded.

    sigNextDataChunk = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._scan_data: Optional[ScanData] = None
        self._back_scan_data: Optional[ScanData] = None
        self.raw_data_container: Optional[RawDataContainer] = None

        self._constraints: Optional[ScanConstraints] = None

        self._target_pos = dict()
        self._stored_target_pos = dict()
        self._start_scan_after_cursor = False
        self._abort_cursor_move = False

        self.__ni_ao_write_timer = None
        self._min_step_interval = 1e-3
        self._scanner_distance_atol = 1e-9

        self._thread_lock_cursor = Mutex()
        self._thread_lock_data = Mutex()

        # handle to the uncorrected scanner instance, not wrapped by a potential CoordinateTransformMixin
        self.bare_scanner = NiScanningProbeInterfuseBare

    def on_activate(self):

        # Sanity checks for ni_ao and ni finite sampling io
        # TODO check that config values within fsio range?
        assert set(self._position_ranges) == set(self._frequency_ranges) == set(self._resolution_ranges), \
            f'Channels in position ranges, frequency ranges and resolution ranges do not coincide'

        assert set(self._input_channel_units).union(self._position_ranges) == set(self._ni_channel_mapping), \
            f'Not all specified channels are mapped to an ni card physical channel'

        # TODO: Any case where ni_ao and ni_fio potentially don't have the same channels?
        specified_ni_finite_io_channels_set = set(self._ni_finite_sampling_io().constraints.input_channel_units).union(
            set(self._ni_finite_sampling_io().constraints.output_channel_units))
        mapped_channels = set([val.lower() for val in self._ni_channel_mapping.values()])

        assert set(mapped_channels).issubset(specified_ni_finite_io_channels_set), \
            f'Channel mapping does not coincide with ni finite sampling io.'

        # Constraints
        axes = list()
        for axis in self._position_ranges:
            position_range = tuple(self._position_ranges[axis])
            resolution_range = tuple(self._resolution_ranges[axis])
            res_default = 50
            if not resolution_range[0] <= res_default <= resolution_range[1]:
                res_default = resolution_range[0]
            frequency_range = tuple(self._frequency_ranges[axis])
            freq_default = 500
            if not frequency_range[0] <= freq_default <= frequency_range[1]:
                freq_default = frequency_range[0]
            max_step = abs(position_range[1] - position_range[0])

            position = ScalarConstraint(default=min(position_range), bounds=position_range)
            resolution = ScalarConstraint(default=res_default, bounds=resolution_range, enforce_int=True)
            frequency = ScalarConstraint(default=freq_default, bounds=frequency_range)
            step = ScalarConstraint(default=0, bounds=(0, max_step))

            axes.append(ScannerAxis(name=axis,
                                    unit='m',
                                    position=position,
                                    step=step,
                                    resolution=resolution,
                                    frequency=frequency,)
                        )
        channels = list()
        for channel, unit in self._input_channel_units.items():
            channels.append(ScannerChannel(name=channel,
                                           unit=unit,
                                           dtype='float64'))

        back_scan_capability = BackScanCapability.AVAILABLE | BackScanCapability.RESOLUTION_CONFIGURABLE
        self._constraints = ScanConstraints(axis_objects=tuple(axes),
                                            channel_objects=tuple(channels),
                                            back_scan_capability=back_scan_capability,
                                            has_position_feedback=False,
                                            square_px_only=False)

        self._target_pos = self.bare_scanner.get_position(self)  # get voltages/pos from ni_ao
        self._toggle_ao_setpoint_channels(False)  # And free ao resources after that
        self._t_last_move = time.perf_counter()
        self.__init_ao_timer()
        self.__t_last_follow = None

        self.sigNextDataChunk.connect(self._fetch_data_chunk, QtCore.Qt.QueuedConnection)

    def _toggle_ao_setpoint_channels(self, enable: bool) -> None:
        ni_ao = self._ni_ao()
        for channel in ni_ao.constraints.setpoint_channels:
            ni_ao.set_activity_state(channel, enable)

    @property
    def _ao_setpoint_channels_active(self) -> bool:
        mapped_channels = set(self._ni_channel_mapping.values())
        return all(
            state for ch, state in self._ni_ao().activity_states.items() if ch in mapped_channels
        )

    def on_deactivate(self):
        """
        Deactivate the module
        """
        self._abort_cursor_movement()
        if self._ni_finite_sampling_io().is_running:
            self._ni_finite_sampling_io().stop_buffered_frame()

    @property
    def constraints(self) -> ScanConstraints:
        """ Read-only property returning the constraints of this scanning probe hardware.
        """
        return self._constraints

    def reset(self):
        """ Hard reset of the hardware.
        """
        pass

    @property
    def scan_settings(self) -> Optional[ScanSettings]:
        """ Property returning all parameters needed for a 1D or 2D scan. Returns None if not configured.
        """
        if self._scan_data:
            return self._scan_data.settings
        else:
            return None

    @property
    def back_scan_settings(self) -> Optional[ScanSettings]:
        """ Property returning all parameters of the backwards scan. Returns None if not configured or not available.
        """
        if self._back_scan_data:
            return self._back_scan_data.settings
        else:
            return None

    def configure_scan(self, settings: ScanSettings) -> None:
        """ Configure the hardware with all parameters needed for a 1D or 2D scan.
        Raise an exception if the settings are invalid and do not comply with the hardware constraints.

        @param ScanSettings settings: ScanSettings instance holding all parameters
        """
        if self.is_scan_running:
            raise RuntimeError('Unable to configure scan parameters while scan is running. '
                               'Stop scanning and try again.')

        # check settings - will raise appropriate exceptions if something is not right
        self.constraints.check_settings(settings)
        self.log.debug('Scan settings fulfill constraints.')

        with self._thread_lock_data:
            settings = self._clip_ranges(settings)
            self._scan_data = ScanData.from_constraints(settings, self._constraints)

            # reset back scan to defaults
            if len(settings.axes) == 1:
                back_resolution = (self.__default_backward_resolution,)
            else:
                back_resolution = (self.__default_backward_resolution, settings.resolution[1])
            back_scan_settings = ScanSettings(
                channels=settings.channels,
                axes=settings.axes,
                range=settings.range,
                resolution=back_resolution,
                frequency=settings.frequency,
            )
            self._back_scan_data = ScanData.from_constraints(back_scan_settings, self._constraints)

            self.log.debug(f'New scan data and back scan data created.')
            self.raw_data_container = RawDataContainer(settings.channels,
                                                       settings.resolution[
                                                           1] if settings.scan_dimension == 2 else 1,
                                                       settings.resolution[0],
                                                       back_scan_settings.resolution[0])
            self.log.debug(f'New RawDataContainer created.')

        self._ni_finite_sampling_io().set_sample_rate(settings.frequency)
        self._ni_finite_sampling_io().set_active_channels(
            input_channels=(self._ni_channel_mapping[in_ch] for in_ch in self._input_channel_units),
            output_channels=(self._ni_channel_mapping[ax] for ax in self.constraints.axes.keys())
        )

        self._ni_finite_sampling_io().set_output_mode(SamplingOutputMode.JUMP_LIST)

        ni_scan_dict = self._init_ni_scan_arrays(settings, back_scan_settings)
        self._ni_finite_sampling_io().set_frame_data(ni_scan_dict)

    def configure_back_scan(self, settings: ScanSettings) -> None:
        """ Configure the hardware with all parameters of the backwards scan.
        Raise an exception if the settings are invalid and do not comply with the hardware constraints.

        @param ScanSettings settings: ScanSettings instance holding all parameters for the back scan
        """
        if self.is_scan_running:
            raise RuntimeError('Unable to configure scan parameters while scan is running. '
                               'Stop scanning and try again.')

        forward_settings = self.scan_settings
        # check settings - will raise appropriate exceptions if something is not right
        self.constraints.check_back_scan_settings(settings, forward_settings)
        self.log.debug('Back scan settings fulfill constraints.')

        with self._thread_lock_data:
            self._back_scan_data = ScanData.from_constraints(settings, self._constraints)
            self.log.debug(f'New back scan data created.')
            self.raw_data_container = RawDataContainer(forward_settings.channels,
                                                       forward_settings.resolution[
                                                           1] if forward_settings.scan_dimension == 2 else 1,
                                                       forward_settings.resolution[0],
                                                       settings.resolution[0])
            self.log.debug(f'New RawDataContainer created.')

        ni_scan_dict = self._init_ni_scan_arrays(forward_settings, settings)
        self._ni_finite_sampling_io().set_frame_data(ni_scan_dict)

    def move_absolute(self, position, velocity=None, blocking=False):
        """ Move the scanning probe to an absolute position as fast as possible or with a defined
        velocity.

        Log error and return current target position if something fails or a scan is in progress.
        """

        # assert not self.is_running, 'Cannot move the scanner while, scan is running'
        if self.is_scan_running:
            self.log.error('Cannot move the scanner while, scan is running')
            return self.bare_scanner.get_target(self)

        if not set(position).issubset(self.constraints.axes):
            self.log.error('Invalid axes name in position')
            return self.bare_scanner.get_target(self)

        try:
            self._prepare_movement(position, velocity=velocity)

            self.__start_ao_write_timer()
            if blocking:
                self.__wait_on_move_done()

            self._t_last_move = time.perf_counter()

            return self.bare_scanner.get_target(self)
        except:
            self.log.exception("Couldn't move: ")

    def __wait_on_move_done(self):
        try:
            t_start = time.perf_counter()
            while self.is_move_running:
                self.log.debug(f"Waiting for move done: {self.is_move_running}, {1e3*(time.perf_counter()-t_start)} ms")
                QGuiApplication.processEvents()
                time.sleep(self._min_step_interval)

            #self.log.debug(f"Move_abs finished after waiting {1e3*(time.perf_counter()-t_start)} ms ")
        except:
            self.log.exception("")

    def move_relative(self, distance, velocity=None, blocking=False):
        """ Move the scanning probe by a relative distance from the current target position as fast
        as possible or with a defined velocity.

        Log error and return current target position if something fails or a 1D/2D scan is in
        progress.
        """
        current_position = self.bare_scanner.get_position(self)
        end_pos = {ax: current_position[ax] + distance[ax] for ax in distance}
        self.move_absolute(end_pos, velocity=velocity, blocking=blocking)

        return end_pos

    def get_target(self):
        """ Get the current target position of the scanner hardware
        (i.e. the "theoretical" position).

        @return dict: current target position per axis.
        """
        if self.is_scan_running:
            return self._stored_target_pos
        else:
            return self._target_pos

    def get_position(self):
        """ Get a snapshot of the actual scanner position (i.e. from position feedback sensors).
        For the same target this value can fluctuate according to the scanners positioning accuracy.

        For scanning devices that do not have position feedback sensors, simply return the target
        position (see also: ScanningProbeInterface.get_target).

        @return dict: current position per axis.
        """
        with self._thread_lock_cursor:
            if not self._ao_setpoint_channels_active:
                self._toggle_ao_setpoint_channels(True)

            pos = self._voltage_dict_to_position_dict(self._ni_ao().setpoints)
            return pos

    def start_scan(self):
        """Start a scan as configured beforehand.
        Log an error if something fails or a 1D/2D scan is in progress.
        """
        try:

            #self.log.debug(f"Start scan in thread {self.thread()}, QT.QThread {QtCore.QThread.currentThread()}... ")

            if self.thread() is not QtCore.QThread.currentThread():
                QtCore.QMetaObject.invokeMethod(self, '_start_scan',
                                                QtCore.Qt.BlockingQueuedConnection)
            else:
                self._start_scan()

        except:
            self.log.exception("")

    @QtCore.Slot()
    def _start_scan(self):
        try:
            if self._scan_data is None:
                # todo: raising would be better, but from this delegated thread exceptions get lost
                self.log.error('Scan Data is None. Scan settings need to be configured before starting')

            if self.is_scan_running:
                self.log.error('Cannot start a scan while scanning probe is already running')

            with self._thread_lock_data:
                self._scan_data.new_scan()
                self._back_scan_data.new_scan()
                self._stored_target_pos = self.bare_scanner.get_target(self).copy()
                self.log.debug(f"Target pos at scan start: {self._stored_target_pos}")
                self._scan_data.scanner_target_at_start = self._stored_target_pos
                self._back_scan_data.scanner_target_at_start = self._stored_target_pos

            # todo: scanning_probe_logic exits when scanner not locked right away
            # should rather ignore/wait until real hw timed scanning starts
            self.module_state.lock()

            first_scan_position = {ax: pos[0] for ax, pos
                                   in zip(self.scan_settings.axes, self.scan_settings.range)}
            self._move_to_and_start_scan(first_scan_position)

        except Exception as e:
            self.module_state.unlock()
            self.log.exception("Starting scan failed.", exc_info=e)

    def stop_scan(self):
        """Stop the currently running scan.
        Log an error if something fails or no 1D/2D scan is in progress.
        """
        #self.log.debug("Stopping scan")
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self, '_stop_scan',
                                            QtCore.Qt.BlockingQueuedConnection)
        else:
            self._stop_scan()

    @QtCore.Slot()
    def _stop_scan(self):
        if not self.is_scan_running:
            self.log.error('No scan in progress. Cannot stop scan.')

        # self.log.debug("Stopping scan...")
        self._start_scan_after_cursor = False  # Ensure Scan HW is not started after movement
        if self._ao_setpoint_channels_active:
            self._abort_cursor_movement()
            # self.log.debug("Move aborted")

        if self._ni_finite_sampling_io().is_running:
            self._ni_finite_sampling_io().stop_buffered_frame()
            # self.log.debug("Frame stopped")

        self.module_state.unlock()
        # self.log.debug("Module unlocked")

        self.log.debug(f"Finished scan, move to stored target: {self._stored_target_pos}")
        self.bare_scanner.move_absolute(self, self._stored_target_pos)
        self._stored_target_pos = dict()

    def get_scan_data(self) -> Optional[ScanData]:
        """ Read-only property returning the ScanData instance used in the scan.
        """
        if self._scan_data is None:
            return None
        else:
            with self._thread_lock_data:
                return self._scan_data.copy()

    def get_back_scan_data(self) -> Optional[ScanData]:
        """ Retrieve the ScanData instance used in the backwards scan.
        """
        if self._scan_data is None:
            return None
        else:
            with self._thread_lock_data:
                return self._back_scan_data.copy()

    def emergency_stop(self):
        """

        @return:
        """
        # TODO: Implement. Yet not used in logic till yet? Maybe sth like this:
        # self._ni_finite_sampling_io().terminate_all_tasks()
        # self._ni_ao().set_activity_state(False)
        pass

    @property
    def is_scan_running(self):
        """
        Read-only flag indicating the module state.

        @return bool: scanning probe is running (True) or not (False)
        """
        # module state used to indicate hw timed scan running
        #self.log.debug(f"Module in state: {self.module_state()}")
        #assert self.module_state() in ('locked', 'idle')  # TODO what about other module states?
        if self.module_state() == 'locked':
            return True
        else:
            return False

    @property
    def is_move_running(self):
        with self._thread_lock_cursor:
            running = self.__t_last_follow is not None
            return running

    def _check_scan_end_reached(self):
        # not thread safe, call from thread_lock protected code only
        return self.raw_data_container.is_full

    def _fetch_data_chunk(self):
        try:
            # self.log.debug(f'fetch chunk: {self._ni_finite_sampling_io().samples_in_buffer}, {self.is_scan_running}')
            # chunk_size = self._scan_data.settings.resolution[0] + self.__backwards_line_resolution
            chunk_size = 10  # TODO Hardcode or go line by line as commented out above?
            # Request a minimum of chunk_size samples per loop
            try:
                samples_dict = self._ni_finite_sampling_io().get_buffered_samples(chunk_size) \
                    if self._ni_finite_sampling_io().samples_in_buffer < chunk_size\
                    else self._ni_finite_sampling_io().get_buffered_samples()
            except ValueError:  # ValueError is raised, when more samples are requested then pending or still to get
                # after HW stopped
                samples_dict = self._ni_finite_sampling_io().get_buffered_samples()

            reverse_routing = {val.lower(): key for key, val in self._ni_channel_mapping.items()}

            new_data = {reverse_routing[key]: samples for key, samples in samples_dict.items()}

            with self._thread_lock_data:
                self.raw_data_container.fill_container(new_data)
                self._scan_data.data = self.raw_data_container.forwards_data()
                self._back_scan_data.data = self.raw_data_container.backwards_data()

                if self._check_scan_end_reached():
                    self.stop_scan()
                elif not self.is_scan_running:
                    return
                else:
                    self.sigNextDataChunk.emit()

        except Exception as e:
            self.log.error("Error while fetching data chunk.", exc_info=e)
            self.stop_scan()

    def _position_to_voltage(self, axis, positions):
        """
        @param str axis: scanner axis name for which the position is to be converted to voltages
        @param np.array/single value position(s): Position (value(s)) to convert to voltage(s) of corresponding
        ni_channel derived from axis string

        @return np.array/single value: Position(s) converted to voltage(s) (value(s)) [single value & 1D np.array depending on input]
                      for corresponding ni_channel (keys)
        """

        ni_channel = self._ni_channel_mapping[axis]
        voltage_range = self._ni_finite_sampling_io().constraints.output_channel_limits[ni_channel]
        position_range = self.constraints.axes[axis].position.bounds

        slope = np.diff(voltage_range) / np.diff(position_range)
        intercept = voltage_range[1] - position_range[1] * slope

        converted = np.clip(positions * slope + intercept, min(voltage_range), max(voltage_range))

        try:
            # In case of single value, use just this value
            voltage_data = converted.item()
        except ValueError:
            voltage_data = converted

        return voltage_data

    def _pos_dict_to_vec(self, position):

        pos_list = [el[1] for el in sorted(position.items())]
        return np.asarray(pos_list)

    def _pos_vec_to_dict(self, position_vec):

        if isinstance(position_vec, dict):
            raise ValueError(f"Position can't be provided as dict.")

        axes = sorted(self.constraints.axes.keys())
        return {axes[idx]: pos for idx, pos in enumerate(position_vec)}

    def _voltage_dict_to_position_dict(self, voltages):
        """
        @param dict voltages: Voltages (value(s)) to convert to position(s) of corresponding scanner axis (keys)

        @return dict: Voltage(s) converted to position(s) (value(s)) [single value & 1D np.array depending on input] for
                      for corresponding axis (keys)
        """

        reverse_routing = {val.lower(): key for key, val in self._ni_channel_mapping.items()}

        # TODO check voltages given correctly checking?
        positions_data = dict()
        for ni_channel in voltages:
            try:
                axis = reverse_routing[ni_channel]
                voltage_range = self._ni_finite_sampling_io().constraints.output_channel_limits[ni_channel]
                position_range = self.constraints.axes[axis].position.bounds

                slope = np.diff(position_range) / np.diff(voltage_range)
                intercept = position_range[1] - voltage_range[1] * slope

                converted = voltages[ni_channel] * slope + intercept
                # round position values to 100 pm. Avoids float precision errors
                converted = np.around(converted, 10)
            except KeyError:
                # if one of the AO channels is not used for confocal
                continue

            try:
                # In case of single value, use just this value
                positions_data[axis] = converted.item()
            except ValueError:
                positions_data[axis] = converted

        return positions_data

    def _get_scan_lines(self, settings: ScanSettings, back_settings: ScanSettings) -> Dict[str, np.ndarray]:
        if settings.scan_dimension == 1:
            axis = settings.axes[0]

            horizontal = np.linspace(settings.range[0][0], settings.range[0][1],
                                     settings.resolution[0])
            horizontal_return_line = np.linspace(settings.range[0][1], settings.range[0][0],
                                                 back_settings.resolution[0])

            horizontal_single_line = np.concatenate((horizontal,
                                                     horizontal_return_line))

            coord_dict = {axis: horizontal_single_line}

        elif settings.scan_dimension == 2:
            horizontal_resolution = settings.resolution[0]
            horizontal_back_resolution = back_settings.resolution[0]
            vertical_resolution = settings.resolution[1]

            # horizontal scan array / "fast axis"
            horizontal_axis = settings.axes[0]
            horizontal = np.linspace(settings.range[0][0], settings.range[0][1],
                                     horizontal_resolution)

            horizontal_return_line = np.linspace(settings.range[0][1],
                                                 settings.range[0][0],
                                                 horizontal_back_resolution)
            # a single back and forth line
            horizontal_single_line = np.concatenate((horizontal, horizontal_return_line))
            # need as much lines as we have in the vertical directions
            horizontal_scan_array = np.tile(horizontal_single_line, vertical_resolution)

            # vertical scan array / "slow axis"
            vertical_axis = settings.axes[1]
            vertical = np.linspace(settings.range[1][0], settings.range[1][1],
                                   vertical_resolution)

            # during horizontal line, the vertical line keeps its value
            vertical_lines = np.repeat(vertical.reshape(vertical_resolution, 1), horizontal_resolution, axis=1)
            # during backscan of horizontal, the vertical axis increases its value by "one index"
            vertical_return_lines = np.linspace(vertical[:-1], vertical[1:], horizontal_back_resolution).T
            # need to extend the vertical lines at the end, as we reach it earlier then for the horizontal axes
            vertical_return_lines = np.concatenate((vertical_return_lines,
                                                    np.ones((1, horizontal_back_resolution)) * vertical[-1]
                                                    ))

            vertical_scan_array = np.concatenate((vertical_lines, vertical_return_lines), axis=1).ravel()

            # TODO We could drop the last return line in the initialization, as it is not read in anyways till yet.

            coord_dict = {horizontal_axis: horizontal_scan_array,
                          vertical_axis: vertical_scan_array
            }

        else:
            raise ValueError(f"Not supported scan dimension: {settings.scan_dimension}")

        return self._expand_coordinate(coord_dict)

    def _init_scan_grid(self, settings: ScanSettings, back_settings: ScanSettings) -> Dict[str, np.ndarray]:
        scan_coords = self._get_scan_lines(settings, back_settings)
        return scan_coords

    def _check_scan_grid(self, scan_coords):
        for ax, coords in scan_coords.items():
            position_min = self.constraints.axes[ax].position.minimum
            position_max = self.constraints.axes[ax].position.maximum
            out_of_range = any(coords < position_min) or any(coords > position_max)

            if out_of_range:
                raise ValueError(f"Scan axis {ax} out of range [{position_min}, {position_max}]")

    def _clip_ranges(self, settings: ScanSettings):
        valid_scan_grid = False
        i_trial, n_max_trials = 0, 25
        
        while not valid_scan_grid and i_trial < n_max_trials:
            ranges = settings.range
            if i_trial > 0:
                ranges = self._shrink_scan_ranges(ranges)
            settings_dict = asdict(settings)
            settings_dict['range'] = ranges
            settings = ScanSettings.from_dict(settings_dict)

            try:
                self._init_ni_scan_arrays(settings, settings)
                valid_scan_grid = True
            except ValueError:
                valid_scan_grid = False

            i_trial += 1

        if not valid_scan_grid:
            raise ValueError("Couldn't create scan grid. ")

        if i_trial > 1:
            self.log.warning(f"Adapted out-of-bounds scan range to {ranges}")
        return settings

    @staticmethod
    def _shrink_scan_ranges(ranges, factor=0.01):
        lengths = [stop - start for (start, stop) in ranges]

        return [(start + factor * lengths[idx], stop - factor * lengths[idx]) for idx, (start, stop) in enumerate(ranges)]

    def _init_ni_scan_arrays(self, settings: ScanSettings, back_settings: ScanSettings)\
            -> Dict[str, np.ndarray]:
        """
        @param ScanSettings settings: scan parameters

        @return dict: NI channel name to voltage 1D numpy array mapping for all axes
        """
        # TODO maybe need to clip to voltage range in case of float precision error in conversion?
        scan_coords = self._init_scan_grid(settings, back_settings)
        self._check_scan_grid(scan_coords)

        scan_voltages = {self._ni_channel_mapping[ax]: self._position_to_voltage(ax, val) for ax, val in scan_coords.items()}
        return scan_voltages

    def __ao_cursor_write_loop(self):

        t_start = time.perf_counter()
        try:
            current_pos_vec = self._pos_dict_to_vec(self.bare_scanner.get_position(self))

            with self._thread_lock_cursor:
                stop_loop = self._abort_cursor_move


                target_pos_vec = self._pos_dict_to_vec(self._target_pos)
                connecting_vec = target_pos_vec - current_pos_vec
                distance_to_target = np.linalg.norm(connecting_vec)

                # Terminate follow loop if target is reached
                if distance_to_target < self._scanner_distance_atol:
                    stop_loop = True

                if not stop_loop:
                    # Determine delta t and update timestamp for next iteration
                    if not self.__t_last_follow:
                        self.__t_last_follow = time.perf_counter()

                    delta_t = t_start - self.__t_last_follow
                    #self.log.debug(f"Write loop duration: {1e3*(time.perf_counter()-self.__t_last_follow)} ms")
                    self.__t_last_follow = t_start

                    # Calculate new position to go to
                    max_step_distance = delta_t * self._follow_velocity

                    if max_step_distance < distance_to_target:
                        direction_vec = connecting_vec / distance_to_target
                        new_pos_vec = current_pos_vec + max_step_distance * direction_vec
                    else:
                        new_pos_vec = target_pos_vec

                    new_pos = self._pos_vec_to_dict(new_pos_vec)
                    new_voltage = {self._ni_channel_mapping[ax]: self._position_to_voltage(ax, pos)
                                   for ax, pos in new_pos.items()}

                    self._ni_ao().setpoints = new_voltage
                    #self.log.debug(f'Cursor_write_loop move to {new_pos}, Dist= {distance_to_target} '
                    #               f' to target {self._target_pos} took {1e3*(time.perf_counter()-t_start)} ms.')

                    # Start single-shot timer to call this follow loop again after some wait time
                    t_overhead = time.perf_counter() - t_start
                    self.__ni_ao_write_timer.start(int(round(1000 * max(0, self._min_step_interval - t_overhead))))

            if stop_loop:
                #self.log.debug(f'Cursor_write_loop stopping at {current_pos_vec}, dist= {distance_to_target}')
                self._abort_cursor_movement()

                if self._start_scan_after_cursor:
                    self._start_hw_timed_scan()
        except:
            self.log.exception("Error in ao write loop: ")

    def _start_hw_timed_scan(self):

        #self.log.debug("Starting hw timed scan")
        try:
            self._ni_finite_sampling_io().start_buffered_frame()
            self.sigNextDataChunk.emit()
        except Exception as e:
            self.log.error(f'Could not start frame due to {str(e)}')
            self.module_state.unlock()

        self._start_scan_after_cursor = False

    def _abort_cursor_movement(self):
        """
        Abort the movement and release ni_ao resources.
        """

        #self.log.debug(f"Aborting move.")
        self._target_pos = self.bare_scanner.get_position(self)

        with self._thread_lock_cursor:

            self._abort_cursor_move = True
            self.__t_last_follow = None
            self._toggle_ao_setpoint_channels(False)

            #self.log.debug("hw turned off")

    def _move_to_and_start_scan(self, position):
        self._prepare_movement(position)
        self._start_scan_after_cursor = True
        #self.log.debug("Starting timer to move to scan position")
        self.__start_ao_write_timer()

    def _prepare_movement(self, position, velocity=None):
        """
        Clips values of position to allowed range and fills up the write queue.
        If re-entered from a different thread, clears current write queue and start
        a new movement.
        """
        # FIXME When position is changed real fast one gets the QT warnings
        #  QObject::killTimer: Timers cannot be stopped from another thread
        #  QObject::startTimer: Timers cannot be started from another thread

        #self.log.debug("Preparing movement")

        with self._thread_lock_cursor:
            self._abort_cursor_move = False
            if not self._ao_setpoint_channels_active:
                self._toggle_ao_setpoint_channels(True)

            constr = self.constraints

            for axis, pos in position.items():
                in_range_flag, _ = in_range(pos, *constr.axes[axis].position.bounds)
                if not in_range_flag:
                    position[axis] = float(constr.axes[axis].position.clip(position[axis]))
                    self.log.warning(f'Position {pos} out of range {constr.axes[axis].position.bounds} '
                                     f'for axis {axis}. Value clipped to {position[axis]}')
                # TODO Adapt interface to use "in_range"?
                self._target_pos[axis] = position[axis]

            #self.log.debug(f"New target pos: {self._target_pos}")

            # TODO Add max velocity as a hardware constraint/ Calculate from scan_freq etc?
            if velocity is None:
                velocity = self.__max_move_velocity
            v_in_range, velocity = in_range(velocity, 0, self.__max_move_velocity)
            if not v_in_range:
                self.log.warning(f'Requested velocity is exceeding the maximum velocity of {self.__max_move_velocity} '
                                 f'm/s. Move will be done at maximum velocity')

            self._follow_velocity = velocity

        #self.log.debug("Movement prepared")
        # TODO Keep other axis constant?

    def __init_ao_timer(self):
        self.__ni_ao_write_timer = QtCore.QTimer(parent=self)

        self.__ni_ao_write_timer.setSingleShot(True)
        self.__ni_ao_write_timer.timeout.connect(self.__ao_cursor_write_loop, QtCore.Qt.QueuedConnection)
        self.__ni_ao_write_timer.setInterval(1e3*self._min_step_interval)  # (ms), dynamically calculated during write loop

    def __start_ao_write_timer(self):
        #self.log.debug(f"ao start write timer in thread {self.thread()}, QT.QThread {QtCore.QThread.currentThread()} ")
        try:
            if not self.is_move_running:
                #self.log.debug("Starting AO write timer...")
                if self.thread() is not QtCore.QThread.currentThread():
                    QtCore.QMetaObject.invokeMethod(self.__ni_ao_write_timer,
                                                    'start',
                                                    QtCore.Qt.BlockingQueuedConnection)
                else:
                    self.__ni_ao_write_timer.start()
            else:
                pass
                #self.log.debug("Dropping timer start, already running")

        except:
            self.log.exception("")


class RawDataContainer:
    def __init__(self, channel_keys, number_of_scan_lines: int,
                 forward_line_resolution: int, backwards_line_resolution: int):
        self.forward_line_resolution = forward_line_resolution
        self.number_of_scan_lines = number_of_scan_lines
        self.forward_line_resolution = forward_line_resolution
        self.backwards_line_resolution = backwards_line_resolution

        self._raw = {key: np.full(self.frame_size, np.nan) for key in channel_keys}

    @property
    def frame_size(self) -> int:
        return self.number_of_scan_lines * (self.forward_line_resolution + self.backwards_line_resolution)

    def fill_container(self, samples_dict):
        # get index of first nan from one element of dict
        first_nan_idx = self.number_of_non_nan_values
        for key, samples in samples_dict.items():
            self._raw[key][first_nan_idx:first_nan_idx + len(samples)] = samples

    def forwards_data(self):
        reshaped_2d_dict = dict.fromkeys(self._raw)
        for key in self._raw:
            if self.number_of_scan_lines > 1:
                reshaped_arr = self._raw[key].reshape(self.number_of_scan_lines,
                                                      self.forward_line_resolution + self.backwards_line_resolution)
                reshaped_2d_dict[key] = reshaped_arr[:, :self.forward_line_resolution].T
            elif self.number_of_scan_lines == 1:
                reshaped_2d_dict[key] = self._raw[key][:self.forward_line_resolution]
        return reshaped_2d_dict

    def backwards_data(self):
        reshaped_2d_dict = dict.fromkeys(self._raw)
        for key in self._raw:
            if self.number_of_scan_lines > 1:
                reshaped_arr = self._raw[key].reshape(self.number_of_scan_lines,
                                                      self.forward_line_resolution + self.backwards_line_resolution)
                reshaped_2d_dict[key] = reshaped_arr[:, self.forward_line_resolution:].T
            elif self.number_of_scan_lines == 1:
                reshaped_2d_dict[key] = self._raw[key][self.forward_line_resolution:]

        return reshaped_2d_dict

    @property
    def number_of_non_nan_values(self):
        """
        returns number of not NaN samples
        """
        return np.sum(~np.isnan(next(iter(self._raw.values()))))

    @property
    def is_full(self):
        return self.number_of_non_nan_values == self.frame_size


class NiScanningProbeInterfuse(CoordinateTransformMixin, NiScanningProbeInterfuseBare):
    def _init_scan_grid(self, settings: ScanSettings, back_settings: ScanSettings) -> Dict[str, np.ndarray]:
        scan_coords_transf = self.coordinate_transform(super()._init_scan_grid(settings, back_settings), inverse=False)
        return scan_coords_transf

    # start and stop scan need to be reimplemented
    # for QtCore.QMetaObject.invokeMethod to work
    @QtCore.Slot()
    def _start_scan(self):
        super()._start_scan()

    @QtCore.Slot()
    def _stop_scan(self):
        super()._stop_scan()
