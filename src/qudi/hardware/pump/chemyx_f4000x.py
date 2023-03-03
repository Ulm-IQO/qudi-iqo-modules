# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware module for Chemyx F4000X Syringe pump.

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

import pyvisa
from pyvisa import constants
import time
from enum import Enum
from qudi.core.configoption import ConfigOption
from qudi.interface.syringe_pump_interface import SyringePumpInterface, PumpStatus, SyringePumpConstraints 

def response_splitter(response, returntype=str, split_delimiter=' = '):
    """
    Method that splits a response of form "<variable_name> = <value>"
    and returns only the value by formatting it with the given returntype.
    @param response, str: response that should be split
    @param returntype, default str: specifies the type of the return value
    @return returnvalue, returntype: return value of type returntype
    """
    returnvalue = returntype(response.split(split_delimiter)[-1])
    return returnvalue

class PumpMode(Enum):
    """
    Enum class that contains the operating modes of the pump
    """
    BASIC = 0
    # for now only Basic mode has been implemented and program mode may not function as intended
    PROGRAM = 1

class PumpParameters(Enum):
    """
    Enum class that contains all settable parameters of the pump.
    """
    UNIT = 'unit'
    DIAMETER = 'dia'
    TRANSFER_RATE = 'rate'
    TIME = 'time'
    TRANSFER_VOLUME = 'volume'
    DELAY = 'delay' # hasn't been implemented here

