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

from qudi.interface.scanning_probe_interface import ScanningProbeInterface, ScanConstraints, \
    ScannerAxis, ScannerChannel, ScanData
from qudi.core.configoption import ConfigOption
from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
import numpy as np
from PySide2 import QtCore
from qudi.util.mutex import RecursiveMutex
from qudi.util.enums import SamplingOutputMode


class NiScanningProbeInterfuse(ScanningProbeInterface):
    """
    TODO: Document

    ni_scanning_probe:
        module.Class: 'interfuse.ni_scanning_probe_interfuse.NiScanningProbeInterfuse'
        connect:
            ni_finite_sampling_io: 'ni_finite_sampling_io'
            ni_ao: 'ni_ao'
        ni_channel_mapping: #TODO: Allow "DevX/..." notation? Actually functions in nfsio check Try none the less once!
            x: 'ao0'  #TODO: Actually DevX needs to be referenced somehow here ...
            y: 'ao1'
            z: 'ao2'
            APD1: 'PFI8'
        position_ranges: # in m
            x: [-100e-6, 100e-6]
            y: [0, 200e-6]
            z: [-100e-6, 100e-6]
        frequency_ranges: #Aka values written/retrieved per second; Check with connected HW
            x: [1, 5000]
            y: [1, 5000]
            z: [1, 1000]
        resolution_ranges:
            x: [1, 10000]
            y: [1, 10000]
            z: [2, 1000]
        input_channel_units:
            APD1: 'c/s'
    """

    ni_finite_sampling_io = Connector(interface='FiniteSamplingIOInterface')
    ni_ao = Connector(interface='ProcessValueInterface')
    ni_ao = Connector(interface='ProcessSetpointInterface')

    _ni_channel_mapping = ConfigOption(name='ni_channel_mapping', missing='error')
    _position_ranges = ConfigOption(name='position_ranges', missing='error')
    _frequency_ranges = ConfigOption(name='frequency_ranges', missing='error')
    _resolution_ranges = ConfigOption(name='resolution_ranges', missing='error')
    _input_channel_units = ConfigOption(name='input_channel_units', missing='error')

    __current_position = StatusVar(name='current_position_values', default=dict())  # TODO Move this to Ni AO

    _threaded = True  # Interfuse is by default not threaded.

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        self._current_scan_frequency = -1
        self._current_scan_ranges = [tuple(), tuple()]
        self._current_scan_axes = tuple()
        self._current_scan_resolution = tuple()

        self._scan_data = None

        self._constraints = None

        self.__ni_ao_runout_timer = None

        self._thread_lock = RecursiveMutex()

    def on_activate(self):

        if not self.__current_position:  # TODO Move this to Ni_AO
            self.log.warning(f'Could not recall latest position on start up. Scanner position at center of its ranges')
            self.__current_position = {ax: min(rng) + (max(rng) - min(rng)) / 2 for ax, rng in
                                       self._position_ranges.items()}
            self.move_absolute(self.__current_position)

        # Sanity checks for ni_ao and ni finite sampling io
        assert set(self._position_ranges) == set(self._frequency_ranges) == set(self._resolution_ranges), \
            f'Channels in position ranges, frequency ranges and resolution ranges do not coincide'

        assert set(self._input_channel_units).union(self._position_ranges) == set(self._ni_channel_mapping), \
            f'Not all specified channels are mapped to an ni card physical channel'

        # TODO: Any case where ni_ao and ni_fio potentially don't have the same channels?
        specified_ni_finite_io_channels_set = set(self.ni_finite_sampling_io().constraints.input_channel_units).union(
            set(self.ni_finite_sampling_io().constraints.output_channel_units))
        mapped_channels = set([val.lower() for val in self._ni_channel_mapping.values()])

        assert set(mapped_channels).issubset(specified_ni_finite_io_channels_set), \
            f'Channel mapping does not coincide with ni finite sampling io.'

        # assert set(self._ni_channel_mapping.values()).issubset(
        #     set(self.ni_ao() channels), \
        #     f'Channel mapping does not coincide with ni finite sampling io.' # TODO Add once implemented

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
                                            backscan_configurable=False,  # TODO where is this actually used
                                            has_position_feedback=False,
                                            square_px_only=False)  # TODO where is this actually used

        # Timer to free resources after pure ni ao
        self.__ni_ao_runout_timer = QtCore.QTimer()
        self.__ni_ao_runout_timer.setSingleShot(True)
        self.__ni_ao_runout_timer.setInterval(750)  # TODO HW test
        self.__ni_ao_runout_timer.timeout.connect(self.__free_ni_ao_resources, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        """
        Deactivate the module
        """
        pass

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

        @param dict scan_settings: scan_settings dictionary holding all the parameters 'axes', 'resolution', 'ranges'  # TODO update in interface

        @return (bool, ScanSettings): Failure indicator (fail=True),
                                      altered ScanSettings instance (same as "settings")
        """

        with self._thread_lock:
            if self.is_running:
                self.log.error('Unable to configure scan parameters while scan is running. '
                               'Stop scanning and try again.')
                return True, self.scan_settings

            axes = scan_settings.get('axes', self._current_scan_axes)
            ranges = tuple(
                (min(r), max(r)) for r in scan_settings.get('range', self._current_scan_ranges)
            )
            resolution = scan_settings.get('resolution', self._current_scan_resolution)
            frequency = float(scan_settings.get('frequency', self._current_scan_frequency))

            if 'axes' in scan_settings:
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

                try:
                    self._scan_data = ScanData(
                        channels=tuple(self._constraints.channels.values()),
                        scan_axes=tuple(self._constraints.axes[ax] for ax in axes),
                        scan_range=ranges,
                        scan_resolution=tuple(resolution),
                        scan_frequency=frequency,
                        position_feedback_axes=None
                    )

                    self._current_scan_resolution = tuple(resolution)
                    self._current_scan_ranges = ranges
                    self._current_scan_axes = tuple(axes)
                    self._current_scan_frequency = frequency

                    return False, self.scan_settings

                except Exception as e:
                    self.log.error(f'Error while initializing ScanData instance: {e}')
                    return True, self.scan_settings

    def move_absolute(self, position, velocity=None):
        """ Move the scanning probe to an absolute position as fast as possible or with a defined
        velocity.

        Log error and return current target position if something fails or a scan is in progress.
        """
        if not self.ni_ao().is_active:
            # TODO set activity state -> Initialize ni_ao task Needs implementation
            # TODO Maybe use a timer to free resources once cross hair is no longer dragged
            self.ni_ao().set_activity_state(True)
            # TODO wait for Ni AO to become active? (Time a task set up and check)

        self.__start_ao_runout_timer()
        try:
            self.ni_ao().setpoints = self._position_to_voltage(position)
            self.__current_position.update(position)
            return self.__current_position
        except:
            self.log.error('Something failed')  # TODO Refine!; What about target position? Should this now be set?
            return position

    def move_relative(self, distance, velocity=None):
        """ Move the scanning probe by a relative distance from the current target position as fast
        as possible or with a defined velocity.

        Log error and return current target position if something fails or a 1D/2D scan is in
        progress.


        """
        if not self.ni_ao().is_active:
            # TODO set activity state -> Initialize ni_ao task Needs implementation
            # TODO Maybe use a timer to free resources once cross hair is no longer dragged
            pass
        pass

    def get_target(self):
        """ Get the current target position of the scanner hardware
        (i.e. the "theoretical" position).

        @return dict: current target position per axis.
        """

        self._voltage_to_position(self.ni_ao().process_values)

        return self._voltage_to_position(self.ni_ao().process_values)

    def get_position(self):
        """ Get a snapshot of the actual scanner position (i.e. from position feedback sensors).
        For the same target this value can fluctuate according to the scanners positioning accuracy.

        For scanning devices that do not have position feedback sensors, simply return the target
        position (see also: ScanningProbeInterface.get_target).

        @return dict: current position per axis.
        """
        return self.get_target()

    def start_scan(self):
        """

        @return (bool): Failure indicator (fail=True)
        """
        if self._scan_data is None:
            self.log.error('Scan Data is None. Scan settings need to be configured before starting')
            return True

        if self.is_running:
            self.log.error('Cannot start a scan while scanning probe is already running')
            return True
        else:
            self.module_state.lock()

        with self._thread_lock:

            try:
                self.ni_finite_sampling_io().set_sample_rate(self._current_scan_frequency)
                self.ni_finite_sampling_io().set_active_channels(
                    input_channels=(self._ni_channel_mapping[in_ch] for in_ch in self._input_channel_units),
                    output_channels=(self._ni_channel_mapping[ax] for ax in self._current_scan_axes)
                    # TODO Use all axes and keep the unused constant?
                )

                self.ni_finite_sampling_io().set_output_mode(SamplingOutputMode.JUMP_LIST)

                self._scan_data.new_scan()

                ni_scan_dict = self._initialize_ni_scan_arrays()

                self.ni_finite_sampling_io().set_frame_data(ni_scan_dict)
                self.ni_finite_sampling_io().start_buffered_frame()
                return False

            except AssertionError as e:
                self.log.error(e)
                self.module_state.unlock()
                return True

    def stop_scan(self):
        """

        @return bool: Failure indicator (fail=True)
        """
        try:
            self.ni_finite_sampling_io().stop_buffered_frame()
            self.module_state.unlock()
            return False  # TODO
        except Exception as e:
            self.log.error(f'Error occurred while stopping the finite IO frame:\n{e}')
            return True

    def get_scan_data(self):
        """

        @return (bool, ScanData): Failure indicator (fail=True), ScanData instance used in the scan
        """

        try:
            samples_dict = self.ni_finite_sampling_io().get_buffered_samples()
            # TODO; should we ask for a line with number of samples?
            reverse_routing = {val: key for key, val in self._ni_channel_mapping.items()}

            for ni_ch in samples_dict:
                input_ch = reverse_routing[ni_ch]
                self._scan_data[input_ch]

                fill_direction = lambda x: 1 if x % 2 == 0 else -1



        except Exception as e:
            self.log.error(e)
            return True, self._scan_data

    def emergency_stop(self):
        """

        @return:
        """
        pass

    @property
    def is_running(self):
        """
        Read-only flag indicating the module state.

        @return bool: scanning probe is running (True) or not (False)
        """
        assert self.module_state() in ('locked', 'idle')  # TODO what about other module states?
        if self.module_state() == 'locked':
            return True
        else:
            return False

    @property
    def scan_settings(self):
        with self._thread_lock:
            settings = {'axes': tuple(self._current_scan_axes),
                        'range': tuple(self._current_scan_ranges),
                        'resolution': tuple(self._current_scan_resolution),
                        'frequency': self._current_scan_frequency}
            return settings

    def _position_to_voltage(self, positions):
        """
        @param dict positions: Position (value(s)) to convert to voltage(s) of corresponding ni_channels (keys)

        @return dict: Position(s) converted to voltage(s) (value(s)) [single value & 1D np.array depending on input]
                      for corresponding ni_channel (keys)
        """

        # TODO type checking?
        voltage_data = dict()
        for axis in positions:
            ni_channel = self._ni_channel_mapping[axis]
            voltage_range = self.ni_finite_sampling_io().constraints.output_channel_limits[ni_channel]
            position_range = self.get_constraints().axes[axis].value_range

            slope = np.diff(voltage_range) / np.diff(position_range)
            intercept = voltage_range[1] - position_range[1] * slope

            converted = positions[axis] * slope + intercept

            try:
                # In case of single value, use just this value
                voltage_data[ni_channel] = converted.item()
            except ValueError:
                voltage_data[ni_channel] = converted

        return voltage_data

    def _voltage_to_position(self, voltages):
        """
        @param dict voltages: Voltages (value(s)) to convert to position(s) of corresponding axis (keys)

        @return dict: Voltage(s) converted to position(s) (value(s)) [single value & 1D np.array depending on input] for
                      for corresponding axis (keys)
        """

        reverse_routing = {val: key for key, val in self._ni_channel_mapping.items()}

        # TODO type checking?
        positions_data = dict()
        for ni_channel in voltages:
            axis = reverse_routing[ni_channel]
            voltage_range = self.ni_finite_sampling_io().constraints.output_channel_limits[ni_channel]
            position_range = self.get_constraints().axes[axis].value_range

            slope = np.diff(position_range) / np.diff(voltage_range)
            intercept = position_range[1] - voltage_range[1] * slope

            converted = voltages[ni_channel] * slope + intercept

            try:
                # In case of single value, use just this value
                positions_data[axis] = converted.item()
            except ValueError:
                positions_data[axis] = converted

        return positions_data

    def _initialize_ni_scan_arrays(self):
        """
        @return dict: Where keys coincide with the ni_channel for the current scan axes and values are the
                      corresponding voltage 1D numpy arrays for each axis
        """

        if self._scan_data.scan_dimension == 1:
            positions_dict = dict(zip(
                self._current_scan_axes,
                np.linspace(*self._current_scan_ranges[0], self._current_scan_resolution[0]))
            )

            return self._position_to_voltage(positions_dict)

        elif self._scan_data.scan_dimension == 2:
            backwards_line_resolution = 50  # TODO adjust toolchain to incorporate this

            horizontal_resolution = self._current_scan_resolution[0]
            vertical_resolution = self._current_scan_resolution[1]

            # horizontal scan array / "fast axis"
            horizontal = np.linspace(*self._current_scan_ranges[0], horizontal_resolution)

            horizontal_return_line = np.linspace(self._current_scan_ranges[0][1],
                                                 self._current_scan_ranges[0][0],
                                                 backwards_line_resolution)

            horizontal_single_line = np.concatenate((horizontal, horizontal_return_line))

            horizontal_scan_array = np.tile(horizontal_single_line, vertical_resolution)

            # vertical scan array / "slow axis"
            vertical = np.linspace(*self._current_scan_ranges[1], vertical_resolution)
            self.vert = vertical

            vertical_return_lines = np.linspace(vertical[:-1], vertical[1:], backwards_line_resolution).T
            vertical_return_lines = np.concatenate((vertical_return_lines,
                                                    np.ones((1, backwards_line_resolution))*vertical[-1]
                                                    ))

            vertical_lines = np.repeat(vertical.reshape(vertical_resolution, 1), horizontal_resolution, axis=1)

            vertical_scan_array = np.concatenate((vertical_lines, vertical_return_lines), axis=1).ravel()

            positions_dict = dict(zip(self._current_scan_axes,
                                      (horizontal_scan_array, vertical_scan_array)))

            return self._position_to_voltage(positions_dict)
        else:
            raise NotImplementedError('Ni Scan arrays could not be initialized for given ScanData dimension')

    def __start_ao_runout_timer(self):
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__ni_ao_runout_timer,
                                            'start',
                                            QtCore.Qt.BlockingQueuedConnection)
        else:
            self.__ni_ao_runout_timer.start()

    def __stop_ao_runout_timer(self):
        if self.thread() is not QtCore.QThread.currentThread():
            QtCore.QMetaObject.invokeMethod(self.__ni_ao_runout_timer,
                                            'stop',
                                            QtCore.Qt.BlockingQueuedConnection)
        else:
            self.__ni_ao_runout_timer.stop()

    def __free_ni_ao_resources(self):
        self.log.info("Freed up Ni AO resources")
        self.ni_ao().set_activity_state(False)
