from PySide2 import QtCore
from PySide2 import QtWidgets
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import traceback
from uncertainties import ufloat
from dataclasses import asdict

from qudi.core.module import LogicBase
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.util.tomldatastorage import TOMLHeaderDataStorage
from qudi.util.datafitting import FitContainer, FitConfigurationsModel
from qudi.util.mutex import Mutex

class ScanningExcitationLogic(LogicBase):
    """
    A logic to pilot an `ExcitationScannerInterface`.

    Copy and paste configuration example:
    ```yaml
      excitation_scanner_logic:
        module.Class: 'scanning_excitation_spectroscopy.ScanningExcitationLogic'
        options:
          beep: True # default
          watchdog_repeat_time_ms: 500 # default
        connect:
          scanner: excitation_scanner_hardware
    ```
    """
    _scanner = Connector(name='scanner', interface='ExcitationScannerInterface')

    _watchdog_repeat_time = ConfigOption(name="watchdog_repeat_time_ms", default=500)
    _beep = ConfigOption(name="beep", default=True)

    _spectrum = StatusVar(name='spectrum', default=[None, None, None])
    _fit_region = StatusVar(name='fit_region', default=[0, 1])
    _fit_config = StatusVar(name='fit_config', default=dict())
    _notes = StatusVar(name='notes', default="")
    _display_channel_column_number = StatusVar(name='display_channel_column_number', default=0)

    _sig_get_spectrum = QtCore.Signal(bool)

    sig_data_updated = QtCore.Signal()
    sig_state_updated = QtCore.Signal()
    sig_scanner_state_updated = QtCore.Signal(str)
    sig_scanner_variables_updated = QtCore.Signal()
    sig_fit_updated = QtCore.Signal(str, object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._fit_config_model = None
        self._fit_container = None

        self._logic_id = None

        # locking for thread safety
        self._lock = Mutex()

        self._stop_acquisition = False
        self._acquisition_running = False
        self._fit_results = None
        self._fit_method = ''
        self._watchdog_timer = QtCore.QTimer(parent=self)
        self._scanner_start_requested = False

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self._fit_config_model = FitConfigurationsModel(parent=self)
        self._fit_config_model.load_configs(self._fit_config)
        self._fit_container = FitContainer(parent=self, config_model=self._fit_config_model)
        self.fit_region = self._fit_region

        display_channel_is_correct = self._display_channel_column_number in self._scanner().data_format.data_column_number
        if not display_channel_is_correct:
            self._display_channel_column_number = self._scanner().data_format.data_column_number[0]
        spectrum_shape_is_correct = len(self._spectrum) == len(self._scanner().data_format.data_column_names)
        if not spectrum_shape_is_correct:
            self.log.info("resetting spectrum")
            self._spectrum = [None for _ in range(len(self._scanner().data_format.data_column_names))]
        self._sig_get_spectrum.connect(self.get_spectrum, QtCore.Qt.QueuedConnection)
        self._watchdog_timer.setSingleShot(True)
        self._watchdog_timer.timeout.connect(self._watchdog, QtCore.Qt.QueuedConnection)
        self._watchdog_timer.start(100)

    def on_deactivate(self):
        """ Reverse steps of activation
        """
        self._sig_get_spectrum.disconnect()
        self._fit_config = self._fit_config_model.dump_configs()

    def stop(self):
        self._stop_acquisition = True

    def run_get_spectrum(self, reset=True):
        self._sig_get_spectrum.emit(reset)

    def get_spectrum(self, reset=True):
        self._stop_acquisition = False
        if reset:
            ncols = len(self._scanner().data_format.data_column_names)
            with self._lock:
                self._spectrum = [None for _ in range(ncols)]
        self.sig_state_updated.emit()

        self._scanner().start_scan()
        self._scanner_start_requested = True
        self.__start_watchdog_timer()

        return self.spectrum

    def __start_watchdog_timer(self):
        # A timer can only be started from its own thread, so this method ensures
        # that it is being started in the thread of the logic.
        try:
            if self.thread() is not QtCore.QThread.currentThread():
                self._watchdog_timer.setInterval(self._watchdog_repeat_time)
                QtCore.QMetaObject.invokeMethod(self._watchdog_timer,
                                                "start",
                                                QtCore.Qt.BlockingQueuedConnection)
            else:
                pass

        except:
            self.log.exception("")

    @property
    def acquisition_running(self):
        return self._scanner().scan_running

    @property
    def spectrum(self):
        with self._lock:
            if self._spectrum[self._display_channel_column_number] is None:
                return None
            return np.copy(self._spectrum[self._display_channel_column_number])

    def get_spectrum_at_x(self, x, step_num=0):
        if self.frequency is None or self.spectrum is None:
            return -1
        roi = self.step_number==step_num
        return np.interp(x, self.frequency[roi], self.spectrum[roi])

    @property
    def frequency(self):
        with self._lock:
            if self._spectrum[self._scanner().data_format.frequency_column_number] is None:
                return None
            return np.copy(self._spectrum[self._scanner().data_format.frequency_column_number])

    @property
    def step_number(self):
        with self._lock:
            if self._spectrum[self._scanner().data_format.step_number_column_number] is None:
                return None
            return np.copy(self._spectrum[self._scanner().data_format.step_number_column_number])

    @property
    def time(self):
        with self._lock:
            if self._spectrum[self._scanner().data_format.time_column_number] is None:
                return None
        return np.copy(self._spectrum[self._scanner().data_format.time_column_number])

    def save_spectrum_data(self, name_tag='', root_dir=None, parameter=None):
        """ Saves the current spectrum data to a file.
        @param string name_tag: postfix name tag for saved filename.
        @param string root_dir: overwrite the file position in necessary
        @param dict parameter: additional parameters to add to the saved file
        """

        timestamp = datetime.now()

        # write experimental parameters
        scan_variables = {'repetitions': self.repetitions,
                      'exposure' : self.exposure_time,
                      }

        for (variable, value) in self._scanner().control_dict.items():
            scan_variables[variable] = asdict(value)

        parameters = {
            'scan_setup': scan_variables
        }

        if self.fit_method != 'No Fit' and self.fit_results is not None:
            fit_setup = {}
            fit_setup['method'] = self.fit_method
            fit_setup['results'] = self.fit_results.params
            fit_setup['region'] = self.fit_region
            parameters['fit'] = fit_setup

        if parameter:
            parameters.update(parameter)

        if self.frequency is None or self.spectrum is None or self.step_number is None:
            self.log.error('No data to save.')
            return

        med = np.median(self.frequency)
        rescale_factor_freq, prefix_freq = self._get_si_scaling(np.max(self.frequency)-med)

        data = []
        header = []
        for (colname, colunit, col) in zip(self._scanner().data_format.data_column_names, self._scanner().data_format.data_column_unit, self._spectrum):
            if col is None:
                self.log.error('No spectrum to save.')
                return
            data.append(col)
            header.append(f"{colname} ({colunit})")
        file_label = 'spectrum' + name_tag
        # save the date to file
        ds = TOMLHeaderDataStorage(root_dir=self.module_default_data_dir if root_dir is None else root_dir,
                             include_global_metadata=True)

        file_path, _, _ = ds.save_data(np.array(data).T,
                                       column_headers=header,
                                       metadata=parameters,
                                       nametag=file_label,
                                       timestamp=timestamp,
                                       notes=self._notes,
                                       column_dtypes=[float] * len(header))

        # save the figure into a file
        figure, ax1 = plt.subplots()
        rescale_factor, prefix = self._get_si_scaling(np.max(data[self.display_channel_column_number]))

        n_step = np.unique(self.step_number)
        for i in n_step:
            roi = self.step_number == i
            freq = (self.frequency[roi] - med) / rescale_factor_freq
            count = self.spectrum[roi] / rescale_factor
            ax1.plot(freq,
                     count,
                     linestyle=':',
                     linewidth=0.5,
                     )

        fit_displayed = self.fit_method != 'No Fit' and self.fit_results is not None
        if fit_displayed:
            frequency = (self.fit_results.high_res_best_fit[0]-med) / rescale_factor_freq

            ax1.plot(frequency,
                     self.fit_results.high_res_best_fit[1] / rescale_factor,
                     linestyle=':',
                     linewidth=0.5,
                     label="Fit",
                     )
            # these are matplotlib.patch.Patch properties
            props = dict(boxstyle='round', facecolor='wheat', alpha=0.5)
            textstr = f"fit method: {self.fit_method}\nfit results:\n"
            for (name,param) in self.fit_results.params.items():
                textstr += f"  - {name} = {ufloat(param.value, param.stderr)}\n"
            textstr += f"fit region: {self.fit_region}"
            # place a text box in upper left in axes coords
            ax1.text(0.05, 0.95, textstr, transform=ax1.transAxes, fontsize=14,
        verticalalignment='top', bbox=props)

        ax1.set_xlabel(f"Frequency ({prefix_freq}Hz)")
        ax1.set_ylabel('Intensity ({} count)'.format(prefix))
        figure.tight_layout()

        ds.save_thumbnail(figure, file_path=file_path.rsplit('.', 1)[0])

        self.log.debug(f'Spectrum saved to:{file_path}')
        return file_path

    @staticmethod
    def _get_si_scaling(number):

        prefix = ['', 'k', 'M', 'G', 'T', 'P']
        prefix_index = 0
        rescale_factor = 1

        # Rescale spectrum data with SI prefix
        while number / rescale_factor > 1000:
            rescale_factor = rescale_factor * 1000
            prefix_index = prefix_index + 1

        intensity_prefix = prefix[prefix_index]
        return rescale_factor, intensity_prefix

    @property
    def exposure_time(self):
        return self._scanner().get_exposure_time()

    @exposure_time.setter
    def exposure_time(self, value):
        self._scanner().set_exposure_time(value)
        self.sig_state_updated.emit()

    @property
    def repetitions(self):
        return self._scanner().get_repeat_number()
    @repetitions.setter
    def repetitions(self, v):
        self._scanner().set_repeat_number(v)
    @property
    def idle(self):
        return self._scanner().get_idle_value()
    @idle.setter
    def idle(self, v):
        self._scanner().set_idle_value(v)
    @property
    def notes(self):
        return self._notes
    @notes.setter
    def notes(self, v):
        self._notes = str(v)

    @property 
    def variables(self):
        return self._scanner().control_dict
    def set_variable(self, name, value):
        self._scanner().set_control(name, value)
        self.sig_scanner_variables_updated.emit()

    def available_channels(self):
        indices = self._scanner().data_format.data_column_number
        return [self._scanner().data_format.data_column_names[i] for i in indices]

    @property
    def display_channel_column_number(self):
        return self._display_channel_column_number
    @display_channel_column_number.setter
    def display_channel_column_number(self, v):
        self._display_channel_column_number = v
        self.sig_data_updated.emit()
    def set_display_channel_from_name(self, name):
        self.display_channel_column_number = self._scanner().data_format.data_column_names.index(name)

    def _watchdog(self):
        try:
            with self._lock:
                ncols = len(self._scanner().data_format.data_column_names)
                data = self._scanner().get_current_data()
                if len(data) > 0:
                    self._spectrum = [data[:,i] for i in range(ncols)]
            self.sig_data_updated.emit()
            st = self._scanner().state_display
            self.sig_scanner_state_updated.emit(st)
            if self._scanner_start_requested or self._scanner().scan_running:
                self._scanner_start_requested = not self._scanner().scan_running
                if self._stop_acquisition:
                    self._scanner().stop_scan()
                    self._stop_acquisition = False
                self._watchdog_timer.start(self._watchdog_repeat_time)
            else:
                if self._beep:
                    QtWidgets.QApplication.instance().beep()
                if self.frequency is not None:
                    self.fit_region = (min(self.frequency), max(self.frequency))
                    self.idle = (min(self.frequency) + max(self.frequency)) / 2
                    self.sig_state_updated.emit()
        except:
            self.log.exception("")



    ################
    # Fitting things

    @property
    def fit_config_model(self):
        return self._fit_config_model

    @property
    def fit_container(self):
        return self._fit_container

    def do_fit(self, fit_method):
        self.log.debug("do a fit")
        if fit_method == 'No Fit':
            self.sig_fit_updated.emit('No Fit', None)
            return 'No Fit', None

        self.fit_region = self._fit_region
        if self.frequency is None or self.spectrum is None:
            self.log.error('No data to fit.')
            self.sig_fit_updated.emit('No Fit', None)
            return 'No Fit', None

        step_num = self._fit_region[2]
        roi = self.step_number == step_num
        frequency = self.frequency[roi]
        start = np.searchsorted(frequency, self._fit_region[0], 'left')
        end = np.searchsorted(frequency, self._fit_region[1], 'right')

        if end - start < 2:
            self.log.error('Fit region limited the data to less than two points. Fit not possible.')
            self.sig_fit_updated.emit('No Fit', None)
            return 'No Fit', None

        frequency = frequency[start:end]
        y_data = self.spectrum[roi][start:end]
        self.log.debug(f"Fitting from {start} to {end}")

        try:
            self._fit_method, self._fit_results = self._fit_container.fit_data(fit_method, frequency, y_data)
        except:
            self.log.exception(f'Data fitting failed:\n{traceback.format_exc()}')
            self.sig_fit_updated.emit('No Fit', None)
            return 'No Fit', None

        self.sig_fit_updated.emit(self._fit_method, self._fit_results)
        self.log.debug("done")
        return self._fit_method, self._fit_results

    @property
    def fit_results(self):
        return self._fit_results

    @property
    def fit_method(self):
        return self._fit_method

    @property
    def fit_region(self):
        return self._fit_region

    @fit_region.setter
    def fit_region(self, fit_region):
        if len(fit_region) == 2:
            fit_region = (fit_region[0], fit_region[1], 0)
        #assert len(fit_region) == 3, f'fit_region has to be of length 3 but was {len(fit_region)}'

        if self.frequency is None:
            return
        fit_region = fit_region if fit_region[0] <= fit_region[1] else (fit_region[1], fit_region[0], fit_region[2])
        new_region = (max(min(self.frequency), fit_region[0]), min(max(self.frequency), fit_region[1]), fit_region[2])
        self._fit_region = new_region
        self.sig_state_updated.emit()

