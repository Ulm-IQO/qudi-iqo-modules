# -*- coding: utf-8 -*-
"""
APT Motor Controller for Thorlabs.

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

"""
This module was developed from PyAPT, written originally by Michael Leung
(mcleung@stanford.edu). Have a look in:
    https://github.com/HaeffnerLab/Haeffner-Lab-LabRAD-Tools/blob/master/cdllservers/APTMotor/APTMotorServer.py
APT.dll and APT.lib were provided to PyAPT thanks to SeanTanner@ThorLabs .
All the specific error and status code are taken from:
    https://github.com/UniNE-CHYN/thorpy
The rest of the documentation is based on the Thorlabs APT Server documentation
which can be obtained directly from
    https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=APT
"""


from logging import Logger
from typing import Dict, List, Type, Union
from qudi.core.meta import ConfigOption
from qudi.interface.motor_interface import MotorInterface
from dataclasses import dataclass
from thorlabs_apt_device.devices.tdc001 import TDC001
from thorlabs_apt_device.devices.aptdevice_motor import APTDevice_Motor
from thorlabs_apt_device.utils import to_pos, to_acc, to_vel, from_acc, from_pos, from_vel


@dataclass(frozen=True)
class ConversionFactor:
    encoder_factor: float
    velocity_factor: float
    acceleration_factor: float
    time_factor: float


# Taken from the Thorlabs APT Communications manual
@dataclass(frozen=True)
class ConversionFactorPRM1Z8(ConversionFactor):
    encoder_factor = 1919.6418578623391
    velocity_factor = 42941.66
    acceleration_factor = 14.66
    time_factor = 2048 / (6e6)

BRUSHED_DC_CONTROLLER_CONVERSION_FACTOR_REGISTRY: Dict[str, Type[ConversionFactor]] = {
    "PRM1-Z8": ConversionFactorPRM1Z8,
}


class APTMotorAxis:
    def __init__(
        self, controller: APTDevice_Motor, label: str, bay: int, channel: int, conversion_factors: Type[ConversionFactor], logger: Logger
    ) -> None:
        self.controller = controller
        self.label = label
        self.bay = bay
        self.channel = channel
        self.conversion_factors = conversion_factors
        self.log = logger

    def get_constraints(self) -> dict:
        raise NotImplementedError

    def move_rel(self, distance: float) -> None:
        encoded = from_pos(distance, self.conversion_factors.encoder_factor)
        self.log.debug(f"axis '{self.label}' moving relative by {distance} ({encoded})")
        self.controller.move_relative(
            encoded, bay=self.bay, channel=self.channel
        )

    def move_abs(self, position: float) -> None:
        encoded = from_pos(position, self.conversion_factors.encoder_factor)
        self.log.debug(f"axis '{self.label}' moving absolute to {position} ({encoded})")
        self.controller.move_absolute(
            encoded, bay=self.bay, channel=self.channel
        )

    def abort(self) -> None:
        self.log.debug(f"axis '{self.label}' stopping movement")
        self.controller.stop(bay=self.bay, channel=self.channel)

    def get_status(self) -> dict:
        keys_to_check = ["moving_forward", "moving_reverse", "jogging_forward", "jogging_reverse"]
        return {key: self.status[key] for key in keys_to_check}

    def calibrate(self) -> None:
        self.log.debug(f"axis '{self.label}' started homing")
        self.controller.home()

    def enable(self, state: bool = True) -> None:
        self.log.debug(f"axis '{self.label}' {'enabled' if state else 'disabled'}")
        self.controller.set_enabled(state=state, bay=self.bay, channel=self.channel)

    @property
    def position(self) -> float:
        return to_pos(self.status['position'], self.conversion_factors.encoder_factor)

    @property
    def velocity(self) -> float:
        return to_vel(self.velocity_parameters["max_velocity"], self.conversion_factors.velocity_factor, self.conversion_factors.time_factor)

    @velocity.setter
    def velocity(self, velocity: float) -> None:
        self._set_velocity_params(velocity=velocity, acceleration=self.acceleration)

    @property
    def acceleration(self) -> float:
        return to_acc(
            self.velocity_parameters["acceleration"],
            self.conversion_factors.acceleration_factor,
            self.conversion_factors.time_factor,
        )

    @acceleration.setter
    def acceleration(self, acceleration: float) -> None:
        self._set_velocity_params(velocity=self.velocity, acceleration=acceleration)

    @property
    def status(self) -> dict:
        return self.controller.status_[self.bay][self.channel]

    @property
    def velocity_parameters(self) -> dict:
        return self.controller.velparams_[self.bay][self.channel]

    def _set_velocity_params(self, velocity: float, acceleration: float) -> None:
        encoded_acc = from_acc(acceleration, self.conversion_factors.acceleration_factor, self.conversion_factors.time_factor)
        encoded_vel = from_vel(velocity, self.conversion_factors.velocity_factor, self.conversion_factors.time_factor)

        self.log.debug(f"axis '{self.label}' setting velocity parameters to velocity: {velocity} ({encoded_vel}), acceleration: {acceleration} ({encoded_acc})")
        self.controller.set_velocity_params(
            acceleration=encoded_acc,
            max_velocity=encoded_vel,
            bay=self.bay,
            channel=self.channel,
        )


class APTMotor(MotorInterface):
    """
    APT Motor Controller Baseclass.
    This base class should not be used directly, but rather inherited from the individual controller implementations below.

    A config file entry for a rotary stage would look like this:

    apt_motor:
        module.Class: 'motor.aptmotor.APTMotor'
        options:
            serial_port: 'COM5'
            axes:
                x:
                    bay: 0
                    channel: 0
                    stage: "PRM1-Z8"

    """


    _serial_port = ConfigOption("serial_port", default="COM1", missing="warn")
    _axes_configs = ConfigOption(name='axes', default=dict(), missing='warn')

    def __init__(self, controller_class: APTDevice_Motor, *args, **kwargs) -> None:
        self._controller_class = controller_class
        self._axes = {}

        super().__init__(*args, **kwargs)

    def on_activate(self) -> None:
        self._connect()

    def on_deactivate(self) -> None:
        self._controller.close()
        del self._controller

    def get_constraints(self):
        raise NotImplementedError

    def move_rel(self, param_dict):
        return self._do_for_multiple_axes(param_dict, lambda ax, pos, : ax.move_rel(pos))

    def move_abs(self, param_dict):
        return self._do_for_multiple_axes(param_dict, lambda ax, pos, : ax.move_abs(pos))

    def abort(self):
        return self._do_for_multiple_axes(None, lambda ax: ax.abort())

    def get_pos(self, param_list=None):
        return self._do_for_multiple_axes(param_list, lambda ax: ax.position)

    def get_status(self, param_list=None):
        return self._do_for_multiple_axes(param_list, lambda ax: ax.get_status())

    def calibrate(self, param_list=None):
        return self._do_for_multiple_axes(param_list, lambda ax: ax.calibrate())

    def get_velocity(self, param_list=None):
        return self._do_for_multiple_axes(param_list, lambda ax: ax.velocity)

    def set_velocity(self, param_dict):
        return self._do_for_multiple_axes(param_dict, lambda ax, vel, : setattr(ax, "velocity", vel))

    def _do_for_multiple_axes(self, param_iterator: Union[List, Dict, None], method):
        if param_iterator is None:
            for _, ax in self._axes.items():
                return method(ax)
        elif isinstance(param_iterator, Dict):
            for ax_name, param in param_iterator.items():
                return method(self._axes[ax_name], param)
        else:
            for ax_name in param_iterator:
                return method(self._axes[ax_name])


    def _connect(self) -> None:
        self._controller = self._controller_class(serial_port=self._serial_port, home=False)
        self._axes = {ax: APTMotorAxis(self._controller, ax, self._axes_configs[ax]["bay"], self._axes_configs[ax]["channel"], BRUSHED_DC_CONTROLLER_CONVERSION_FACTOR_REGISTRY[self._axes_configs[ax]["stage"]], self.log) for ax in self._axes_configs}


class TDC001Motor(APTMotor):
    """
    Hardware class for the Thorlabs TDC001 controller combined with the PRM1Z8 rotational stage.

    tdc001_motor:
        module.Class: 'motor.aptmotor.TDC001Motor'
        options:
            serial_port: 'COM5'
            stage: "PRM1-Z8"

    """
    _stage = ConfigOption("stage", missing="error")
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(TDC001, *args, **kwargs)

    def on_activate(self) -> None:
        # only one axis can be connected to the controller, if no axes_config is given set default
        config = {"bay": 0, "channel": 0, "stage": self._stage}
        if not self._axes_configs:
            self._axes_configs = {"x": config}
        else:
            self._axes_configs = {list(self._axes_configs.keys())[0]: config}
        return super().on_activate()

