import os
import pyqtgraph as pg
from PySide2 import QtCore, QtWidgets
import numpy as np

from qudi.core.statusvariable import StatusVar
from qudi.util.uic import loadUi
from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.util.colordefs import QudiPalettePale as palette
from qudi.core.module import GuiBase
from qudi.interface.data_instream_interface import StreamChannelType

class WavemeterHistogramMainWindow(QtWidgets.QMainWindow):
    """ Create the Main Window based on the *.ui file. """

    def __init__(self, **kwargs):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, 'ui_wavemeter_histogram_gui.ui')

        # Load it
        super().__init__(**kwargs)
        loadUi(ui_file, self)

class WavemeterHistogramGui(GuiBase):
    """
    GUI module to be used in conjunction with WavemeterHistogramLogic.

    Example config for copy-paste:

    wavemeter_histogram_logic:
        module.Class: 'wavemeter.wavemeter_histogram_gui.WavemeterHistogramGui'
        options:
            use_antialias: True  # optional, set to False if you encounter performance issues
        connect:
            _wavemeter_histogram_logic_con: wavemeter_histogram_logic
    """

    # declare connectors
    _wavemeter_histogram_logic_con = Connector(interface='WavemeterLogic')

    # declare ConfigOptions
    #_use_antialias = ConfigOption('use_antialias', default=True)

    sigStartCounter = QtCore.Signal()
    sigStopCounter = QtCore.Signal()
    sigStartRecording = QtCore.Signal()
    sigStopRecording = QtCore.Signal()
    sigSettingsChanged = QtCore.Signal(dict)

    _view_settings = StatusVar(name='visible_settings', default={})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._wavemeter_logic = None
        self._mw = None
        self._pw = None
        self._vb = None

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """

        self._wavemeter_logic = self._wavemeter_histogram_logic_con()

        #####################
        # Configuring the dock widgets
        # Use the inherited class 'WavemeterHistogramMainWindow' to create the GUI window
        self._mw = WavemeterHistogramMainWindow()

        # Setup dock widgets
        # self._mw.centralwidget.hide()
        self._mw.setDockNestingEnabled(True)

        # Get hardware constraints and extract channel names
        #TODO twice the hardware constraints neccesary?
        hw_constr = self._wavemeter_logic.streamer_constraints
        digital_channels = [ch.name for ch in hw_constr.digital_channels]
        analog_channels = [ch.name for ch in hw_constr.analog_channels]
        all_channels = digital_channels + analog_channels

        # Configure PlotWidget
        self._pw = self._mw.PlotWidget
        self._pw.setLabel('bottom', 'Wavelength', units='nm')
        self._pw.setLabel('left', 'Fluorescence', units='counts/s')
        self._pw.setLabel('right', 'Histogram', units='averaged counts per bin/s')
        self._pw.setMouseEnabled(x=False, y=False)
        self._pw.setMouseTracking(False)
        self._pw.setMenuEnabled(False)
        #self._pw.hideButtons()
        # Create second ViewBox to plot with two independent y-axes
        self._vb = pg.ViewBox()
        self._pw.scene().addItem(self._vb)
        self._pw.getAxis('right').linkToView(self._vb)
        self._vb.setXLink(self._pw)
        self._vb.setMouseEnabled(x=False, y=False)
        self._vb.setMenuEnabled(False)
        # Sync resize events
        self._pw.plotItem.vb.sigResized.connect(self.__update_viewbox_sync)

        #####################
        # Set up channel settings dialog
        #self._init_channel_settings_dialog()

        #####################
        # Set up trace view selection dialog
        #self._init_trace_view_selection_dialog()

        #####################
        # Connecting user interactions
        self._mw.start_trace_Action.triggered.connect(self.start_clicked)
        self._mw.actionClear_trace_data.triggered.connect(self.clear_trace_data)
        #self._mw.record_trace_Action.triggered.connect(self.record_clicked)
        #self._mw.trace_snapshot_Action.triggered.connect(
        #    self._time_series_logic.save_trace_snapshot, QtCore.Qt.QueuedConnection
        #)

        #self._mw.trace_length_DoubleSpinBox.editingFinished.connect(self.data_window_changed)
        #self._mw.data_rate_DoubleSpinBox.editingFinished.connect(self.data_rate_changed)
        #self._mw.oversampling_SpinBox.editingFinished.connect(self.oversampling_changed)
        #self._mw.moving_average_spinBox.editingFinished.connect(self.moving_average_changed)
        #self._mw.curr_value_comboBox.currentIndexChanged.connect(self.current_value_channel_changed)

        # Connect the default view action
        #self._mw.restore_default_view_Action.triggered.connect(self.restore_default_view)
        #self._mw.trace_toolbar_view_Action.triggered[bool].connect(
        #    self._mw.trace_control_ToolBar.setVisible
        #)
        #self._mw.trace_settings_view_Action.triggered[bool].connect(
        #    self._mw.trace_settings_DockWidget.setVisible
        #)
        #self._mw.trace_settings_DockWidget.visibilityChanged.connect(
        #    self._mw.trace_settings_view_Action.setChecked
        #)
        #self._mw.trace_control_ToolBar.visibilityChanged.connect(
        #    self._mw.trace_toolbar_view_Action.setChecked
        #)

        #self._mw.trace_view_selection_Action.triggered.connect(self._vsd.show)
        #self._mw.channel_settings_Action.triggered.connect(self._csd.show)

        #self._vsd.accepted.connect(self.apply_trace_view_selection)
        #self._vsd.rejected.connect(self.keep_former_trace_view_selection)
        #self._vsd.buttonBox.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(
        #    self.apply_trace_view_selection)
        #self._csd.accepted.connect(self.apply_channel_settings)
        #self._csd.rejected.connect(self.keep_former_channel_settings)
        #self._csd.buttonBox.button(QtWidgets.QDialogButtonBox.Apply).clicked.connect(
        #    self.apply_channel_settings)

        #########old core#########
        self._mw.binSpinBox.setValue(self._wavemeter_logic.get_bins())
        self._mw.binSpinBox.editingFinished.connect(self.recalculate_histogram)

        self._mw.minDoubleSpinBox.setValue(self._wavemeter_logic.get_min_wavelength())
        self._mw.minDoubleSpinBox.editingFinished.connect(self.recalculate_histogram)

        self._mw.maxDoubleSpinBox.setValue(self._wavemeter_logic.get_max_wavelength())
        self._mw.maxDoubleSpinBox.editingFinished.connect(self.recalculate_histogram)

        ## Create an empty plot curve to be filled later, set its pen
        self.curve_data_points = pg.PlotDataItem(
            pen=pg.mkPen(palette.c1),
            symbol=None
        )

        self.curve_nm_counts = pg.PlotDataItem(
            pen=pg.mkPen(palette.c2, style=QtCore.Qt.DotLine),
            symbol=None
        )

        self._pw.addItem(self.curve_data_points)
        self._vb.addItem(self.curve_nm_counts)

        # scatter plot
        self._spw = self._mw.scatterPlotWidget
        self._spi = self._spw.plotItem
        self._spw.setLabel('bottom', 'Wavelength', units='nm')
        self._spw.setLabel('left', 'Time', units='s')
        self._scatterplot = pg.ScatterPlotItem(size=10, pen=pg.mkPen(None),
                                               brush=pg.mkBrush(255, 255, 255, 20))
        self._spw.addItem(self._scatterplot)
        self._spw.setXLink(self._pw)

        #signal for scatterplot
        self._wavemeter_logic.sig_new_data_points.connect(self.add_data_points)

        #####################
        # starting the physical measurement
        self.sigStartCounter.connect(
            self._wavemeter_logic.start_scanning, QtCore.Qt.QueuedConnection)
        self.sigStopCounter.connect(
            self._wavemeter_logic.stop_scanning, QtCore.Qt.QueuedConnection)
        #self.sigStartRecording.connect(
            #self._time_series_logic.start_recording, QtCore.Qt.QueuedConnection)
        #self.sigStopRecording.connect(
            #self._time_series_logic.stop_recording, QtCore.Qt.QueuedConnection)
        #self.sigSettingsChanged.connect(
            #self._time_series_logic.configure_settings, QtCore.Qt.QueuedConnection)

        #####################
        # Setting default parameters
        self.update_status()
        # self.update_settings()
        self.update_data()

        ##################
        # Handling signals from the logic
        self._wavemeter_logic.sigDataChanged.connect(
            self.update_data, QtCore.Qt.QueuedConnection)
        #self._wavemeter_logic.sigSettingsChanged.connect(
            #self.update_settings, QtCore.Qt.QueuedConnection)
        self._wavemeter_logic.sigStatusChanged.connect(
            self.update_status, QtCore.Qt.QueuedConnection)

        #TODO
        #self._init_gui_view()

        self.show()
        return

    def show(self):
        """Make window visible and put it above all other windows.
        """
        QtWidgets.QMainWindow.show(self._mw)
        self._mw.activateWindow()
        self._mw.raise_()
        return

    def on_deactivate(self):
        """ Deactivate the module
        """
        # disconnect signals
        self._pw.plotItem.vb.sigResized.disconnect()

        #self._vsd.accepted.disconnect()
        #self._vsd.rejected.disconnect()
        #self._vsd.buttonBox.button(QtWidgets.QDialogButtonBox.Apply).clicked.disconnect()
        #self._csd.accepted.disconnect()
        #self._csd.rejected.disconnect()
        #self._csd.buttonBox.button(QtWidgets.QDialogButtonBox.Apply).clicked.disconnect()

        self._mw.start_trace_Action.triggered.disconnect()
        self._mw.actionClear_trace_data.triggered.disconnect()
        #self._mw.record_trace_Action.triggered.disconnect()
        #self._mw.trace_snapshot_Action.triggered.disconnect()
        #self._mw.trace_length_DoubleSpinBox.editingFinished.disconnect()
        #self._mw.data_rate_DoubleSpinBox.editingFinished.disconnect()
        #self._mw.oversampling_SpinBox.editingFinished.disconnect()
        #self._mw.moving_average_spinBox.editingFinished.disconnect()
        #self._mw.restore_default_view_Action.triggered.disconnect()
        #self._mw.trace_toolbar_view_Action.triggered[bool].disconnect()
        #self._mw.trace_settings_view_Action.triggered[bool].disconnect()
        #self._mw.trace_settings_DockWidget.visibilityChanged.disconnect()
        #self._mw.trace_control_ToolBar.visibilityChanged.disconnect()
        self.sigStartCounter.disconnect()
        self.sigStopCounter.disconnect()
        #self.sigStartRecording.disconnect()
        #self.sigStopRecording.disconnect()
        #self.sigSettingsChanged.disconnect()
        self._wavemeter_logic.sigDataChanged.disconnect()
        #self._time_series_logic.sigSettingsChanged.disconnect()
        #self._time_series_logic.sigStatusChanged.disconnect()

        self._mw.close()

    @QtCore.Slot(object)
    def update_data(self, data=None):
        """ The function that grabs the data and sends it to the plot.
        """
        #self._mw.wavelengthLabel.setText('{0:,.6f} nm '.format(self._wm_logger_logic.current_wavelength))
        self._mw.autoMinLabel.setText('Minimum: {0:3.6f} (nm)   '.format(self._wavemeter_logic.get_min_wavelength()))
        self._mw.autoMaxLabel.setText('Maximum: {0:3.6f} (nm)   '.format(self._wavemeter_logic.get_max_wavelength()))

        x_axis = self._wavemeter_logic.histogram_axis
        #x_axis_hz = (
        #        3.0e17 / x_axis
        #        - 6.0e17 / (self._wm_logger_logic.get_max_wavelength() + self._wm_logger_logic.get_min_wavelength())
        #    )
        if data is not None:
            self.curve_data_points.setData(data[0, :], data[1, :])

        self.curve_nm_counts.setData(x=x_axis, y=self._wavemeter_logic.histogram)
        #self.curve_hz_counts.setData(x=x_axis_hz, y=self._wm_logger_logic.histogram)
        #self.curve_envelope.setData(x=x_axis, y=self._wm_logger_logic.envelope_histogram)
        return 0

    @QtCore.Slot()
    def start_clicked(self):
        """ Handling the Start button to stop and restart the counter.
        """
        self._mw.start_trace_Action.setEnabled(False)
        self._mw.actionClear_trace_data.setEnabled(False)
        #self._mw.record_trace_Action.setEnabled(False)
        #self._mw.data_rate_DoubleSpinBox.setEnabled(False)
        #self._mw.oversampling_SpinBox.setEnabled(False)
        #self._mw.trace_length_DoubleSpinBox.setEnabled(False)
        #self._mw.moving_average_spinBox.setEnabled(False)
        #self._mw.channel_settings_Action.setEnabled(False)
        if self._mw.start_trace_Action.isChecked():
            #settings = {'trace_window_size': self._mw.trace_length_DoubleSpinBox.value(),
            #            'data_rate': self._mw.data_rate_DoubleSpinBox.value(),
            #            'oversampling_factor': self._mw.oversampling_SpinBox.value(),
            #            'moving_average_width': self._mw.moving_average_spinBox.value()}
            #self.sigSettingsChanged.emit(settings)
            self.sigStartCounter.emit()
        else:
            self.sigStopCounter.emit()
        return

    def recalculate_histogram(self):
        self._wavemeter_logic.recalculate_histogram(
            bins=self._mw.binSpinBox.value(),
            xmin=self._mw.minDoubleSpinBox.value(),
            xmax=self._mw.maxDoubleSpinBox.value()
        )

    @QtCore.Slot(object, object)
    def add_data_points(self, xpoints=None, ypoints=None):
        if xpoints is not None and ypoints is not None:
            self._scatterplot.addPoints(xpoints, ypoints, brush=pg.intColor(xpoints[0]/100, 255))
        return

    @QtCore.Slot()
    def __update_viewbox_sync(self):
        """
        Helper method to sync plots for both y-axes.
        """
        self._vb.setGeometry(self._pw.plotItem.vb.sceneBoundingRect())
        self._vb.linkedViewChanged(self._pw.plotItem.vb, self._vb.XAxis)
        return

    @QtCore.Slot(bool)
    def update_status(self, running=None):
        """
        Function to ensure that the GUI displays the current measurement status

        @param bool running: True if the data trace streaming is running
        @missing param bool recording: True if the data trace recording is active
        """
        if running is None:
            running = self._wavemeter_logic.module_state() == 'locked'
        #if recording is None:
        #    recording = self._time_series_logic.data_recording_active

        self._mw.start_trace_Action.setChecked(running)
        self._mw.start_trace_Action.setText('Stop trace' if running else 'Start trace')

        #self._mw.record_trace_Action.setChecked(recording)
        #self._mw.record_trace_Action.setText('Save recorded' if recording else 'Start recording')

        self._mw.start_trace_Action.setEnabled(True)
        self._mw.actionClear_trace_data.setEnabled(not running)
        #self._mw.record_trace_Action.setEnabled(running)
        return

    @QtCore.Slot()
    def clear_trace_data(self):
        #clear trace data and histogram
        self._wavemeter_logic._data_index = 0
        self._wavemeter_logic._trace_data = np.empty((2, 0), dtype=np.float64)
        self._scatterplot.clear()
        self._wavemeter_logic.start_time_bool = True

        self._wavemeter_logic.histogram = np.zeros(self._wavemeter_logic.histogram_axis.shape)
        # self.envelope_histogram = np.zeros(self.histogram_axis.shape)
        self._wavemeter_logic.rawhisto = np.zeros(self._wavemeter_logic.get_bins())
        # self.envelope_histogram = np.zeros(self._bins)
        self._wavemeter_logic.sumhisto = np.ones(self._wavemeter_logic.get_bins()) * 1.0e-10
        return
