import os
from PySide2 import QtCore, QtWidgets, QtGui
import datetime
import numpy as np
from pyqtgraph import PlotWidget
import pyqtgraph as pg

from qudi.util.colordefs import QudiPalettePale as palette
from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.util.paths import get_artwork_dir

from qudi.util.widgets.fitting import FitWidget
from qudi.util.widgets.plotting.interactive_curve import InteractiveCurvesWidget

class WavemeterHistogramMainWindow(QtWidgets.QMainWindow):
    """ Create the Main Window for Wavemeter """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.setWindowTitle('qudi: Laser Scanning')

        # Create QActions
        icon_path = os.path.join(get_artwork_dir(), 'icons')

        icon = QtGui.QIcon(os.path.join(icon_path, 'application-exit'))
        self.action_close = QtWidgets.QAction('Close')
        self.action_close.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'document-save'))
        self.action_save = QtWidgets.QAction('Save')
        #self.action_save.setToolTip('Save all available plots, each in their own file.')
        self.action_save.setIcon(icon)

        #icon = QtGui.QIcon(os.path.join(icon_path, 'configure'))
        #self.action_show_fit_configuration = QtWidgets.QAction('Fit Configuration')
        #self.action_show_fit_configuration.setToolTip(
        #    'Open a dialog to edit data fitting configurations.')
        #self.action_show_fit_configuration.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'start-counter'))
        self.start_trace_Action = QtWidgets.QAction('Start trace')
        self.start_trace_Action.setCheckable(True)
        self.start_trace_Action.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'edit-clear'))
        self.actionClear_trace_data = QtWidgets.QAction('Clear trace data')
        self.actionClear_trace_data.setIcon(icon)

        self.actionToggle_x_axis = QtWidgets.QAction('Change to frequency')
        self.actionToggle_x_axis.setCheckable(True)

        # Create menu bar and add actions
        menu_bar = QtWidgets.QMenuBar()
        menu = menu_bar.addMenu('File')
        menu.addAction(self.start_trace_Action)
        menu.addAction(self.actionClear_trace_data)
        menu.addSeparator()
        menu.addAction(self.action_save)
        menu.addSeparator()
        menu.addAction(self.action_close)

        menu = menu_bar.addMenu('View')
        menu.addAction(self.actionToggle_x_axis)

        self.setMenuBar(menu_bar)

        # Create toolbar
        toolbar = QtWidgets.QToolBar()
        #TODO size policy
        toolbar.addAction(self.start_trace_Action)
        toolbar.addAction(self.actionClear_trace_data)
        toolbar.addAction(self.action_save)
        toolbar.addSeparator()
        toolbar.addAction(self.actionToggle_x_axis)
        self.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)

        # Create centralwidget, windows and layout
        self.centralwidget = QtWidgets.QWidget()
        self.wavelengthLabel2 = QtWidgets.QLabel(self.centralwidget)
        self.frequencyLabel = QtWidgets.QLabel(self.centralwidget)
        self.scatterPlotWidget = PlotWidget(self.centralwidget)
        #TODO Alter plot widget for interactive curve widget to display marker
        #self.PlotWidget = PlotWidget(self.centralwidget)
        self.PlotWidget = InteractiveCurvesWidget(self.centralwidget)
        self.PlotWidget.add_marker_selection(position=(0, 0),
                                              mode=self.PlotWidget.SelectionMode.X)

        # Create Histogram parameter dock widget
        self.DockWidget = QtWidgets.QDockWidget()
        self.dockWidgetContents = QtWidgets.QWidget()
        #label
        self.binLabel = QtWidgets.QLabel(self.dockWidgetContents)
        self.binLabel.setText('Bins (#)')
        self.minLabel = QtWidgets.QLabel(self.dockWidgetContents)
        self.minLabel.setText("Minimum wavelength (nm)")
        self.maxLabel = QtWidgets.QLabel(self.dockWidgetContents)
        self.maxLabel.setText("Maximum wavelength (nm)")
        #spin boxes
        self.binSpinBox = QtWidgets.QSpinBox(self.dockWidgetContents)
        self.binSpinBox.setMinimum(1)
        self.binSpinBox.setMaximum(10000)
        self.binSpinBox.setProperty("value", 200)

        self.minDoubleSpinBox = QtWidgets.QDoubleSpinBox(self.dockWidgetContents)
        self.minDoubleSpinBox.setDecimals(6)
        self.minDoubleSpinBox.setMinimum(1.0)
        self.minDoubleSpinBox.setMaximum(10000.0)
        self.minDoubleSpinBox.setProperty("value", 650.0)

        self.maxDoubleSpinBox = QtWidgets.QDoubleSpinBox(self.dockWidgetContents)
        self.maxDoubleSpinBox.setDecimals(6)
        self.maxDoubleSpinBox.setMinimum(1.0)
        self.maxDoubleSpinBox.setMaximum(10000.0)
        self.maxDoubleSpinBox.setProperty("value", 750.0)

        #Layouts
        layout1 = QtWidgets.QHBoxLayout()
        layout1.addWidget(self.wavelengthLabel2)
        layout1.addStretch()
        layout1.addWidget(self.frequencyLabel)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(layout1)
        #layout.addStretch()
        layout.addWidget(self.PlotWidget)
        layout.addWidget(self.scatterPlotWidget)

        layout2 = QtWidgets.QHBoxLayout()
        layout2.addWidget(self.binLabel)
        layout2.addWidget(self.binSpinBox)
        layout2.addStretch()
        layout2.addWidget(self.minLabel)
        layout2.addWidget(self.minDoubleSpinBox)
        layout2.addStretch()
        layout2.addWidget(self.maxLabel)
        layout2.addWidget(self.maxDoubleSpinBox)

        self.centralwidget.setLayout(layout)

        self.setCentralWidget(self.centralwidget)

        self.dockWidgetContents.setLayout(layout2)
        self.DockWidget.setWidget(self.dockWidgetContents)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.DockWidget)

        # Connect close actions
        self.action_close.triggered.connect(self.close)


