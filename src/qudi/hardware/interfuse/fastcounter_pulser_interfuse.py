from qudi.core.connector import Connector
from qudi.core.configoption import ConfigOption
from qudi.interface.fast_counter_interface import FastCounterInterface
from qudi.util.mutex import RecursiveMutex
from qtpy import QtCore
import time


class FastCounterRestartInterfuse(FastCounterInterface):
    """
    This interfuse restarts the fastcounter and the pulse generator

    example configuration:
        fastcounter_pulser_interfuse:
            module.Class: 'interfuse.fastcounter_pulser_interfuse.FastCounterRestartInterfuse'
            connect:
                fastcounter: 'adlink9834'
                pulsegenerator: 'awg70k'
            options:
                wait_time: 0.05 #s
    """
    pulsegenerator = Connector(interface='PulserInterface')
    fastcounter = Connector(interface='FastCounterInterface')

    wait_time = ConfigOption(default=100) # in s

    restart_fastcounter_signal = QtCore.Signal()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.stop_flag = True
        self._thread_lock = RecursiveMutex()
        self.stop_flag = False

    def on_activate(self):
        self.stop_flag = True
        self.restart_fastcounter_signal.connect(self.restart_fastcounter, QtCore.Qt.QueuedConnection)

    def on_deactivate(self):
        self.stop_flag = True

    def restart_fastcounter(self):
        with self._thread_lock:
            if self.stop_flag:
                self.log.info("Fastcounter restarter stopped")
                return

            if self.fastcounter().get_status() == 3:
                self.log.info("Restarting fastcounter")
                err = self.pulsegenerator().pulser_off()
                if err != 0:
                    self.log.error("Couldn't restart AWG")
            else:
                self.fastcounter().continue_measure()
                self.pulsegenerator().pulser_on()

        time.sleep(self.wait_time)
        self.restart_fastcounter_signal.emit()

    def get_constraints(self):
        """ Retrieve the hardware constrains from the Fast counting device.

        @return dict: dict with keys being the constraint names as string and
                      items are the definition for the constaints.

         The keys of the returned dictionary are the str name for the constraints
        (which are set in this method).

                    NO OTHER KEYS SHOULD BE INVENTED!

        If you are not sure about the meaning, look in other hardware files to
        get an impression. If still additional constraints are needed, then they
        have to be added to all files containing this interface.

        The items of the keys are again dictionaries which have the generic
        dictionary form:
            {'min': <value>,
             'max': <value>,
             'step': <value>,
             'unit': '<value>'}

        Only the key 'hardware_binwidth_list' differs, since they
        contain the list of possible binwidths.

        If the constraints cannot be set in the fast counting hardware then
        write just zero to each key of the generic dicts.
        Note that there is a difference between float input (0.0) and
        integer input (0), because some logic modules might rely on that
        distinction.

        ALL THE PRESENT KEYS OF THE CONSTRAINTS DICT MUST BE ASSIGNED!

        # Example for configuration with default values:

        constraints = dict()

        # the unit of those entries are seconds per bin. In order to get the
        # current binwidth in seonds use the get_binwidth method.
        constraints['hardware_binwidth_list'] = []

        """
        return self.fastcounter().get_constraints()

    def configure(self, bin_width_s, record_length_s, number_of_gates=0):
        """ Configuration of the fast counter.

        @param float bin_width_s: Length of a single time bin in the time
                                  trace histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each
                                      single gate in seconds.
        @param int number_of_gates: optional, number of gates in the pulse
                                    sequence. Ignore for not gated counter.

        @return tuple(binwidth_s, record_length_s, number_of_gates):
                    binwidth_s: float the actual set binwidth in seconds
                    gate_length_s: the actual record length in seconds
                    number_of_gates: the number of gated, which are accepted, None if not-gated
        """
        self.log.warn("configure")
        return self.fastcounter().configure(bin_width_s, record_length_s, number_of_gates)

    def get_status(self):
        """ Receives the current status of the Fast Counter and outputs it as
            return value.

        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
        -1 = error state
        """
        self.log.warn("status")
        return self.fastcounter().get_status()

    def start_measure(self):
        """ Start the fast counter. """
        with self._thread_lock:
            self.stop_flag = False
            self.restart_fastcounter_signal.emit()
            self.fastcounter().start_measure()

    def stop_measure(self):
        """ Stop the fast counter. """
        with self._thread_lock:
            self.stop_flag = True
            self.fastcounter().stop_measure()

    def pause_measure(self):
        """ Pauses the current measurement.

        Fast counter must be initially in the run state to make it pause.
        """
        with self._thread_lock:
            self.stop_flag = True
            self.fastcounter().pause_measure()

    def continue_measure(self):
        """ Continues the current measurement.

        If fast counter is in pause state, then fast counter will be continued.
        """
        with self._thread_lock:
            self.stop_flag = False
            self.restart_fastcounter_signal.emit()
            self.fastcounter().continue_measure()

    def is_gated(self):
        """ Check the gated counting possibility.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        return self.fastcounter().is_gated()

    def get_binwidth(self):
        """ Returns the width of a single timebin in the timetrace in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)
        """
        return self.fastcounter().get_binwidth()

    def get_data_trace(self):
        """ Polls the current timetrace data from the fast counter.

        Return value is a numpy array (dtype = int64).
        The binning, specified by calling configure() in forehand, must be
        taken care of in this hardware class. A possible overflow of the
        histogram bins must be caught here and taken care of.
        If the counter is NOT GATED it will return a tuple (1D-numpy-array, info_dict) with
            returnarray[timebin_index]
        If the counter is GATED it will return a tuple (2D-numpy-array, info_dict) with
            returnarray[gate_index, timebin_index]

        info_dict is a dictionary with keys :
            - 'elapsed_sweeps' : the elapsed number of sweeps
            - 'elapsed_time' : the elapsed time in seconds

        If the hardware does not support these features, the values should be None
        """
        self.fastcounter().get_data_trace()
