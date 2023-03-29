# -*- coding: utf-8 -*-

"""
This file contains the Qudi hardware file implementation for FastComtec p7887 .

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

#TODO: start stop works but pause does not work, i guess gui/logic problem
#TODO: Check if there are more modules which are missing, and more settings for FastComtec which need to be put, should we include voltage threshold?

import time
import ctypes
import numpy as np

from qudi.core.configoption import ConfigOption
from qudi.interface.fast_counter_interface import FastCounterInterface
from qudi.interface.data_instream_interface import DataInStreamInterface, DataInStreamConstraints, StreamingMode


"""
Remark to the usage of ctypes:
All Python types except integers (int), strings (str), and bytes (byte) objects
have to be wrapped in their corresponding ctypes type, so that they can be
converted to the required C data type.

ctypes type     C type                  Python type
----------------------------------------------------------------
c_bool          _Bool                   bool (1)
c_char          char                    1-character bytes object
c_wchar         wchar_t                 1-character string
c_byte          char                    int
c_ubyte         unsigned char           int
c_short         short                   int
c_ushort        unsigned short          int
c_int           int                     int
c_uint          unsigned int            int
c_long          long                    int
c_ulong         unsigned long           int
c_longlong      __int64 or
                long long               int
c_ulonglong     unsigned __int64 or
                unsigned long long      int
c_size_t        size_t                  int
c_ssize_t       ssize_t or
                Py_ssize_t              int
c_float         float                   float
c_double        double                  float
c_longdouble    long double             float
c_char_p        char *
                (NUL terminated)        bytes object or None
c_wchar_p       wchar_t *
                (NUL terminated)        string or None
c_void_p        void *                  int or None

