import os
from PySide2 import QtCore, QtWidgets, QtGui
import time
import numpy as np
from pyqtgraph import PlotWidget
import pyqtgraph as pg

from qudi.util.colordefs import QudiPalettePale as palette
from qudi.core.module import GuiBase
from qudi.core.connector import Connector
from qudi.util.paths import get_artwork_dir
from typing import Optional, Mapping, Sequence, Union, Tuple, List
from lmfit.model import ModelResult as _ModelResult

from qudi.util.widgets.fitting import FitWidget
from qudi.util.widgets.fitting import FitConfigurationDialog
from qudi.util.widgets.plotting.interactive_curve import InteractiveCurvesWidget

class WavemeterHistogramMainWindow(QtWidgets.QMainWindow):
    """ Create the Main Window for Wavemeter """

    sigFitClicked = QtCore.Signal(str)

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

        icon = QtGui.QIcon(os.path.join(icon_path, 'configure'))
        self.action_show_fit_configuration = QtWidgets.QAction('Fit Configuration')
        self.action_show_fit_configuration.setToolTip(
            'Open a dialog to edit data fitting configurations.')
        self.action_show_fit_configuration.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'start-counter'))
        self.start_trace_Action2 = QtWidgets.QAction('Start Wavemeter')
        self.start_trace_Action2.setCheckable(True)
        self.start_trace_Action2.setToolTip('Start wavemeter to display wavelength')
        self.start_trace_Action2.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'start-counter'))
        self.start_trace_Action = QtWidgets.QAction('Start trace')
        self.start_trace_Action.setCheckable(True)
        self.start_trace_Action.setToolTip('Start counter and wavemeter for data acquisition')
        self.start_trace_Action.setIcon(icon)

        icon = QtGui.QIcon(os.path.join(icon_path, 'edit-clear'))
        self.actionClear_trace_data = QtWidgets.QAction('Clear trace data')
        self.actionClear_trace_data.setIcon(icon)

        self.actionToggle_x_axis = QtWidgets.QAction('Change to frequency')
        self.actionToggle_x_axis.setCheckable(True)

        self.action_autoscale_hist = QtWidgets.QAction('Autoscale Histogram')
        self.action_autoscale_hist.setToolTip('Automatically set boundaries of histogram with min/max x value')

        # Create menu bar and add actions
        menu_bar = QtWidgets.QMenuBar()
        menu = menu_bar.addMenu('File')
        menu.addAction(self.start_trace_Action2)
        menu.addAction(self.start_trace_Action)
        menu.addAction(self.actionClear_trace_data)
        menu.addSeparator()
        menu.addAction(self.action_save)
        menu.addSeparator()
        menu.addAction(self.action_close)

        menu = menu_bar.addMenu('View')
        menu.addAction(self.action_show_fit_configuration)
        menu.addAction(self.actionToggle_x_axis)
        menu.addAction(self.action_autoscale_hist)

        self.setMenuBar(menu_bar)

        # Create toolbar
        toolbar = QtWidgets.QToolBar()
        #TODO size policy
        toolbar.addAction(self.start_trace_Action2)
        toolbar.addAction(self.start_trace_Action)
        toolbar.addAction(self.actionClear_trace_data)
        toolbar.addAction(self.action_save)
        toolbar.addSeparator()
        toolbar.addAction(self.action_show_fit_configuration)
        toolbar.addAction(self.actionToggle_x_axis)
        toolbar.addAction(self.action_autoscale_hist)
        self.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)

        # Create centralwidget, windows and layout
        self.centralwidget = QtWidgets.QWidget()

        self.scatterPlotWidget = PlotWidget(self.centralwidget)
        self.PlotWidget = InteractiveCurvesWidget(self.centralwidget)
        self.PlotWidget.add_marker_selection(position=(0, 0),
                                              mode=self.PlotWidget.SelectionMode.X)
        self.PlotWidget._plot_editor.setVisible(False)

        self.fit_widget = FitWidget()

        #Create current wavelength/freq dock widget
        self.DockWidget2 = QtWidgets.QDockWidget()
        self.dockWidgetContents2 = QtWidgets.QWidget()
        self.wavelengthLabel2 = QtWidgets.QLabel(self.dockWidgetContents2)
        self.frequencyLabel = QtWidgets.QLabel(self.dockWidgetContents2)

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
        self.minDoubleSpinBox.setDecimals(7)
        self.minDoubleSpinBox.setMinimum(1.0)
        self.minDoubleSpinBox.setMaximum(10000.0)
        self.minDoubleSpinBox.setProperty("value", 650.0)

        self.maxDoubleSpinBox = QtWidgets.QDoubleSpinBox(self.dockWidgetContents)
        self.maxDoubleSpinBox.setDecimals(7)
        self.maxDoubleSpinBox.setMinimum(1.0)
        self.maxDoubleSpinBox.setMaximum(10000.0)
        self.maxDoubleSpinBox.setProperty("value", 750.0)

        #Layouts
        layout1 = QtWidgets.QHBoxLayout()
        layout1.addWidget(self.wavelengthLabel2)
        layout1.addStretch()
        layout1.addWidget(self.frequencyLabel)

        layout3 = QtWidgets.QHBoxLayout()
        layout3.addWidget(self.PlotWidget)
        layout3.addWidget(self.fit_widget)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(layout3)
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

        self.dockWidgetContents2.setLayout(layout1)
        self.DockWidget2.setWidget(self.dockWidgetContents2)
        self.addDockWidget(QtCore.Qt.TopDockWidgetArea, self.DockWidget2)
        # Connect close actions
        self.action_close.triggered.connect(self.close)

        self.sigFitClicked = self.fit_widget.sigDoFit


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
    sigDoFit = QtCore.Signal(str)  # fit_config_name
    sigSaveData = QtCore.Signal(str)  # postfix_string
    #_sig_gui_refresh = QtCore.Signal(object) #rate


    # declare connectors
    _wavemeter_histogram_logic_con = Connector(interface='WavemeterLogic')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._wavemeter_logic = None
        self._mw = None
        self._pw = None
        self._vb = None

        self._fit_config_dialog = None

    def on_activate(self):
        """ Definition and initialisation of the GUI.
        """

        self._wavemeter_logic = self._wavemeter_histogram_logic_con()

        # Use the inherited class 'WavemeterHistogramMainWindow' to create the GUI window
        self._mw = WavemeterHistogramMainWindow()

        #fit stuff
        self._fit_config_dialog = FitConfigurationDialog(parent=self._mw, fit_config_model=self._wavemeter_logic.fit_config_model)
        self._mw.fit_widget.link_fit_container(self._wavemeter_logic.get_fit_container())

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
        self.curve_data_points = pg.PlotDataItem(
            symbolPen=pg.mkPen(palette.c1),
            symbol='o',
            pen=None,
            symbolSize=6
        )
        self.curve_data_points.setDownsampling(auto=True, method='peak') #auto, method
        self._pw._plot_widget.addItem(self.curve_data_points)

        #Add plot for interactive plot widget
        #self._pw.plot(name='RawData')
        self._pw.plot(name='Histogram', pen=pg.mkPen(palette.c2))
        self._pw.plot(name='Envelope')
        #add fit plot for histogram data
        self._pw.plot_fit(name='Histogram')

        #TODO rectangular window choice for histogram later
        #region = pg.LinearRegionItem(values=(735.9865620, 735.9866255), orientation='vertical')
        #self._pw._plot_widget.addItem(region)

        # scatter plot
        self._spw = self._mw.scatterPlotWidget
        self._spw.setLabel('bottom', 'Wavelength', units='nm')
        self._spw.setLabel('left', 'Time', units='s')
        self._scatterplot = pg.PlotDataItem(
            pen=None,
            symbol='o',
            symbolPen=pg.mkPen(palette.c3),
            symbolSize=4
        )
        self._scatterplot.setDownsampling(auto=True, method='peak')
        self._spw.addItem(self._scatterplot)
        #self._spw.setXLink(self._pw)

        #####################
        # Connecting file interactions
        self._mw.action_show_fit_configuration.triggered.connect(self._fit_config_dialog.show)
        self._mw.start_trace_Action.triggered.connect(self.start_clicked)
        self._mw.start_trace_Action2.triggered.connect(self.start_clicked_wavemeter)
        self._mw.actionClear_trace_data.triggered.connect(self.clear_trace_data)
        self._mw.action_save.triggered.connect(self._save_clicked)
        #self._sig_gui_refresh.connect(self.update_data_gui, QtCore.Qt.QueuedConnection)
        # Connect the view actions
        self._mw.actionToggle_x_axis.triggered.connect(self.toggle_axis)
        self._mw.sigFitClicked.connect(self._fit_clicked)
        self._mw.action_autoscale_hist.triggered.connect(self.autoscale_histogram_gui)

        # Connect signals to logic
        self.sigStartCounter.connect(
            self._wavemeter_logic.start_scanning, QtCore.Qt.QueuedConnection)
        self.sigStopCounter.connect(
            self._wavemeter_logic.stop_scanning, QtCore.Qt.QueuedConnection)
        self.sigDoFit.connect(self._wavemeter_logic.do_fit, QtCore.Qt.QueuedConnection)
        self.sigSaveData.connect(self._wavemeter_logic.save_data, QtCore.Qt.BlockingQueuedConnection)


        # Connect signals from logic
        self._wavemeter_logic.sigDataChanged.connect(
            self.update_data, QtCore.Qt.QueuedConnection)
        self._wavemeter_logic.sigStatusChanged.connect(
            self.update_status, QtCore.Qt.QueuedConnection)
        self._wavemeter_logic.sigFitChanged.connect(self._update_fit_data, QtCore.Qt.QueuedConnection)
        # signal for scatterplot
        self._wavemeter_logic.sig_new_data_points.connect(self.add_data_points,
                                                          QtCore.Qt.QueuedConnection)
        # signal for current wavelength
        self._wavemeter_logic.sigNewWavelength2.connect(self.display_current_wavelength,
                                                        QtCore.Qt.QueuedConnection)
        #Double spin box actions
        self._mw.binSpinBox.setValue(self._wavemeter_logic.get_bins())
        self._mw.binSpinBox.editingFinished.connect(self.recalculate_histogram)
        self._mw.minDoubleSpinBox.setValue(self._wavemeter_logic._xmin_histo)
        self._mw.minDoubleSpinBox.editingFinished.connect(self.recalculate_histogram)
        self._mw.maxDoubleSpinBox.setValue(self._wavemeter_logic._xmax_histo)
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
        self._mw.action_save.triggered.disconnect()
        self._mw.start_trace_Action.triggered.disconnect()
        self._mw.start_trace_Action2.triggered.disconnect()
        self._mw.actionClear_trace_data.triggered.disconnect()
        self._mw.actionToggle_x_axis.triggered.disconnect()
        self._mw.action_autoscale_hist.triggered.disconnect()
        self._mw.sigFitClicked.disconnect()

        self._mw.binSpinBox.editingFinished.disconnect()
        self._mw.maxDoubleSpinBox.editingFinished.disconnect()
        self._mw.minDoubleSpinBox.editingFinished.disconnect()

        # Disconnect signals to logic
        self.sigStartCounter.disconnect()
        self.sigStopCounter.disconnect()
        self.sigDoFit.disconnect()
        self.sigSaveData.disconnect()

        # Disconnect signals from logic
        self._wavemeter_logic.sigDataChanged.disconnect()
        self._wavemeter_logic.sig_new_data_points.disconnect()
        self._wavemeter_logic.sigNewWavelength2.disconnect()
        self._wavemeter_logic.sigStatusChanged.disconnect()
        self._wavemeter_logic.sigFitChanged.disconnect()

        self._fit_config_dialog.close()
        self._mw.close()

        self._fit_config_dialog = None
        self._mw = None

    @QtCore.Slot(object, object)
    def display_current_wavelength(self, current_wavelength, current_freq):
        if current_wavelength is not None:
            self._mw.wavelengthLabel2.setText('{0:,.6f} nm '.format(current_wavelength))
            self._mw.frequencyLabel.setText('{0:,.6f} Hz '.format(current_freq))
            if not self._wavemeter_logic.x_axis_hz_bool:
                self._pw.move_marker_selection((current_wavelength, 0), 0)
            elif self._wavemeter_logic.x_axis_hz_bool:
                self._pw.move_marker_selection((current_freq, 0), 0)
        return

    @QtCore.Slot(object, object)
    def add_data_points(self, xpoints=None, ypoints=None):
        #if xpoints is not None and ypoints is not None:
        #    if not self._wavemeter_logic.x_axis_hz_bool:
                #self._scatterplot.setData(self._wavemeter_logic.wavelength, self._wavemeter_logic.timings#, symbolPen=pg.intColor(xpoints[0] / 100, 255)
                #                            )
                #self._scatterplot.addPoints(list(xpoints), ypoints)
        #    elif self._wavemeter_logic.x_axis_hz_bool:
                #self._scatterplot.setData(self._wavemeter_logic.frequency, self._wavemeter_logic.timings#,
                                            #symbolPen=pg.intColor(xpoints[0] / 100, 255)
                #                            )
                #xpoints2 = list(3.0e17 / xpoints)
                #self._scatterplot.addPoints(xpoints2, ypoints)
        return

    '''
    @QtCore.Slot(object)
    def update_data_gui(self, rate) -> None:

        if self.gui_refresh:
            timings, counts, wavelength, frequency = self._wavemeter_logic.get_list_values()
            if len(wavelength) > 0:
                if not self._wavemeter_logic.x_axis_hz_bool:
                    if len(wavelength) == len(counts) and len(wavelength) == len(timings):
                        self.curve_data_points.setData(wavelength, counts)
                        self._scatterplot.setData(wavelength, timings)
                elif self._wavemeter_logic.x_axis_hz_bool:
                    self.curve_data_points.setData(frequency, counts)
                    self._scatterplot.setData(frequency, timings)
            time.sleep(1/rate)
            self._sig_gui_refresh.emit(rate)
    '''
    '''
    @QtCore.Slot(object, object)
    def update_data(self, data_wavelength=None, data_counts=None):
        """ The function that grabs the data and sends it to the plot.
        """
        x_axis = self._wavemeter_logic.histogram_axis

        if not self._wavemeter_logic.x_axis_hz_bool:
            if data_wavelength is not None and data_counts is not None:
                #self.curve_data_points.addPoints(list(data_wavelength), list(data_counts))
                #self.curve_data_points.setData(self._wavemeter_logic.wavelength, self._wavemeter_logic.counts)
                #self._scatterplot.setData(self._wavemeter_logic.wavelength,
                #                          self._wavemeter_logic.timings
                #                          )
                self._pw.set_data('Histogram', x=x_axis, y=self._wavemeter_logic.histogram)
                self._pw.set_data('Envelope', x=x_axis, y=self._wavemeter_logic.envelope_histogram)
                #self._pw.set_data('RawData', x=data[0, :], y=data[1, :])
                self._pw.move_marker_selection((data_wavelength[0], 0), 0)

        elif self._wavemeter_logic.x_axis_hz_bool:
            if data_wavelength is not None and data_counts is not None:
                data_freq = 3.0e17 / data_wavelength
                #self.curve_data_points.addPoints(list(data_freq), list(data_counts))
                #self.curve_data_points.setData(self._wavemeter_logic.frequency, self._wavemeter_logic.counts)
                #self._scatterplot.setData(self._wavemeter_logic.frequency,
                #                          self._wavemeter_logic.timings
                #                          )
                self._pw.set_data('Histogram', x=3.0e17 / x_axis, y=self._wavemeter_logic.histogram)
                self._pw.set_data('Envelope', x=3.0e17 / x_axis, y=self._wavemeter_logic.envelope_histogram)
                #self._pw.set_data('RawData', x=data[3, :], y=data[1, :])
                self._pw.move_marker_selection((data_freq[0], 0), 0)

        return 0
    '''

    @QtCore.Slot(object, object, object, object)
    def update_data(self, timings, counts, wavelength, frequency):
        """ The function that grabs the data and sends it to the plot.
        """
        x_axis = self._wavemeter_logic.histogram_axis

        if not self._wavemeter_logic.x_axis_hz_bool:
            if len(wavelength) > 0 and len(wavelength) == len(counts) == len(timings) == len(frequency):
                self.curve_data_points.setData(wavelength, counts)
                self._scatterplot.setData(wavelength, timings)
                self._pw.set_data('Histogram', x=x_axis, y=self._wavemeter_logic.histogram)
                self._pw.set_data('Envelope', x=x_axis, y=self._wavemeter_logic.envelope_histogram)
                # self._pw.set_data('RawData', x=data[0, :], y=data[1, :])
                #TODO Marker
                #self._pw.move_marker_selection((data_wavelength[0], 0), 0)

        elif self._wavemeter_logic.x_axis_hz_bool:
            if len(wavelength) > 0 and len(wavelength) == len(counts) == len(timings) == len(frequency):
                #data_freq = 3.0e17 / data_wavelength
                # self.curve_data_points.addPoints(list(data_freq), list(data_counts))
                self.curve_data_points.setData(frequency, counts)
                self._scatterplot.setData(frequency, timings)
                self._pw.set_data('Histogram', x=3.0e17 / x_axis, y=self._wavemeter_logic.histogram)
                self._pw.set_data('Envelope', x=3.0e17 / x_axis,
                                  y=self._wavemeter_logic.envelope_histogram)
                # self._pw.set_data('RawData', x=data[3, :], y=data[1, :])
                # TODO Marker
                #self._pw.move_marker_selection((data_freq[0], 0), 0)

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

        self._mw.start_trace_Action.setEnabled(True)
        self._mw.actionClear_trace_data.setEnabled(not running)
        self._mw.actionToggle_x_axis.setEnabled(not running)
        self._mw.action_save.setEnabled(not running)
        self._mw.start_trace_Action2.setEnabled(not running)
        return

    @QtCore.Slot()
    def start_clicked(self):
        """ Handling the Start button to stop and restart the counter.
        """
        self._mw.start_trace_Action.setEnabled(False)
        self._mw.actionClear_trace_data.setEnabled(False)
        self._mw.actionToggle_x_axis.setEnabled(False)
        self._mw.action_save.setEnabled(False)
        self._mw.start_trace_Action2.setEnabled(False)

        if self._mw.start_trace_Action.isChecked():
            self.sigStartCounter.emit()
        else:
            self.sigStopCounter.emit()
        return

    def start_clicked_wavemeter(self):
        #self._mw.start_trace_Action2.setEnabled(False)
        if self._mw.start_trace_Action2.isChecked():
            if self._wavemeter_logic._streamer().start_stream() < 0:
                self.log.error('Error while starting streaming device data acquisition.')
                return -1
            self._mw.start_trace_Action2.setText('Stop Wavemeter')
            icon_path = os.path.join(get_artwork_dir(), 'icons')
            icon2 = QtGui.QIcon(os.path.join(icon_path, 'stop-counter'))
            self._mw.start_trace_Action2.setIcon(icon2)
        else:
            self._wavemeter_logic._streamer().stop_stream()
            self._mw.start_trace_Action2.setText('Start Wavemeter')
            icon_path = os.path.join(get_artwork_dir(), 'icons')
            icon1 = QtGui.QIcon(os.path.join(icon_path, 'start-counter'))
            self._mw.start_trace_Action2.setIcon(icon1)

        return

    @QtCore.Slot()
    def toggle_axis(self):
        self._mw.actionToggle_x_axis.setEnabled(False)
        if self._mw.actionToggle_x_axis.isChecked():  # if true toggle to Hz and change boolean x_axis_hz_bool to True and change gui dispaly
            self._mw.actionToggle_x_axis.setText('Change to wavelength')
            #clear any fits
            self._pw.clear_fits()
            # Change the curve plot
            self._wavemeter_logic.x_axis_hz_bool = True
            x_axis_hz = 3.0e17 / self._wavemeter_logic.histogram_axis
            self._pw.set_data('Histogram', x=x_axis_hz, y=self._wavemeter_logic.histogram)
            self._pw.set_data('Envelope', x=x_axis_hz, y=self._wavemeter_logic.envelope_histogram)
            data = self._wavemeter_logic._trace_data
            if len(data[0]) > 0:
                self.curve_data_points.setData(data[3, :], data[1, :])
                #self._pw.set_data('RawData', x=data[3, :], y=data[1, :])
                self._pw.move_marker_selection((data[3, -1], 0), 0)
                # Change the scatterplot
                self._scatterplot.setData(data[3, :], data[0, :])

            # change labels
            self._pw.set_labels('Frequency', 'Flourescence')
            self._pw.set_units('Hz', 'counts/s')
            self._spw.setLabel('bottom', 'Frequency', units='Hz')
            #change dockwidget
            self._mw.minLabel.setText("Minimum Frequency (THz)")
            temp = self._mw.minDoubleSpinBox.value()
            self._mw.minDoubleSpinBox.setValue(3e5 / self._mw.maxDoubleSpinBox.value())
            self._mw.maxLabel.setText('Maximum Frequency (Thz)')
            self._mw.maxDoubleSpinBox.setValue(3e5 / temp)

        else:
            self._mw.actionToggle_x_axis.setText('Change to frequency')
            self._wavemeter_logic.x_axis_hz_bool = False
            #clear any  fits
            self._pw.clear_fits()
            x_axis = self._wavemeter_logic.histogram_axis
            self._pw.set_data('Histogram', x=x_axis, y=self._wavemeter_logic.histogram)
            self._pw.set_data('Envelope', x=x_axis, y=self._wavemeter_logic.envelope_histogram)
            data = self._wavemeter_logic._trace_data
            if len(data[0]) > 0:
                self.curve_data_points.setData(data[2, :], data[1, :])
                #self._pw.set_data('RawData', x=data[0, :], y=data[1, :])
                self._pw.move_marker_selection((data[2, -1], 0), 0)
                # Change the scatterplot
                self._scatterplot.setData(data[2, :], data[0, :])

            self._pw.set_labels('Wavelength', 'Flourescence')
            self._pw.set_units('nm', 'counts/s')
            self._spw.setLabel('bottom', 'Wavelength', units='nm')
            #change dockwidget
            self._mw.minLabel.setText("Minimum Wavelength (nm)")
            temp = self._mw.minDoubleSpinBox.value()
            self._mw.minDoubleSpinBox.setValue(3e5 / self._mw.maxDoubleSpinBox.value())
            self._mw.maxLabel.setText('Maximum Wavelength (nm)')
            self._mw.maxDoubleSpinBox.setValue(3e5 / temp)

        self._mw.actionToggle_x_axis.setEnabled(True)
        return

    @QtCore.Slot()
    def clear_trace_data(self):
        # clear trace data and histogram
        self._wavemeter_logic._data_index = 0
        self._wavemeter_logic._trace_data = np.empty((4, 0), dtype=np.float64)
        self._wavemeter_logic.wavelength = []
        self._wavemeter_logic.frequency = []
        self._wavemeter_logic.timings = []
        self._wavemeter_logic.counts = []
        self._wavemeter_logic._xmax = -1
        self._wavemeter_logic._xmin = 100000

        self.curve_data_points.clear()
        self._scatterplot.clear()
        self._wavemeter_logic.start_time_bool = True
        self._pw.clear_fits()

        self._wavemeter_logic.histogram = np.zeros(self._wavemeter_logic.histogram_axis.shape)
        self._wavemeter_logic.envelope_histogram = np.zeros(self._wavemeter_logic.histogram_axis.shape)
        self._wavemeter_logic.rawhisto = np.zeros(self._wavemeter_logic.get_bins())
        self._wavemeter_logic.sumhisto = np.ones(self._wavemeter_logic.get_bins()) * 1.0e-10
        return

    def recalculate_histogram(self) -> None:
        if not self._wavemeter_logic.x_axis_hz_bool:
            self._wavemeter_logic.recalculate_histogram(
                bins=self._mw.binSpinBox.value(),
                xmin=self._mw.minDoubleSpinBox.value(),
                xmax=self._mw.maxDoubleSpinBox.value()
            )
        if self._wavemeter_logic.x_axis_hz_bool: #when in Hz return value into nm wavelength
            self._wavemeter_logic.recalculate_histogram(
                bins=self._mw.binSpinBox.value(),
                xmin=3e5 / self._mw.maxDoubleSpinBox.value(),
                xmax=3e5 / self._mw.minDoubleSpinBox.value()
            )

    def autoscale_histogram_gui(self) -> None:
        self._wavemeter_logic.autoscale_histogram()
        '''x_axis = self._wavemeter_logic.histogram_axis
        if not self._wavemeter_logic.x_axis_hz_bool:
            if len(self._wavemeter_logic._trace_data[0] > 1):
                self._pw.set_data('Histogram', x=x_axis, y=self._wavemeter_logic.histogram)

        elif self._wavemeter_logic.x_axis_hz_bool:
            if len(self._wavemeter_logic._trace_data[0] > 1):
                self._pw.set_data('Histogram', x=3.0e17 / x_axis, y=self._wavemeter_logic.histogram)'''
        if not self._wavemeter_logic.x_axis_hz_bool:
            self._mw.minDoubleSpinBox.setValue(self._wavemeter_logic._xmin_histo)
            self._mw.maxDoubleSpinBox.setValue(self._wavemeter_logic._xmax_histo)
        if self._wavemeter_logic.x_axis_hz_bool:
            self._mw.minDoubleSpinBox.setValue(3e5 / self._wavemeter_logic._xmax_histo)
            self._mw.maxDoubleSpinBox.setValue(3e5 / self._wavemeter_logic._xmin_histo)


    @QtCore.Slot()
    def __update_viewbox_sync(self):
        """
        Helper method to sync plots for both y-axes.
        """
        self._vb.setGeometry(self._pw.plotItem.vb.sceneBoundingRect())
        self._vb.linkedViewChanged(self._pw.plotItem.vb, self._vb.XAxis)
        return

    def _fit_clicked(self, fit_config: str) -> None:
        self.sigDoFit.emit(fit_config)

    def _update_fit_data(self, fit_config: Optional[str] = None,
                         fit_results: Optional[Mapping[str, Union[None, _ModelResult]]] = None
                         ) -> None:
        """ Function that handles the fit results received from the logic via a signal """
        if fit_config is None or fit_results is None:
            fit_config, fit_results = self._wavemeter_logic.get_fit_results()
        if not fit_config:
            fit_config = 'No Fit'

        if fit_config == 'No Fit':
            self._pw.clear_fits()
        else:
            fit_data = fit_results.high_res_best_fit

            x_data = fit_data[0]
            y_data = fit_data[1]
            self._pw.set_fit_data(name='Histogram', x=x_data, y=y_data, pen='r')

    def _save_clicked(self) -> None:
        self.sigSaveData.emit('')
