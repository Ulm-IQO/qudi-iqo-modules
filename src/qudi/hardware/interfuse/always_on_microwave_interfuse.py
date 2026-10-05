# -*- coding: utf-8 -*-

"""
AlwaysOnMicrowaveInterfuse
==========================

Wraps a MicrowaveInterface hardware module (e.g. sg384.py) so that off()
is suppressed for as long as the wrapped hardware is confirmed, live, to
still be configured as an external IQ modulator. All other calls are
forwarded unchanged.

Use case: a signal generator whose carrier feeds an external I/Q mixer,
with the AWG's envelope doing all the pulse shaping. PulsedMeasurementLogic
calls microwave.off()/on() around every measurement/pause -- this interfuse
keeps the physical carrier running through that cycle, since gating it
would defeat the point of external IQ modulation.

Live per-call check (off())
----------------------------
Every off() call queries the wrapped hardware's iq_modulator_active()
freshly -- there is no cached "always-on" flag. This means:
  - IQ modulator confirmed active right now  -> off() is a no-op (the real
                                                hardware off() is NOT called).
  - IQ modulator NOT confirmed active        -> off() forwards to real
                                                hardware (nothing left to
                                                justify keeping it on).
  - Wrapped hardware has no iq_modulator_active() method -> fails safe,
    off() forwards to real hardware, warning logged.
Setting require_iq_modulator: False skips the check and makes off()
unconditionally suppressed on every call (no verification at all).

module_state
------------
Callers (e.g. PulsedMeasurementLogic) check module_state() on whatever is
connected to their microwave connector -- this interfuse, not the wrapped
hardware -- and expect it to flip idle/locked in step with off()/cw_on()
calls, logging an error otherwise. This interfuse's module_state therefore
tracks THAT calling convention, not the literal, physical RF output state:
cw_on() always locks it, and off() always unlocks it, on every call,
regardless of whether off() actually reached real hardware that time. The
true, physical output state is NOT module_state -- check
iq_modulator_active() (or the wrapped hardware's own state) for that.

Escape hatch
------------
force_off() is a real hardware write, not part of MicrowaveInterface, so
ordinary callers (including PulsedMeasurementLogic) can never trigger it
by accident. Use it from a console when you actually want the RF off
regardless of IQ modulator state.

Example config:

    mw_always_on:
        module.Class: 'interfuse.always_on_microwave_interfuse.AlwaysOnMicrowaveInterfuse'
        connect:
            microwave: 'sg384'
        options:
            require_iq_modulator: True
            auto_enable_on_activate: True
"""

from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.interface.microwave_interface import MicrowaveInterface


