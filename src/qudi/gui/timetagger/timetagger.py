
from sys import displayhook
import numpy as np
import os
import pyqtgraph as pg
from PySide2 import QtCore, QtWidgets, QtGui
import time
import datetime

from qudi.core.connector import Connector
from qudi.util.colordefs import QudiPalettePale as palette
from qudi.core.module import GuiBase
from qudi.util.widgets.plotting import colorbar
from qudi.util.colordefs import ColorScaleInferno
from qudi.util.colordefs import QudiPalette as palette
from qudi.core.statusvariable import StatusVar
from qudi.util.units import ScaledFloat
from qudi.util.mutex import Mutex
from qudi.util.widgets.fitting import FitWidget, FitConfigurationDialog
from qtpy import uic

class TTWindow(QtWidgets.QMainWindow):
    """ Create the Main Window based on the *.ui file. """

    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, 'timetagger.ui')

        # Load it
        super(TTWindow, self).__init__()
        uic.loadUi(ui_file, self)
        self.show()



class TTGui(GuiBase):
    """
    Main GUI for the Timetagger module implementing Counting, Autocorrelation, and histogram functions.

    """
    
    # declare connectors
    timetaggerlogic = Connector(name='timetaggerlogic', interface='TimeTaggerLogic')

    sigToggleCounter = QtCore.Signal(object)
    sigToggleCorr = QtCore.Signal(object)
    sigToggleHist = QtCore.Signal(object)

    _counter_freq = StatusVar('counter_freq', default=50)
    _counter_length = StatusVar('counter_length', default=10)

    _corr_bin_width = StatusVar('corr_bin_width', default=50)
    _corr_record_length = StatusVar('corr_record_length', default=10)

    _hist_bin_width = StatusVar('hist_bin_width', default=50)
    _hist_record_length = StatusVar('hist_record_length', default=10)
    _save_folderpath = StatusVar('save_folder_path', default='Default')
   
    save_folderpath = StatusVar('save_folderpath', default='')


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._timetaggerlogic = None
        self.fit_widget = None
        self._fit_config_dialog = None
        self._mw = None
        self._pw = None

    def on_deactivate(self):
        """ Reverse steps of activation

        @return int: error code (0:OK, -1:error)
        """
        self._save_window_geometry(self._mw)
        self.__disconnect_fit_control_signals()
        self._fsd.close()
        self._fsd = None
        self._mw.close()

    def on_activate(self):
        self._timetaggerlogic = self.timetaggerlogic()
        self._mw = TTWindow()
        self._restore_window_geometry(self._mw)
        self._use_antialias = True

        self.threadlock_counter = Mutex()
        self.threadlock_corr = Mutex()

        # Fit settings dialog
        self._fsd = FitConfigurationDialog(
            parent=self._mw,
            fit_config_model=self._timetaggerlogic.fit_config_model
        )
        self._mw.actionFit_settings.triggered.connect(self._fsd.show)

        # Setup dock widgets
        self._mw.centralwidget.hide()
        self._mw.setDockNestingEnabled(True)

        # Configure PlotWidget
        self._pw = self._mw.counterGraphicsView
        self._pw.setLabel('bottom', 'Time', units='s')
        self._pw.setLabel('left', 'Counts', units='c/s')
        self._pw.setMouseEnabled(x=False, y=False)
        self._pw.setMouseTracking(False)
        self._pw.setMenuEnabled(False)
        self._pw.hideButtons()

        self._corr_pw = self._mw.corrGraphicsView
        self._corr_pw.setLabel('bottom', 'Time', units='s')
        self._corr_pw.setLabel('left', 'g2', units='arb.')

        self._hist_pw = self._mw.histGraphicsView
        self._hist_pw.setLabel('bottom', 'Time', units='s')
        self._hist_pw.setLabel('left', 'Events', units='arb.')

        
        # self._corr_pw.setMouseEnabled(x=False, y=False)
        # self._corr_pw.setMouseTracking(False)
        # self._corr_pw.setMenuEnabled(False)
        # self._corr_pw.hideButtons()

        color = []
        color.append(pg.mkColor(17,95,154))
        color.append(pg.mkColor(153,31,23))
        color.append(pg.mkColor(118,198,143))
        color.append(pg.mkColor(255,180,0))
        color.append(pg.mkColor(226, 124, 124))
        color.append(pg.mkColor(144, 128, 255))

        # Get hardware constraints and extract channel names
        hw_constr = self._timetaggerlogic._constraints
        counter_channels = hw_constr['counter']['channels']
        hist_channels = hw_constr['hist']['channels']

        self.counter_channel_checkBoxes = {}
        self._mw.count_display_comboBox.addItem(f'Channel Sum')
        for ch in counter_channels:
            self._mw.count_display_comboBox.addItem(f'Channel {ch}')

        for index, ch in enumerate(counter_channels):
            label_var_name = 'ch_{0}_Label'.format(ch)
            setattr(self._mw, label_var_name, QtWidgets.QLabel(self._mw))
            label_var = getattr(self._mw, label_var_name) # get the reference
            # set axis_label for the label:
            label_var.setObjectName(label_var_name)
            label_var.setText(str(ch))

            self._mw.counterChannelGridLayout.addWidget(label_var, index, 2, 1, 2)


            label_var_name = 'ch_{0}_checkBox'.format(ch)
            setattr(self._mw, label_var_name, QtWidgets.QCheckBox(self._mw))
            label_var = getattr(self._mw, label_var_name) # get the reference
            # set axis_label for the label:
            label_var.setObjectName(label_var_name)

            self._mw.counterChannelGridLayout.addWidget(label_var, index, 1, 1, 1)
            self.counter_channel_checkBoxes[ch] = label_var
            label_var.setChecked(True)
            label_var.toggled.connect(self.update_counter)
            
        for ch in hist_channels:
            self._mw.histChannelComboBox.addItem(f'{ch}')

        self.curves = dict()
        self.averaged_curves = dict()
        for i, ch in enumerate(counter_channels):
            # Determine pen style
            # FIXME: Choosing a pen width != 1px (not cosmetic) causes massive performance drops
            # For mixed signals each signal type (digital or analog) has the same color
            # If just a single signal type is present, alternate the colors accordingly
            if i % 3 == 0:
                pen1 = pg.mkPen(color[0], cosmetic=True)
                pen2 = pg.mkPen(color[1], cosmetic=True)
            elif i % 3 == 1:
                pen1 = pg.mkPen(color[2], cosmetic=True)
                pen2 = pg.mkPen(color[3], cosmetic=True)
            else:
                pen1 = pg.mkPen(color[4], cosmetic=True)
                pen2 = pg.mkPen(color[5], cosmetic=True)
            self.averaged_curves[ch] = pg.PlotCurveItem(pen=pen2,
                                                        clipToView=True,
                                                        downsampleMethod='subsample',
                                                        autoDownsample=True,
                                                        antialias=self._use_antialias)
            self.curves[ch] = pg.PlotCurveItem(pen=pen1,
                                               clipToView=True,
                                               downsampleMethod='subsample',
                                               autoDownsample=True,
                                               antialias=self._use_antialias)
        self.curves['sum'] = pg.PlotCurveItem(pen=pen1,
                                                    clipToView=True,
                                                    downsampleMethod='subsample',
                                                    autoDownsample=True,
                                                    antialias=self._use_antialias)
        
        self.curves['corr'] = pg.PlotCurveItem(pen=pg.mkPen(color[2], cosmetic=True),
                                                    clipToView=True,
                                                    downsampleMethod='subsample',
                                                    autoDownsample=True,
                                                    antialias=self._use_antialias)

        self.curves['hist'] = pg.PlotCurveItem(pen=pg.mkPen(color[2], cosmetic=True),
                                                    clipToView=True,
                                                    downsampleMethod='subsample',
                                                    autoDownsample=True,
                                                    antialias=self._use_antialias)
        self._corr_pw.addItem(self.curves['corr'])  

        self.fit_curve = self._corr_pw.plot()
        self.fit_curve.setPen(palette.c2, width=2)
        # self._corr_pw.addItem(self.fit_curve)

        self._hist_pw.addItem(self.curves['hist']) 
        
        # Connecting user interactions
        self._mw.toggleCounterPushButton.toggled.connect(self.update_counter)
        self._mw.counterCountFreqDoubleSpinBox.setValue(self._counter_freq)
        self._mw.counterCountLengthDoubleSpinBox.setValue(self._counter_length)
        self._mw.count_display_comboBox.currentTextChanged.connect(self.update_counter)
        self.sigToggleCounter.connect(
            self._timetaggerlogic.configure_counter, QtCore.Qt.QueuedConnection)
        self._timetaggerlogic.sigCounterDataChanged.connect(
            self.update_counter_data, QtCore.Qt.QueuedConnection)

        self._mw.toggleCorrPushButton.toggled.connect(self.update_corr)
        self._mw.corrBinWidthDoubleSpinBox.setValue(self._corr_bin_width)
        self._mw.corrRecordLengthDoubleSpinBox.setValue(self._corr_record_length)
        self.sigToggleCorr.connect(
            self._timetaggerlogic.configure_corr, QtCore.Qt.QueuedConnection)
        self._timetaggerlogic.sigCorrDataChanged.connect(
            self.update_corr_data, QtCore.Qt.QueuedConnection)
        
        #Correlation fitting
        self.fit_widget = FitWidget(parent=self._mw, fit_container=self._timetaggerlogic.fit_container)
        self._mw.fitLayout.addWidget(self.fit_widget)
        self.__connect_fit_control_signals()
        
        self._timetaggerlogic.sig_fit_updated.connect(self.update_fit)

        self._mw.toggleHistPushButton.toggled.connect(self.update_hist)
        self._mw.histBinWidthDoubleSpinBox.setValue(self._hist_bin_width)
        self._mw.histRecordLengthDoubleSpinBox.setValue(self._hist_record_length)
        self.sigToggleHist.connect(
            self._timetaggerlogic.configure_hist, QtCore.Qt.QueuedConnection)
        self._timetaggerlogic.sigHistDataChanged.connect(
            self.update_hist_data, QtCore.Qt.QueuedConnection)
        self._mw.saveAllPushButton.clicked.connect(self._save_data_clicked)
        self._mw.currPathLabel.setText(self._save_folderpath)
        self._mw.DailyPathPushButton.clicked.connect(self._daily_path_clicked)
        self._mw.newPathPushButton.clicked.connect(self._new_path_clicked)

        self._mw.counter_checkBox.setChecked(True)
    
    def show(self):
        """Make window visible and put it above all other windows.
        """
        QtWidgets.QMainWindow.show(self._mw)
        self._mw.activateWindow()
        self._mw.raise_()
        return
        
    def update_counter(self):
        self._counter_freq = self._mw.counterCountFreqDoubleSpinBox.value()
        self._counter_length = self._mw.counterCountLengthDoubleSpinBox.value()
        channels = {}
        active_channels = []
        items = self._pw.items()
        for ch in self.counter_channel_checkBoxes:
            channels[ch] = self.counter_channel_checkBoxes[ch].isChecked()
            if channels[ch]:
                active_channels.append(ch)
            if channels[ch] and self.curves[ch] not in items:
                self._pw.addItem(self.curves[ch])
                self._pw.addItem(self.averaged_curves[ch])
            elif not channels[ch] and self.curves[ch] in items:
                self._pw.removeItem(self.curves[ch])
                self._pw.removeItem(self.averaged_curves[ch])
            
        toggle = self._mw.toggleCounterPushButton.isChecked()
        disp = self._mw.count_display_comboBox.currentText()
        signal_data = {'counter': (self._counter_freq, self._counter_length, channels, toggle, disp)}
        self.sigToggleCounter.emit(signal_data)
    
    def update_counter_data(self, data):
        for ch in data['trace_data']:
            x_arr, y_arr = data['trace_data'][ch]
            self.curves[ch].setData(y=y_arr, x=x_arr)
        for ch in data['trace_data_avg']:
            x_arr, y_arr = data['trace_data_avg'][ch]
            self.averaged_curves[ch].setData(y=y_arr, x=x_arr)
        counts = data['sum']
        self._mw.count_display_label.setText('{:.2r}c/s'.format(ScaledFloat(counts)))

    def update_corr(self):
        self._corr_bin_width = self._mw.corrBinWidthDoubleSpinBox.value()
        self._corr_record_length = self._mw.corrRecordLengthDoubleSpinBox.value()

        toggle = self._mw.toggleCorrPushButton.isChecked()
        signal_data = {'corr': (self._corr_bin_width, self._corr_record_length, toggle)}
        self.sigToggleCorr.emit(signal_data)
    
    def update_corr_data(self, data):
        x_arr, y_arr = data['corr_data']
        self.curves['corr'].setData(y=y_arr, x=x_arr)
    
    def update_hist(self):
        self._hist_bin_width = self._mw.histBinWidthDoubleSpinBox.value()
        self._hist_record_length = self._mw.histRecordLengthDoubleSpinBox.value()

        toggle = self._mw.toggleHistPushButton.isChecked()
        signal_data = {'hist': (self._hist_bin_width, self._hist_record_length, int(self._mw.histChannelComboBox.currentText()), toggle)}
        self.sigToggleHist.emit(signal_data)
    
    def update_hist_data(self, data):
        x_arr, y_arr = data['hist_data']
        self.curves['hist'].setData(y=y_arr, x=x_arr)
    
    def _new_path_clicked(self):
        new_path = QtWidgets.QFileDialog.getExistingDirectory(self._mw, 'Select Folder')
        if new_path:
            self._save_folderpath = new_path
            self._mw.currPathLabel.setText(self._save_folderpath)
    
    def _daily_path_clicked(self):
        self._save_folderpath = 'Default'
        self._mw.currPathLabel.setText(self._save_folderpath)

    def _save_data_clicked(self):
        save_types = {'counter': self._mw.counter_checkBox.isChecked(), 'corr': self._mw.corr_checkBox.isChecked(), 'hist': self._mw.hist_checkBox.isChecked()}
        for st in save_types:
            if save_types[st]:
                save_type = st
                break
        if self._mw.newPathPushButton.isChecked() and self._mw.newPathPushButton.isEnabled():
            #new_path = QtWidgets.QFileDialog.getExistingDirectory(self._mw, 'Select Folder')
            #if new_path:
            #self._save_folderpath = new_path
            self._mw.currPathLabel.setText(self._save_folderpath)
            self._mw.newPathPushButton.setChecked(False)
            save = True
        if self._mw.DailyPathPushButton.isChecked():
            self._save_folderpath = 'Default'
            self._mw.currPathLabel.setText(self._save_folderpath)
            save = True
        # if save:
        self._timetaggerlogic._save_recorded_data(to_file=True, name_tag=self._mw.saveTagLineEdit.text(), save_figure=True, save_type=save_type, save_path = self._save_folderpath)

    def update_fit(self, fit_method, fit_results):
        """ Update the drawn fit curve.
        """
        print("HI!")
        if fit_method != 'No Fit' and fit_results is not None:
            # redraw the fit curve in the GUI plot.
            self.fit_curve.setData(x=fit_results.high_res_best_fit[0],
                                                   y=fit_results.high_res_best_fit[1])
            print(fit_results)
        else:
            self.fit_curve.setData(x=[], y=[])


    def __connect_fit_control_signals(self):
        self.fit_widget.link_fit_container(self._timetaggerlogic.fit_container)
        self.fit_widget.sigDoFit.connect(self._timetaggerlogic.do_fit)

    def __disconnect_fit_control_signals(self):
        self.fit_widget.sigDoFit.disconnect()