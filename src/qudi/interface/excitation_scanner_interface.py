from abc import abstractmethod
from collections import OrderedDict
from typing import Iterable, Union, Tuple, Type
from collections import OrderedDict
import numpy as np
from dataclasses import InitVar, dataclass, field

from qudi.core.module import Base
_Variable_Type = Union[int, float, bool]


@dataclass(frozen=True)
class ExcitationScanDataFormat:
    time_column_number: int
    step_number_column_number: int
    frequency_column_number: int
    data_column_number: Iterable[int]
    data_column_unit: Iterable[str]
    data_column_names: Iterable[str]

@dataclass
class ExcitationScanControlValue:
    name: str
    value: _Variable_Type
    limits: Tuple[_Variable_Type, _Variable_Type]
    type: Type[_Variable_Type]
    unit: str = ''

@dataclass
class ExcitationScanControlVariable:
    name: str
    limits: Tuple[_Variable_Type, _Variable_Type]
    type: Type[_Variable_Type]
    unit: str = ''

@dataclass
class ExcitationScannerConstraints:
    exposure_limits: Tuple[float, float]
    repeat_limits: Tuple[int, int]
    idle_value_limits: Tuple[float, float]
    control_variables_list: InitVar[Iterable[ExcitationScanControlVariable]]
    control_variables: OrderedDict[str, ExcitationScanControlVariable] = field(init=False)
    def __post_init__(self, control_variables_list):
        self.control_variables = OrderedDict([
            (cv.name, cv)
            for cv in control_variables_list
        ])
    def variable_in_range(self, name:str,value:_Variable_Type):
        if name not in self.control_variables:
            return False
        cv = self.control_variables[name]
        if cv.type == bool:
            return True
        mini,maxi = cv.limits
        return mini <= value <= maxi
    def set_limits(self, name:str,mini:_Variable_Type,maxi:_Variable_Type):
        if name not in self.control_variables:
            raise KeyError(f"Unknown variable {name}")
        cv = self.control_variables[name]
        cv.limits = mini,maxi
    def exposure_in_range(self, value):
        mini,maxi = self.exposure_limits
        return mini <= value <= maxi
    def repeat_in_range(self, value):
        mini,maxi = self.repeat_limits
        return mini <= value <= maxi
    def idle_value_in_range(self, value):
        mini,maxi = self.idle_value_limits
        return mini <= value <= maxi


class ExcitationScannerInterface(Base):
    @property
    @abstractmethod
    def scan_running(self) -> bool:
        "Return True if a scan can be launched."
        pass
    @property 
    @abstractmethod
    def state_display(self) -> str:
        "Return a string that gives the current operation running for display purposes."
        pass
    @abstractmethod
    def start_scan(self) -> None:
        "Start scanning in a non_blocking way."
        pass
    @abstractmethod
    def stop_scan(self) -> None:
        "Stop scanning in a non_blocking way."
        pass
    @property
    @abstractmethod
    def constraints(self) -> ExcitationScannerConstraints:
        "Get the constraints for this scanner."
        pass
    @abstractmethod
    def set_control(self, variable: str, value:_Variable_Type) -> None:
        "Set a control variable value."
        pass
    @abstractmethod
    def get_control(self, variable: str) -> _Variable_Type:
        "Get a control variable value."
        pass
    @property 
    def control_dict(self) -> OrderedDict[str, ExcitationScanControlValue]:
        "Get a dict with all the control variables."
        return OrderedDict([
            (k,
             ExcitationScanControlValue(name=k, 
                                        limits=cv.limits, 
                                        type=cv.type, 
                                        unit=cv.unit, 
                                        value=self.get_control(k))
             ) for (k,cv) in self.constraints.control_variables.items()
        ])

    @abstractmethod
    def get_current_data(self) -> np.ndarray:
        """Return current scan data. 

        It is an array of dimensions (number_of_repetitions * number_of_samples_per_step, 3 + n)
        where n is the number of channels saved by the hardware.
        """
        pass
    @abstractmethod
    def set_exposure_time(self, time:float) -> None:
        "Set exposure time for one data point."
        pass
    @abstractmethod
    def set_repeat_number(self, n:int) -> None:
        "Set number of repetition of each segment of the scan."
        pass
    @abstractmethod
    def set_idle_value(self, n:float) -> None:
        "Set idle value."
        pass
    @abstractmethod
    def get_exposure_time(self) -> float:
        "Get exposure time for one data point."
        pass
    @abstractmethod
    def get_repeat_number(self) -> int:
        "Get number of repetition of each segment of the scan."
        pass
    @abstractmethod
    def get_idle_value(self) -> float:
        "Get idle value."
        pass
    @property
    @abstractmethod
    def data_format(self) -> ExcitationScanDataFormat:
        "Return the data format used in this implementation of the interface."
        pass
    @property
    def frequency_column_number(self) -> int:
        "Shortcut for `self.data_format.frequency_column_number`."
        return self.data_format.frequency_column_number
    @property
    def step_number_column_number(self) -> int:
        "Shortcut for `self.data_format.step_number_column_number`."
        return self.data_format.step_number_column_number
    @property
    def time_column_number(self) -> int:
        "Shortcut for `self.data_format.time_column_number`."
        return self.data_format.time_column_number