class AlwaysOnMicrowaveInterfuse(MicrowaveInterface):
    """ Suppresses off() on a MicrowaveInterface module while it is live-
    confirmed to still be an active external IQ modulator.
    """

    _microwave = Connector(name='microwave', interface=MicrowaveInterface)

    # Live-verify IQ modulator state via iq_modulator_active() before
    # suppressing off(). If False, off() is always suppressed, unverified.
    _require_iq_modulator = ConfigOption('require_iq_modulator', default=True)

    # Attempt to turn the carrier on immediately at activation.
    _auto_enable_on_activate = ConfigOption('auto_enable_on_activate', default=True)

    def on_activate(self):
        """ Optionally enable the carrier at startup. off()'s own behavior
        is independent of this -- it re-checks live on every call.
        """
        if not self._auto_enable_on_activate:
            self.log.info(
                'AlwaysOnMicrowaveInterfuse: auto_enable_on_activate is '
                'False -- not enabling carrier at activation.'
            )
            return

        mw = self._microwave()

        if self._require_iq_modulator:
            if not hasattr(mw, 'iq_modulator_active'):
                self.log.warning(
                    'AlwaysOnMicrowaveInterfuse: hardware has no '
                    'iq_modulator_active() method -- not auto-enabling.'
                )
                return
            if not mw.iq_modulator_active():
                self.log.warning(
                    'AlwaysOnMicrowaveInterfuse: IQ modulator not '
                    'confirmed active -- not auto-enabling.'
                )
                return

        self.log.info('AlwaysOnMicrowaveInterfuse: enabling carrier at activation.')
        mw.cw_on()
        if self.module_state() == 'idle':
            self.module_state.lock()

    def on_deactivate(self):
        """ Deliberately leaves the carrier running -- call force_off()
        first if you want it off before deactivating.
        """
        pass

    # =========================================================================
    # MicrowaveInterface -- properties, forwarded unchanged
    # =========================================================================

    @property
    def constraints(self):
        return self._microwave().constraints

    @property
    def is_scanning(self):
        return self._microwave().is_scanning

    @property
    def cw_power(self):
        return self._microwave().cw_power

    @property
    def cw_frequency(self):
        return self._microwave().cw_frequency

    @property
    def scan_power(self):
        return self._microwave().scan_power

    @property
    def scan_frequencies(self):
        return self._microwave().scan_frequencies

    @property
    def scan_mode(self):
        return self._microwave().scan_mode

    @property
    def scan_sample_rate(self):
        return self._microwave().scan_sample_rate

    # =========================================================================
    # MicrowaveInterface -- methods
    # =========================================================================

    def set_cw(self, frequency, power):
        """ Forwarded unchanged. """
        self._microwave().set_cw(frequency, power)

    def cw_on(self):
        """ Forwards to hardware, then locks this interfuse's own
        module_state (callers check module_state on THIS object).
        """
        self._microwave().cw_on()
        if self.module_state() == 'idle':
            self.module_state.lock()

    def off(self):
        """ Suppresses the real hardware off() while the IQ modulator is
        live-confirmed active; otherwise forwards through to real
        hardware. See module docstring, "Live per-call check".

        module_state is ALWAYS unlocked by the end of this call, whether
        or not the real hardware off() was actually invoked -- see module
        docstring, "module_state", for why this does not reflect the
        literal physical RF state.
        """
        if not self._require_iq_modulator:
            self.log.info(
                'AlwaysOnMicrowaveInterfuse: off() ignored -- '
                'require_iq_modulator is False. Use force_off() to '
                'actually disable output.'
            )
            if self.module_state() == 'locked':
                self.module_state.unlock()
            return

        mw = self._microwave()

        if not hasattr(mw, 'iq_modulator_active'):
            self.log.warning(
                'AlwaysOnMicrowaveInterfuse: off() -- hardware has no '
                'iq_modulator_active() method. Forwarding off() through.'
            )
            mw.off()
            if self.module_state() == 'locked':
                self.module_state.unlock()
            return

        if mw.iq_modulator_active():
            self.log.info(
                'AlwaysOnMicrowaveInterfuse: off() ignored -- IQ modulator '
                'confirmed active. Use force_off() to actually disable output.'
            )
            if self.module_state() == 'locked':
                self.module_state.unlock()
            return

        self.log.warning(
            'AlwaysOnMicrowaveInterfuse: off() -- IQ modulator not active. '
            'Forwarding off() through to real hardware.'
        )
        mw.off()
        if self.module_state() == 'locked':
            self.module_state.unlock()

    def configure_scan(self, power, frequencies, mode, sample_rate):
        """ Forwarded unchanged -- raises whatever the hardware raises. """
        self._microwave().configure_scan(power, frequencies, mode, sample_rate)

    def start_scan(self):
        """ Forwarded unchanged. """
        self._microwave().start_scan()

    def reset_scan(self):
        """ Forwarded unchanged. """
        self._microwave().reset_scan()

    # =========================================================================
    # Escape hatch -- not part of MicrowaveInterface
    # =========================================================================

    def force_off(self):
        """ Unconditionally disables RF output, bypassing the IQ-modulator
        check, and unlocks this interfuse's module_state to match.
        """
        self.log.info('AlwaysOnMicrowaveInterfuse: force_off() -- disabling RF output.')
        self._microwave().off()
        if self.module_state() == 'locked':
            self.module_state.unlock()