"""
# Reconstruct the proper structure of the variables, which can be extracted
# from the header file 'struct.h'.

class AcqStatus(ctypes.Structure):
    """ Create a structured Data type with ctypes where the dll can write into.

    This object handles and retrieves the acquisition status data from the
    Fastcomtec.

    int started;                // acquisition status: 1 if running, 0 else
    double runtime;             // running time in seconds
    double totalsum;            // total events
    double roisum;              // events within ROI
    double roirate;             // acquired ROI-events per second
    double nettosum;            // ROI sum with background subtracted
    double sweeps;              // Number of sweeps
    double stevents;            // Start Events
    unsigned long maxval;       // Maximum value in spectrum
    """
    _fields_ = [('started', ctypes.c_int),
                ('runtime', ctypes.c_double),
                ('totalsum', ctypes.c_double),
                ('roisum', ctypes.c_double),
                ('roirate', ctypes.c_double),
                ('ofls', ctypes.c_double),
                ('sweeps', ctypes.c_double),
                ('stevents', ctypes.c_double),
                ('maxval', ctypes.c_ulong), ]


class AcqSettings(ctypes.Structure):
    _fields_ = [('range',       ctypes.c_long),
                ('cftfak',      ctypes.c_long),
                ('roimin',      ctypes.c_long),
                ('roimax',      ctypes.c_long),
                ('nregions',    ctypes.c_long),
                ('caluse',      ctypes.c_long),
                ('calpoints',   ctypes.c_long),
                ('param',       ctypes.c_long),
                ('offset',      ctypes.c_long),
                ('xdim',        ctypes.c_long),
                ('bitshift',    ctypes.c_ulong),
                ('active',      ctypes.c_long),
                ('eventpreset', ctypes.c_double),
                ('dummy1',      ctypes.c_double),
                ('dummy2',      ctypes.c_double),
                ('dummy3',      ctypes.c_double), ]

class ACQDATA(ctypes.Structure):
    """ Create a structured Data type with ctypes where the dll can write into.

    This object handles and retrieves the acquisition data of the Fastcomtec.
    """
    _fields_ = [('s0', ctypes.POINTER(ctypes.c_ulong)),
                ('region', ctypes.POINTER(ctypes.c_ulong)),
                ('comment', ctypes.c_char_p),
                ('cnt', ctypes.POINTER(ctypes.c_double)),
                ('hs0', ctypes.c_int),
                ('hrg', ctypes.c_int),
                ('hcm', ctypes.c_int),
                ('hct', ctypes.c_int), ]


class BOARDSETTING(ctypes.Structure):
    _fields_ = [('sweepmode',   ctypes.c_long),
                ('prena',       ctypes.c_long),
                ('cycles',      ctypes.c_long),
                ('sequences',   ctypes.c_long),
                ('syncout',     ctypes.c_long),
                ('digio',       ctypes.c_long),
                ('digval',      ctypes.c_long),
                ('dac0',        ctypes.c_long),
                ('dac1',        ctypes.c_long),
                ('dac2',        ctypes.c_long),
                ('dac3',        ctypes.c_long),
                ('dac4',        ctypes.c_long),
                ('dac5',        ctypes.c_long),
                ('fdac',        ctypes.c_int),
                ('tagbits',     ctypes.c_int),
                ('extclk',      ctypes.c_int),
                ('maxchan',     ctypes.c_long),
                ('serno',       ctypes.c_long),
                ('ddruse',      ctypes.c_long),
                ('active',      ctypes.c_long),
                ('holdafter',   ctypes.c_double),
                ('swpreset',    ctypes.c_double),
                ('fstchan',     ctypes.c_double),
                ('timepreset',  ctypes.c_double), ]


class FastComtec(FastCounterInterface, DataInStreamInterface):
    """ Hardware Class for the FastComtec Card.

    stable: Jochen Scheuer, Simon Schmitt

    Example config for copy-paste:

    fastcomtec_mcs6:
        module.Class: 'fastcomtec.fastcomtecmcs6.FastComtec'
        options:
            gated: False
            trigger_safety: 400e-9
            aom_delay: 390e-9
            minimal_binwidth: 0.2e-9

    """

    gated = ConfigOption('gated', False, missing='warn')
    trigger_safety = ConfigOption('trigger_safety', 400e-9, missing='warn')
    aom_delay = ConfigOption('aom_delay', 390e-9, missing='warn')
    minimal_binwidth = ConfigOption('minimal_binwidth', 0.2e-9, missing='warn')

    def __init__(self, config, **kwargs):
        super().__init__(config=config, **kwargs)

        self.log.debug('The following configuration was found.')

        # checking for the right configuration
        for key in config.keys():
            self.log.info('{0}: {1}'.format(key,config[key]))

        #this variable has to be added because there is no difference
        #in the fastcomtec it can be on "stopped" or "halt"
        self.stopped_or_halt = "stopped"
        self.timetrace_tmp = []


        ####### variables for the DataInStreamInterface ##########
        self._sample_rate = None
        self._buffer_size = None
        self._use_circular_buffer = None
        self._data_type = None
        self._streaming_mode = None
        self._stream_length = None

        self._constraints = None

    def on_activate(self):
        """ Initialisation performed during activation of the module.
        """

        self.dll = ctypes.windll.LoadLibrary('C:\Windows\System32\DMCS6.dll')
        if self.gated:
            self.change_sweep_mode(gated=True)
        else:
            self.change_sweep_mode(gated=False)
        return

        ########## activation of DataInStreamInterface #########
        self._streaming_mode = StreamingMode.CONTINUOUS
        
        # Create constraints
        self._constraints = DataInStreamConstraints(
            digital_channels=tuple(
                StreamChannel(name=src,
                              type=StreamChannelType.DIGITAL,
                              unit='counts') for src in self._digital_sources
            ),
            analog_channels=tuple(
                StreamChannel(name=src,
                              type=StreamChannelType.ANALOG,
                              unit='V') for src in self._analog_sources
            ),
            analog_sample_rate={'default'    : 1,
                                'bounds'     : (self._device_handle.ai_min_rate,
                                                self._device_handle.ai_max_multi_chan_rate),
                                'increment'  : 1,
                                'enforce_int': False},
            # FIXME: What is the minimum frequency for the digital counter timebase?
            digital_sample_rate={'default'    : 1,
                                 'bounds'     : (0.1, self._device_handle.ci_max_timebase),
                                 'increment'  : 0.1,
                                 'enforce_int': False},
            read_block_size={'default'    : 1,
                             'bounds'     : (1, int(self._max_channel_samples_buffer)),
                             'increment'  : 1,
                             'enforce_int': True},
            streaming_modes=(StreamingMode.CONTINUOUS,),  # TODO: Implement FINITE streaming mode
            data_type=np.float64,
            allow_circular_buffer=True
        )
        self._constraints.combined_sample_rate = self._constraints.analog_sample_rate.copy()

    def on_deactivate(self):
        """ Deinitialisation performed during deactivation of the module.
        """
        return

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
        """

        constraints = dict()

        # the unit of those entries are seconds per bin. In order to get the
        # current binwidth in seonds use the get_binwidth method.
        constraints['hardware_binwidth_list'] = list(self.minimal_binwidth * (2 ** np.array(
                                                     np.linspace(0,24,25))))
        constraints['max_sweep_len'] = 6.8
        constraints['max_bins'] = 6.8 /0.2e-9

        ####################### TODO #########################
        # Implement the constraints getter for the DataInStreamInterface
        # possibly by setting a flag that only returns the DataInStreamInterace if set to True


        """
        Return the constraints on the settings for this data streamer.

        @return DataInStreamConstraints: Instance of DataInStreamConstraints containing constraints
        """
        return constraints

    def configure(self, bin_width_s, record_length_s, number_of_gates=1):
        """ Configuration of the fast counter.

        @param float bin_width_s: Length of a single time bin in the time trace
                                  histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single
                                      gate in seconds.
        @param int number_of_gates: optional, number of gates in the pulse
                                    sequence. Ignore for not gated counter.

        @return tuple(binwidth_s, record_length_s, number_of_gates):
                    binwidth_s: float the actual set binwidth in seconds
                    gate_length_s: the actual record length in seconds
                    number_of_gates: the number of gated, which are accepted,
                    None if not-gated
        """

        # when not gated, record length = total sequence length, when gated, record length = laser length.
        # subtract 200 ns to make sure no sequence trigger is missed
        self.set_binwidth(bin_width_s)

        if self.gated:
            # sequential acquisition, new line on every "sync" trigger
            self.configure_gated_counter(bin_width_s, record_length_s,
                                         cycles=number_of_gates, preset=1)
        else:
            # one acquisition for all taus, one sync trigger per acquisition
            # subtract time to make sure no sequence trigger is missed
            no_of_bins = int((record_length_s - self.trigger_safety) / bin_width_s)
            self.change_sweep_mode(False, cycles=None, preset=None)
            self.set_length(no_of_bins)

        self.set_cycles(number_of_gates)

        return self.get_binwidth(), self.get_length() * self.get_binwidth(), number_of_gates

    def get_status(self):
        """
        Receives the current status of the Fast Counter and outputs it as return value.
        0 = unconfigured
        1 = idle
        2 = running
        3 = paused
        -1 = error state
        """
        status = AcqStatus()
        self.dll.GetStatusData(ctypes.byref(status), 0)
        # status.started = 3 measn that fct is about to stop
        while status.started == 3:
            time.sleep(0.1)
            self.dll.GetStatusData(ctypes.byref(status), 0)
        if status.started == 1:
            return 2
        elif status.started == 0:
            if self.stopped_or_halt == "stopped":
                return 1
            elif self.stopped_or_halt == "halt":
                return 3
            else:
                self.log.error('There is an unknown status from FastComtec. The status message was %s' % (str(status.started)))
                return -1
        else:
            self.log.error(
                'There is an unknown status from FastComtec. The status message was %s' % (str(status.started)))
            return -1


    def start_measure(self):
        """Start the measurement. """
        status = self.dll.Start(0)
        while self.get_status() != 2:
            time.sleep(0.05)
        return status

    def stop_measure(self):
        """Stop the measurement. """
        self.stopped_or_halt = "stopped"
        status = self.dll.Halt(0)
        while self.get_status() != 1:
            time.sleep(0.05)
        if self.gated:
            self.timetrace_tmp = []
        return status

    def pause_measure(self):
        """Make a pause in the measurement, which can be continued. """
        self.stopped_or_halt = "halt"
        status = self.dll.Halt(0)
        while self.get_status() != 3:
            time.sleep(0.05)

        if self.gated:
            self.timetrace_tmp = self.get_data_trace()
        return status

    def continue_measure(self):
        """Continue a paused measurement. """
        if self.gated:
            status = self.start_measure()
        else:
            status = self.dll.Continue(0)
            while self.get_status() != 2:
                time.sleep(0.05)
        return status


    def is_gated(self):
        """ Check the gated counting possibility.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        return self.gated


    def get_binwidth(self):
        """ Returns the width of a single timebin in the timetrace in seconds.

        @return float: current length of a single bin in seconds (seconds/bin)

        The red out bitshift will be converted to binwidth. The binwidth is
        defined as 2**bitshift*minimal_binwidth.
        """
        return self.minimal_binwidth*(2**int(self.get_bitshift()))

    def get_data_trace(self):
        """
        Polls the current timetrace data from the fast counter and returns it as a numpy array (dtype = int64).
        The binning specified by calling configure() must be taken care of in this hardware class.
        A possible overflow of the histogram bins must be caught here and taken care of.
        If the counter is UNgated it will return a 1D-numpy-array with returnarray[timebin_index]
        If the counter is gated it will return a 2D-numpy-array with returnarray[gate_index, timebin_index]

          @return arrray: Time trace.
        """
        setting = AcqSettings()
        self.dll.GetSettingData(ctypes.byref(setting), 0)
        N = setting.range

        if self.is_gated():
            bsetting=BOARDSETTING()
            self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
            H = bsetting.cycles
            if H==0:
                H=1
            data = np.empty((H, int(N / H)), dtype=np.uint32)

        else:
            data = np.empty((N,), dtype=np.uint32)

        p_type_ulong = ctypes.POINTER(ctypes.c_uint32)
        ptr = data.ctypes.data_as(p_type_ulong)
        self.dll.LVGetDat(ptr, 0)
        time_trace = np.int64(data)

        if self.gated and self.timetrace_tmp != []:
            time_trace = time_trace + self.timetrace_tmp

        info_dict = {'elapsed_sweeps': None,
                     'elapsed_time': None}  # TODO : implement that according to hardware capabilities
        return time_trace, info_dict


    # =========================================================================
    #                           Non Interface methods
    # =========================================================================

    def set_gated(self, gated):
        """ Change the gated status of the fast counter.

        @return bool: Boolean value indicates if the fast counter is a gated
                      counter (TRUE) or not (FALSE).
        """
        self.change_sweep_mode(gated)
        return self.gated

    def get_bitshift(self):
        """Get bitshift from Fastcomtec.

        @return int settings.bitshift: the red out bitshift
        """
        settings = AcqSettings()
        self.dll.GetSettingData(ctypes.byref(settings), 0)
        return int(settings.bitshift)

    def set_bitshift(self, bitshift):
        """ Sets the bitshift properly for this card.

        @param int bitshift:

        @return int: asks the actual bitshift and returns the red out value
        """
        cmd = 'BITSHIFT={0}'.format(hex(bitshift))
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        return self.get_bitshift()

    def set_binwidth(self, binwidth):
        """ Set defined binwidth in Card.

        @param float binwidth: the current binwidth in seconds

        @return float: Red out bitshift converted to binwidth

        The binwidth is converted into to an appropiate bitshift defined as
        2**bitshift*minimal_binwidth.
        """
        bitshift = int(np.log2(binwidth/self.minimal_binwidth))
        new_bitshift=self.set_bitshift(bitshift)

        return self.minimal_binwidth*(2**new_bitshift)


    # def set_length(self, length_bins, preset=None, cycles=None, sequences=None):
    #     """ Sets the length of the length of the actual measurement.
    #
    #     @param int length_bins: Length of the measurement in bins
    #
    #     @return float: Red out length of measurement
    #     """
    #     constraints = self.get_constraints()
    #     if length_bins * self.get_binwidth() < constraints['max_sweep_len']:
    #         # Smallest increment is 64 bins. Since it is better if the range is too short than too long, round down
    #         if self.gated:
    #             length_bins = int(64 * int(length_bins / 64))
    #             cmd = 'RANGE={0}'.format(int(length_bins))
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             cmd = 'roimax={0}'.format(int(length_bins))
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             if preset != None:
    #                 cmd = 'swpreset={0}'.format(preset)
    #                 self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             if cycles != None and cycles != 0:
    #                 cmd = 'cycles={0}'.format(cycles)
    #                 self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #                 # Fastcomtec crashes for big number of cycles without waiting time
    #                 if cycles > 1000:
    #                     time.sleep(10)
    #             if sequences != None and sequences != 0:
    #                 cmd = 'sequences={0}'.format(sequences)
    #                 self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             return self.get_length()
    #         else:
    #             if preset != None:
    #                 cmd = 'swpreset={0}'.format(preset)
    #             else:
    #                 cmd = 'swpreset={0}'.format(1)
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             if cycles != None and cycles != 0:
    #                 cmd = 'cycles={0}'.format(cycles)
    #             else:
    #                 cmd = 'cycles={0}'.format(1)
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             # Fastcomtec crashes for big number of cycles without waiting time
    #             if cycles > 1000:
    #                 time.sleep(10)
    #             if sequences != None and sequences != 0:
    #                 cmd = 'sequences={0}'.format(sequences)
    #             else:
    #                 cmd = 'sequences={0}'.format(1)
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             length_bins = int(64 * int(length_bins / 64))
    #             cmd = 'RANGE={0}'.format(int(length_bins))
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             cmd = 'roimax={0}'.format(int(length_bins))
    #             self.dll.RunCmd(0, bytes(cmd, 'ascii'))
    #             return self.get_length()
    #
    #     else:
    #         self.log.error(
    #             'Length of sequence is too high: %s' % (str(length_bins * self.get_binwidth())))
    #         return -1

    def set_length(self, length_bins):
        """ Sets the length of the length of the actual measurement.

        @param int length_bins: Length of the measurement in bins

        @return float: Red out length of measurement
        """
        # First check if no constraint is
        constraints = self.get_constraints()
        if self.is_gated():
            cycles = self.get_cycles()
        else:
            cycles = 1
        if length_bins *  cycles < constraints['max_bins']:
            # Smallest increment is 64 bins. Since it is better if the range is too short than too long, round down
            length_bins = int(64 * int(length_bins / 64))
            cmd = 'RANGE={0}'.format(int(length_bins))
            self.dll.RunCmd(0, bytes(cmd, 'ascii'))
            #cmd = 'roimax={0}'.format(int(length_bins))
            #self.dll.RunCmd(0, bytes(cmd, 'ascii'))

            # insert sleep time, otherwise fast counter crashed sometimes!
            time.sleep(0.5)
            return length_bins
        else:
            self.log.error('Dimensions {0} are too large for fast counter1!'.format(length_bins *  cycles))
            return -1


    def get_length(self):
        """ Get the length of the current measurement.

          @return int: length of the current measurement in bins
        """

        if self.is_gated():
            cycles = self.get_cycles()
            if cycles ==0:
                cycles = 1
        else:
            cycles = 1
        setting = AcqSettings()
        self.dll.GetSettingData(ctypes.byref(setting), 0)
        length = int(setting.range / cycles)
        return length


    def set_delay_start(self, delay_s):
        """ Sets the record delay length

        @param int delay_s: Record delay after receiving a start trigger

        @return int : specified delay in unit of bins
        """

        # A delay can only be adjusted in steps of 6.4ns
        delay_bins = np.rint(delay_s / 6.4e-9)
        cmd = 'fstchan={0}'.format(int(delay_bins))
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        return delay_bins

    def get_delay_start(self):
        """ Returns the current record delay length

        @return float delay_s: current record delay length in seconds
        """
        bsetting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
        delay_s = bsetting.fstchan * 6.4e-9
        return delay_s




