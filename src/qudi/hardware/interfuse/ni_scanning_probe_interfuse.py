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

from PySide2 import QtCore
from PySide2.QtGui import QGuiApplication

from qudi.interface.scanning_probe_interface import ScanningProbeInterface, ScanConstraints, \
    ScannerAxis, ScannerChannel, ScanData
from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector
from qudi.util.mutex import RecursiveMutex, Mutex
from qudi.util.enums import SamplingOutputMode
from qudi.util.helpers import in_range



class NiScanningProbeInterfuse(ScanningProbeInterface):
    """
    This interfuse combines modules of a National Instrument device to make up a scanning probe hardware.
    One module for software timed analog output (NIXSeriesAnalogOutput) to position e.g. a scanner to a specific
    position and a hardware timed module for in and output (NIXSeriesFiniteSamplingIO) to realize 1D/2D scans.

    Example config for copy-paste:

    ni_scanning_probe:
        module.Class: 'interfuse.ni_scanning_probe_interfuse.NiScanningProbeInterfuse'
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
            backwards_line_resolution: 50 # optional
            move_velocity: 400e-6 #m/s; This speed is used for scanner movements and avoids jumps from position to position.
    """

    # TODO What about channels which are not "calibrated" to 'm', e.g. just use 'V'?
    # TODO Bool indicators deprecated; Change in scanning probe toolchain

    _ni_finite_sampling_io = Connector(name='scan_hardware', interface='FiniteSamplingIOInterface')
    _ni_ao = Connector(name='analog_output', interface='ProcessSetpointInterface')

    _ni_channel_mapping = ConfigOption(name='ni_channel_mapping', missing='error')
    _position_ranges = ConfigOption(name='position_ranges', missing='error')
    _frequency_ranges = ConfigOption(name='frequency_ranges', missing='error')
    _resolution_ranges = ConfigOption(name='resolution_ranges', missing='error')
    _input_channel_units = ConfigOption(name='input_channel_units', missing='error')

    __backwards_line_resolution = ConfigOption(name='backwards_line_resolution', default=50)
    __max_move_velocity = ConfigOption(name='maximum_move_velocity', default=400e-6)

    _threaded = True  # Interfuse is by default not threaded.

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        self._current_scan_frequency = -1
        self._current_scan_ranges = [tuple(), tuple()]
        self._current_scan_axes = tuple()
        self._current_scan_resolution = tuple()

        self._scan_data = None
        self._constraints = None

        self._target_pos = dict()
        self._stored_target_pos = dict()
        self._start_scan_after_cursor = False

        self.__ni_ao_write_timer = None
        self._min_step_interval = 1e-3
        self._scanner_distance_tol = 1e-9

        self.__read_pos = -1
        self.__scan_stopped = False

        self._thread_lock_cursor = Mutex()
        self._thread_lock_data = Mutex()

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
            axes.append(ScannerAxis(name=axis,
                                    unit='m',
                                    value_range=self._position_ranges[axis],
                                    step_range=(0, abs(np.diff(self._position_ranges[axis]))),
                                    resolution_range=self._resolution_ranges[axis],
                                    frequency_range=self._frequency_ranges[axis])
                        )
        channels = list()
        for channel, unit in self._input_channel_units.items():
            channels.append(ScannerChannel(name=channel,
                                           unit=unit,
                                           dtype=np.float64))

        self._constraints = ScanConstraints(axes=axes,
                                            channels=channels,
                                            backscan_configurable=False,  # TODO incorporate in scanning_probe toolchain
                                            has_position_feedback=False,  # TODO incorporate in scanning_probe toolchain
                                            square_px_only=False)  # TODO incorporate in scanning_probe toolchain
