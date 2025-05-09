from PySide2 import QtCore
from PySide2.QtCore import QThread, QMutex, QWaitCondition
import time
from datetime import datetime
import numpy as np
from qudi.core.connector import Connector
from qudi.core.statusvariable import StatusVar
from qudi.core.configoption import ConfigOption
from qudi.core.module import LogicBase
from qudi.interface.data_instream_interface import StreamingMode, SampleTiming

class PowerSweepLogic(LogicBase):
    _qdplot_logic = Connector(name="plot_logic", interface="QDPlotLogic")
    _powermeter_time_series_logic = Connector(name="powermeter_time_series_logic", interface="TimeSeriesReaderLogic")
    _time_series_reader_logic = Connector(name="time_series_reader_logic", interface="TimeSeriesReaderLogic")

    buffer_size = ConfigOption(name='buffer_size', default=2**16)
    refresh_time = ConfigOption(name='refresh_time_ms', default=500)

    duration = StatusVar(name='duration', default=60)

    sigStart = QtCore.Signal()
    sigStop = QtCore.Signal()
    sigTimer = QtCore.Signal(int)
    sigTickTimer = QtCore.Signal()
    sigStopReading = QtCore.Signal()
    sigCleanupTraces = QtCore.Signal()
    _threaded = True
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._powers = None
        self._powers_times = None
        self._values = None
        self._times = None
        self._interpolated_powers = None
        self._power_index = 0
        self._values_index = 0
        self.plot_index = 0
        self.timestamp = datetime.now()
        self.channel_count = 0
        self._mutex = QMutex()
        self._wait_condition = QWaitCondition()
        self._timer = QtCore.QTimer()
        self._timer.setSingleShot(True)
        self._tick_timer = QtCore.QTimer()
        self._tick_timer.setSingleShot(False)

    def update_data(self, data, data_time):
        added_samples = data.size // self.channel_count
        if data_time is not None:
            self._times[self._values_index:(self._values_index+added_samples)] = data_time
        self._values[(self._values_index*self.channel_count):((self._values_index+added_samples)*self.channel_count)] = data
        self._values_index += added_samples

    def update_power(self, power, power_time):
        added_samples = power.size
        if power_time is not None:
            self._powers_times[self._power_index:(self._power_index+added_samples)] = power_time
        self._powers[self._power_index:(self._power_index+added_samples)] = power
        self._power_index += added_samples

    def interpolate(self):
        if self._values_index == 0 or self._power_index == 0:
            return
        self.log.info("interpolating...")
        power_times = self._powers_times[:self._power_index]
        powers = self._powers[:self._power_index]
        permsort = np.argsort(power_times)
        power_times = power_times[permsort]
        powers = powers[permsort]
        times = self._times[:self._values_index]
        roi = times < power_times[-1]
        self._interpolated_powers = np.interp(times[roi], power_times, powers)
        self._qdplot_logic().set_data(
            plot_index=self.plot_index, 
            data={
                k: (self._interpolated_powers, self._values[i:(self._values_index*self.channel_count+i):self.channel_count][roi])
                for (i,k) in enumerate(self._time_series_reader_logic().active_channel_names)
            },
            name=f"Power sweep {self.timestamp}",
            clear_old=True,
        )

    def on_activate(self):
        self._tick_timer.timeout.connect(self.interpolate)
        self._timer.timeout.connect(self._tick_timer.stop, QtCore.Qt.QueuedConnection)
        self._timer.timeout.connect(self.stop)
        self.sigTimer.connect(self._timer.start, QtCore.Qt.QueuedConnection)
        self.sigTickTimer.connect(self._tick_timer.start, QtCore.Qt.QueuedConnection)
        self._tick_timer.setInterval(self.refresh_time)

    def start(self):
        self.log.info("Starting power sweep.")
        #self._qdplot_logic().add_plot()
        self.plot_index = self._qdplot_logic().plot_count - 1
        self.timestamp = datetime.now()
        self.channel_count = len(self._time_series_reader_logic().active_channel_names)

        self._powermeter_time_series_logic().sigNewRawData.connect(self.update_power, QtCore.Qt.QueuedConnection)
        self._time_series_reader_logic().sigNewRawData.connect(self.update_data, QtCore.Qt.QueuedConnection)
        self.sigCleanupTraces.connect(self._powermeter_time_series_logic().set_trace_settings, QtCore.Qt.QueuedConnection)
        self.sigCleanupTraces.connect(self._time_series_reader_logic().set_trace_settings, QtCore.Qt.QueuedConnection)
        self.sigStart.connect(self._time_series_reader_logic().start_reading, QtCore.Qt.QueuedConnection)
        self.sigStart.connect(self._powermeter_time_series_logic().start_reading, QtCore.Qt.QueuedConnection)
        self.sigStart.connect(self._time_series_reader_logic().start_recording, QtCore.Qt.QueuedConnection)
        self.sigStart.connect(self._powermeter_time_series_logic().start_recording, QtCore.Qt.QueuedConnection)
        self.sigStop.connect(self._time_series_reader_logic().stop_recording, QtCore.Qt.QueuedConnection)
        self.sigStop.connect(self._powermeter_time_series_logic().stop_recording, QtCore.Qt.QueuedConnection)
        self.sigStop.connect(self._tick_timer.stop, QtCore.Qt.QueuedConnection)
        self.sigStop.connect(self._timer.stop, QtCore.Qt.QueuedConnection)
        self.sigStopReading.connect(self._powermeter_time_series_logic().stop_reading, QtCore.Qt.QueuedConnection)
        self.sigStopReading.connect(self._time_series_reader_logic().stop_reading, QtCore.Qt.QueuedConnection)
        self._power_index = 0
        self._values_index = 0
        self.log.info("Connected signals.")
        self.sigStopReading.emit()
        time.sleep(1)
        self.sigCleanupTraces.emit()
        time.sleep(1)

        self._powers = np.zeros(self.buffer_size)
        powermeter_constraints = self._powermeter_time_series_logic().streamer_constraints
        if powermeter_constraints.sample_timing == SampleTiming.TIMESTAMP:
            self._powers_times = np.zeros(self.buffer_size)
        else:
            power_step = 1 / self._powermeter_time_series_logic().sampling_rate
            self._powers_times = np.arange(start=0, stop=(self.buffer_size-1)*power_step, step=power_step)
        self._qdplot_logic().set_units(self.plot_index, x=list(powermeter_constraints.channel_units.values())[0])
        self._values = np.zeros(self.buffer_size*self.channel_count)
        if self._time_series_reader_logic().streamer_constraints.sample_timing == SampleTiming.TIMESTAMP:
            self._times = np.zeros(self.buffer_size)
        else:
            step = 1 / self._time_series_reader_logic().sampling_rate
            self._times = np.arange(start=0, stop=(self.buffer_size-1)*step, step=step)

        self.log.info("Starting recording.")
        self.sigTimer.emit(self.duration*1000)
        self.sigStart.emit()
        self.sigTickTimer.emit()
        self.log.info("Power sweep started.")

    def stop(self):
        self.log.info("Cleaning up.")
        self._powermeter_time_series_logic().sigNewRawData.disconnect(self.update_power)
        self._time_series_reader_logic().sigNewRawData.disconnect(self.update_data)
        self.sigStop.emit()
        self.sigStart.disconnect()
        self.sigStop.disconnect()
        self.sigStopReading.disconnect()
        self.sigCleanupTraces.disconnect()
        self.interpolate()
        path = self._qdplot_logic().save_data(self.plot_index, "power_sweep")
        self.log.info(f"Power sweep saved to {path}")        

    def on_deactivate(self):
        self._tick_timer.timeout.disconnect(self.interpolate)
        self._timer.timeout.disconnect(self._tick_timer.stop)
        self.sigTimer.disconnect(self._timer.start)
        self.sigTickTimer.disconnect(self._tick_timer.start)
        self._powers = None
        self._powers_times = None
        self._interpolated_powers = None
        self._times = None
        self._values = None