################################# Methods for gated counting ##########################################

    def configure_gated_counter(self, bin_width_s, record_length_s, preset=None, cycles=None, sequences=None):
        """ Configuration of the gated counter.

        @param float bin_width_s: Length of a single time bin in the time trace
                                  histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single
                                      gate in seconds.
        @param int preset: optional, number of preset
        @param int cycles: optional, number of cycles
        @param int sequences: optional, number of sequences.

        @return tuple(binwidth_s, no_of_bins, cycles, preset, sequences):
                    binwidth_s: float the actual set binwidth in seconds
                    no_of_bins: Length in bins
                    cycles: Number of Cycles
                    preset: Number of preset
                    sequences: Number of sequences
        """

        self.set_binwidth(bin_width_s)
        # Change to gated sweep mode
        self.change_sweep_mode(True, cycles, preset)

        no_of_bins = int((record_length_s + self.aom_delay) / bin_width_s)
        self.set_length(no_of_bins)
        if sequences is not None:
            self.set_sequences(sequences)

        return self.get_binwidth(), no_of_bins, self.get_cycles(), self.get_preset(), self.get_sequences()


    def change_sweep_mode(self, gated, cycles=None, preset=None):
        """ Change the sweep mode (gated, ungated)

        @param bool gated: Gated or ungated
        @param int cycles: Optional, change number of cycles. If gated = number of laser pulses.
        @param int preset: Optional, change number of preset. If gated, typically = 1.
        """

        # Reduce length to prevent crashes
        #self.set_length(1440)
        if gated:
            self.set_cycle_mode(sequential_mode=True, cycles=cycles)
            self.set_preset_mode(mode=16, preset=preset)
            self.gated = True
        else:
            self.set_cycle_mode(sequential_mode=False, cycles=cycles)
            self.set_preset_mode(mode=0, preset=preset)
            self.gated = False
        return gated


    def set_preset_mode(self, mode=16, preset=None):
        """ Turns on or off a specific preset mode

        @param int mode: O for off, 4 for sweep preset, 16 for start preset
        @param int preset: Optional, change number of presets

        @return just the input
        """

        # Specify preset mode
        cmd = 'prena={0}'.format(hex(mode))
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))

        # Set the cycles if specified
        if preset is not None:
            self.set_preset(preset)

        return mode, preset


    def set_preset(self, preset):
        """ Sets the preset/

        @param int preset: Preset in sweeps of starts

        @return int mode: specified save mode
        """
        cmd = 'swpreset={0}'.format(preset)
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        return preset

    def get_preset(self):
        """ Gets the preset
       @return int mode: current preset
        """
        bsetting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
        preset = bsetting.swpreset
        return int(preset)


    def set_cycle_mode(self, sequential_mode=True, cycles=None):
        """ Turns on or off the sequential cycle mode that writes to new memory on every
        sync trigger. If disabled, photons are summed.

        @param bool sequential_mode: Set or unset cycle mode for sequential acquisition
        @param int cycles: Optional, Change number of cycles

        @return: just the input
        """
        # First set cycles to 1 to prevent crashes

        cycles_old = self.get_cycles() if cycles is None else cycles
        self.set_cycles(1)

        # Turn on or off sequential cycle mode
        if sequential_mode:
            cmd = 'sweepmode={0}'.format(hex(1978500))
        else:
            cmd = 'sweepmode={0}'.format(hex(1978496))
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))

        self.set_cycles(cycles_old)

        return sequential_mode, cycles

    def set_cycles(self, cycles):
        """ Sets the cycles

        @param int cycles: Total amount of cycles

        @return int mode: current cycles
        """
        # Check that no constraint is violated
        constraints = self.get_constraints()
        if cycles == 0:
            cycles = 1
        if self.get_length() * cycles  < constraints['max_bins']:
            cmd = 'cycles={0}'.format(cycles)
            self.dll.RunCmd(0, bytes(cmd, 'ascii'))
            time.sleep(0.5)
            return cycles
        else:
            self.log.error('Dimensions {0} are too large for fast counter!'.format(self.get_length() * cycles))
            return -1

    def get_cycles(self):
        """ Gets the cycles
        @return int mode: current cycles
        """
        bsetting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
        cycles = bsetting.cycles
        return cycles


    def set_sequences(self, sequences):
        """ Sets the cycles

        @param int cycles: Total amount of cycles

        @return int mode: current cycles
        """
        # Check that no constraint is violated
        cmd = 'sequences={0}'.format(sequences)
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        return sequences

    def get_sequences(self):
        """ Gets the cycles
        @return int mode: current cycles
        """
        bsetting = BOARDSETTING()
        self.dll.GetMCSSetting(ctypes.byref(bsetting), 0)
        sequences = bsetting.sequences
        return sequences



    def set_dimension(self, length, cycles):
        """ Get the dimension of the 2D Trace

          @param int cycles: Vertical dimension in bins
          @param int length: Horizontal dimension in bins
        """

        self.set_length(length)
        self.set_cycles(cycles)
        return length, cycles


    def get_dimension(self):
        """ Get the dimension of the 2D Trace

          @return int cycles: Vertical dimension in bins
          @return int length: Horizontal dimension in bins
        """
        cycles = self.get_cycles()
        length = self.get_length()
        return length, cycles


