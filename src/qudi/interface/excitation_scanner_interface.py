from abc import abstractmethod
from collections import OrderedDict
from typing import Iterable, Union, Tuple, Dict, Type
import numpy as np

from qudi.core.module import Base
_Variable_Type = Union[int, float, bool]

class ExcitationScannerConstraints:
    def __init__(self, exposure_limits:Tuple[float,float], 
                 repeat_limits:Tuple[int,int], 
                 idle_value_limits:Tuple[float,float], 
                 control_variables:Iterable[str], 
                 control_variable_limits:Iterable[Tuple[_Variable_Type,_Variable_Type]], 
                 control_variable_units:Iterable[str], 
                 control_variable_types:Iterable[Type[_Variable_Type]]):
        self.exposure_limits=exposure_limits
        self.repeat_limits=repeat_limits
        self.idle_value_limits=idle_value_limits
        self.control_variables:list[str]=list(control_variables)
        self.control_variable_limits:list[Tuple[_Variable_Type,_Variable_Type]]=list(control_variable_limits)
        self.control_variable_types:list[Type[_Variable_Type]]=list(control_variable_types)
        self.control_variable_units=control_variable_units
        self._control_variables_dict = {
            name:dict(name=name, limits=limits, type=t, unit=unit) for (name,limits,t,unit) in zip(self.control_variables, self.control_variable_limits, self.control_variable_types, self.control_variable_units)
        }
    def get_control_variables(self):
        return self._control_variables_dict
    def variable_in_range(self, name:str,value:_Variable_Type):
        if name not in self.control_variables:
            return False
        i = self.control_variables.index(name)
        if self.control_variable_types[i] == bool:
            return True
        mini,maxi=self.control_variable_limits[i]
        return mini <= value <= maxi
    def set_limits(self, name:str,mini:_Variable_Type,maxi:_Variable_Type):
        if name not in self.control_variables:
            raise KeyError(f"Unknown variable {name}")
        i = self.control_variables.index(name)
        self.control_variable_limits[i] = mini,maxi
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
    def control_dict(self):
        "Get a dict with all the control variables."
        c = self.constraints.get_control_variables()
        return OrderedDict([(k,
                             dict(name=k, 
                                  limits=c[k]['limits'], 
                                  type=c[k]['type'], 
                                  unit=c[k]['unit'], 
                                  value=self.get_control(k))
                             ) for k in self.constraints.control_variables])

    @abstractmethod
    def get_current_data(self) -> np.ndarray:
        "Return current scan data."
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
    def data_column_names(self):
        pass
    @property
    @abstractmethod
    def data_column_unit(self):
        pass
    @property
    @abstractmethod
    def data_column_number(self):
        pass
    @property
    @abstractmethod
    def frequency_column_number(self):
        pass
    @property
    @abstractmethod
    def step_number_column_number(self):
        pass
    @property
    @abstractmethod
    def time_column_number(self):
        pass


class ExcitationScanData:
    pass

