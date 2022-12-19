# -*- coding: utf-8 -*-

"""
This file contains a gui for the pid controller logic.

Copyright (c) 2021, the qudi developers. See the AUTHORS.md file at the top-level directory of this
distribution and on <https://github.com/Ulm-IQO/qudi-iqo-modules/>

This file is part of qudi.

Qudi is free software: you can redistribute it and/or modify it under the terms of
the GNU Lesser General Public License as published by the Free Software Foundation,
either version 3 of the License, or (at your option) any later version.

Qudi is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License along with qudi.
If not, see <https://www.gnu.org/licenses/>.
"""

import numpy as np
import os
import pyqtgraph as pg

from qudi.core.connector import Connector
import qudi.util.uic as uic
from qudi.util.colordefs import QudiPalettePale as palette
from qudi.util.units import create_formatted_output
from qudi.core.module import GuiBase
from PySide2 import QtCore, QtWidgets


class PIDMainWindow(QtWidgets.QMainWindow):
    """ Create the Main Window based on the *.ui file. """

    def __init__(self):
        # Get the path to the *.ui file
        this_dir = os.path.dirname(__file__)
        ui_file = os.path.join(this_dir, 'ui_pid_control.ui')

        # Load it
        super().__init__()
        uic.loadUi(ui_file, self)
        self.show()


class PIDGui(GuiBase):
    """ Gui module to monitor and control a PID process

    Example config:

    pid_gui:
        module.Class: 'pidgui.pidgui.PIDGui'
        connect:
            pid_logic: pid_logic

    """

    # declare connectors
    pidlogic = Connector(name='pid_logic', interface='PIDLogic')

    sigStart = QtCore.Signal()
    sigStop = QtCore.Signal()

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        self.log.debug('The following configuration was found.')

        # checking for the right configuration
        for key in config.keys():
            self.log.info('{0}: {1}'.format(key,config[key]))

        # initialize attributes
        self._pid_logic = None
        self._mw = None
        self._pw = None

        self.process_value_unit = None
        self.control_value_unit = None

        self.plot1 = None
        self.plot2 = None
        self._curve1 = None
        self._curve2 = None
        self._curve3 = None

    def on_activate(self):
        """ Definition and initialisation of the GUI plus staring the measurement.

        """
        self._pid_logic = self.pidlogic()

        #####################
        # Configuring the dock widgets
        # Use the inherited class 'CounterMainWindow' to create the GUI window
        self._mw = PIDMainWindow()

        # Setup dock widgets
        self._mw.centralwidget.hide()
        self._mw.setDockNestingEnabled(True)

        # Plot labels.
        self.process_value_unit = self._pid_logic.process_value_unit()
        self.control_value_unit = self._pid_logic.control_value_unit()
        control_value_limits = self._pid_logic.get_control_limits()

        self._pw = self._mw.trace_PlotWidget

        self.plot1 = self._pw.plotItem
        self.plot1.setLabel(
            'left',
            '<font color={0}>Process Value</font> and <font color={1}>Setpoint</font>'.format(
                palette.c1.name(),
                palette.c2.name()),
            units=self.process_value_unit)
        self.plot1.setLabel('bottom', 'Time', units='s')
        self.plot1.showAxis('right')
        self.plot1.getAxis('right').setLabel(
            'Control Value',
            units=self.control_value_unit,
            color=palette.c3.name())

        self.plot2 = pg.ViewBox()
        self.plot1.scene().addItem(self.plot2)
        self.plot1.getAxis('right').linkToView(self.plot2)
        self.plot2.setXLink(self.plot1)

        ## Create an empty plot curve to be filled later, set its pen
        self._curve1 = pg.PlotDataItem(pen=pg.mkPen(palette.c1),#, style=QtCore.Qt.DotLine),
                                       symbol=None
                                       #symbol='o',
                                       #symbolPen=palette.c1,
                                       #symbolBrush=palette.c1,
                                       #symbolSize=3
                                       )

        self._curve3 = pg.PlotDataItem(pen=pg.mkPen(palette.c2),
                                       symbol=None
                                       )

        self._curve2 = pg.PlotDataItem(pen=pg.mkPen(palette.c3),#, style=QtCore.Qt.DotLine),
                                       symbol=None
                                       #symbol='o',
                                       #symbolPen=palette.c3,
                                       #symbolBrush=palette.c3,
                                       #symbolSize=3
                                       )