################################### Methods for SSR interface ####################################


    def configure_ssr_counter(self, counts_per_readout=None, countlength=None):
        # FIXME: Change description
        """ Configuration of the gated counter.
        @param float bin_width_s: Length of a single time bin in the time trace
                                  histogram in seconds.
        @param float record_length_s: Total length of the timetrace/each single
                                      gate in seconds.
        @param int preset: optional, number of preset
        @param int cycles: optional, number of cycles
        @param int sequences: optional, number of sequences.

        @return tuple(binwidth_s, no_of_bins, cycles, preset, sequences):
                    binwidth_s: float the actual set binwidth in seconds
                    no_of_bins: Length in bins
                    cycles: Number of Cycles
                    preset: Number of preset
                    sequences: Number of sequences
        """
        self.change_sweep_mode(gated=True, cycles=countlength, preset=counts_per_readout)
        self.set_sequences(1)
        time.sleep(0.1)
        return



#################################### Methods for saving ###############################################


    def change_filename(self, name):
        """ Changes filename

        @param str name: Location and name of the file
        """
        cmd = 'mpaname=%s' % name
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        return name

    def change_save_mode(self, mode):
        """ Changes the save mode of Mcs6

        @param int mode: Specifies the save mode (0: No Save at Halt, 1: Save at Halt,
                        2: Write list file, No Save at Halt, 3: Write list file, Save at Halt

        @return int mode: specified save mode
        """
        cmd = 'savedata={0}'.format(mode)
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        return mode

    def save_data(self, filename):
        """ save the current settings and data

        @param str filename: Location and name of the savefile
        """
        self.change_filename(filename)
        cmd = 'savempa'
        self.dll.RunCmd(0, bytes(cmd, 'ascii'))
        return filename




