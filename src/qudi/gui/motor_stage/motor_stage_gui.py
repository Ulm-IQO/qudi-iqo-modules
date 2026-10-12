# -*- coding: utf-8 -*-
"""
GUI for a motorized stage of independent axes (e.g. the magnet stage on Thorlabs Kinesis
controllers): positions, soft limits, slider / relative / absolute moves, STOP, and a settings
tab for motion parameters and homing.

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

from PySide6 import QtWidgets, QtCore, QtGui

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.core.statusvariable import StatusVar
from qudi.core.module import GuiBase
from qudi.logic.motor_stage_logic import MotorStageLogic
from qudi.gui.source_meter_control.source_meter_control_gui import EasyInputDoubleSpinBox

_SLIDER_STEPS = 1000


def _title_label(text):
    """ Large, bold axis name at the top of each axis box. """
    label = QtWidgets.QLabel(text)
    font = QtGui.QFont()
    font.setPointSize(18)
    font.setBold(True)
    label.setFont(font)
    return label


class _SafeSpinBox(EasyInputDoubleSpinBox):
    """ Ignores the mouse wheel unless the box has keyboard focus (was clicked), so scrolling the
    window over a box cannot change a limit or target by accident. """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


def _spinbox(unit, decimals=4, minimum=-1e6, maximum=1e6, value=0.0, suffix=None):
    box = _SafeSpinBox()
    box.setDecimals(decimals)
    box.setRange(minimum, maximum)
    box.setValue(value)
    box.setSuffix(' ' + (suffix if suffix is not None else unit))
    box.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                      QtWidgets.QSizePolicy.Policy.Preferred)
    return box


class AxisStageWidget(QtWidgets.QGroupBox):
    """ Main-tab controls of one axis. Emits requests; it never talks to the hardware itself. """

    sigMoveAbsolute = QtCore.Signal(str, float)
    sigMoveRelative = QtCore.Signal(str, float)
    sigSetLimits = QtCore.Signal(str, float, float)
    sigRestoreSaved = QtCore.Signal(str)
    sigAcceptController = QtCore.Signal(str)
    sigHome = QtCore.Signal(str)
    sigApplyParams = QtCore.Signal(str, object)
    sigReenable = QtCore.Signal(str)
    sigSetPosition = QtCore.Signal(str, float)

    def __init__(self, axis, info, remembered_backlash=None, parent=None):
        super().__init__(parent)
        self.axis = axis
        self.unit = info['unit']
        travel = info.get('travel')
        self._travel = tuple(travel) if travel else None
        # last non-zero backlash, restored when backlash correction is switched back on
        self.remembered_backlash = remembered_backlash
        self._backlash = None           # current controller value, None = unknown
        self._usable = False
        # Kinesis only accepts targets within the stage travel (controller coordinates)
        lim_min, lim_max = travel if travel else (-1e6, 1e6)
        self._limits = None
        self._dragging = False

        normal = self.font()
        self.title_label = _title_label(axis)
        self.not_homed_label = QtWidgets.QLabel('NOT HOMED - position relative to power-up')
        not_homed_font = QtGui.QFont()
        not_homed_font.setPointSize(11)
        not_homed_font.setBold(True)
        self.not_homed_label.setFont(not_homed_font)
        self.not_homed_label.setStyleSheet('color: red;')
        self.not_homed_label.setToolTip(
            'The controller counts from wherever the stage was when it was switched on, so the '
            'position and the limits do not refer to the real stage travel. Home the axis to fix '
            'this.')
        self.not_homed_label.setVisible(False)

        self.position_label = QtWidgets.QLabel('—')
        big = QtGui.QFont()
        big.setPointSize(14)
        big.setBold(True)
        self.position_label.setFont(big)
        self.status_label = QtWidgets.QLabel('')
        self.status_label.setFont(normal)
        self.status_label.setWordWrap(True)

        # soft limits
        self.lower_box = _spinbox(self.unit, minimum=lim_min, maximum=lim_max)
        self.upper_box = _spinbox(self.unit, minimum=lim_min, maximum=lim_max)
        self.limits_button = QtWidgets.QPushButton('Apply limits')
        self.limits_button.setToolTip('The stage refuses every move outside [lower, upper] '
                                      '(including the backlash overshoot).')
        self.limits_button.clicked.connect(self._apply_limits)
        # limits are also applied when editing a box is finished (Enter / leaving the box)
        self.lower_box.editingFinished.connect(self._limits_edited)
        self.upper_box.editingFinished.connect(self._limits_edited)
        self.full_range_button = QtWidgets.QPushButton('Full range…')
        self.full_range_button.setToolTip(
            'Set the limits to the full stage travel {0}. Removes the protection of the soft '
            'limits.'.format('(unknown for this axis)' if self._travel is None else
                             '[{0:.4f}, {1:.4f}] {2}'.format(*self._travel, self.unit)))
        self.full_range_button.clicked.connect(self._full_range)

        # homing (ignores the soft limits, asks for confirmation)
        self.home_button = QtWidgets.QPushButton('Home…')
        self.home_button.setToolTip('Moves the axis to its home switch, IGNORING the soft limits.')
        self.home_button.clicked.connect(self._home)

        # declare the current position (sets the controller counter, no motion)
        self.set_position_button = QtWidgets.QPushButton('Set position…')
        self.set_position_button.setToolTip(
            'Tell qudi where the axis is now, e.g. 25 mm when you know it sits at the forward end. '
            'Only the controller\'s position counter changes, the stage does not move.')
        self.set_position_button.clicked.connect(self._set_position)
        self._position = None
        self._referenced = False

        # slider (moves on release only)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, _SLIDER_STEPS)
        self.slider_label = QtWidgets.QLabel('')
        self.slider.sliderPressed.connect(self._slider_pressed)
        self.slider.sliderMoved.connect(self._slider_moved)
        self.slider.sliderReleased.connect(self._slider_released)
        self.slider.setToolTip('Drag to choose a target within the limits; the stage moves when '
                               'you release the slider.')

        # relative / absolute moves (relative distance is signed: negative moves backwards)
        self.step_box = _spinbox(self.unit, value=0.1)
        self.step_box.setToolTip('Distance to move; negative values move in the negative direction.')
        self.rel_button = QtWidgets.QPushButton('Move rel.')
        self.rel_button.clicked.connect(lambda: self.sigMoveRelative.emit(self.axis, self.step_box.value()))
        self.target_box = _spinbox(self.unit, minimum=lim_min, maximum=lim_max)
        self.abs_button = QtWidgets.QPushButton('Move abs.')
        self.abs_button.clicked.connect(lambda: self.sigMoveAbsolute.emit(self.axis, self.target_box.value()))

        # backlash correction on/off (off = backlash 0, so moves right up to the limits are allowed)
        self.backlash_checkbox = QtWidgets.QCheckBox('Backlash correction')
        self.backlash_checkbox.setToolTip(
            'On: the stage overshoots the target by the backlash distance and comes back, which '
            'takes up gear play. Moves then need that much room inside the limits.\n'
            'Off: backlash set to 0, moves can go right up to the limits.\n'
            'The value itself is set on the Settings tab.')
        self.backlash_checkbox.clicked.connect(self._backlash_clicked)
        self.backlash_checkbox.setEnabled(False)

        # position mismatch (after a controller power cycle, ...)
        self.mismatch_label = QtWidgets.QLabel('')
        self.mismatch_label.setWordWrap(True)
        self.mismatch_label.setStyleSheet('color: orange;')
        self.restore_button = QtWidgets.QPushButton('Set controller to saved position')
        self.accept_button = QtWidgets.QPushButton('Accept controller position')
        self.restore_button.clicked.connect(self._restore_saved)
        self.accept_button.clicked.connect(self._accept_controller)
        self.mismatch_frame = QtWidgets.QWidget()
        mismatch_layout = QtWidgets.QGridLayout(self.mismatch_frame)
        mismatch_layout.setContentsMargins(0, 0, 0, 0)
        mismatch_layout.addWidget(self.mismatch_label, 0, 0, 1, 2)
        mismatch_layout.addWidget(self.restore_button, 1, 0)
        mismatch_layout.addWidget(self.accept_button, 1, 1)
        self.mismatch_frame.setVisible(False)
        self._mismatch = None

        # motor fault (motor disabled by the controller, connection lost)
        self.fault_label = QtWidgets.QLabel('')
        self.fault_label.setWordWrap(True)
        self.fault_label.setStyleSheet('color: red; font-weight: bold;')
        self.reenable_button = QtWidgets.QPushButton('Re-enable motor…')
        self.reenable_button.setToolTip('Switches the motor on again. Does not move the stage.')
        self.reenable_button.clicked.connect(self._reenable)
        self.fault_frame = QtWidgets.QWidget()
        fault_layout = QtWidgets.QGridLayout(self.fault_frame)
        fault_layout.setContentsMargins(0, 0, 0, 0)
        fault_layout.addWidget(self.fault_label, 0, 0, 1, 2)
        fault_layout.addWidget(self.reenable_button, 1, 0)
        self.fault_frame.setVisible(False)
        self._fault = None

        layout = QtWidgets.QGridLayout(self)
        row = 0
        title_row = QtWidgets.QHBoxLayout()
        title_row.addWidget(self.title_label)
        title_row.addWidget(self.not_homed_label)
        title_row.addStretch(1)
        layout.addLayout(title_row, row, 0, 1, 4)
        self.set_position_button.setFont(normal)
        layout.addWidget(self.set_position_button, row, 4)
        self.home_button.setFont(normal)
        layout.addWidget(self.home_button, row, 5)
        row += 1
        layout.addWidget(self.position_label, row, 0, 1, 2)
        layout.addWidget(self.status_label, row, 2, 1, 4)
        row += 1
        for col, widget in enumerate((QtWidgets.QLabel('Lower limit'), self.lower_box,
                                      QtWidgets.QLabel('Upper limit'), self.upper_box,
                                      self.limits_button, self.full_range_button)):
            widget.setFont(normal)
            layout.addWidget(widget, row, col)
        row += 1
        layout.addWidget(self.slider, row, 0, 1, 5)
        layout.addWidget(self.slider_label, row, 5)
        row += 1
        for col, widget in enumerate((QtWidgets.QLabel('Relative distance'), self.step_box,
                                      self.rel_button)):
            widget.setFont(normal)
            layout.addWidget(widget, row, col)
        self.backlash_checkbox.setFont(normal)
        layout.addWidget(self.backlash_checkbox, row, 3, 1, 3)
        row += 1
        for col, widget in enumerate((QtWidgets.QLabel('Absolute target'), self.target_box,
                                      self.abs_button)):
            widget.setFont(normal)
            layout.addWidget(widget, row, col)
        row += 1
        self.mismatch_frame.setFont(normal)
        layout.addWidget(self.mismatch_frame, row, 0, 1, 6)
        row += 1
        self.fault_frame.setFont(normal)
        layout.addWidget(self.fault_frame, row, 0, 1, 6)
        self.slider_label.setFont(normal)
        self._set_controls_enabled(False)

    # ---- updates from the logic ---------------------------------------------------------------

    def update_state(self, state):
        position, status, limits = state['position'], state['status'], state['limits']
        self.position_label.setText('—' if position is None else
                                    '{0:.4f} {1}'.format(position, self.unit))
        text, color = self._status_text(status)
        self.status_label.setText(text)
        self.status_label.setStyleSheet('color: {0};'.format(color) if color else '')

        if limits is not None:
            self._limits = tuple(limits)
            # the boxes always show the applied limits, except while being edited (a refused
            # value thus springs back)
            for box, value in ((self.lower_box, limits[0]), (self.upper_box, limits[1])):
                if not box.hasFocus() and abs(box.value() - value) > 1e-9:
                    box.setValue(value)
        self._mismatch = status.get('position_mismatch')
        if self._mismatch:
            self.mismatch_label.setText(
                'Controller position {0:.4f} {2} differs from the saved position {1:.4f} {2} '
                '(e.g. after a power cycle). Moves are blocked until you choose which one is '
                'right. Neither button moves the stage.'.format(
                    self._mismatch['controller'], self._mismatch['saved'], self.unit))
        self.mismatch_frame.setVisible(bool(self._mismatch))
        self._fault = status.get('fault')
        if self._fault:
            self.fault_label.setText(
                'MOTOR FAULT: {0}.\nMoves and homing are blocked. Check the stage mechanically '
                '(obstruction, locking screw, cable, power) before re-enabling.'.format(self._fault))
        self.fault_frame.setVisible(bool(self._fault))

        can_move = (status.get('connected') and not status.get('blocked') and not self._mismatch
                    and not self._fault and self._limits is not None)
        self._set_controls_enabled(can_move, moving=status.get('moving') or status.get('homing'))
        usable = bool(status.get('connected') and not status.get('blocked') and not self._fault)
        moving = bool(status.get('moving') or status.get('homing'))
        referenced = bool(status.get('referenced'))
        self._referenced = referenced
        self._position = position
        # only shown when the controller can be read (otherwise "not homed" is not known)
        self.not_homed_label.setVisible(usable and not referenced)
        self.limits_button.setEnabled(usable)
        self.full_range_button.setEnabled(usable and self._travel is not None)
        self.set_position_button.setEnabled(usable and not moving)
        # homing is allowed with a position mismatch: it defines the position anew
        self.home_button.setEnabled(usable and not moving)
        self._usable = usable and not moving
        self._update_backlash_checkbox()
        if position is not None and not self._dragging:
            self._set_slider(position)

    def update_params(self, params):
        """ Motion parameters read from the controller (only the backlash is used here). """
        if 'backlash' not in params:
            return
        self._backlash = params['backlash']
        if self._backlash:
            self.remembered_backlash = self._backlash
        self._update_backlash_checkbox()

    def _update_backlash_checkbox(self):
        known = self._backlash is not None
        self.backlash_checkbox.setChecked(known and self._backlash > 0)
        # switching on needs a value to restore; switching off is always possible
        can_switch = known and (self._backlash > 0 or bool(self.remembered_backlash))
        self.backlash_checkbox.setEnabled(self._usable and can_switch)

    def _backlash_clicked(self, checked):
        value = self.remembered_backlash if checked else 0.0
        if checked and not value:
            self._update_backlash_checkbox()
            return
        # show the controller state until the read-back confirms the change
        self.backlash_checkbox.setChecked(not checked)
        self.sigApplyParams.emit(self.axis, {'backlash': value})

    def _status_text(self, status):
        if not status.get('connected'):
            return 'not connected: {0}'.format(status.get('blocked') or ''), 'red'
        if status.get('blocked'):
            return 'BLOCKED: {0}'.format(status['blocked']), 'red'
        if status.get('fault'):
            return 'MOTOR FAULT, see below', 'red'
        if status.get('position_mismatch'):
            return 'position mismatch, see below', 'orange'
        parts = ['homing' if status.get('homing') else 'moving' if status.get('moving') else 'idle']
        if not status.get('enabled'):
            parts.append('motor disabled')
        if status.get('cw_limit') or status.get('ccw_limit'):
            parts.append('at hardware limit switch')
        return ', '.join(parts), 'orange' if len(parts) > 1 else ''

    def _set_controls_enabled(self, can_move, moving=False):
        enabled = bool(can_move) and not moving
        span_ok = self._limits is not None and self._limits[1] > self._limits[0]
        for widget in (self.rel_button, self.abs_button):
            widget.setEnabled(enabled)
        self.slider.setEnabled(enabled and span_ok)

    # ---- slider -------------------------------------------------------------------------------

    def _slider_value_to_position(self, value):
        lower, upper = self._limits
        return lower + (upper - lower) * value / _SLIDER_STEPS

    def _set_slider(self, position):
        if self._limits is None or self._limits[1] <= self._limits[0]:
            return
        lower, upper = self._limits
        value = round((position - lower) / (upper - lower) * _SLIDER_STEPS)
        self.slider.blockSignals(True)
        self.slider.setValue(max(0, min(_SLIDER_STEPS, value)))
        self.slider.blockSignals(False)

    def _slider_pressed(self):
        self._dragging = True
        self._press_value = self.slider.value()

    def _slider_moved(self, value):
        self.slider_label.setText('→ {0:.4f} {1}'.format(self._slider_value_to_position(value), self.unit))

    def _slider_released(self):
        self._dragging = False
        self.slider_label.setText('')
        # a click on the handle without dragging must not move the stage (the slider value is
        # only an approximation of the position)
        if self._limits is not None and self.slider.value() != getattr(self, '_press_value', None):
            self.sigMoveAbsolute.emit(self.axis, self._slider_value_to_position(self.slider.value()))

    # ---- buttons ------------------------------------------------------------------------------

    def _apply_limits(self):
        self.sigSetLimits.emit(self.axis, self.lower_box.value(), self.upper_box.value())

    def _limits_edited(self):
        if not self.limits_button.isEnabled():
            return
        new = (self.lower_box.value(), self.upper_box.value())
        if self._limits is None or max(abs(a - b) for a, b in zip(new, self._limits)) > 1e-9:
            self._apply_limits()

    def _full_range(self):
        if self._travel is None:
            return
        lower, upper = self._travel
        not_homed = ('' if self._referenced else
                     '\n\nThe axis is NOT HOMED: this range is in controller coordinates (counted '
                     'from the power-up or set position), so it may not match the real ends of the '
                     'stage.')
        if _confirm(self, 'Set limits to full range',
                    'Set the limits of "{0}" to the full stage travel [{1:.4f}, {2:.4f}] {3}?\n\n'
                    'The soft limits then no longer protect anything: every position the stage '
                    'can reach is allowed. Only do this if nothing can collide along the whole '
                    'travel. The stage does not move now.{4}'.format(
                        self.axis, lower, upper, self.unit, not_homed)):
            self.lower_box.setValue(lower)
            self.upper_box.setValue(upper)
            self.sigSetLimits.emit(self.axis, lower, upper)

    def _home(self):
        if _confirm(self, 'Home axis',
                    'Home axis "{0}"?\n\nHoming drives the axis to its home limit switch and '
                    'IGNORES the soft limits. Make sure nothing is in the way along the whole '
                    'path before you confirm.'.format(self.axis)):
            self.sigHome.emit(self.axis)

    def _set_position(self):
        lower, upper = self._travel if self._travel else (-1e6, 1e6)
        current = self._position if self._position is not None else lower
        homed = ('\n\nThe axis is HOMED: setting a position discards the home reference.'
                 if self._referenced else '')
        value = _ask_position(
            self, 'Set position of "{0}"'.format(self.axis),
            'Where is "{0}" right now? (currently shown: {1:.4f} {2})\n\n'
            'Only the controller\'s position counter is changed, the stage does NOT move. '
            'Moves are then possible within the stage travel [{3:.4f}, {4:.4f}] {2} counted from '
            'this value, so enter the real position (e.g. {4:.4f} if it sits at the forward end).\n\n'
            'The axis stays NOT HOMED and its limits are reset to this position: set new limits '
            'afterwards.{5}'.format(self.axis, current, self.unit, lower, upper, homed),
            current, lower, upper)
        if value is not None:
            self.sigSetPosition.emit(self.axis, value)

    def _reenable(self):
        if self._fault and _confirm(
                self, 'Re-enable motor',
                'Re-enable the motor of "{0}"?\n\n{1}.\n\nThe controller switched the motor off '
                'by itself, often because the stage was blocked. Only continue if you have '
                'checked that nothing is in the way and the stage can move freely.\n\n'
                'Re-enabling does not move the stage. If the controller lost its position, '
                'you will be asked which position is right.'.format(self.axis, self._fault)):
            self.sigReenable.emit(self.axis)

    def _restore_saved(self):
        if self._mismatch and _confirm(
                self, 'Set controller to saved position',
                'Set the controller position counter of "{0}" to the saved position {1:.4f} {2}?\n\n'
                'Only the number is changed, the motor does not move. Do this if the stage has not '
                'been moved since qudi last saw it (e.g. the controller was only switched off and '
                'on).'.format(self.axis, self._mismatch['saved'], self.unit)):
            self.sigRestoreSaved.emit(self.axis)

    def _accept_controller(self):
        if self._mismatch and _confirm(
                self, 'Accept controller position',
                'Take the controller position {0:.4f} {1} of "{2}" as the true position?\n\n'
                'The motor does not move. The soft limits stay as they are and then refer to this '
                'position: check them before moving.'.format(
                    self._mismatch['controller'], self.unit, self.axis)):
            self.sigAcceptController.emit(self.axis)


class AxisSettingsWidget(QtWidgets.QGroupBox):
    """ Settings-tab controls of one axis: motion parameters, homing, controller info. """

    sigApplyParams = QtCore.Signal(str, object)
    sigReadParams = QtCore.Signal(str)
    sigHome = QtCore.Signal(str)

    def __init__(self, axis, info, parent=None):
        super().__init__(parent)
        self.axis = axis
        unit = info['unit']
        self.title_label = _title_label(axis)
        self.boxes = {
            'velocity': _spinbox(unit, minimum=0.0, suffix=unit + '/s'),
            'acceleration': _spinbox(unit, minimum=0.0, suffix=unit + '/s²'),
            'backlash': _spinbox(unit, minimum=0.0),
            'homing_velocity': _spinbox(unit, minimum=0.0, suffix=unit + '/s'),
        }
        tips = {
            'velocity': 'Maximum velocity of moves.',
            'acceleration': 'Acceleration/deceleration of moves.',
            'backlash': 'Overshoot used to take up gear play: the stage moves this far past the '
                        'target and back. Moves need this much room inside the limits.',
            'homing_velocity': 'Velocity while homing.',
        }
        self.read_button = QtWidgets.QPushButton('Read from controller')
        self.apply_button = QtWidgets.QPushButton('Apply')
        self.home_button = QtWidgets.QPushButton('Home…')
        self.home_button.setToolTip('Moves the axis to its home switch, IGNORING the soft limits.')
        self.read_button.clicked.connect(lambda: self.sigReadParams.emit(self.axis))
        self.apply_button.clicked.connect(self._apply)
        self.home_button.clicked.connect(self._home)

        travel = info.get('travel')
        info_text = 'Controller {0}, S/N {1}{2}, {3} counts/{4}, stage travel {5}'.format(
            info.get('controller'), info.get('serial'),
            ', channel {0}'.format(info['channel']) if info.get('controller') == 'BSC' else '',
            'unverified' if info.get('counts_per_unit') is None else '{0:g}'.format(info['counts_per_unit']),
            unit, 'unknown' if not travel else '{0:.3f} … {1:.3f} {2}'.format(travel[0], travel[1], unit))
        info_label = QtWidgets.QLabel(info_text)
        info_label.setWordWrap(True)

        # controls live in a body widget that is disabled as a whole, so the axis name stays
        # readable and the per-box enabled state (unsupported parameter) is kept
        self.body = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(self)
        outer.addWidget(self.title_label)
        outer.addWidget(self.body)
        layout = QtWidgets.QGridLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        for row, (key, label) in enumerate((('velocity', 'Max. velocity'),
                                            ('acceleration', 'Acceleration'),
                                            ('backlash', 'Backlash'),
                                            ('homing_velocity', 'Homing velocity')), start=1):
            name = QtWidgets.QLabel(label)
            name.setToolTip(tips[key])
            self.boxes[key].setToolTip(tips[key])
            layout.addWidget(name, row, 0)
            layout.addWidget(self.boxes[key], row, 1)
        layout.addWidget(self.read_button, 1, 2)
        layout.addWidget(self.apply_button, 2, 2)
        layout.addWidget(self.home_button, 4, 2)
        layout.addWidget(info_label, 5, 0, 1, 3)
        self.body.setEnabled(False)

    def update_params(self, params):
        for key, value in params.items():
            box = self.boxes.get(key)
            if box is None:
                continue
            box.setEnabled(value is not None)
            if value is not None and not box.hasFocus():
                box.setValue(value)

    def set_available(self, available):
        self.body.setEnabled(bool(available))

    def _apply(self):
        params = {key: box.value() for key, box in self.boxes.items() if box.isEnabled()}
        self.sigApplyParams.emit(self.axis, params)

    def _home(self):
        if _confirm(self, 'Home axis',
                    'Home axis "{0}"?\n\nHoming drives the axis to its home limit switch and '
                    'IGNORES the soft limits. Make sure nothing is in the way along the whole '
                    'path.'.format(self.axis)):
            self.sigHome.emit(self.axis)


def _ask_position(parent, title, text, value, minimum, maximum):
    """ Number input dialog; returns the value or None if cancelled. """
    value, ok = QtWidgets.QInputDialog.getDouble(parent, title, text, value, minimum, maximum, 4)
    return value if ok else None


def _confirm(parent, title, text):
    answer = QtWidgets.QMessageBox.warning(
        parent, title, text,
        QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
        QtWidgets.QMessageBox.StandardButton.Cancel)
    return answer == QtWidgets.QMessageBox.StandardButton.Yes


class MotorStageMainWindow(QtWidgets.QMainWindow):
    def __init__(self, title, n_axes, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle(title)
        self.stop_button = QtWidgets.QPushButton('STOP ALL AXES')
        self.stop_button.setMinimumHeight(40)
        self.stop_button.setStyleSheet('background-color: #b00000; color: white; font-weight: bold;')

        # View -> Axes per row: arranges the axis boxes in a grid (e.g. 3 per row = 2 rows of 3)
        view_menu = self.menuBar().addMenu('View')
        per_row_menu = view_menu.addMenu('Axes per row')
        self.axes_per_row_group = QtGui.QActionGroup(self)
        self.axes_per_row_group.setExclusive(True)
        self.axes_per_row_actions = dict()
        for n in range(1, max(1, n_axes) + 1):
            action = QtGui.QAction(str(n), self.axes_per_row_group)
            action.setCheckable(True)
            action.setData(n)
            per_row_menu.addAction(action)
            self.axes_per_row_actions[n] = action

        self.tabs = QtWidgets.QTabWidget()
        self.stage_layout = QtWidgets.QGridLayout()
        self.settings_layout = QtWidgets.QGridLayout()
        for layout, name in ((self.stage_layout, 'Stage'), (self.settings_layout, 'Settings')):
            layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
            page = QtWidgets.QWidget()
            page.setLayout(layout)
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, name)
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)


class MotorStageGui(GuiBase):
    """
    Example config for copy-paste:

        magnet_stage_gui:
            module.Class: 'motor_stage.motor_stage_gui.MotorStageGui'
            options:
                window_title: 'qudi: Magnet Stage'   # optional
            connect:
                stage_logic: 'magnet_stage_logic'
    """

    stage_logic = Connector(interface=MotorStageLogic)
    _window_title = ConfigOption(name='window_title', default='qudi: Motor Stage', missing='nothing')
    _axes_per_row = StatusVar(name='axes_per_row', default=1)   # set via View -> Axes per row
    # last non-zero backlash per axis, restored by the "Backlash correction" checkbox
    _remembered_backlash = StatusVar(name='remembered_backlash', default=dict())

    sigMoveAbsolute = QtCore.Signal(str, float)
    sigMoveRelative = QtCore.Signal(str, float)
    sigSetLimits = QtCore.Signal(str, float, float)
    sigRestoreSaved = QtCore.Signal(str)
    sigAcceptController = QtCore.Signal(str)
    sigApplyParams = QtCore.Signal(str, object)
    sigReadParams = QtCore.Signal(str)
    sigHome = QtCore.Signal(str)
    sigReenable = QtCore.Signal(str)
    sigSetPosition = QtCore.Signal(str, float)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._mw = None
        self._axis_widgets = dict()
        self._settings_widgets = dict()

    def on_activate(self):
        logic = self.stage_logic()
        self._mw = MotorStageMainWindow(self._window_title, len(logic.axes))
        queued = QtCore.Qt.ConnectionType.QueuedConnection

        for axis in logic.axes:
            info = logic.get_axis_info(axis)
            remembered = self._remembered_backlash.get(axis) if isinstance(
                self._remembered_backlash, dict) else None
            stage_widget = AxisStageWidget(axis, info, remembered_backlash=remembered)
            settings_widget = AxisSettingsWidget(axis, info)
            stage_widget.sigMoveAbsolute.connect(self.sigMoveAbsolute)
            stage_widget.sigMoveRelative.connect(self.sigMoveRelative)
            stage_widget.sigSetLimits.connect(self.sigSetLimits)
            stage_widget.sigRestoreSaved.connect(self.sigRestoreSaved)
            stage_widget.sigAcceptController.connect(self.sigAcceptController)
            stage_widget.sigHome.connect(self.sigHome)
            stage_widget.sigApplyParams.connect(self.sigApplyParams)
            stage_widget.sigReenable.connect(self.sigReenable)
            stage_widget.sigSetPosition.connect(self.sigSetPosition)
            settings_widget.sigApplyParams.connect(self.sigApplyParams)
            settings_widget.sigReadParams.connect(self.sigReadParams)
            settings_widget.sigHome.connect(self.sigHome)
            self._axis_widgets[axis] = stage_widget
            self._settings_widgets[axis] = settings_widget
        self._set_axes_per_row(self._axes_per_row)
        self._mw.axes_per_row_group.triggered.connect(
            lambda action: self._set_axes_per_row(action.data()))

        # STOP is called directly in this thread, never queued behind other commands
        self._mw.stop_button.clicked.connect(logic.stop_all)

        self.sigMoveAbsolute.connect(logic.move_absolute, queued)
        self.sigMoveRelative.connect(logic.move_relative, queued)
        self.sigSetLimits.connect(logic.set_limits, queued)
        self.sigRestoreSaved.connect(logic.restore_saved_position, queued)
        self.sigAcceptController.connect(logic.accept_controller_position, queued)
        self.sigApplyParams.connect(logic.set_motion_params, queued)
        self.sigReadParams.connect(logic.refresh_motion_params, queued)
        self.sigHome.connect(logic.home, queued)
        self.sigReenable.connect(logic.reenable, queued)
        self.sigSetPosition.connect(logic.set_position, queued)
        logic.sigAxisUpdated.connect(self._axis_updated, queued)
        logic.sigMotionParamsUpdated.connect(self._params_updated, queued)

        for axis in logic.axes:
            self._axis_updated(axis, logic.get_axis_state(axis))
            self.sigReadParams.emit(axis)

        self._restore_window_geometry(self._mw)
        self.show()

    def on_deactivate(self):
        logic = self.stage_logic()
        logic.sigAxisUpdated.disconnect(self._axis_updated)
        logic.sigMotionParamsUpdated.disconnect(self._params_updated)
        self._mw.stop_button.clicked.disconnect()
        self._mw.axes_per_row_group.triggered.disconnect()
        for signal in (self.sigMoveAbsolute, self.sigMoveRelative, self.sigSetLimits,
                       self.sigRestoreSaved, self.sigAcceptController, self.sigApplyParams,
                       self.sigReadParams, self.sigHome, self.sigReenable, self.sigSetPosition):
            signal.disconnect()
        self._remembered_backlash = {axis: widget.remembered_backlash
                                     for axis, widget in self._axis_widgets.items()
                                     if widget.remembered_backlash}
        self._save_window_geometry(self._mw)
        self._mw.close()
        self._axis_widgets = dict()
        self._settings_widgets = dict()

    def _set_axes_per_row(self, n):
        """ Arrange the axis boxes of both tabs in a grid with n boxes per row. """
        n_axes = len(self._axis_widgets)
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = 1
        n = max(1, min(n, max(1, n_axes)))
        self._axes_per_row = n
        self._mw.axes_per_row_actions[n].setChecked(True)
        for layout, widgets in ((self._mw.stage_layout, self._axis_widgets),
                                (self._mw.settings_layout, self._settings_widgets)):
            for widget in widgets.values():
                layout.removeWidget(widget)
            for col in range(layout.columnCount()):
                layout.setColumnStretch(col, 0)
            for i, widget in enumerate(widgets.values()):
                layout.addWidget(widget, i // n, i % n)
            for col in range(n):
                layout.setColumnStretch(col, 1)

    def show(self):
        self._mw.show()
        self._mw.raise_()
        self._mw.activateWindow()

    @QtCore.Slot(str, object)
    def _axis_updated(self, axis, state):
        widget = self._axis_widgets.get(axis)
        if widget is not None:
            widget.update_state(state)
        settings = self._settings_widgets.get(axis)
        if settings is not None:
            status = state['status']
            settings.set_available(status.get('connected') and not status.get('blocked'))

    @QtCore.Slot(str, object)
    def _params_updated(self, axis, params):
        for widgets in (self._settings_widgets, self._axis_widgets):
            widget = widgets.get(axis)
            if widget is not None:
                widget.update_params(params)