class WavemeterHistogramGui(GuiBase):
    """
    GUI module to be used in conjunction with WavemeterHistogramLogic.

    Example config for copy-paste:
    #TODO
    wavemeter_histogram_logic:
        module.Class: 'wavemeter.wavemeter_histogram_gui_2.WavemeterHistogramGui'
        options:
            use_antialias: True  # optional, set to False if you encounter performance issues
        connect:
            _wavemeter_histogram_logic_con: wavemeter_histogram_logic
    """
    #TODO declare signals
    sigStartCounter = QtCore.Signal()
    sigStopCounter = QtCore.Signal()

    # declare connectors
    _wavemeter_histogram_logic_con = Connector(interface='WavemeterLogic')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._wavemeter_logic = None
        self._mw = None
        self._pw = None
        self._vb = None
        self._x_axis_hz_bool = False #by default display wavelength

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """

        self._wavemeter_logic = self._wavemeter_histogram_logic_con()

        # Use the inherited class 'WavemeterHistogramMainWindow' to create the GUI window
        self._mw = WavemeterHistogramMainWindow()

        #TODO
        # Configure PlotWidget now with interactive curve ...

        #histogram plot widget
        self._pw = self._mw.PlotWidget
        self._pw.set_labels('Wavelength', 'Fluorescence')
        self._pw.set_units('nm', 'counts/s')
        #self._pw.setLabel('bottom', 'Wavelength', units='nm')
        #self._pw.setLabel('left', 'Fluorescence', units='counts/s')
        #self._pw.setLabel('right', 'Histogram', units='counts (averaged) per bin/s')
        # Create second ViewBox to plot with two independent y-axes
        #self._vb = pg.ViewBox()
        #self._pw.scene().addItem(self._vb)
        #self._pw.getAxis('right').linkToView(self._vb)
        #self._vb.setXLink(self._pw)
        #self._pw.plotItem.vb.sigResized.connect(self.__update_viewbox_sync)

        # Create an empty plot curve to be filled later, set its pen
        self.curve_data_points = pg.ScatterPlotItem(  # PlotDataItem
            pen=pg.mkPen(palette.c1),
            # symbol=None
        )
        self.curve_nm_counts = pg.PlotDataItem(
            pen=pg.mkPen(palette.c2, width=1),
            symbol=None
        )  # style=QtCore.Qt.DotLine
        self._pw._plot_widget.addItem(self.curve_data_points)
        #self._vb.addItem(self.curve_nm_counts)

        #Add plot for interactive plot widget
        self._pw.plot(name='RawData')
        self._pw.plot(name='Histogram', pen=pg.mkPen(palette.c2))

        # scatter plot
        self._spw = self._mw.scatterPlotWidget
        self._spw.setLabel('bottom', 'Wavelength', units='nm')
        self._spw.setLabel('left', 'Time', units='s')
        self._scatterplot = pg.ScatterPlotItem(size=10, pen=pg.mkPen(None),
                                               brush=pg.mkBrush(255, 255, 255, 20))
        self._spw.addItem(self._scatterplot)
        #self._spw.setXLink(self._pw)

        #####################
        # Connecting file interactions
        self._mw.start_trace_Action.triggered.connect(self.start_clicked)
        self._mw.actionClear_trace_data.triggered.connect(self.clear_trace_data)
        # Connect the view actions
        self._mw.actionToggle_x_axis.triggered.connect(self.toggle_axis)

        # Connect signals to logic
        self.sigStartCounter.connect(
            self._wavemeter_logic.start_scanning, QtCore.Qt.QueuedConnection)
        self.sigStopCounter.connect(
            self._wavemeter_logic.stop_scanning, QtCore.Qt.QueuedConnection)

        # Connect signals from logic
        self._wavemeter_logic.sigDataChanged.connect(
            self.update_data, QtCore.Qt.QueuedConnection)
        self._wavemeter_logic.sigStatusChanged.connect(
            self.update_status, QtCore.Qt.QueuedConnection)
        # signal for scatterplot
        self._wavemeter_logic.sig_new_data_points.connect(self.add_data_points,
                                                          QtCore.Qt.QueuedConnection)
        # signal for current wavelength
        self._wavemeter_logic.sigNewWavelength2.connect(self.display_current_wavelength,
                                                        QtCore.Qt.QueuedConnection)
        #Double spin box actions
        self._mw.binSpinBox.setValue(self._wavemeter_logic.get_bins())
        self._mw.binSpinBox.editingFinished.connect(self.recalculate_histogram)
        self._mw.minDoubleSpinBox.setValue(self._wavemeter_logic.get_min_wavelength())
        self._mw.minDoubleSpinBox.editingFinished.connect(self.recalculate_histogram)
        self._mw.maxDoubleSpinBox.setValue(self._wavemeter_logic.get_max_wavelength())
        self._mw.maxDoubleSpinBox.editingFinished.connect(self.recalculate_histogram)

        self.show()
        #TODO
        #self.restore_view()

    def show(self):
        """ Make window visible and put it above all other windows. """
        self._mw.show()
        self._mw.activateWindow()
        self._mw.raise_()

    def on_deactivate(self):
        """ Deactivate the module """

        # Connect the main window restore view actions

        # disconnect signals
        #self._pw.plotItem.vb.sigResized.disconnect()
        #self._mw.action_save.triggered.disconnect()
        self._mw.start_trace_Action.triggered.disconnect()
        self._mw.actionClear_trace_data.triggered.disconnect()
        self._mw.actionToggle_x_axis.triggered.disconnect()

        self._mw.binSpinBox.editingFinished.disconnect()
        self._mw.maxDoubleSpinBox.editingFinished.disconnect()
        self._mw.minDoubleSpinBox.editingFinished.disconnect()

        # Disconnect signals to logic
        self.sigStartCounter.disconnect()
        self.sigStopCounter.disconnect()

        # Disconnect signals from logic
        self._wavemeter_logic.sigDataChanged.disconnect()
        self._wavemeter_logic.sig_new_data_points.disconnect()
        self._wavemeter_logic.sigNewWavelength2.disconnect()

        #self._fit_config_dialog.close()
        self._mw.close()

        #self._fit_config_dialog = None
        self._mw = None

    @QtCore.Slot(object, object)
    def display_current_wavelength(self, current_wavelength, current_freq):
        if current_wavelength is not None:
            self._mw.wavelengthLabel2.setText('{0:,.6f} nm '.format(current_wavelength))
            self._mw.frequencyLabel.setText('{0:,.6f} GHz '.format(current_freq))
        return

    @QtCore.Slot(object, object)
    def add_data_points(self, xpoints=None, ypoints=None):
        if xpoints is not None and ypoints is not None:
            if not self._x_axis_hz_bool:
                self._scatterplot.addPoints(list(xpoints), ypoints,
                                            brush=pg.intColor(xpoints[0] / 100, 255))
            elif self._x_axis_hz_bool:
                xpoints2 = list(3.0e17 / xpoints)
                self._scatterplot.addPoints(xpoints2, ypoints,
                                            brush=pg.intColor(xpoints[0] / 100, 255))
        return

    @QtCore.Slot(object, object)
    def update_data(self, data_wavelength=None, data_counts=None):
        """ The function that grabs the data and sends it to the plot.
        """

        #self._mw.autoMinLabel.setText(
        #    'Minimum: {0:3.6f} (nm)   '.format(self._wavemeter_logic.get_min_wavelength()))
        #self._mw.autoMaxLabel.setText(
        #    'Maximum: {0:3.6f} (nm)   '.format(self._wavemeter_logic.get_max_wavelength()))

        x_axis = self._wavemeter_logic.histogram_axis

        if not self._x_axis_hz_bool:
            if data_wavelength is not None and data_counts is not None:
                self.curve_data_points.addPoints(list(data_wavelength), list(data_counts))
                data = self._wavemeter_logic._trace_data
                if data is not None:
                    self._pw.set_data('Histogram', x=x_axis, y=self._wavemeter_logic.histogram)
                    self._pw.set_data('RawData', x=data[0, :], y=data[1, :])
                    self._pw.move_marker_selection((data_wavelength[0], 0), 0)

            self.curve_nm_counts.setData(x=x_axis, y=self._wavemeter_logic.histogram)

        elif self._x_axis_hz_bool:
            if data_wavelength is not None and data_counts is not None:
                data_freq = 3.0e17 / data_wavelength
                self.curve_data_points.addPoints(list(data_freq), list(data_counts))
                data = self._wavemeter_logic._trace_data
                self._pw.set_data('Histogram', x=3.0e17 / x_axis, y=self._wavemeter_logic.histogram)
                self._pw.set_data('RawData', x=data[3, :], y=data[1, :])
                self._pw.move_marker_selection((data_freq[0], 0), 0)

            self.curve_nm_counts.setData(x=3.0e17 / x_axis, y=self._wavemeter_logic.histogram)

        return 0

    @QtCore.Slot(bool)
    def update_status(self, running=None):
        """
        Function to ensure that the GUI displays the current measurement status

        @param bool running: True if the data trace streaming is running
        @missing param bool recording: True if the data trace recording is active
        """
        if running is None:
            running = self._wavemeter_logic.module_state() == 'locked'
        # if recording is None:
        #    recording = self._time_series_logic.data_recording_active

        self._mw.start_trace_Action.setChecked(running)
        self._mw.start_trace_Action.setText('Stop trace' if running else 'Start trace')
        icon_path = os.path.join(get_artwork_dir(), 'icons')
        icon1 = QtGui.QIcon(os.path.join(icon_path, 'start-counter'))
        icon2 = QtGui.QIcon(os.path.join(icon_path, 'stop-counter'))
        self._mw.start_trace_Action.setIcon(icon2 if running else icon1)

        # self._mw.record_trace_Action.setChecked(recording)
        # self._mw.record_trace_Action.setText('Save recorded' if recording else 'Start recording')

        self._mw.start_trace_Action.setEnabled(True)
        self._mw.actionClear_trace_data.setEnabled(not running)
        self._mw.actionToggle_x_axis.setEnabled(not running)
        # self._mw.record_trace_Action.setEnabled(running)
        return

    @QtCore.Slot()
    def start_clicked(self):
        """ Handling the Start button to stop and restart the counter.
        """
        self._mw.start_trace_Action.setEnabled(False)
        self._mw.actionClear_trace_data.setEnabled(False)
        self._mw.actionToggle_x_axis.setEnabled(False)
        # self._mw.record_trace_Action.setEnabled(False)

        if self._mw.start_trace_Action.isChecked():
            # settings = {'trace_window_size': self._mw.trace_length_DoubleSpinBox.value(),
            #            'data_rate': self._mw.data_rate_DoubleSpinBox.value(),
            #            'oversampling_factor': self._mw.oversampling_SpinBox.value(),
            #            'moving_average_width': self._mw.moving_average_spinBox.value()}
            # self.sigSettingsChanged.emit(settings)
            self.sigStartCounter.emit()
        else:
            self.sigStopCounter.emit()
        return

    @QtCore.Slot()
    def toggle_axis(self):
        self._mw.actionToggle_x_axis.setEnabled(False)
        if self._mw.actionToggle_x_axis.isChecked():  # if true toggle to Hz and change boolean x_axis_hz_bool to True and change gui dispaly
            self._mw.actionToggle_x_axis.setText('Change to wavelength')
            # Change the curve plot
            self._x_axis_hz_bool = True
            x_axis_hz = 3.0e17 / self._wavemeter_logic.histogram_axis
            #self.curve_nm_counts.setData(x=x_axis_hz, y=self._wavemeter_logic.histogram)
            self._pw.set_data('Histogram', x=x_axis_hz, y=self._wavemeter_logic.histogram)
            data = self._wavemeter_logic._trace_data
            if len(data[0]) > 0:
                self.curve_data_points.setData(data[3, :], data[1, :])
                self._pw.set_data('RawData', x=data[3, :], y=data[1, :])
                self._pw.move_marker_selection((data[3, -1], 0), 0)
                # Change the scatterplot
                self._scatterplot.setData(data[3, :], data[2, :])

            # change labels
            self._pw.set_labels('Frequency', 'Flourescence')
            self._pw.set_units('Hz', 'counts/s')
            self._spw.setLabel('bottom', 'Frequency', units='Hz')

        else:
            self._mw.actionToggle_x_axis.setText('Change to frequency')
            self._x_axis_hz_bool = False
            x_axis = self._wavemeter_logic.histogram_axis
            #self.curve_nm_counts.setData(x=x_axis, y=self._wavemeter_logic.histogram)
            self._pw.set_data('Histogram', x=x_axis, y=self._wavemeter_logic.histogram)
            data = self._wavemeter_logic._trace_data
            if len(data[0]) > 0:
                self.curve_data_points.setData(data[0, :], data[1, :])
                self._pw.set_data('RawData', x=data[0, :], y=data[1, :])
                self._pw.move_marker_selection((data[0, -1], 0), 0)
                # Change the scatterplot
                self._scatterplot.setData(data[0, :], data[2, :])

            self._pw.set_labels('Wavelength', 'Flourescence')
            self._pw.set_units('nm', 'counts/s')
            self._spw.setLabel('bottom', 'Wavelength', units='nm')
        self._mw.actionToggle_x_axis.setEnabled(True)
        return

    @QtCore.Slot()
    def clear_trace_data(self):
        # clear trace data and histogram
        self._wavemeter_logic._data_index = 0
        self._wavemeter_logic._trace_data = np.empty((4, 0), dtype=np.float64)
        self.curve_data_points.clear()
        self._scatterplot.clear()
        self._wavemeter_logic.start_time_bool = True

        self._wavemeter_logic.histogram = np.zeros(self._wavemeter_logic.histogram_axis.shape)
        self._wavemeter_logic.rawhisto = np.zeros(self._wavemeter_logic.get_bins())
        self._wavemeter_logic.sumhisto = np.ones(self._wavemeter_logic.get_bins()) * 1.0e-10
        return

    def recalculate_histogram(self):
        self._wavemeter_logic.recalculate_histogram(
            bins=self._mw.binSpinBox.value(),
            xmin=self._mw.minDoubleSpinBox.value(),
            xmax=self._mw.maxDoubleSpinBox.value()
        )

    @QtCore.Slot()
    def __update_viewbox_sync(self):
        """
        Helper method to sync plots for both y-axes.
        """
        self._vb.setGeometry(self._pw.plotItem.vb.sceneBoundingRect())
        self._vb.linkedViewChanged(self._pw.plotItem.vb, self._vb.XAxis)
        return