######################## Methods to fulfill gated counter interface ###################
    ######## (NOT TESTED SINCE GATED COUNTER IS NOT WORKING PROBABLY YET) ##########

    def get_2D_trace(self):
        if self.is_gated():
            return self.get_data_trace()
        else:
            self.log.error('Counter is not gated!!!')
            return -1

    def get_count_length(self):
        return self.get_length()


    def set_count_length(self, length):
        self.set_length(length)
        return length

    def get_counting_samples(self):
        return self.get_cycles()

    def set_counting_samples(self, samples):
        self.set_cycles(samples)
        return samples

    def save_raw_data(self,nametag):
        self.save_data(nametag)
        return nametag

    ############################ Methods for the DataInstreamerInterface ########################
    #############################################################################################

    @property
    def sample_rate(self) -> float:
        """
        The currently set sample rate

        @return float: current sample rate in Hz
        """
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, samplerate: float):
        """
        Set the sample rate

        @param float, samplerate: samplerate that should be set
        """
        # TODO implement a useful check whether sample rate is in a sensible range
        self._sample_rate = samplerate

    @property
    def data_type(self) -> type:
        """
        Read-only property.
        The data type of the stream data. Must be numpy type.

        @return type: stream data type (numpy type)
        """
        return self._data_type

    @property
    def buffer_size(self) -> int:
        """
        The currently set buffer size.
        Buffer size corresponds to the number of samples that can be buffered. 
        It determines the number of lines than can be read by from the measurement file to the internal memory of the PC.

        @return int: current buffer size in samples per channel
        """
        return self._buffer_size

    @buffer_size.setter
    def buffer_size(self, buffersize: int):
        """
        Sets number of lines that can be read from the measurement file into the memory of the PC.

        @param int, buffersize: number of lines that should be read into the memory
        """
        # TODO implement reasonable check for buffersize's constraints
        self._buffer_size = buffersize

    @property
    def use_circular_buffer(self) -> bool:
        """
        A flag indicating if circular sample buffering is being used or not.

        @return bool: indicate if circular sample buffering is used (True) or not (False)
        """
        return self._use_circular_buffer

    @use_circular_buffer.setter
    def use_circular_buffer(self, flag: bool):
        """
        Set the flag indicating if circular sample buffering is being used or not.
        @param bool, flag: indicate if circular sample buffering is used (True) or not (False) 
        """
        self._use_circular_buffer = flag

    @property
    def streaming_mode(self) -> StreamingMode:
        """
        The currently configured streaming mode Enum.

        @return StreamingMode: Finite (StreamingMode.FINITE) or continuous
                               (StreamingMode.CONTINUOUS) data acquisition
        """
        return self._streaming_mode

    @streaming_mode.setter
    def streaming_mode(self, mode: int):
        """
        Method to set the streaming mode

        @param int mode: value that is in the StreamingMode class (StreamingMode.CONTINUOUS: 0, StreamingMode.FINITE: 1)
        """
        # TODO do we need a settings settable checker?
        # if self._check_settings_change():
        mode = StreamingMode(mode)
        if mode not in self._constraints.streaming_modes:
            self.log.error('Unknown streaming mode "{0}" encountered.\nValid modes are: {1}.'
                           ''.format(mode, self._constraints.streaming_modes))
            return
        self._streaming_mode = mode
        return

    @property
    def stream_length(self) -> int:
        """
        Property holding the total number of samples per channel to be acquired by this stream.
        This number is only relevant if the streaming mode is set to StreamingMode.FINITE.

        @return int: The number of samples to acquire per channel. Ignored for continuous streaming.
        """
        return self._stream_length
    
    @stream_length.setter
    def stream_length(self, length: int):
        # TODO do we need the check whether settings are currently changeable, e.g. are settings changeable during a running measurement?
        # if not then a checker has to be implemented, which checks the status of the device.
        # thus only proceeding when the status is idle or something like that.
        # get_status() method earlier in this file could be used for that
        # if self._check_settings_change():
        length = int(length)
        if length < 1:
            self.log.error('Stream_length must be a positive integer >= 1.')
            return
        self._stream_length = length
        return

    @property
    def all_settings(self) -> dict:
        """
        Read-only property to return a dict containing all current settings and values that can be
        configured using the method "configure". Basically returns the same as "configure".

        @return dict: Dictionary containing all configurable settings
        """
        return {'sample_rate': self.__sample_rate,
                'streaming_mode': self.__streaming_mode,
                'active_channels': self.active_channels,
                'stream_length': self.__stream_length,
                'buffer_size': self.__buffer_size,
                'use_circular_buffer': self.__use_circular_buffer}

    @property
    def number_of_channels(self) -> int:
        """
        Read-only property to return the currently configured number of active data channels.

        @return int: the currently set number of channels
        """
        return len(self.__active_channels)

    @property
    def active_channels(self) -> tuple:
        """
        The currently configured data channel properties.
        Returns a dict with channel names as keys and corresponding StreamChannel instances as
        values.

        @return dict: currently active data channel properties with keys being the channel names
                      and values being the corresponding StreamChannel instances.
        """
        constr = self._constraints
        return(*(ch.copy() for ch in constr.digital_channels if ch.name in self.__active_channels),
               *(ch.copy() for ch in constr.analog_channels if ch.name in self.__active_channels))

    @active_channels.setter
    def active_channels(self, channels: str):
        # TODO checker for channel settings
        # if self._check_settings_change():
        avail_channels = tuple(ch.name for ch in self.available_channels)
        if any(ch not in avail_channels for ch in channels):
            self.log.error('Invalid channel to stream from encountered ({0}).\nValid channels '
                           'are: {1}'
                           ''.format(tuple(channels), tuple(self.available_channels)))
            return
        self.__active_channels = tuple(channels)
        return

    @property
    def available_channels(self):
        """
        Read-only property to return the currently used data channel properties.
        Returns a dict with channel names as keys and corresponding StreamChannel instances as
        values.

        @return dict: data channel properties for all available channels with keys being the channel
                      names and values being the corresponding StreamChannel instances.
        """
        pass

    @property
    def available_samples(self):
        """
        Read-only property to return the currently available number of samples per channel ready
        to read from buffer.

        @return int: Number of available samples per channel
        """
        pass

    @property
    def buffer_overflown(self):
        """
        Read-only flag to check if the read buffer has overflown.
        In case of a circular buffer it indicates data loss.
        In case of a non-circular buffer the data acquisition should have stopped if this flag is
        coming up.
        Flag will only be reset after starting a new data acquisition.

        @return bool: Flag indicates if buffer has overflown (True) or not (False)
        """
        pass

    @property
    def is_running(self):
        """
        Read-only flag indicating if the data acquisition is running.

        @return bool: Data acquisition is running (True) or not (False)
        """
        pass

    def configure(self, sample_rate=None, streaming_mode=None, active_channels=None,
                  total_number_of_samples=None, buffer_size=None, use_circular_buffer=None):
        """
        Method to configure all possible settings of the data input stream.

        @param float sample_rate: The sample rate in Hz at which data points are acquired
        @param StreamingMode streaming_mode: The streaming mode to use (finite or continuous)
        @param iterable active_channels: Iterable of channel names (str) to be read from.
        @param int total_number_of_samples: In case of a finite data stream, the total number of
                                            samples to read per channel
        @param int buffer_size: The size of the data buffer to pre-allocate in samples per channel
        @param bool use_circular_buffer: Use circular buffering (True) or stop upon buffer overflow
                                         (False)

        @return dict: All current settings in a dict. Keywords are the same as kwarg names.
        """
        pass

    def start_stream(self):
        """
        Start the data acquisition and data stream.

        @return int: error code (0: OK, -1: Error)
        """
        pass

    def stop_stream(self):
        """
        Stop the data acquisition and data stream.

        @return int: error code (0: OK, -1: Error)
        """
        pass

    def read_data_into_buffer(self, buffer, number_of_samples=None):
        """
        Read data from the stream buffer into a 1D/2D numpy array given as parameter.
        In case of a single data channel the numpy array can be either 1D or 2D. In case of more
        channels the array must be 2D with the first index corresponding to the channel number and
        the second index serving as sample index:
            buffer.shape == (self.number_of_channels, number_of_samples)
        The numpy array must have the same data type as self.data_type.
        If number_of_samples is omitted it will be derived from buffer.shape[1]

        This method will not return until all requested samples have been read or a timeout occurs.

        @param numpy.ndarray buffer: The numpy array to write the samples to
        @param int number_of_samples: optional, number of samples to read per channel. If omitted,
                                      this number will be derived from buffer axis 1 size.

        @return int: Number of samples read into buffer; negative value indicates error
                     (e.g. read timeout)
        """
        pass

    def read_available_data_into_buffer(self, buffer):
        """
        Read data from the stream buffer into a 1D/2D numpy array given as parameter.
        In case of a single data channel the numpy array can be either 1D or 2D. In case of more
        channels the array must be 2D with the first index corresponding to the channel number and
        the second index serving as sample index:
            buffer.shape == (self.number_of_channels, number_of_samples)
        The numpy array must have the same data type as self.data_type.

        This method will read all currently available samples into buffer. If number of available
        samples exceed buffer size, read only as many samples as fit into the buffer.

        @param numpy.ndarray buffer: The numpy array to write the samples to

        @return int: Number of samples read into buffer; negative value indicates error
                     (e.g. read timeout)
        """
        pass

    def read_data(self, number_of_samples=None):
        """
        Read data from the stream buffer into a 2D numpy array and return it.
        The arrays first index corresponds to the channel number and the second index serves as
        sample index:
            return_array.shape == (self.number_of_channels, number_of_samples)
        The numpy arrays data type is the one defined in self.data_type.
        If number_of_samples is omitted all currently available samples are read from buffer.

        This method will not return until all requested samples have been read or a timeout occurs.

        If no samples are available, this method will immediately return an empty array.
        You can check for a failed data read if number_of_samples != <return_array>.shape[1].

        @param int number_of_samples: optional, number of samples to read per channel. If omitted,
                                      all available samples are read from buffer.

        @return numpy.ndarray: The read samples in a numpy array
        """
        pass

    def read_single_point(self):
        """
        This method will initiate a single sample read on each configured data channel.
        In general this sample may not be acquired simultaneous for all channels and timing in
        general can not be assured. Us this method if you want to have a non-timing-critical
        snapshot of your current data channel input.
        May not be available for all devices.
        The returned 1D numpy array will contain one sample for each channel.

        @return numpy.ndarray: 1D array containing one sample for each channel. Empty array
                               indicates error.
        """
        pass

# =========================================================================
#   The following methods have to be carefully reviewed and integrated as
#   internal methods/function, because they might be important one day.
# =========================================================================



def SetLevel(self, start, stop):
    setting = AcqSettings()
    self.dll.GetSettingData(ctypes.byref(setting), 0)

    def FloatToWord(r):
        return int((r + 2.048) / 4.096 * int('ffff', 16))

    setting.dac0 = (setting.dac0 & int('ffff0000', 16)) | FloatToWord(start)
    setting.dac1 = (setting.dac1 & int('ffff0000', 16)) | FloatToWord(stop)
    self.dll.StoreSettingData(ctypes.byref(setting), 0)
    self.dll.NewSetting(0)
    return self.GetLevel()


def GetLevel(self):
    setting = AcqSettings()
    self.dll.GetSettingData(ctypes.byref(setting), 0)

    def WordToFloat(word):
        return (word & int('ffff', 16)) * 4.096 / int('ffff', 16) - 2.048

    return WordToFloat(setting.dac0), WordToFloat(setting.dac1)
