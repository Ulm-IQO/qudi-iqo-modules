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
import numpy as np
from PySide2 import QtCore
from qudi.util.mutex import RecursiveMutex, Mutex
from qudi.util.enums import SamplingOutputMode
from qudi.util.helpers import in_range
import time


class NiScanningProbeInterfuse(ScanningProbeInterface):
    """
    TODO: Document

    ni_scanning_probe:
        module.Class: 'interfuse.ni_scanning_probe_interfuse.NiScanningProbeInterfuse'
        connect:
            scan_hardware: 'ni_finite_sampling_io'
            analog_output: 'ni_ao'
        ni_channel_mapping: #TODO: Allow "DevX/..." notation? Actually functions in nfsio check Try none the less once!
            x: 'ao0'  #TODO: Actually DevX needs to be referenced somehow here ...
            y: 'ao1'
            z: 'ao2'
            APD1: 'PFI8'
        position_ranges: # in m #TODO What about channels which are not "calibrated" to 'm', e.g. just use 'V'?
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
        backwards_line_resolution: 50 # optional
        move_velocity: 400e-6 #m/s
    """

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

        self.__write_stack = dict()

        self._target_pos = dict()
        self._stored_target_pos = dict()
        self._velocity = -1.0

        self.__ni_ao_runout_timer = None

        self.__read_pos = -1

        self._thread_lock = RecursiveMutex()  # TODO According to @Neverhorst should rather use Mutex, but scan does not start anymore

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

        # Timer to free resources after pure ni ao
        self.__ni_ao_runout_timer = QtCore.QTimer()
        self.__ni_ao_runout_timer.setSingleShot(True)
        self.__ni_ao_runout_timer.setInterval(5)  # Calc time delta after calls and use this
        # TODO HW test if this Delta t works (used in move velo calculation) 1ms was causing issues on simulated Ni.
        self.__ni_ao_runout_timer.timeout.connect(self.__ao_write_loop, QtCore.Qt.QueuedConnection)

        self._target_pos = self._voltage_dict_to_position_dict(self._ni_ao().setpoints)  # get voltages/pos from ni_ao

    def on_deactivate(self):
        """
        Deactivate the module
        """

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

                except Exception as e:
                    self.log.error(f'Error while initializing ScanData instance: {e}')
                    return True, self.scan_settings

                try:
                    self._ni_finite_sampling_io().set_sample_rate(self._current_scan_frequency)
                    self._ni_finite_sampling_io().set_active_channels(
                        input_channels=(self._ni_channel_mapping[in_ch] for in_ch in self._input_channel_units),
                        output_channels=(self._ni_channel_mapping[ax] for ax in self._current_scan_axes)
                        # TODO Use all axes and keep the unused constant? basically just constants in ni scan dict.
                    )

                    self._ni_finite_sampling_io().set_output_mode(SamplingOutputMode.JUMP_LIST)

                    ni_scan_dict = self._initialize_ni_scan_arrays(self._scan_data)

                    self._ni_finite_sampling_io().set_frame_data(ni_scan_dict)

                except Exception as e:
                    self.log.error(f'Error while configuring Ni fsio hardware: {e}')
                    return True, self.scan_settings

                return False, self.scan_settings

    def move_absolute(self, position, velocity=None):
        """ Move the scanning probe to an absolute position as fast as possible or with a defined
        velocity.

        Log error and return current target position if something fails or a scan is in progress.
        """

        print(f'move abs {time.time()}')
        # assert not self.is_running, 'Cannot move the scanner while, scan is running'
        if self.is_running:
            self.log.error('Cannot move the scanner while, scan is running')
            return self.get_position()

        if not set(position).issubset(self.get_constraints().axes):
            self.log.error('Invalid axes name in position')
            return self.get_position()

        with self._thread_lock:
            start_pos = self.get_position()
            constr = self.get_constraints()

            for axis, pos in position.items():
                in_range_flag, _ = in_range(pos, *constr.axes[axis].value_range)
                if not in_range_flag:
                    self.log.warning(f'Position {pos} out of range {constr.axes[axis].value_range} '
                                     f'for axis {axis}. Value clipped to {position[axis]}')
                position[axis] = float(constr.axes[axis].clip_value(position[axis]))
                # TODO Adapt interface to use "in_range"?
                self._target_pos[axis] = position[axis]

            dist = np.sqrt(np.sum([(position[axis]-start_pos[axis])**2 for axis in position]))

            print(f'Move by distance: {dist*1e6} um')

            # TODO Add max velocity as a hardware constraint/ Calculate from scan_freq etc?
            if velocity is not None and velocity <= self.__max_move_velocity:
                self._velocity = velocity
            elif velocity is not None and velocity > self.__max_move_velocity:
                self.log.warning(f'Requested velocity is exceeding the maximum velocity of {self.__max_move_velocity} '
                                 f'm/s. Move will be done at maximum velocity')
                self._velocity = self.__max_move_velocity
            else:
                self._velocity = self.__max_move_velocity

            granularity = velocity * self.__ni_ao_runout_timer.interval()*1e-3

            self.__write_stack = {axis: np.linspace(start_pos[axis],
                                                    position[axis],
                                                    max(2, np.ceil(dist / granularity).astype('int'))
                                                    )[1:]  # Since start_pos is already taken
                                  for axis in position}
            # TODO Keep other axis constant?
            # TODO The whole "write_stack" thing is intended to not make to big of jumps in the scanner move ...

            if not self._ni_ao().is_active:
                self._ni_ao().set_activity_state(True)
                self.log.info('start')  #TODO Remove
            self._move_start_timestamp = time.time()
            self.__start_ao_runout_timer()

            return self.get_target()

    def move_relative(self, distance, velocity=None):
        """ Move the scanning probe by a relative distance from the current target position as fast
        as possible or with a defined velocity.

        Log error and return current target position if something fails or a 1D/2D scan is in
        progress.


        """
        current_position = self.get_position()
        end_pos = {ax: current_position[ax] + distance[ax] for ax in distance}
        self.move_absolute(end_pos, velocity=velocity)

        return end_pos

    # def wait_for_move_done(self):
    #     # FIXME This just basically stops erverything and just prolongs the timer ... so useless
    #     target = self.get_target()
    #     pos = self.get_position()
    #     move_distance = np.sqrt(np.sum([
    #         (target[axis]-pos[axis])**2 for axis in target]))
    #     while self.get_position().items() != self.get_target().items():
    #         if time.time() - self._move_start_timestamp < 1.1 * move_distance/self._velocity:  # TODO Is this timeout ok?
    #             continue
    #         else:
    #             raise TimeoutError(f'Move took too long')
    #
    # def test(self, pos):
    #     self.move_absolute(pos, velocity=1e-6)
    #     self.wait_for_move_done()

    def get_target(self):
        """ Get the current target position of the scanner hardware
        (i.e. the "theoretical" position).

        @return dict: current target position per axis.
        """

        return self._target_pos

    def get_position(self):
        """ Get a snapshot of the actual scanner position (i.e. from position feedback sensors).
        For the same target this value can fluctuate according to the scanners positioning accuracy.

        For scanning devices that do not have position feedback sensors, simply return the target
        position (see also: ScanningProbeInterface.get_target).

        @return dict: current position per axis.
        """
        return self._voltage_dict_to_position_dict(self._ni_ao().setpoints)

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

        with self._thread_lock:
            try:

                self._scan_data.new_scan()

                # TODO Move to start position before scan ... and also wait till its there

                self._ni_finite_sampling_io().start_buffered_frame()

                self.__read_pos = 0

                self.module_state.lock()

                return False

            except Exception as e:
                self.log.error(f'Something failed while starting the scan: {e}')
                self.module_state.unlock()
                return True

    def stop_scan(self):
        """

        @return bool: Failure indicator (fail=True)
        # FIXME Fix the mess of bool indicators, int return values etc in toolchain
        """
        try:
            if self._ni_finite_sampling_io().is_running:
                self._ni_finite_sampling_io().stop_buffered_frame()
                self.module_state.unlock()

                # FIXME How to handle move after premature stop of scan. Need to move back to "crosshair" position.
                #  As it is now, this will pretty sure cause "scanner jumps" as position is not matching ni_ao.

                return False  # TODO Bool indicators deprecated

            # TODO Somehow gui element is not toggled back after scan done ... whats the difference to scanner dummy?

        except Exception as e:
            self.log.error(f'Error occurred while stopping the finite IO frame:\n{e}')
            return True

    def get_scan_data(self):
        """

        @return (bool, ScanData): Failure indicator (fail=True), ScanData instance used in the scan
        #  TODO change interface
        """

        if not self.is_running:
            self.log.warning('Scan is not running, cannot get new data')
            # return True, None
            return None

        with self._thread_lock:

            samples_per_complete_line = self.__backwards_line_resolution + self._current_scan_resolution[0]
            samples_dict = self._ni_finite_sampling_io().get_buffered_samples(samples_per_complete_line)
            # Potentially we could also use get_buff.. without samples, but that would require some more thought
            # while writing to ScanData

            reverse_routing = {val.lower(): key for key, val in self._ni_channel_mapping.items()}
            # TODO extract terminal stuff? meaning allow DevX/... notation in config?

            try:
                for ni_ch in samples_dict.keys():
                    input_ch = reverse_routing[ni_ch]
                    line_data = samples_dict[ni_ch][self.__backwards_line_resolution:  # Exclude "start line & ret. line
                                                    self.__backwards_line_resolution + self._current_scan_resolution[0]]

                    if self._scan_data.scan_dimension == 1:
                        self._scan_data.data[input_ch] = line_data
                        self.stop_scan()  # TODO Should the hw stop itself?
                        # return False, self._scan_data
                        return self._scan_data

                    elif self._scan_data.scan_dimension == 2:
                        self._scan_data.data[input_ch][:, self.__read_pos] = line_data

                        self.__read_pos += 1
                        if self.__read_pos == self._current_scan_resolution[1]:
                            self.stop_scan()  # TODO Should the hw stop itself?
                        # return False, self._scan_data
                        return self._scan_data
                    else:
                        self.log.error('Invalid Scan Dimension')
                        # return True, None
                        return None

            except Exception as e:
                self.log.error(f'Error occurred while retrieving data {e}, Scan was stopped')
                self.stop_scan()  # TODO Delete later?
                return True, self._scan_data

    def emergency_stop(self):
        """

        @return:
        """
        # TODO: Implement. Yet not used in logic till yet? Maybe sth like this:
        # self._ni_finite_sampling_io().terminate_all_tasks()
        # self._ni_ao().set_activity_state(False)
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

        converted = positions * slope + intercept

        try:
            # In case of single value, use just this value
            voltage_data = converted.item()
        except ValueError:
            voltage_data = converted

        return voltage_data

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

            start_position = self.get_position()[axis]
            # TODO Needs rework, since this is now called in configure scan. Start pos is useless then

            horizontal_start_line = np.linspace(self._position_to_voltage(axis, start_position),
                                                self._position_to_voltage(axis, scan_data.scan_range[0][0]),
                                                self.__backwards_line_resolution)

            horizontal = np.linspace(*self._position_to_voltage(axis, scan_data.scan_range[0]),
                                     horizontal_resolution)

            horizontal_return_line = np.linspace(self._position_to_voltage(axis, scan_data.scan_range[0][1]),
                                                 self._position_to_voltage(axis, scan_data.scan_range[0][0]),
                                                 self.__backwards_line_resolution)
            # TODO Return line for 1d included due to possible hysteresis. Might be able to drop it,
            #  but then get_scan_data needs to be changed accordingly

            horizontal_single_line = np.concatenate((horizontal_start_line,
                                                     horizontal,
                                                     horizontal_return_line,
                                                     horizontal_start_line[::-1]))

            voltage_dict = {self._ni_channel_mapping[axis]: horizontal_single_line}

            return voltage_dict

        elif scan_data.scan_dimension == 2:

            horizontal_resolution = scan_data.scan_resolution[0]
            vertical_resolution = scan_data.scan_resolution[1]

            # horizontal scan array / "fast axis"
            horizontal_axis = scan_data.scan_axes[0]
            horizontal_start_position = self.get_position()[horizontal_axis]
            # line to start of scan with backwards resolution steps
            horizontal_start_line = np.linspace(self._position_to_voltage(horizontal_axis, horizontal_start_position),
                                                self._position_to_voltage(horizontal_axis, scan_data.scan_range[0][0]),
                                                self.__backwards_line_resolution)

            horizontal = np.linspace(*self._position_to_voltage(horizontal_axis, scan_data.scan_range[0]),
                                     horizontal_resolution)

            horizontal_return_line = np.linspace(self._position_to_voltage(horizontal_axis, scan_data.scan_range[0][1]),
                                                 self._position_to_voltage(horizontal_axis, scan_data.scan_range[0][0]),
                                                 self.__backwards_line_resolution)
            # a single back and forth line
            horizontal_single_line = np.concatenate((horizontal, horizontal_return_line))
            # need as much lines as we have in the vertical directions
            horizontal_scan_array = np.tile(horizontal_single_line, vertical_resolution)
            # scan array consists of line to start and then the back and forth lines
            horizontal_scan_array = np.concatenate((horizontal_start_line, horizontal_scan_array))

            # vertical scan array / "slow axis"

            vertcial_axis = scan_data.scan_axes[1]
            vertcial_start_position = self.get_position()[vertcial_axis]
            # line to start of scan with backwards resolution steps
            vertcial_start_line = np.linspace(self._position_to_voltage(vertcial_axis, vertcial_start_position),
                                              self._position_to_voltage(vertcial_axis, scan_data.scan_range[1][0]),
                                              self.__backwards_line_resolution)

            vertical = np.linspace(*self._position_to_voltage(vertcial_axis, scan_data.scan_range[1]),
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

            vertical_scan_array = np.concatenate((vertcial_start_line, vertical_scan_array))

            # TODO We could drop the last return line in the initialization, as it is not read in anyways till yet.

            voltage_dict = {
                self._ni_channel_mapping[horizontal_axis]: horizontal_scan_array,
                self._ni_channel_mapping[vertcial_axis]: vertical_scan_array
            }

            return voltage_dict
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

    def __ao_write_loop(self):
        # TODO Adjust interval in each call to this function, to not accumulate error (feedback from @Neverhorst)
        with self._thread_lock:
            new_voltage = {self._ni_channel_mapping[ax]: self._position_to_voltage(ax, values[0])
                           for ax, values in self.__write_stack.items()}
            self._ni_ao().setpoints = new_voltage
            self.__write_stack = {ax: values[1:] for ax, values in self.__write_stack.items()}

        if not all([values.size == 0 for values in self.__write_stack.values()]):
            if self.thread() is QtCore.QThread.currentThread():
                self.__start_ao_runout_timer()
        else:
            # TODO Add a timeout that the resources are not so frequently freed.
            self._ni_ao().set_activity_state(False)
            self.log.info("Freed up Ni AO resources")
