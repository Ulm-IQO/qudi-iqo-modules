from qtpy import QtCore
import numpy as np
import datetime as dt
import matplotlib.pyplot as plt

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.module import LogicBase
from qudi.util.mutex import Mutex
import traceback
from qtpy import QtCore
from qudi.util.datastorage import TextDataStorage, ImageFormat
from qudi.util.units import ScaledFloat
from qudi.util.datafitting import FitContainer, FitConfigurationsModel
class TimeTaggerLogic(LogicBase):
    """ Logic module agreggating multiple hardware switches.
    """

    timetagger = Connector(interface='TT')
    queryInterval = ConfigOption('query_interval', 500)
    
    sigCounterDataChanged = QtCore.Signal(object)
    sigCorrDataChanged = QtCore.Signal(object)
    sigHistDataChanged = QtCore.Signal(object)

    sigUpdate = QtCore.Signal()
    sigNewMeasurement = QtCore.Signal()
    sigHistRefresh = QtCore.Signal(float)
    sigUpdateGuiParams=QtCore.Signal()

    sig_fit_updated = QtCore.Signal(str, object)
    _default_fit_configs = (
        {'name'             : 'g2',
        'model'            : 'Autocorrelation',
        'estimator'        : 'Dip',
        'custom_parameters': None},
    )

    def __init__(self, **kwargs):
        """ Create CwaveScannerLogic object with connectors.

          @param dict kwargs: optional parameters
        """
        super().__init__(**kwargs)
        self._fit_config_model = None
        self._fit_container = None
        self._fit_results = None
        self._fit_method = ''
        # locking for thread safety
        self.threadlock = Mutex()
        self.stopRequested = False

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """
        self._timetagger = self.timetagger()
        self._constraints = self._timetagger._constraints
        self.stopRequested = False

        self._fit_config_model = FitConfigurationsModel(parent=self)
        self._fit_config_model.load_configs(self._default_fit_configs)
        self._fit_container = FitContainer(parent=self, config_model=self._fit_config_model)

        self._counter_poll_timer = QtCore.QTimer()
        self._counter_poll_timer.setSingleShot(False)
        self._counter_poll_timer.timeout.connect(self.acquire_data_block, QtCore.Qt.QueuedConnection)
        self._counter_poll_timer.setInterval(50)

        self._corr_poll_timer = QtCore.QTimer()
        self._corr_poll_timer.setSingleShot(False)
        self._corr_poll_timer.timeout.connect(self.acquire_corr_block, QtCore.Qt.QueuedConnection)
        self._corr_poll_timer.setInterval(50)
        
        self._hist_poll_timer = QtCore.QTimer()
        self._hist_poll_timer.setSingleShot(False)
        self._hist_poll_timer.timeout.connect(self.acquire_hist_block, QtCore.Qt.QueuedConnection)
        self._hist_poll_timer.setInterval(50)

        self.counter = None
        self.trace_data = {}
        self.counter_params = self._timetagger._counter
        self.hist_params = self._timetagger._hist
        self.corr_params  = self._timetagger._corr

        self._recorded_data = None
        self.trace_data = None
        self.corr_data = None
        self.hist_data = None

        self.metadata = {'counter':None, 'hist':None, 'corr':None}
    
    def on_deactivate(self):
        self._fit_config = self._fit_config_model.dump_configs()
        pass
    
    def configure_counter(self, data):
        self.counter_freq, self.counter_length, self.counter_channels, self.counter_toggle, self.display_channel = data['counter']

        with self.threadlock:
            bin_width = int(1/self.counter_freq*1e12)
            n_values = int(self.counter_length*1e12/bin_width)
            self.toggled_channels = []
            self.display_channel_number = 0
            for ch in self.counter_channels:
                if self.counter_channels[ch]:
                    self.toggled_channels.append(ch)
                    if self.display_channel == f'Channel {ch}':
                        self.display_channel_number = ch

            if self.toggled_channels and self.counter_toggle:
                self.counter = self._timetagger.counter(channels = self.toggled_channels, bin_width = bin_width, n_values = n_values)
        
                meta_dict = {'Channels': self.toggled_channels, 'Bin Width': bin_width/1e12, 'Number of Bins': n_values, 'Units': [(ch,'Cps') for ch in self.toggled_channels]}
                self.metadata.update([['counter', meta_dict]])
                self._counter_poll_timer.start()
    
    def configure_corr(self, data):
        self.corr_bin_width, self.corr_record_length, self.corr_toggled = data['corr']
        self.corr_record_length *= 1e6
        with self.threadlock:
            if self.corr_toggled:
                self.corr = self._timetagger.correlation(channel_start = self._constraints['corr']['channel_start'], 
                                                        channel_stop = self._constraints['corr']['channel_stop'], 
                                                        bin_width = int(self.corr_bin_width), 
                                                        number_of_bins = int(self.corr_record_length/self.corr_bin_width))
        
                meta_dict = {'Channel start': self._constraints['corr']['channel_start'], 'Channel stop': self._constraints['corr']['channel_stop'], 
                        'Bin Width': int(self.corr_bin_width)/1e12, 'Number of Bins': int(self.corr_record_length/self.corr_bin_width), 'Units': [('g2','arb.u.')]}
                self.metadata.update([['corr', meta_dict]])
                self._corr_poll_timer.start()
    
    def configure_hist(self, data):
        self.hist_bin_width, self.hist_record_length, self.hist_channel, self.hist_toggled = data['hist']
        self.hist_record_length *= 1e6

        if self.hist_toggled:
            self.hist = self._timetagger.histogram(channel = self.hist_channel, 
                                                   trigger_channel = self._constraints['hist']['trigger_channel'], 
                                                   bin_width = int(self.hist_bin_width), 
                                                   number_of_bins = int(self.hist_record_length/self.hist_bin_width))

            meta_dict = {'Histogram Channel': self.hist_channel, 'Trigger Channel': self._constraints['hist']['trigger_channel'], 
                	    'Bin Width': int(self.hist_bin_width)/1e12, 'Number of Bins': int(self.hist_record_length/self.hist_bin_width), 'Units': [(self.hist_channel,'Counts')]}
            self.metadata.update([['hist', meta_dict]])
            self._hist_poll_timer.start()


    def acquire_data_block(self):
        """
        This method gets the available data from the hardware.

        It runs repeatedly by being connected to a QTimer timeout signal.
        """
        with self.threadlock:
            if not self.counter_toggle or not self.counter:
                self._counter_poll_timer.stop()
                return
            self.trace_data = {}
            self.trace_data_avg = {}
            counter_sum = None
            raw = self.counter.getDataNormalized()
            index = self.counter.getIndex()/1e12
            w = int(round(len(index)/50))
            # raw = np.random.random(100)
            # index = np.arange(100)
            counter_sum = np.zeros_like(raw[0])
            for i, ch in enumerate(self.toggled_channels):
                self.trace_data[ch] = (index, raw[i])
                avg = np.convolve(raw[i], np.ones(w), 'same') / w
                self.trace_data_avg[ch] = (index[w:-w], avg[w:-w])
                
                if self.display_channel_number==0:
                    counter_sum += raw[i]
                elif self.display_channel_number==ch:
                    counter_sum += raw[i]

            self.sigCounterDataChanged.emit({'trace_data':self.trace_data, 'trace_data_avg':self.trace_data_avg,'sum': np.mean(np.nan_to_num(counter_sum[-w:-1]))})
        return
    
    def acquire_corr_block(self):
        with self.threadlock:
            if not self.corr_toggled:
                self._corr_poll_timer.stop()
                return
            raw = self.corr.getDataNormalized()
            index = self.corr.getIndex()/1e12
            # raw = np.random.random(100)
            # index = np.arange(100)
            self.corr_data = (index, np.nan_to_num(raw))
            self.sigCorrDataChanged.emit({'corr_data':self.corr_data})
        return   
    
    def acquire_hist_block(self):
        with self.threadlock:
            if not self.hist_toggled:
                self._hist_poll_timer.stop()
                return
            raw = self.hist.getData()
            index = self.hist.getIndex()/1e12
            # raw = np.random.random(100)
            # index = np.arange(100)
            self.hist_data = (index, np.nan_to_num(raw))
            self.sigHistDataChanged.emit({'hist_data':self.hist_data})
        return
    
    @QtCore.Slot()
    def _save_recorded_data(self, to_file=True, name_tag='', save_figure=True, save_type='counter', save_path='Default'):
        """ Save the data and writes it to a file.

        @param bool to_file: indicate, whether data have to be saved to file
        @param str name_tag: an additional tag, which will be added to the filename upon save
        @param bool save_figure: select whether png and pdf should be saved

        @return dict parameters: Dictionary which contains the saving parameters
        """

        self._recorded_data = {'counter': self.trace_data, 'corr': self.corr_data, 'hist': self.hist_data}[save_type]
        if not self._recorded_data:
            self.log.error('No data has been recorded. Save to file failed.')
            return np.empty(0), dict()
        
        data_arr = np.array([self._recorded_data[1]])
        if save_type == 'counter':
            data_arr = []
            for ch in self.toggled_channels:
                data_arr.append(np.nan_to_num(self._recorded_data[ch][1]))
            data_arr = np.array(data_arr)
        if data_arr.size == 0:
            self.log.error('No data has been recorded. Save to file failed.')
            return np.empty(0), dict()
        
        # write the parameters:
        parameters = self.metadata[save_type]
        
        if to_file:
            # If there is a postfix then add separating underscore
            filelabel = '{1}_data_trace_{0}'.format(name_tag, save_type) if name_tag else f'{save_type}_data_trace'
        
            # prepare the data in a dict:
            header = ['{0} ({1})'.format(ch, unit) for ch, unit in self.metadata[save_type]['Units']]

            data = data_arr.transpose()
            filepath = self.module_default_data_dir 
            y_unit = self.metadata[save_type]['Units'][0][1]

            self.data_rate = 1/self.metadata[save_type]['Bin Width']

            fig = self._draw_figure(data_arr, self.data_rate, y_unit) if save_figure else None
            if not save_path == 'Default':
                filepath = save_path

            data_storage = TextDataStorage(root_dir=filepath,
                               comments='# ', 
                               delimiter='\t',
                               file_extension='.dat',
                               column_formats=['.8f' for i in self.metadata[save_type]['Units']],
                               include_global_metadata=True,
                               image_format=ImageFormat.PNG)

            file_path, timestamp, (rows, columns) = data_storage.save_data(data, 
                                                               timestamp=dt.datetime.now(), 
                                                               metadata=parameters, 
                                                               notes='',
                                                               nametag=filelabel,
                                                               column_headers=header,
                                                               column_dtypes=[float for i in self.metadata[save_type]['Units']])
            if fig:
                data_storage.save_thumbnail(fig, file_path.rsplit('.')[0])
            self.log.info('Time series saved to: {0}'.format(file_path))
        return data_arr, parameters

    def _draw_figure(self, data, timebase, y_unit):
        """ Draw figure to save with data file.

        @param: nparray data: a numpy array containing counts vs time for all detectors

        @return: fig fig: a matplotlib figure object to be saved to file.
        """
        # Create figure and scale data
        max_abs_value = ScaledFloat(max(data.max(), np.abs(data.min())))
        time_data = np.arange(data.shape[1]) / timebase
        fig, ax = plt.subplots()
        if max_abs_value.scale:
            ax.plot(time_data,
                    data.transpose() / max_abs_value.scale_val,
                    linestyle='-',
                    linewidth=1)
        else:
            ax.plot(time_data, data.transpose(), linestyle='-', linewidth=1)
        ax.set_xlabel('Time (s)')
        ax.set_ylabel('Signal ({0}{1})'.format(max_abs_value.scale, y_unit))
        return fig
    
    ################
    # Fitting things

    @property
    def fit_config_model(self):
        return self._fit_config_model

    @property
    def fit_container(self):
        return self._fit_container

    def do_fit(self, fit_method):
        print("hey", fit_method)
        if fit_method == 'No Fit':
            print("NOFIT")
            self.sig_fit_updated.emit('No Fit', None)
            return 'No Fit', None

        # self.fit_region = self._fit_region
        if self.corr_data is None:
            print("NO data")
            self.log.error('No data to fit.')
            self.sig_fit_updated.emit('No Fit', None)
            return 'No Fit', None

    
        x_data = self.corr_data[0]#[start:end]
        y_data = self.corr_data[1]#[start:end]
        
        try:
            self._fit_method, self._fit_results = self._fit_container.fit_data(fit_method, x_data, y_data)
        except:
            self.log.exception(f'Data fitting failed:\n{traceback.format_exc()}')
            self.sig_fit_updated.emit('No Fit', None)
            return 'No Fit', None

        self.sig_fit_updated.emit(self._fit_method, self._fit_results)
        return self._fit_method, self._fit_results

    @property
    def fit_results(self):
        return self._fit_results

    @property
    def fit_method(self):
        return self._fit_method