#
        self._target_pos = self.get_position()  # get voltages/pos from ni_ao
        self._t_last_move = time.perf_counter()
        self.__init_ao_timer()
        self.__t_last_follow = None


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
        self.__stop_ao_write_timer()

        with self._thread_lock_cursor:
            self.__write_queue = dict()

        self._toggle_ao_setpoint_channels(False)
        if self._ni_finite_sampling_io().is_running:
            self._ni_finite_sampling_io().stop_buffered_frame()

    def get_constraints(self):
        """ Get hardware constraints/limitations.

        @return dict: scanner constraints
        """
        return self._constraints

    def reset(self):
        """ Hard reset of the hardware.
        """
        pass

    def configure_scan(self, scan_settings):
        """ Configure the hardware with all parameters needed for a 1D or 2D scan.

        @param dict scan_settings: scan_settings dictionary holding all the parameters 'axes', 'resolution', 'ranges'
        #  TODO update docstring in interface

        @return (bool, ScanSettings): Failure indicator (fail=True),
                                      altered ScanSettings instance (same as "settings")
        """


        if self.is_scan_running:
            self.log.error('Unable to configure scan parameters while scan is running. '
                           'Stop scanning and try again.')
            return True, self.scan_settings

        axes = scan_settings.get('axes', self._current_scan_axes)
        ranges = tuple(
            (min(r), max(r)) for r in scan_settings.get('range', self._current_scan_ranges)
        )
        resolution = scan_settings.get('resolution', self._current_scan_resolution)
        frequency = float(scan_settings.get('frequency', self._current_scan_frequency))

        if not set(axes).issubset(self._position_ranges):
            self.log.error('Unknown axes names encountered. Valid axes are: {0}'
                           ''.format(set(self._position_ranges)))
            return True, self.scan_settings

        if len(axes) != len(ranges) or len(axes) != len(resolution):
            self.log.error('"axes", "range" and "resolution" must have same length.')
            return True, self.scan_settings
        for i, ax in enumerate(axes):
            for axis_constr in self._constraints.axes.values():
                if ax == axis_constr.name:
                    break
            if ranges[i][0] < axis_constr.min_value or ranges[i][1] > axis_constr.max_value:
                self.log.error('Scan range out of bounds for axis "{0}". Maximum possible range'
                               ' is: {1}'.format(ax, axis_constr.value_range))
                return True, self.scan_settings
            if resolution[i] < axis_constr.min_resolution or resolution[i] > axis_constr.max_resolution:
                self.log.error('Scan resolution out of bounds for axis "{0}". Maximum possible '
                               'range is: {1}'.format(ax, axis_constr.resolution_range))
                return True, self.scan_settings
            if i == 0:
                if frequency < axis_constr.min_frequency or frequency > axis_constr.max_frequency:
                    self.log.error('Scan frequency out of bounds for fast axis "{0}". Maximum '
                                   'possible range is: {1}'
                                   ''.format(ax, axis_constr.frequency_range))
                    return True, self.scan_settings
            with self._thread_lock_data:
                try:
                    self._scan_data = ScanData(
                        channels=tuple(self._constraints.channels.values()),
                        scan_axes=tuple(self._constraints.axes[ax] for ax in axes),
                        scan_range=ranges,
                        scan_resolution=tuple(resolution),
                        scan_frequency=frequency,
                        position_feedback_axes=None
                    )
                    #self.log.debug(f"New scanData created: {self._scan_data.data}")

                except:
                    self.log.exception("")
                    return True, self.scan_settings

            try:
                self._ni_finite_sampling_io().set_sample_rate(frequency)
                self._ni_finite_sampling_io().set_active_channels(
                    input_channels=(self._ni_channel_mapping[in_ch] for in_ch in self._input_channel_units),
                    output_channels=(self._ni_channel_mapping[ax] for ax in axes)
                    # TODO Use all axes and keep the unused constant? basically just constants in ni scan dict.
                )

                self._ni_finite_sampling_io().set_output_mode(SamplingOutputMode.JUMP_LIST)

                ni_scan_dict = self._initialize_ni_scan_arrays(self._scan_data)

                self._ni_finite_sampling_io().set_frame_data(ni_scan_dict)

            except:
                self.log.exception("")
                return True, self.scan_settings

            self._current_scan_resolution = tuple(resolution)
            self._current_scan_ranges = ranges
            self._current_scan_axes = tuple(axes)
            self._current_scan_frequency = frequency

            return False, self.scan_settings

    def move_absolute(self, position, velocity=None, blocking=False):
        """ Move the scanning probe to an absolute position as fast as possible or with a defined
        velocity.

        Log error and return current target position if something fails or a scan is in progress.
        """

        # assert not self.is_running, 'Cannot move the scanner while, scan is running'
        if self.is_scan_running:
            self.log.error('Cannot move the scanner while, scan is running')
            return self.get_target()

        if not set(position).issubset(self.get_constraints().axes):
            self.log.error('Invalid axes name in position')
            return self.get_target()

        self._prepare_movement(position, velocity=velocity)

        self.__start_ao_write_timer()
        if blocking:
            self.__wait_on_move_done()

        self._t_last_move = time.perf_counter()

        return self.get_target()

    def __wait_on_move_done(self):
        try:
            t_start = time.perf_counter()
            while self.is_move_running:
                self.log.debug(f"Waiting for move to finish. Write queue: {self.__write_queue}")
                QGuiApplication.processEvents()
                time.sleep(1e-3*self._timer_target_interval_ms)

            delta = np.asarray(list(self.get_position().values())) - np.asarray(list(self.get_target().values()))
            self.log.debug(f"Move_abs finished after {1e3*(time.perf_counter()-t_start)} ms "
                           f"at pos= {self.get_position()}. Target= {self.get_target()}. "
                           f"|Delta|= {np.linalg.norm(delta)}")
        except:
            self.log.exception("")

    def move_relative(self, distance, velocity=None, blocking=False):
        """ Move the scanning probe by a relative distance from the current target position as fast
        as possible or with a defined velocity.

        Log error and return current target position if something fails or a 1D/2D scan is in
        progress.
        """
        current_position = self.get_position()
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
        if not self._ao_setpoint_channels_active:
            self._toggle_ao_setpoint_channels(True)
        return self._voltage_dict_to_position_dict(self._ni_ao().setpoints)

    def start_scan(self):
        try:

            #self.log.debug(f"Start scan in thread {self.thread()}, QT.QThread {QtCore.QThread.currentThread()}... ")

            if self.thread() is not QtCore.QThread.currentThread():
                QtCore.QMetaObject.invokeMethod(self, '_start_scan',
                                                QtCore.Qt.BlockingQueuedConnection)
            else:
                self._start_scan()
            #self.log.debug(f"Scan started in hw thread")

        except:
            self.log.exception("")
        return 0

    @QtCore.Slot()
    def _start_scan(self):
        """

        @return (bool): Failure indicator (fail=True)
        """
        if self._scan_data is None:
            self.log.error('Scan Data is None. Scan settings need to be configured before starting')
            return -1

        if self.is_scan_running:
            self.log.error('Cannot start a scan while scanning probe is already running')
            return -1

        try:
            with self._thread_lock_data:
                self._scan_data.new_scan()

                #self.log.debug(f"New scan data: {self._scan_data.data}, position {self._scan_data._position_data}")
                self._stored_target_pos = self.get_target().copy()
                self._scan_data.scanner_target_at_start = self._stored_target_pos

            # todo: scanning_probe_logic exits when scanner not locked right away
            # should rather ignore/wait until real hw timed scanning starts
            self.log.debug(f"Start scan with settings {self.scan_settings}")
            # lock indicates scanning, not cursor movement
            self.module_state.lock()

            first_scan_position = {ax: pos[0] for ax, pos
                                   in zip(self.scan_settings['axes'], self.scan_settings['range'])}
            self._move_to_and_start_scan(first_scan_position)
            self.__read_pos = 0

            return 0  # FIXME Bool indicators deprecated

        except Exception as e:
            self.log.exception("")
            self.module_state.unlock()
            return -1

    def stop_scan(self):

       #self.log.debug(f"Stop scan in thread {self.thread()}, QT.QThread {QtCore.QThread.currentThread()}... ")

       if self.thread() is not QtCore.QThread.currentThread():
           QtCore.QMetaObject.invokeMethod(self, '_stop_scan',
                                           QtCore.Qt.BlockingQueuedConnection)
       else:
           self._stop_scan()

       return 0

    @QtCore.Slot()
    def _stop_scan(self):
        """

        @return bool: Failure indicator (fail=True)
        # FIXME Fix the mess of bool indicators, int return values etc in toolchain
        """
        try:
            #self.log.debug("Stopping scan...")
            if self._ao_setpoint_channels_active:
                self._abort_cursor_movement()
                #self.log.debug("Move aborted")

            if self._ni_finite_sampling_io().is_running:
                self._ni_finite_sampling_io().stop_buffered_frame()
                #self.log.debug("Frame stopped")

            self.module_state.unlock()
            #self.log.debug("Module unlocked")

            self.move_absolute(self._stored_target_pos)
            self._stored_target_pos = dict()
            return False  # TODO Bool indicators deprecated

        except:
            self.log.exception("")
            return True

    def get_scan_data(self):
        """

        @return (bool, ScanData): Failure indicator (fail=True), ScanData instance used in the scan
        #  TODO change interface
        """
        # todo: get_scan data ussage for polling hw &iterating __read_pos seems sketchy
        # => this hw file should implement it's own polling loop and provide updated ._scan_data
        # when get_scan_data is called
        try:
            if not self.is_scan_running or not self._ni_finite_sampling_io().is_running:
                return self._scan_data
            else:
                # _stop_scan is called asynchronously. Thus .is_scan_running might be True, even if last data frame
                # was already fetched. __scan_stopped is set by the polling thread, guaranteed to signal in time.
                if not self.__scan_stopped:
                    self._fetch_data_line()
                return self._scan_data
        except:
            self.log.exception("")

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
        #self.log.debug(f"Module in state: {self.module_state()}")
        #assert self.module_state() in ('locked', 'idle')  # TODO what about other module states?
        if self.module_state() == 'locked':
            return True
        else:
            return False

    @property
    def is_move_running(self):
        with self._thread_lock_cursor:
            return self.__t_last_follow is not None

    @property
    def scan_settings(self):

        settings = {'axes': tuple(self._current_scan_axes),
                    'range': tuple(self._current_scan_ranges),
                    'resolution': tuple(self._current_scan_resolution),
                    'frequency': self._current_scan_frequency}
        return settings

    @property
    def _write_queue_empty(self):
        # not thread safe, call from thread_lock protected code only
        return all([values.size == 0 for values in self.__write_queue.values()])

    def _check_scan_end_reached(self):
        # not thread safe, call from thread_lock protected code only
        if self.__scan_stopped:
            return True

        if self._scan_data.scan_dimension == 1:
            self.__scan_stopped = True
            return True

        elif self._scan_data.scan_dimension == 2:
            if self.__read_pos == self._current_scan_resolution[1]:
                self.__scan_stopped = True
                return True

        return False

    def _fetch_data_line(self):
        samples_per_complete_line = self._current_scan_resolution[0] + self.__backwards_line_resolution
        # blocking until samples are ready
        #self.log.debug(f"Fetching data, line_idx {self.__read_pos}")
        samples_dict = self._ni_finite_sampling_io().get_buffered_samples(samples_per_complete_line)
        #self.log.debug(f"Samples = {samples_dict}")
        #self.log.debug(f"scanData: {self._scan_data.data}")
        # Potentially we could also use get_buff.. without samples, but that would require some more thought
        # while writing to ScanData

        reverse_routing = {val.lower(): key for key, val in self._ni_channel_mapping.items()}
        # TODO extract terminal stuff? meaning allow DevX/... notation in config?

        try:
            with self._thread_lock_data:
                for ni_ch in samples_dict.keys():
                    input_ch = reverse_routing[ni_ch]
                    line_data = samples_dict[ni_ch][:self._current_scan_resolution[0]]

                    if self._scan_data.scan_dimension == 1:
                        self._scan_data.data[input_ch] = line_data

                    elif self._scan_data.scan_dimension == 2:
                        self._scan_data.data[input_ch][:, self.__read_pos] = line_data
                    else:
                        self.log.error('Invalid Scan Dimension')
                        self.stop_scan()  # TODO Should the hw stop itself?

                if self._scan_data.scan_dimension == 2:
                    self.__read_pos += 1

                if self._check_scan_end_reached():
                    self.stop_scan()

        except:
            self.log.exception("")
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
        position_range = self.get_constraints().axes[axis].value_range

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

        axes = sorted(self.get_constraints().axes.keys())
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
                position_range = self.get_constraints().axes[axis].value_range

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

    def _initialize_ni_scan_arrays(self, scan_data):
        """
        @param ScanData scan_data: The desired ScanData instance

        @return dict: Where keys coincide with the ni_channel for the current scan axes and values are the
                      corresponding voltage 1D numpy arrays for each axis
        """

        # TODO adjust toolchain to incorporate __backwards_line_resolution in settings?
        # TODO maybe need to clip to voltage range in case of float precision error in conversion?

        assert isinstance(scan_data, ScanData), 'This function requires a scan_data object as input'

        if scan_data.scan_dimension == 1:

            axis = scan_data.scan_axes[0]
            horizontal_resolution = scan_data.scan_resolution[0]

            horizontal = np.linspace(*self._position_to_voltage(axis, scan_data.scan_range[0]),
                                     horizontal_resolution)

            horizontal_return_line = np.linspace(self._position_to_voltage(axis, scan_data.scan_range[0][1]),
                                                 self._position_to_voltage(axis, scan_data.scan_range[0][0]),
                                                 self.__backwards_line_resolution)
            # TODO Return line for 1d included due to possible hysteresis. Might be able to drop it,
            #  but then get_scan_data needs to be changed accordingly

            horizontal_single_line = np.concatenate((horizontal,
                                                     horizontal_return_line))

            voltage_dict = {self._ni_channel_mapping[axis]: horizontal_single_line}

            return voltage_dict

        elif scan_data.scan_dimension == 2:

            horizontal_resolution = scan_data.scan_resolution[0]
            vertical_resolution = scan_data.scan_resolution[1]

            # horizontal scan array / "fast axis"
            horizontal_axis = scan_data.scan_axes[0]

            horizontal = np.linspace(*self._position_to_voltage(horizontal_axis, scan_data.scan_range[0]),
                                     horizontal_resolution)

            horizontal_return_line = np.linspace(self._position_to_voltage(horizontal_axis, scan_data.scan_range[0][1]),
                                                 self._position_to_voltage(horizontal_axis, scan_data.scan_range[0][0]),
                                                 self.__backwards_line_resolution)
            # a single back and forth line
            horizontal_single_line = np.concatenate((horizontal, horizontal_return_line))
            # need as much lines as we have in the vertical directions
            horizontal_scan_array = np.tile(horizontal_single_line, vertical_resolution)

            # vertical scan array / "slow axis"

            vertical_axis = scan_data.scan_axes[1]

            vertical = np.linspace(*self._position_to_voltage(vertical_axis, scan_data.scan_range[1]),
                                   vertical_resolution)

            # during horizontal line, the vertical line keeps its value
            vertical_lines = np.repeat(vertical.reshape(vertical_resolution, 1), horizontal_resolution, axis=1)
            # during backscan of horizontal, the vertical axis increases its value by "one index"
            vertical_return_lines = np.linspace(vertical[:-1], vertical[1:], self.__backwards_line_resolution).T
            # need to extend the vertical lines at the end, as we reach it earlier then for the horizontal axes
            vertical_return_lines = np.concatenate((vertical_return_lines,
                                                    np.ones((1, self.__backwards_line_resolution))*vertical[-1]
                                                    ))

            vertical_scan_array = np.concatenate((vertical_lines, vertical_return_lines), axis=1).ravel()

            # TODO We could drop the last return line in the initialization, as it is not read in anyways till yet.

            voltage_dict = {
                self._ni_channel_mapping[horizontal_axis]: horizontal_scan_array,
                self._ni_channel_mapping[vertical_axis]: vertical_scan_array
            }

            return voltage_dict
        else:
            raise NotImplementedError('Ni Scan arrays could not be initialized for given ScanData dimension')

    def __ao_cursor_write_loop(self):

        t_start = time.perf_counter()

        with self._thread_lock_cursor:
            stop_loop = self._abort_cursor_move

            current_pos_vec = self._pos_dict_to_vec(self.get_position())
            target_pos_vec = self._pos_dict_to_vec(self._target_pos)
            connecting_vec = target_pos_vec - current_pos_vec
            distance_to_target = np.linalg.norm(connecting_vec)

            # Terminate follow loop if target is reached
            if distance_to_target < self._scanner_distance_tol:
                self.__t_last_follow = None
                stop_loop = True

            if not stop_loop:
                # Determine delta t and update timestamp for next iteration
                if not self.__t_last_follow:
                    self.__t_last_follow = time.perf_counter()

                delta_t = t_start - self.__t_last_follow
                self.log.debug(f"Write loop duration: {1e3*(time.perf_counter()-self.__t_last_follow)}")
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
                self.log.debug(f'Cursor_write_loop move to {new_pos}, Dist= {distance_to_target} '
                               f'took {1e3*(time.perf_counter()-t_start)} ms.')

                # Start single-shot timer to call this follow loop again after some wait time
                t_overhead = time.perf_counter() - t_start

                self.__ni_ao_write_timer.start(int(round(1000 * max(0, self._min_step_interval - t_overhead))))

        if stop_loop:
            self.log.debug(f'Cursor_write_loop stopping at {current_pos_vec}, dist= {distance_to_target}')

            self._abort_cursor_movement()

            if self._start_scan_after_cursor:
                self._start_hw_timed_scan()


    def _start_hw_timed_scan(self):

        #self.log.debug("Starting hw timed scan")
        try:
            self._ni_finite_sampling_io().start_buffered_frame()
        except Exception as e:
            self.log.error(f'Could not start frame due to {str(e)}')
            self.module_state.unlock()

        self.__scan_stopped = False
        self._start_scan_after_cursor = False

    def _abort_cursor_movement(self):
        """
        Abort the movement, stop the timer and reset interval, release memory and asynchronisly (via timer) free ni_ao resources.
        """

        self.log.debug(f"Aborting move, took {1e3*(time.perf_counter()-self._t_last_move)} ms in total")

        with self._thread_lock_cursor:
            self._abort_cursor_move = True
            self._target_pos = self.get_position()

        self._toggle_ao_setpoint_channels(False)


    def _move_to_and_start_scan(self, position):
        self._prepare_movement(position)
        self._start_scan_after_cursor = True
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

        try:

            with self._thread_lock_cursor:
                self._abort_cursor_move = False
                if not self._ao_setpoint_channels_active:
                    self._toggle_ao_setpoint_channels(True)

                constr = self.get_constraints()

                for axis, pos in position.items():
                    in_range_flag, _ = in_range(pos, *constr.axes[axis].value_range)
                    if not in_range_flag:
                        position[axis] = float(constr.axes[axis].clip_value(position[axis]))
                        self.log.warning(f'Position {pos} out of range {constr.axes[axis].value_range} '
                                         f'for axis {axis}. Value clipped to {position[axis]}')

                    # TODO Adapt interface to use "in_range"?
                    self._target_pos[axis] = position[axis]
                    self.log.debug(f"New target pos: {self._target_pos}")


                # TODO Add max velocity as a hardware constraint/ Calculate from scan_freq etc?
                if velocity is None:
                    velocity = self.__max_move_velocity
                v_in_range, velocity = in_range(velocity, 0, self.__max_move_velocity)
                if not v_in_range:
                    self.log.warning(f'Requested velocity is exceeding the maximum velocity of {self.__max_move_velocity} '
                                     f'm/s. Move will be done at maximum velocity')

                self._follow_velocity = velocity

            # TODO Keep other axis constant?
            # TODO The whole "write_queue" thing is intended to not make to big of jumps in the scanner move ...

        except:
            self.log.exception("")

    def __init_ao_timer(self):
        self.__ni_ao_write_timer = QtCore.QTimer(parent=self)

        self.__ni_ao_write_timer.setSingleShot(True)
        self.__ni_ao_write_timer.timeout.connect(self.__ao_cursor_write_loop, QtCore.Qt.QueuedConnection)

        # set target value 2x the benchmark value, but not below 2 ms
        self._timer_target_interval_ms = 5
        """
        # not needed anymore to benchmark
        t_ao_loop = self._benchmark_ao_write_loop()
        self._timer_target_interval_ms = int(np.max([2, 2 * 1e3 * t_ao_loop]))
        self.log.debug(f"Set ao write loop timer interval to {self._timer_target_interval_ms} ms "
                       f"after benchmark {1e3*t_ao_loop:.3f} ms")
        """

        self.__ni_ao_write_timer.setInterval(self._timer_target_interval_ms)

    def _benchmark_ao_write_loop(self):

        n_loops = 10
        position = self.get_position()

        t_loops = []
        for i in range(n_loops):
            self._prepare_movement(position)
            # scanner shouldn't move during benchmark, so use current position as write vale
            self.__write_queue = {axis: np.asarray([position[axis]]) for axis in position}
            t_start = time.perf_counter()
            self.__ao_cursor_write_loop()
            t_loops.append(time.perf_counter() - t_start)

        t_loop = np.mean(t_loops)

        return t_loop

    def __start_ao_write_timer(self):
        #self.log.debug(f"ao start write timer in thread {self.thread()}, QT.QThread {QtCore.QThread.currentThread()} ")
        try:
            if not self.is_move_running:
                self.__t_last_follow = time.perf_counter()
                self.log.debug("Starting AO write timer...")
                if self.thread() is not QtCore.QThread.currentThread():
                    QtCore.QMetaObject.invokeMethod(self.__ni_ao_write_timer,
                                                    'start',
                                                    QtCore.Qt.BlockingQueuedConnection)
                else:
                    self.__ni_ao_write_timer.start()

        except:
            self.log.exception("")

    def __stop_ao_write_timer(self):
        #self.log.debug(f"ao stop write timer in thread {self.thread()}, QT.QThread {QtCore.QThread.currentThread()} ")
        try:
            if self.thread() is not QtCore.QThread.currentThread():
                QtCore.QMetaObject.invokeMethod(self.__ni_ao_write_timer,
                                                'stop',
                                                QtCore.Qt.BlockingQueuedConnection)
            else:
                self.__ni_ao_write_timer.stop()

            #self.log.debug("Stopped")
        except Exception as e:
            print(f"{str(e)}")



