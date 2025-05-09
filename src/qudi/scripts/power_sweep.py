from qudi.core.scripting.moduletask import ModuleTask
from PySide2 import QtCore
import numpy as np
from qudi.core.connector import Connector

class PowerSweepMeasurement(ModuleTask):
    _powersweep = Connector(name="powersweep", interface="PowerSweepLogic")
    _qdplot_gui = Connector(name="qdplot_gui", interface="QDPlotterGui")
    _powermeter_time_series_gui = Connector(name="powermeter_time_series_gui", interface="TimeSeriesGui")
    _time_series_gui = Connector(name="time_series_gui", interface="TimeSeriesGui")
    sigStart = QtCore.Signal()

    def _setup(self):
        self.sigStart.connect(self._powersweep().start)
    def _run(self):
        self.sigStart.emit()
    def _cleanup(self):
        self.sigStart.disconnect()