class ChemyxF4000X(SyringePumpInterface):
    """
    Class for the Chemyx F4000X syringe pump that is connected using the serial connection.
    This file implements the basic functionality of the pump in Basic mode.
    
    Example Config File:
        chemyxf4000x:
            module.Class: 'pump.chemyx_f4000x.ChemyxF4000X'
            options:
                visa_address: 'ASRL5::INSTR' # visa address of the device
                visa_timeout: 100 # timeout in milliseconds for visa requests
                visa_query_delay: 0.5 # delay in seconds between write and read operations on the visa interface
                baud_rate: 115200 # baud rate which is set on the device
                pump_number: 1 # which pump is being used? {1,2}

    """
    # Config Options serial connection
    _visa_address = ConfigOption(name='visa_address', missing='error')
    _baud_rate = ConfigOption(name='baud_rate', default=9600, missing='warn')
    _timeout = ConfigOption(name='visa_timeout', default=1000, missing='warn')
    _query_delay = ConfigOption(name='visa_query_delay', default=0.0, missing='warn')

    # Syringe-specific config options
    _pump_number = ConfigOption(name='pump_number', missing='error') # the number of the pump that should be controlled by the module

    _data_bits = 8
    _parity = constants.Parity.none 
    _stop_bits = constants.StopBits.one
    _flow_control = constants.ControlFlow.none

    _mode = PumpMode.BASIC

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._constraints = SyringePumpConstraints()
        self._transfer_time = None
        self._transfer_rate = None
        self._transfer_volume = None
        self._units = PumpUnits.ML_PER_MIN # TODO make the units choosable with an enum class

        # Visa Resource Manager
        self._rm = pyvisa.ResourceManager()

        # Variable for storing the instance of a pyvisa connection to the pump
        self._pump = None

        # assign the constraints variable
        self._constraints = SyringePumpConstraints()
        # set the units for the constraints
        self._constraints.units = PumpUnits.UL_PER_MIN
        # assign the constraints for this device given by the manufacturer
        # syringe inner diameter that can be inserted [mm]
        self._constraints.inner_diameter.min = 0.103
        self._constraints.inner_diameter.max = 60.0
        # syringe volume that can be inserted [uL]
        self._constraints.transfer_volume.min = 0.5
        self._constraints.transfer_volume.max = 100 * 10**3
        # flow rates allowed by the pump [uL/min]
        self._constraints.transfer_rate.min = 0.0001
        self._constraints.transfer_rate.max = 170.5 * 10**3

        
    def on_activate(self):
        # open serial connection
        try:
            self._pump = self._rm.open_resource(self._visa_address)
        except:
            self._pump = None
            self.log.error(f'Given visa address "{self._visa_address}" not found in detected visa resources.')
            return

        # set serial connection settings for pyvisa
        self._pump.write_termination = '\r\n'
        self._pump.read_termination = '\r\n>'
        self._pump.baud_rate = self._baud_rate
        self._pump.data_bits = self._data_bits
        self._pump.partiy = self._parity
        self._pump.stop_bits = self._stop_bits
        self._pump.flow_control = self._flow_control
        self._pump.timeout = self._timeout
        self._pump.query_delay = self._query_delay
        
        # set the constraints for the pump
        self.get_constraints()
        # get the currently set settings on the pump
        self.get_last_settings()

    def on_deactivate(self):
        if self.status == PumpStatus.RUNNING:
            self.stop_pump()
        self._pump.close()
        self._rm.close()
        self._pump = None
        self._rm = None
    
    def start_pump(self):
        """
        Method to activate the pump with its currently set settings.
        
        @return int: error code (0: OK, -1: error)
        """
        try:
            response = self.send_command(f'{self._pump_number} start {self._mode.value}')[0]
            # set the stall check counter to 0 and start the timer for the stall checks
            self.log.info(response)
            return 0
        except:
            self.log.error('Could not start the pump.')
            return -1

    def stop_pump(self):
        """
        Method to stop the pump from its current operation.
        
        @return int: error code (0: OK, -1: error)
        """
        try:
            response = self.send_command(f'{self._pump_number} stop {self._mode.value}')[0]
            self.log.info(response)
            return 0
        except:
            self.log.error('Could not stop the pump.')
            return -1

    
    def get_limits(self):
        """
        Method that returns the limits fo transfer rate and transfer volume,
        which is set by the inner diameter of the used syringe.

        @return tuple: ((min_rate, max_rate),(min_volume, max_volume))
        """
        try:
            response = self.send_command(f'{self._pump_number} read limit parameter {self._mode.value}', clear_blank_lines=True)[0]
        except:
            self.log.error(f'Could not get the current limits of pump {self._pump_number} in mode {self._mode.name}.')
            return ((None, None), (None, None))
        # split the response into individual str and convert to float
        response = response.split(' ')
        response = [float(entry) for entry in response]
        # return the tuple, correctly sorted as in the docstring
        return ((response[1], response[0]),(response[3], response[2]))

    def get_constraints(self):
        """
        Method to get constraints of pump.
        The constraints give information on the min/max allowed transfer_volume and rate.

        @return dict of PumpConstraints object: contains the different hardware specific constraints for each pump
        """
        # get the current transfer volume and rate limits from the pump
        current_limits = self.get_limits()
        # get the current units set on the pump and add them to the constraints instance
        self._constraints.units = self.units
        # assign min and max values for the constraints
        self._constraints.transfer_rate.min = current_limits[0][0]
        self._constraints.transfer_rate.max = current_limits[0][1]
        self._constraints.transfer_volume.min = current_limits[1][0]
        self._constraints.transfer_volume.max = current_limits[1][1]
        return self._constraints

    def _get_current_pump_parameters(self):
        """
        Method to query the currently set parameters on pump the pump.

        @return out dict: contains the currently set parameters on the specified pump as a dict {PumpParameter object: value}.
        """
        # send the command and get the response of the pump
        response = self.send_command(f'view parameter {self._pump_number}', clear_blank_lines=True)[0]
        # split the response into the individual variables and remove first blank line
        response = response.split(' \n\r')[1:]
        # cut off parameters from other pump
        if self._pump_number == 1:
            response = response[1:7]
        if self._pump_number == 2:
            response = response[8:]
        
        out = {}
        for line in response:
            splitted_line = line.split(' = ')
            name, value = splitted_line[0], float(splitted_line[-1])
            # assign name and value to the out dict
            out[PumpParameters(name)] = value

        return out

    @property
    def status(self):
        """
        Method to get the current status of the pump (e.g. idle, running, etc.).

        @return PumpStatus object: carries the current status of the pump 
        """
        status = PumpStatus(response_splitter(self.send_command(f'{self._pump_number} pump status')[-1], int))
        if status == PumpStatus.STALLED:
            self.log.error(f'Pump {self._pump_number} has stalled!')
        return status

    @property
    def inner_diameter(self):
        """
        Method to get the currently set value for the inner diameter of the syringe.

        @return float: currently set inner diameter
        """
        return self._get_current_pump_parameters()[PumpParameters('dia')]

    @inner_diameter.setter
    def inner_diameter(self, diameter):
        """
        Method to set the inner diameter of the used syringe.

        @param diameter float: inner diameter that should be set in [mm]

        @return int: error code (0: Ok, -1: error)
        """
        if self.status == PumpStatus.RUNNING:
            self.log.error("Can't set inner diameter as pump is still running!")
            return -1
        if self.in_constraints(diameter, self._constraints.inner_diameter):
            response = self.send_command(f'{self._pump_number} set diameter {diameter}')
            response = response_splitter(response[0], returntype=float)
            if self.change_successful(diameter, response):
                self.log.info(f"Set inner diameter to {response} mm for pump {self._pump_number}.")
                self._inner_diameter = response
                # update constraints
                self.get_constraints()
                return 0
            return -1
        self.log.error(f"Could not set inner diameter {diameter} mm for pump {self._pump_number} because the value is not in the constraints ({self._constraints.inner_diameter.min},{self._constraints.inner_diameter.max}) mm.")
        return -1

    @property
    def transfer_time(self):
        """
        Method to get the currently set transfer time.

        @return float: current transfer time
        """
        return 1 / self._get_current_pump_parameters()[PumpParameters('rate')]
        

    @transfer_time.setter
    def transfer_time(self, transfer_time):
        """
        Method to set the transfer time of the pump.

        @param transfer_time float: transfer time that should be set in (unit is determined by the set units)

        @return int: error code (0: Ok, -1: error)
        """
        if self.status == PumpStatus.RUNNING:
            self.log.error("Can't set transfer time as pump is still running!")
            return -1
        if self.in_constraints(1/transfer_time, self._constraints.transfer_rate):
            response = self.send_command(f'{self._pump_number} set time {transfer_time}', clear_blank_lines=True)
            set_rate = response_splitter(response[0], float)
            set_time = response_splitter(response[1], float)
            if self.change_successful(set_time, transfer_time):
                self.log.info(f"Set transfer time to {set_time} {self._units.get_str_of_denominator()} for pump {self._pump_number}, resulting in a transfer rate of {set_rate} {self._units.get_str_of_unit()}.")
                self._transfer_time = set_time
                self._transfer_rate = set_rate
                return 0
            return -1
        self.log.error(f"Could not set transfer time {transfer_time} {self.units.get_str_of_denominator()} for pump {self._pump_number} because the value is not in the constraints ({1/self._constraints.transfer_rate.min},{1/self._constraints.transfer_rate.max}) {self.units.get_str_of_denominator()}.")
        return -1
    
    @property
    def transfer_rate(self):
        """
        Method to get the currently set transfer rate.

        @return float: current transfer time
        """
        return self._get_current_pump_parameters()[PumpParameters('rate')]


    @transfer_time.setter
    def transfer_rate(self, transfer_rate):
        """
        Method to set the transfer rate of the pump.

        @param transfer_rate float: transfer rate that should be set in (unit is determined by the set units)

        @return int: error code (0: Ok, -1: error)
        """
        if self.status == PumpStatus.RUNNING:
            self.log.error("Can't set transfer rate as pump is still running!")
            return -1
        if self.in_constraints(transfer_rate, self._constraints.transfer_rate):
            response = self.send_command(f'{self._pump_number} set rate {transfer_rate}', clear_blank_lines=True)
            response = response_splitter(response[-1], float)
            if self.change_successful(transfer_rate, response):
                self.log.info(f"Set transfer rate to {response} {self._units.get_str_of_unit()} for pump {self._pump_number}.")
                self._transfer_rate = response
                return 0
            return -1
        self.log.error(f"Could not set transfer rate {transfer_rate} {self.units.get_str_of_unit()} for pump {self._pump_number} because the value is not in the constraints ({self._constraints.transfer_rate.min},{self._constraints.transfer_rate.max}) {self.units.get_str_of_unit()}.")
        return -1


    @property
    def transfer_volume(self):
        """
        Method that returns the currently set transfer volume.

        @return float: current transfer volume
        """
        return self._get_current_pump_parameters()[PumpParameters('volume')]


    @transfer_volume.setter
    def transfer_volume(self, transfer_volume):
        """
        Method to set the transfer volume of the pump.

        @param transfer_volume float: transfer volume that should be set (unit is determined by the set units)

        @return int: error code (0: Ok, -1: error)
        """
        if self.status == PumpStatus.RUNNING:
            self.log.error("Can't set transfer volume as pump is still running!")
            return -1
        if self.in_constraints(transfer_volume, self._constraints.transfer_volume):
            response = self.send_command(f'{self._pump_number} set volume {transfer_volume}', clear_blank_lines=True)
            response = response_splitter(response[-1], float)
            if self.change_successful(transfer_volume, response):
                self.log.info(f"Set transfer volume to {response} {self._units.get_str_of_numerator()} for pump {self._pump_number}.")
                self._transfer_volume = response
                return 0
            return -1
        self.log.error(f"Could not set transfer volume {transfer_volume} {self.units.get_str_of_numerator()} for pump {self._pump_number} because the value is not in the constraints ({self._constraints.transfer_volume.min},{self._constraints.transfer_volume.max}) {self.units.get_str_of_numerator()}.")
        return -1

    @property
    def units(self):
        """
        Method to get the currently set units of the pump.

        @return PumpUnits object: specifies the pump unit
        """
        return PumpUnits(self._get_current_pump_parameters()[PumpParameters('unit')])


    @units.setter
    def units(self, unit):
        """
        Method to set the units of the specified pump.

        @param unit PumpUnits object: specifies the unit to set to

        @return int: error code (0: Ok, -1: error)
        """
        response = PumpUnits.get_unit_from_response(self.send_command(f'{self._pump_number} set units {unit.value}')[0])
        self.log.info(f"Set units to {response.get_str_of_unit()} for pump {self._pump_number}.")
        self._units = response
        return 0

    def get_last_settings(self):
        """
        Method that gets all currently set settings on the pump and assignes
        the class variables to them.
        """
        parameters = self._get_current_pump_parameters()
        self._inner_diameter = parameters[PumpParameters('dia')]
        self._units = PumpUnits(parameters[PumpParameters('unit')])
        self._transfer_rate = parameters[PumpParameters('rate')]
        self._transfer_time = 1 / parameters[PumpParameters('rate')]
        self._transfer_volume = parameters[PumpParameters('volume')]

    def send_command(self, command, clear_blank_lines=False):
        """
        Method that sends the command to the pump and catches the respond of the device.
        It will wait for the specified _query_delay and read the response in bytes and strip all the unecessary parts.

        @param str command: command that should be sent
        @param clear_blank_lines bool, default False: should blank lines be cleared from the response?

        @return list response: response of the system, each entry is a line of the response
        """
        self._pump.write(command)
        time.sleep(self._pump.query_delay)
        response = self._pump.read_bytes(self._pump.bytes_in_buffer)
        # strip the EOM and EOL chars
        response = response.strip(b"\r\n>")
        response = response.split(b"\r\n")
        # remove the command itself from the response
        # and decode the response to a string
        i = 0
        while i < len(response):
            if response[i] == bytes(command, 'utf8'):
                response.pop(i)
                # check whether there is a response (empty list)
                if not response:
                    self.log.error("No response received. Check whether the parameters are out of range of the pump.")
                    return
                continue
            if response[i] == bytes('', 'utf8') and clear_blank_lines:
                response.pop(i)
                continue
            response[i] = response[i].decode('utf8')
            i += 1
        return response

    def change_successful(self, desired_value, received_value):
        """
        Method that checks whether the setting that should be set to <desired_value>
        was actually set by the pump.
        Therefore, the <received_value> will be compared to the <desired_value>,
        logs an error if the two variables don't match.

        @param desired_value int, float: value that should be set on the pump
        @param received_value int, float: value that has been set, which was received from the pump

        @return bool, True if <desired_value> == <received_value>, else False
        """
        if desired_value != received_value:
            self.log.error("Value that should be set on the pump does not match the received actually set value. Check whether you entered a value within the constraints of the current settings.")
            return False
        return True

    def in_constraints(self, desired_value, constraint):
        """
        Method that checks, whether the desired_value lies within the bounds of the constraints.

        @param desired_value float: value that should be set
        @param constraint ScalarConstraint object: contains the min and max allowed value for this parameter

        @return bool: True if desired_value is within the constraint, False if not
        """
        if constraint.min < float(abs(desired_value)) < constraint.max:
            return True
        return False