#        self._curve1 = pg.PlotCurveItem()
#        self._curve1.setPen(palette.c1)

#        self._curve3 = pg.PlotCurveItem()
#        self._curve3.setPen(palette.c2)

#        self._curve2 = pg.PlotCurveItem()
#        self._curve2.setPen(palette.c3)


        self.plot1.addItem(self._curve1)
        self.plot1.addItem(self._curve3)
        self.plot2.addItem(self._curve2)

        self._update_views()
        self.plot1.vb.sigResized.connect(self._update_views)

        # setting the x-axis length correctly
        self._pw.setXRange(0, self._pid_logic.get_buffer_length() * self._pid_logic.timestep)

        #####################
        # set units and range if applicable
        self._mw.setpointDoubleSpinBox.setSuffix(self.process_value_unit)
        self._mw.manualDoubleSpinBox.setRange(*control_value_limits)
        self._mw.manualDoubleSpinBox.setSuffix(self.control_value_unit)

        # Setting default parameters
        self._mw.P_DoubleSpinBox.setValue(self._pid_logic.get_kp())
        self._mw.I_DoubleSpinBox.setValue(self._pid_logic.get_ki())
        self._mw.D_DoubleSpinBox.setValue(self._pid_logic.get_kd())
        self._mw.setpointDoubleSpinBox.setValue(self._pid_logic.get_setpoint())
        self._mw.manualDoubleSpinBox.setValue(self._pid_logic.get_manual_value())
        self._mw.pidEnabledCheckBox.setChecked(self._pid_logic.get_enabled())

        # make correct button state
        self._mw.start_control_Action.setChecked(self._pid_logic.is_recording)

        #####################
        # Connecting user interactions
        self._mw.start_control_Action.triggered.connect(self._start_clicked)
        self._mw.reset_view_Action.triggered.connect(self._reset_clicked)
        self._mw.record_control_Action.triggered.connect(self._save_clicked)

        self._mw.P_DoubleSpinBox.editingFinished.connect(self._kp_changed)
        self._mw.I_DoubleSpinBox.editingFinished.connect(self._ki_changed)
        self._mw.D_DoubleSpinBox.editingFinished.connect(self._kd_changed)

        self._mw.setpointDoubleSpinBox.editingFinished.connect(self._setpoint_changed)
        self._mw.manualDoubleSpinBox.editingFinished.connect(self._manual_value_changed)
        self._mw.pidEnabledCheckBox.toggled.connect(self._pid_enabled_changed)

        # Connect the default view action
        self._mw.restore_default_view_Action.triggered.connect(self._restore_default_view)

        # starting and stopping the data recording loop: setpoint, process value and control value
        self.sigStart.connect(self._pid_logic.start_loop)
        self.sigStop.connect(self._pid_logic.stop_loop)

        self._pid_logic.sigUpdateDisplay.connect(self._update_data)

    def show(self):
        """Make window visible and put it above all other windows.
        """
        QtWidgets.QMainWindow.show(self._mw)
        self._mw.activateWindow()
        self._mw.raise_()

    def on_deactivate(self):
        """ Deactivate the module properly.
        """
        # FIXME: !
        self._mw.close()

    def _update_data(self):
        """ The function that grabs the data and sends it to the plot.
        """

        if self._pid_logic.is_recording:
            # format display values with unit
            pv = create_formatted_output({'Process': {'value': self._pid_logic.get_pv(),
                                                      'unit': self.process_value_unit}})
            pv = pv.split(': ')[1]
            cv = create_formatted_output({'Control': {'value': self._pid_logic.get_cv(),
                                                      'unit': self.control_value_unit}})
            cv = cv.split(': ')[1]
            sp = create_formatted_output({'Setpoint': {'value': self._pid_logic.get_setpoint(),
                                                       'unit': self.process_value_unit}})
            sp = sp.split(': ')[1]

            self._mw.process_value_Label.setText(f'<font color={palette.c1.name()}>{pv}</font>')
            self._mw.control_value_Label.setText(f'<font color={palette.c3.name()}>{cv}</font>')
            self._mw.setpoint_value_Label.setText(f'<font color={palette.c2.name()}>{sp}</font>')

            extra = self._pid_logic._controller.get_extra()
            if 'P' in extra:
                self._mw.labelkP.setText('{0:,.6f}'.format(extra['P']))
            if 'I' in extra:
                self._mw.labelkI.setText('{0:,.6f}'.format(extra['I']))
            if 'D' in extra:
                self._mw.labelkD.setText('{0:,.6f}'.format(extra['D']))
            self._curve1.setData(
                y=self._pid_logic.history[0],
                x=np.arange(0, self._pid_logic.get_buffer_length()) * self._pid_logic.timestep
                )
            self._curve2.setData(
                y=self._pid_logic.history[1],
                x=np.arange(0, self._pid_logic.get_buffer_length()) * self._pid_logic.timestep
                )
            self._curve3.setData(
                y=self._pid_logic.history[2],
                x=np.arange(0, self._pid_logic.get_buffer_length()) * self._pid_logic.timestep
                )

        if self._pid_logic.get_saving_state():
            self._mw.record_control_Action.setText('Save')
        else:
            self._mw.record_control_Action.setText('Start Saving Data')

        if self._pid_logic.is_recording:
            self._mw.start_control_Action.setText('Stop')
        else:
            self._mw.start_control_Action.setText('Start')

    def _update_views(self):
    ## view has resized; update auxiliary views to match
        self.plot2.setGeometry(self.plot1.vb.sceneBoundingRect())

        ## need to re-update linked axes since this was called
        ## incorrectly while views had different shapes.
        ## (probably this should be handled in ViewBox.resizeEvent)
        self.plot2.linkedViewChanged(self.plot1.vb, self.plot2.XAxis)

    def _start_clicked(self):
        """ Handling the Start button to stop and restart the counter.
        """
        if self._pid_logic.is_recording:
            self._mw.start_control_Action.setText('Start')
            self.sigStop.emit()
        else:
            self._mw.start_control_Action.setText('Stop')
            self.sigStart.emit()

    def _reset_clicked(self):
        """ Handling the reset view button to reset the plot.
        """
        self._pid_logic.reset_buffer()
        self._update_data()

    def _save_clicked(self):
        """ Handling the save button to save the data into a file.
        """
        if self._pid_logic.get_saving_state():
            self._mw.record_counts_Action.setText('Start Saving Data')
            self._pid_logic.save_data()
        else:
            self._mw.record_counts_Action.setText('Save')
            self._pid_logic.start_saving()

    def _restore_default_view(self):
        """ Restore the arrangement of DockWidgets to the default
        """
        # Show any hidden dock widgets
        self._mw.pid_trace_DockWidget.show()
        self._mw.pid_parameters_DockWidget.show()

        # re-dock any floating dock widgets
        self._mw.pid_trace_DockWidget.setFloating(False)
        self._mw.pid_parameters_DockWidget.setFloating(False)

        # Arrange docks widgets
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea(1), self._mw.pid_trace_DockWidget)
        self._mw.addDockWidget(QtCore.Qt.DockWidgetArea(8), self._mw.pid_parameters_DockWidget)

    def _kp_changed(self):
        self._pid_logic.set_kp(self._mw.P_DoubleSpinBox.value())

    def _ki_changed(self):
        self._pid_logic.set_ki(self._mw.I_DoubleSpinBox.value())

    def _kd_changed(self):
        self._pid_logic.set_kd(self._mw.D_DoubleSpinBox.value())

    def _setpoint_changed(self):
        self._pid_logic.set_setpoint(self._mw.setpointDoubleSpinBox.value())

    def _manual_value_changed(self):
        self._pid_logic.set_manual_value(self._mw.manualDoubleSpinBox.value())

    def _pid_enabled_changed(self, state):
        self._pid_logic.set_enabled(state)