class PumpUnits(Enum):
    """
    Enum class that gives the correct units given the integer returned by the pump.
    """
    ML_PER_MIN = 0 # mL/min
    ML_PER_HR =  1 # mL/hr
    UL_PER_MIN = 2 # uL/min
    UL_PER_HR =  3 # uL/hr
    
    def get_unit_from_response(response):
        """
        Method that gives the right name for the given unit integer in the response string.
        @param str response: string of the response given by the pump
        @return PumpUnits object, the unit the pump is set to
        """
        # split the response at the ' = '
        # and use the last element which contains the numer image
        unit_int = response_splitter(response, returntype=int)
        # this can easily break, if the baud rate is too low and thus not only an integer is returned here
        # if this happens try increasing the baud rate or increase the delay time
        return PumpUnits(unit_int)

    def get_str_of_unit(self):
        if self == PumpUnits.ML_PER_MIN:
            return "mL/min"
        elif self == PumpUnits.ML_PER_HR:
            return "mL/hr"
        elif self == PumpUnits.UL_PER_MIN:
            return "uL/min"
        elif self == PumpUnits.UL_PER_HR:
            return "uL/hr"

    def get_str_of_numerator(self):
        if self == PumpUnits.ML_PER_MIN:
            return "mL"
        elif self == PumpUnits.ML_PER_HR:
            return "mL"
        elif self == PumpUnits.UL_PER_MIN:
            return "uL"
        elif self == PumpUnits.UL_PER_HR:
            return "uL"

    def get_str_of_denominator(self):
        if self == PumpUnits.ML_PER_MIN:
            return "min"
        elif self == PumpUnits.ML_PER_HR:
            return "hr"
        elif self == PumpUnits.UL_PER_MIN:
            return "min"
        elif self == PumpUnits.UL_PER_HR:
            return "hr"

