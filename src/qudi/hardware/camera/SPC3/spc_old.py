# Author(s):  Elia Mulas
# Revision:   1.0
# Date:       23/06/2023
#
# Copyright 2023 Micro-Photon-Devices S.r.l.
#
# SOFTWARE PRODUCT: Hermes_Python 1.0
#
# Micro-Photon-Devices (MPD) expressly disclaims any warranty for the SOFTWARE PRODUCT.
# The SOFTWARE PRODUCT is provided 'As Is' without any express or implied warranty of any kind,
# including but not limited to any warranties of merchantability, non-infringement, or
# fitness of a particular purpose. MPD does not warrant or assume responsibility for the
# accuracy or completeness of any information, text, graphics, links or other items contained
# within the SOFTWARE PRODUCT. MPD further expressly disclaims any warranty or representation
# to Authorized Users or to any third party.
# In no event shall MPD be liable for any damages (including, without limitation, lost profits,
# business interruption, or lost information) rising out of 'Authorized Users' use of or inability
# to use the SOFTWARE PRODUCT, even if MPD has been advised of the possibility of such damages.
# In no event will MPD be liable for loss of data or for indirect, special, incidental,
# consequential (including lost profit), or other damages based in contract, tort
# or otherwise. MPD shall have no liability with respect to the content of the
# SOFTWARE PRODUCT or any part thereof, including but not limited to errors or omissions contained
# therein, libel, infringements of rights of publicity, privacy, trademark rights, business
# interruption, personal injury, loss of privacy, moral rights or the disclosure of confidential
# information.


import os
import sys
import platform
import numpy as np
from ctypes import *
import matplotlib.pyplot as plt

assert sys.version_info.major >= 3

# handle type
SPC3_H = c_void_p
# return type
SPC3Return = c_int


class SPC3Error(Exception):
    _err_dict = {
        -1: "USB_DEVICE_NOT_RECOGNIZED",
        -3: "CAMERA_NOT_POWERING_UP",
        -5: "COMMUNICATION_ERROR",
        -6: "OUT_OF_BOUND",
        -7: "MISSING_DLL",
        -8: "EMPTY_BUFFER",
        -9: "NOT_EN_MEMORY",
        -10: "NULL_POINTER",
        -11: "INVALID_OP",
        -12: "UNABLE_CREATE_FILE",
        -13: "UNABLE_READ_FILE",
        -14: "FIRMWARE_NOT_COMPATIBLE",
        -15: "POWER_SUPPLY_ERROR",
        -16: "TOO_MUCH_LIGHT",
        -17: "INVALID_NIMG_CORRELATION",
        -18: "SPC3_MEMORY_FULL",
    }

    def __init__(self, ec):
        if ec in self._err_dict.keys():
            message = self._err_dict[ec]
        else:
            message = "UNEXPECTED ERROR (error code is {})".format(str(ec))
        super().__init__(message)


class SPC3(object):
    lib_alias = "SPC3_SDK"
    lib_root_dir = "lib/"
    c_success_code = "OK"
    py_class_version = 1.0

    # enums
    class OutFileFormat:
        SPC3_FILEFORMAT = 0
        TIFF_NO_COMPRESSION = 2

    class GateMode:
        CONTINUOUS = 0
        PULSED = 1
        COARSE = 2

    class CameraMode:
        NORMAL = 0
        ADVANCED = 1

    class TriggerMode:
        NONE = 0
        GATE_CLK = 1
        FRAME = 2

    class State:
        DISABLED = 0
        ENABLED = 1

    class CorrelationMode:
        LINEAR = 0
        MULTITAU = 1

    def __init__(self, mode, Device_ID="", dll_location=""):

        # if platform.system() == "Linux" and platform.uname().machine == "x86_64":
        #     lib_path = os.path.join(self.lib_root_dir, "Linux64/", "libHermes.so")
        #     self.dll = cdll.LoadLibrary(lib_path)

        if platform.system() == "Windows" and platform.uname().machine == "AMD64":

            # lib_dir = os.path.join(self.lib_root_dir, "Win/")
            # lib_path = os.path.join(lib_dir, "{}.dll".format(self.lib_alias))
            # lib_path = 'C:\Users\SPUD1\Documents\qudi_workspace_202401\qudi-iqo-modules\src\qudi\hardware\camera\SPC3\lib\Win'
            lib_path = dll_location
            lib_path = os.path.join(lib_path, "{}.dll".format(self.lib_alias))
            # if sys.version_info.minor < 8:
            #     os.environ["PATH"] = lib_dir + os.pathsep + os.environ["PATH"]

            self.dll = WinDLL(lib_path)

        else:
            raise NotImplementedError("Unsupported platform")

        self.c_handle = SPC3_H()

        # SDK constructor
        self.Constr(mode, Device_ID)

        # Hermes-specific
        self._snap_num_frames = None  # number of frames to be acquired in SNAP mode
        self._data_bits = None  # pixel data depth: 8 or 16 bit
        self._num_counters = 1  # number of enabled counters
        self.row_size = 32  # number of rows in the output data
        self._data_is_signed = False  # true if output data is signed
        self._num_pixels = 0

        self._num_rows = 64

    @property
    def num_pixels(self):
        return self._num_pixels

    @property
    def num_counters(self):
        return self._num_counters

    def __del__(self):
        # Guard against double-free: if Destr() was already called explicitly
        # (e.g. via on_deactivate or a test helper), the caller should have nulled
        # c_handle afterwards (cam.c_handle = c_void_p()).  Without this check,
        # Python GC would call Destr() a second time on the freed handle, causing
        # a NULL_POINTER error or an access violation in the DLL.
        if self.c_handle.value is not None:
            self.Destr()

    def _checkError(self, ec):
        if ec != 0:
            raise SPC3Error(ec)

    def Constr(self, mode, Device_ID):
        """Constr - Constructor.
        It allocates a memory block to contain all the information and buffers required by the Hermes. If multiple devices are connected to the computer,
        a unique camera ID should be provided to correctly identify the camera. The camera ID can be found in the camera documentation (9 numbers and a letter)
        and a list of connected device is printed on the screen upon calling this function. An empty string is accepted too. In this case, the first device on the list will be connected.

        Parameters:
            Hermes_in: Pointer to Hermes handle
            mode: Camera Working mode
            Device_ID: Unique ID to identify the connected device
        Error codes:
            INVALID_OP The Hermes_H points to an occupied memory location
            FIRMWARE_NOT_COMPATIBLE The SDK and Firmware versions are not compatible
            NOT_EN_MEMORY There is not enough memory to run the camera
        """
        f = self.dll.SPC3_Constr
        f.argtypes = [POINTER(SPC3_H), c_int, c_char_p]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesConstr(Hermes_H* Hermes_in, CameraMode m, char* Device_ID);
        ec = f(byref(self.c_handle), mode, Device_ID.encode("utf-8"))
        self._checkError(ec)
        return

    def Destr(self):
        """Destr - Destructor.
        It deallocates the memory block which contains all the information and buffers required by the Hermes. \b WARNING the user must call the destructor before the end of the program
        to avoid memory leaks.

        Parameters:
            None.
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
        """
        f = self.dll.SPC3_Destr
        f.argtypes = [SPC3_H]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesDestr(Hermes_H Hermes);
        ec = f(self.c_handle)
        self._checkError(ec)
        return

    def SetCameraPar(
        self,
        Exposure,
        NFrames,
        NIntegFrames,
        NCounters,
        Force8bit,
        Half_array,
        Signed_data,
    ):
        """SetCameraPar - Set the acquisition parameters for the camera.
        This function behaves differently depending on the operating mode setting. In case of Normal working mode, the exposure time is fixed to 10.40 microseconds.
        Therefore, the parameter Exposure is not considered. Longer exposures can be obtained by summing multiple frames (i.e. by setting NIntegFrames).
        This operating mode does not degrade the signal to noise ratio. In fact, the camera does not have any read-out noise.
        In case of Advanced mode, all the parameters are controlled by the user which can set very long exposure times.
        The time unit of the Exposure parameter is clock cycles i.e. the exposure time is an integer number of internal clock cycles of 10 ns period.
        For example, the value of 10 means 100 ns exposure.

        Parameters:
            Exposure: Exposure time for a single frame (Hardware Integration Time - HIT). The time unit is 10 ns. Meaningful only for Advanced mode. Accepted values: 1 ... 65534
            NFrames: Number of frames per acquisition. Meaningful only for Snap acquisition. Accepted values: 1 ... 65534
            NIntegFrames: Number of integrated frames. Each output frame is the result of the sum of NIntegFrames.  Accepted values: 1 ... 65534
            NCounters: Number of counters per pixels to be used. Accepted values: 1 ... 3
            Force8bit: Force 8-bit per pixel acquisition. Counts are truncated. Meaningful only for Advanced mode.
            Half_array: Acquire only a 32x32 array.
            Signed_data: If enabled, data from counters 2 and 3 are signed data with 8-bit integer part and 1 bit sign.
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            OUT_OF_BOUND Exposure, NFrames and NIntegFrames must be all greater than zero and smaller than 65535
        """
        f = self.dll.SPC3_Set_Camera_Par
        f.argtypes = [
            SPC3_H,
            c_uint16,
            c_uint32,
            c_uint16,
            c_uint16,
            c_int,
            c_int,
            c_int,
        ]
        f.restype = SPC3Return

        Exposure = c_uint16(int(Exposure))
        NFrames = c_uint32(int(NFrames))
        NIntegFrames = c_uint16(int(NIntegFrames))
        NCounters = c_uint16(int(NCounters))
        Force8bit = c_int(int(Force8bit))
        Half_array = c_int(int(Half_array))
        Signed_data = c_int(int(Signed_data))
        #  DllSDKExport HermesReturn HermesSetCameraPar(Hermes_H Hermes, uint16_t Exposure, uint32_t NFrames, uint16_t NIntegFrames, uint16_t NCounters, State Force8bit, State Half_array, State Signed_data);
        ec = f(
            self.c_handle,
            Exposure,
            NFrames,
            NIntegFrames,
            NCounters,
            Force8bit,
            Half_array,
            Signed_data,
        )
        self._checkError(ec)

        # keep record of settings
        self._snap_num_frames = NFrames.value
        self._num_counters = NCounters.value
        self._data_is_signed = bool(Signed_data)

        if Half_array:
            self._num_pixels = 1024
        else:
            self._num_pixels = 2048
        return

    def SetCameraParSubArray(self, Exposure, NFrames, NIntegFrames, Force8bit, Npixels):
        """SetCameraParSubArray - Set the acquisition parameters for the camera when a subarray is used.
        This function has to be used in alternative to the function HermesSetCameraPar() when the acquisition of a subarray is needed. Subarray acquisition has few limitations:
        - the pixels will be readout starting from the upper-left one and moving by rows of 32 pixels toward the center of the array
        - in live mode it is possible to acquire only 1, 2, 4, 8 full rows. If Live mode is started when the camera is set for a different number of pixels, an INVALID_OP error will be returned.
        - in snap and continuous mode any number of pixels ranging from 1 to 256 in the upper semi-array can be acquired (in any case starting from the corner and then reading by rows,
        i.e. if 67 pixel are required 2 rows of 32 pixels + 3 pixels from the third row will be acquired), PROVIDED that the total data acquired is an integer multiple of 1024 bytes.
        For the example above and assuming the camera is set to 8-bit/pixel, it means that 1024 frames (or multiples) must be acquired.
        Each frame will be Npixel*10ns + 160ns long. Acquisitions with a smaller number of frames are not possible. If an acquisition is triggered with invalid values of the parameter, an INVALID_OP error will be returned.
        - only counter number 1 is available.

        This function behaves differently depending on the operating mode setting. In case of Normal working mode, the exposure time is fixed to Npixel*10ns + 160ns.
        Therefore, the parameter Exposure is not considered. Longer exposures can be obtained by summing multiple frames (i.e. by setting NIntegFrames).
        This operating mode does not degrade the signal to noise ratio. In fact, the camera does not have any read-out noise.
        In case of Advanced mode, all the parameters are controlled by the user which can set very long exposure times.
        The time unit of the Exposure parameter is clock cycles i.e. the exposure time is an integer number of internal clock cycles of 10 ns period.
        For example, the value of 10 means 100 ns exposure.

        Parameters:
            Exposure: Exposure time for a single frame (Hardware Integration Time - HIT). The time unit is 10 ns. Meaningful only for Advanced mode. Accepted values: 1 ... 65534
            NFrames: Number of frames per acquisition. Meaningful only for Snap acquisition. Accepted values: 1 ... 65534
            NIntegFrames: Number of integrated frames. Each output frame is the result of the sum of NIntegFrames.  Accepted values: 1 ... 65534
            Force8bit: Force 8-bit per pixel acquisition. Counts are truncated. Meaningful only for Advanced mode.
            Npixels: Number of pixels to be acquired. Accepted values 1 ... 256 (see limitations above for Live mode)
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            OUT_OF_BOUND Exposure, NFrames, NIntegFrames or NPixels out of bound.
        """
        f = self.dll.SPC3_Set_Camera_Par_SubArray
        f.argtypes = [SPC3_H, c_uint16, c_uint32, c_uint16, c_int, c_uint16]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetCameraParSubArray(Hermes_H Hermes, uint16_t Exposure, uint32_t NFrames, uint16_t NIntegFrames, State Force8bit, uint16_t Npixels);
        ec = f(self.c_handle, Exposure, NFrames, NIntegFrames, Force8bit, Npixels)
        self._checkError(ec)

        # keep record of settings
        self._snap_num_frames = NFrames
        self._num_counters = 1
        # self._data_is_signed = bool(Signed_data)

        self._num_pixels = int(Npixels)

        return

    def SetDeadTime(self, Val):
        """SetDeadTime - Update the dead-time setting.
        Every time a photon is detected in a pixel, that pixel remains blind for a fix amount of time which is called dead-time. This setting is user-defined and it ranges
        from MIN_DEAD_TIME and MAX_DEAD_TIME. Only a sub-set of this range is practically selectable: a dead-time calibration is performed during the production of the device.
        This function will set the dead-time to the closest calibrated value to Val.
        The default dead-time value is 50 ns.

        Parameters:
            Val:  New dead-time value in nanoseconds
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP Unable to change the dead-time when the live-mode is ON
        """
        f = self.dll.SPC3_Set_DeadTime
        f.argtypes = [SPC3_H, c_uint16]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetDeadTime(Hermes_H Hermes, uint16_t Val);
        ec = f(self.c_handle, Val)
        self._checkError(ec)
        return

    def SetDeadTimeCorrection(self, s):
        """SetDeadTimeCorrection - Enable or disable the dead-time correction.
        The default setting is disabled.

        Parameters:
            s: New state for the dead-time corrector
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
        """
        f = self.dll.SPC3_Set_DeadTime_Correction
        f.argtypes = [SPC3_H, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetDeadTimeCorrection(Hermes_H Hermes, State s);
        ec = f(self.c_handle, s)
        self._checkError(ec)
        return

    def SetAdvancedMode(self, s):
        """SetAdvancedMode - Change the operating mode.
        Set the operating mode to Normal or Advanced. Normal mode is the default setting. Before starting a new acquisition check that the relevant parameters for the selected modality are correctly set.

        Parameters:
            s: Enable or disable the advanced mode
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
        """
        f = self.dll.SPC3_Set_Advanced_Mode
        f.argtypes = [SPC3_H, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetAdvancedMode(Hermes_H Hermes, State s);
        ec = f(self.c_handle, s)
        self._checkError(ec)
        return

    def SetBackgroundImg(self, Img):
        """SetBackgroundImg - Load a background image to perform hardware background subtraction.
        The control electronics is capable of performing real-time background subtraction. A background image is loaded into the internal camera memory.

        Parameters:
            Img:  Pointer to a 2048 uint16_t array containing the background image. \b WARNING The user should check the array size to avoid the corruption of the memory heap.
        Error codes:
            NULL_POINTER The provided Hermes_H or Img point to an empty memory location
            INVALID_OP Unable to set the background image when the live-mode is ON
        """
        f = self.dll.SPC3_Set_Background_Img
        f.argtypes = [
            SPC3_H,
            np.ctypeslib.ndpointer(dtype=np.uint16, ndim=1, flags="C_CONTIGUOUS"),
        ]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetBackgroundImg(Hermes_H Hermes, uint16_t* Img);
        data = Img.flatten().astype(np.uint16)
        ec = f(self.c_handle, data)
        self._checkError(ec)
        return

    def SetBackgroundSubtraction(self, s):
        """SetBackgroundSubtraction - Enable or disable the hardware background subtraction.


        Parameters:
            s: Enable or disable the background subtraction
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
        """
        f = self.dll.SPC3_Set_Background_Subtraction
        f.argtypes = [SPC3_H, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetBackgroundSubtraction(Hermes_H Hermes, State s);
        ec = f(self.c_handle, s)
        self._checkError(ec)
        return

    def SetGateMode(self, counter, Mode):
        """SetGateMode - Set the gate mode to continuous, coarse or pulsed (only counter 1)


        Parameters:
            counter: Counter to which settings refer. Accepted values: 1..3
            Mode: New gate mode
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP Only counter 1 can be set to Pulsed mode, for fast gating also counter 2 and 3 refers to HermesSetDualGate() and HermesSetTripleGate() functions.
        """
        f = self.dll.SPC3_Set_Gate_Mode
        f.argtypes = [SPC3_H, c_uint16, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetGateMode(Hermes_H Hermes, uint16_t counter, GateMode Mode);
        ec = f(self.c_handle, counter, Mode)
        self._checkError(ec)
        return

    def SetGateValues(self, Shift, Length):
        """SetGateValues - Change the fast Gate settings for counter 1.
        A gate signal is generated within the control electronics to select valid photons for counter 1, i.e. only photons which arrives when the Gate is ON
        are counted. The gate signal is a 50 MHz square wave: shift and length define the phase and duty-cycle of the signal.

        Parameters:
            Shift: Phase shift of the gate signal in the ON state. The unit is thousandths, i.e. 10 means a delay time of 0.01 times a 20 ns periodic signal,
            Length: Duration of the ON gate signal. The unit is percentage.  Accepted values: 0 ... 100
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            OUT_OF_BOUND Shift or length are outside the valid values
        """
        f = self.dll.SPC3_Set_Gate_Values
        f.argtypes = [SPC3_H, c_int16, c_int16]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetGateValues(Hermes_H Hermes, int16_t Shift, int16_t Length);
        ec = f(self.c_handle, Shift, Length)
        self._checkError(ec)
        return

    def SetDualGate(
        self, DualGate_State, StartShift, FirstGateWidth, SecondGateWidth, Gap
    ):
        """SetDualGate - Set parameters for DualGate mode.
        In this mode two counters are used, both in gated mode. Position and width of Gate 1 and width of Gate 2 can be set by user, whereas position of Gate 2
        is automatically set at the end of Gate 1 plus a gap selected by the user, but not smaller than 2ns. Total duration of Gate 1 and Gate 2 plus gap can not exceed 90% of the gate period of 20ns.

        Parameters:
            DualGate_State: Enable or disable dual-gate mode
            StartShift: Start delay for the first gate in thousandths of gate period (20ns). Accepted values: -500 ... +500
            FirstGateWidth: Duration of the ON gate 1 signal. The unit is percentage.  Accepted values: 0 ... 100
            SecondGateWidth: Duration of the ON gate 2 signal. The unit is percentage.  Accepted values: 0 ... 100
            Gap: Gap between the two gates in thousandths of nominal gate period (20ns). Accepted values: 100 ... 1000
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP This mode is not compatible with FLIM mode.
            OUT_OF_RANGE Parameters are out of bound. Please note that the function not only checks if the single parameters are acceptable, but also checks if the combination of parameters would result in an invalid gate
        """
        f = self.dll.SPC3_Set_DualGate
        f.argtypes = [SPC3_H, c_int, c_int, c_int, c_int, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetDualGate(Hermes_H Hermes, State DualGate_State, int StartShift, int FirstGateWidth, int SecondGateWidth, int Gap);
        ec = f(
            self.c_handle,
            DualGate_State,
            StartShift,
            FirstGateWidth,
            SecondGateWidth,
            Gap,
        )
        self._checkError(ec)
        return

    def SetTripleGate(
        self,
        TripleGate_State,
        StartShift,
        FirstGateWidth,
        SecondGateWidth,
        ThirdGateWidth,
        Gap1,
        Gap2,
    ):
        """SetTripleGate - Set parameters for TripleGate mode.
        In this mode three counters are used, all in gated mode. The three gates cannot overlap (even over different periods), and they have to follow the order: Gate1, Gate 3, Gate2.
        Position and width of Gate 1, and width of Gate 3 and 2 can be set by user, whereas position of Gate 3 and Gate 2 are automatically set by the function depending on the Gap1 and Gap2 values
        specified by the user. Gap1 between Gate1 and Gate3 can be as low as 0, Gap2 between Gate3 and Gate2 must be higher than 2ns. Total duration of Gate1, Gate3, Gate2 plus Gap1 and Gap2 can not exceed 90% of the gate period of 20ns.

        Parameters:
            TripleGate_State: Enable or disable triple-gate mode
            StartShift: Start delay for the first gate in thousandths of gate period (20ns). Accepted values: -500 ... +500
            FirstGateWidth: Duration of the ON gate 1 signal. The unit is percentage.  Accepted values: 0 ... 100
            SecondGateWidth: Duration of the ON gate 3 signal. The unit is percentage.  Accepted values: 0 ... 100
            ThirdGateWidth: Duration of the ON gate 2 signal. The unit is percentage.  Accepted values: 0 ... 100
            Gap1: Gap between the gate1 and gate3 in thousandths of nominal gate period (20ns). Accepted values: 0 ... 1000
            Gap2: Gap between the gate3 and gate2 in thousandths of nominal gate period (20ns). Accepted values: 100 ... 1000
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP This mode is not compatible with FLIM mode.
            OUT_OF_RANGE Parameters are out of bound. Please note that the function not only checks if the single parameters are acceptable, but also checks if the combination of parameters would result in an invalid gate
        """
        f = self.dll.SPC3_Set_TripleGate
        f.argtypes = [SPC3_H, c_int, c_int, c_int, c_int, c_int, c_int, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetTripleGate(Hermes_H Hermes, State TripleGate_State, int StartShift, int FirstGateWidth, int SecondGateWidth, int ThirdGateWidth, int Gap1, int Gap2);
        ec = f(
            self.c_handle,
            TripleGate_State,
            StartShift,
            FirstGateWidth,
            SecondGateWidth,
            ThirdGateWidth,
            Gap1,
            Gap2,
        )
        self._checkError(ec)
        return

    def SetCoarseGateValues(self, Counter, Start, Stop):
        """SetCoarseGateValues - Change the coarse Gate settings.
        A gate signal is generated within the control electronics to select valid photons, i.e. only photons which arrives when the Gate is ON
        are counted. The gate signal has a period equal to the hardware integration time, and the start and stop time of the ON period can be adjusted with 10ns steps.
        Different gate settings can be applied to the 3 counters.

        Parameters:
            Counter: Counter to which settings refer. Accepted values: 1..3
            Start: Starting position of the ON period. Can range from 0 to (HIT - 6), where units is 10ns and HIT is the Hardware Integration Time set with HermesSetCameraPar().
            Stop: Stop position of the ON period. Can range from (Start+1) to (HIT - 5), where units is 10ns and HIT is the Hardware Integration Time set with HermesSetCameraPar().
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            OUT_OF_BOUND Start or Stop are outside the valid values
        """
        f = self.dll.SPC3_Set_Coarse_Gate_Values
        f.argtypes = [SPC3_H, c_uint16, c_uint16, c_uint16]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetCoarseGateValues(Hermes_H Hermes, uint16_t Counter, uint16_t Start, uint16_t Stop);
        ec = f(self.c_handle, Counter, Start, Stop)
        self._checkError(ec)
        return

    def SetTriggerOutState(self, Mode):
        """SetTriggerOutState - Select the output signal.

        Parameters:
            Mode: New trigger mode
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
        """
        f = self.dll.SPC3_Set_Trigger_Out_State
        f.argtypes = [SPC3_H, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetTriggerOutState(Hermes_H Hermes, TriggerMode Mode);
        ec = f(self.c_handle, Mode)
        self._checkError(ec)
        return

    def SetSyncInState(self, s, frames):
        """SetSyncInState - Set the sync-in state.
        Set the camera to wait for an input trigger signal before starting an acquisition.

        Parameters:
            s: Enable or disable the synchronization input
            frames: If the synchronization input is enabled, this is the number of frames that are acquired for each pulse (0 means that the acquisition will wait only the first pulse and then continue to the end with no further pauses). Accepted values: 0 ... 100.
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
        """
        f = self.dll.SPC3_Set_Sync_In_State
        f.argtypes = [SPC3_H, c_int, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetSyncInState(Hermes_H Hermes, State s, int frames);
        ec = f(self.c_handle, s, frames)
        self._checkError(ec)
        return

    def LiveSetModeON(self):
        """LiveSetModeON - Turn on the Live mode.
        The camera is set in the Live mode, i.e. it continuously acquires images (free-running mode). The frames which are not transferred to the computer are discarded.
        Therefore, the time-laps between two frames is not constant and it will depend on the transfer speed between the host computer and the camera.
        This mode is very useful to adjust optical components or to align the camera position. When the camera is in Live mode, no acquisition of images by HermesSnapAcquire()
        or HermesContAcqToFileGetMemory() can be performed.

        Parameters:
            None.
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP The live mode has been already started
        """
        f = self.dll.SPC3_Set_Live_Mode_ON
        f.argtypes = [SPC3_H]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesLiveSetModeON(Hermes_H Hermes);
        ec = f(self.c_handle)
        self._checkError(ec)
        return

    def LiveSetModeOFF(self):
        """LiveSetModeOFF - Turn off the Live mode.


        Parameters:
            None.
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP The live mode is already inactive
        """
        f = self.dll.SPC3_Set_Live_Mode_OFF
        f.argtypes = [SPC3_H]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesLiveSetModeOFF(Hermes_H Hermes);
        ec = f(self.c_handle)
        self._checkError(ec)
        return

    def SetFlimPar(self, FLIM_steps, FLIM_shift, FLIM_start, Length):
        """SetFlimPar - Set FLIM parameters.
        The camera can perform automatic time-gated FLIM measurements employing the embedded gate generator. Call this function to setup the FLIM acquisition parameters. Each "FLIM acquisition" is composed by FLIM_steps frames,
        each one consisting of an acquisition with Exposure and NIntegFrames as set with HermesSetCameraPar(). The total time required to perform each FLIM acquisition is passed back to the caller through the referenced FLIM_frame_time variable.

        Parameters:
            FLIM_steps: Number of gate delay steps to be performed. Accepted values: 1 ... 1000
            FLIM_shift: Delay shift between steps in thousandths of gate period (20ns). Accepted values: 1 ... 1000
            FLIM_start: Start delay for FLIM sequence in thousandths of gate period (20ns). Accepted values: -500 ... +500
            Length: Duration of the ON gate signal. The unit is percentage.  Accepted values: 0 ... 100

        Returns:
            Total time required to perform each FLIM acquisition in multiples of 10ns.

        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            OUT_OF_BOUND Parameters are out of bound. Please note that the function not only checks if the single parameters are acceptable, but also checks if the combination of parameters would result in an invalid gate

        """
        f = self.dll.SPC3_Set_FLIM_Par
        f.argtypes = [SPC3_H, c_uint16, c_uint16, c_int16, c_uint16, POINTER(c_int)]
        f.restype = SPC3Return
        FLIM_frame_time = c_int(0)
        #  DllSDKExport HermesReturn HermesSetFlimPar(Hermes_H Hermes, uint16_t FLIM_steps, uint16_t FLIM_shift, int16_t FLIM_start, uint16_t Length, int* FLIM_frame_time);
        ec = f(
            self.c_handle,
            FLIM_steps,
            FLIM_shift,
            FLIM_start,
            Length,
            byref(FLIM_frame_time),
        )
        self._checkError(ec)

        # keep record of settings
        self._num_counters = 1
        self._num_pixels = 2048

        return FLIM_frame_time.value

    def SetFlimState(self, FLIM_State):
        """SetFlimState - Enable or disable FLIM mode. FLIM mode automatically set the number of used counters to 1. FLIM mode cannot be enabled if Exposure time is set to a value lower than 1040.

        Parameters:
            FLIM_State: Enable or disable the FLIM mode
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location.
            INVALID_OP Exposure time is lower than 1040.
        """
        f = self.dll.SPC3_Set_FLIM_State
        f.argtypes = [SPC3_H, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetFlimState(Hermes_H Hermes, State FLIM_State);
        ec = f(self.c_handle, FLIM_State)
        self._checkError(ec)
        return

    def ApplySettings(self):
        """ApplySettings - Apply settings to the camera.
        This function must be called after any Set function, except HermesLiveSetModeON() and HermesLiveSetModeOFF(), in order to apply the settings to the camera.
        If several Set functions need to be called, there is no need to call this function after each Set function. A single call to this function at the end is enough to apply all the settings.

        Parameters:
            None.
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
        """
        f = self.dll.SPC3_Apply_settings
        f.argtypes = [SPC3_H]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesApplySettings(Hermes_H Hermes);
        ec = f(self.c_handle)
        self._checkError(ec)

        # record data depth here

        if self.Is16Bit():
            self._data_bits = 16
        else:
            self._data_bits = 8

        return

    def LiveGetImg(self):
        """LiveGetImg - Get a Live image for each active counter.

        Parameters:
            None

        Returns:
            Live image for each active counter.
        Error codes:
            NULL_POINTER The provided Hermes_H or Img point to an empty memory location
            INVALID_OP The live-mode has not been started yet
        """
        f = self.dll.SPC3_Get_Live_Img
        f.argtypes = [
            SPC3_H,
            np.ctypeslib.ndpointer(dtype=np.uint16, ndim=1, flags="C_CONTIGUOUS"),
        ]
        f.restype = SPC3Return

        data = np.zeros(
            self.row_size * self._num_rows * int(self._num_counters), dtype=np.uint16
        )
        #  DllSDKExport HermesReturn HermesLiveGetImg(Hermes_H Hermes, uint16_t* Img);
        ec = f(self.c_handle, data)
        self._checkError(ec)
        frames = self.BufferToFrames(data, self._num_pixels, self._num_counters)
        return frames[0]  # we always have data of only frame [0] for all counters

    def SnapPrepare(self):
        """SnapPrepare - Prepare the camera to the acquisition of a snap.
        This command configures the camera to acquire a snap of NFrames images, as set by the HermesSetCameraPar() function. In FLIM mode NFrames "FLIM acquisitions" of a FLIM sequence will be acquired.
        If an External Sync is required, the camera will wait for a pulse on the Sync input before acquiring the images and saving them to the
        internal memory, otherwise they are acquired and saved immediately. Once acquired, snap must then be transferred to the PC using the HermesSnapAcquire() function.

        Parameters:
            None.
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP Unable to acquire images when the live mode is ON. Use instead HermesLiveGetImg().
            INVALID_OP When the background subtraction, dead-time correction or normal acquisition mode are enabled,
        """
        f = self.dll.SPC3_Prepare_Snap
        f.argtypes = [SPC3_H]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSnapPrepare(Hermes_H Hermes);
        ec = f(self.c_handle)
        self._checkError(ec)
        return

    def SnapAcquire(self):
        """SnapAcquire - Get a selected number of images.
        Acquire a set of images according to the parameters defined by HermesSetCameraPar(). In FLIM mode NFrames "FLIM acquisitions" will be acquired.
        This command works only when HermesSnapPrepare() has already been called.
        This function will not exit until the required number of images has been downloaded. For this reason,
        if the camera is configured for waiting and External Sync, before calling this function it could be useful to poll the camera for the trigger state,
        using the HermesIsTriggered() function.

        Parameters:
            None.
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP Unable to acquire images when the live mode is ON. Use instead HermesLiveGetImg().
            INVALID_OP When the background subtraction, dead-time correction or normal acquisition mode are enabled,
        """
        f = self.dll.SPC3_Get_Snap
        f.argtypes = [SPC3_H]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSnapAcquire(Hermes_H Hermes);
        ec = f(self.c_handle)
        self._checkError(ec)
        return

    def SnapGetImageBuffer(self):
        """SnapGetImageBuffer - Gets the images acquired in Snap mode.

        Parameters:
            None
        Error codes:
            NULL_POINTER The provided Hermes_H or BUFFER_H point to an empty memory location
        """
        size = (
            self._snap_num_frames
            * self._data_bits
            // 8
            * self._num_pixels
            * self._num_counters
        )
        buf = POINTER(c_uint8)()
        DataDepth = c_int(0)

        f = self.dll.SPC3_Get_Image_Buffer
        f.argtypes = [SPC3_H, POINTER(POINTER(c_uint8)), POINTER(c_int)]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSnapGetImageBuffer(Hermes_H Hermes, BUFFER_H* buffer, int* DataDepth);
        ec = f(self.c_handle, byref(buf), byref(DataDepth))
        self._checkError(ec)

        # if required, cast the buffer pointer from uint8_t* to uint16_t*
        # Update _data_bits if SDK returns different depth than configured
        if self._data_bits != DataDepth.value:
            self._data_bits = DataDepth.value

        if DataDepth.value == 16:
            buf = cast(buf, POINTER(c_uint16))
            count = size // 2
        else:
            count = size
        data = np.ctypeslib.as_array(
            buf, shape=(count,)
        )  # automatically deduces dtype from c_types POINTER(c_xyz)

        frames = self.BufferToFrames(data, self._num_pixels, self._num_counters)
        return frames

    def SnapGetImgPosition(self, Position, counter):
        """SnapGetImgPosition - Export an acquired image to an user allocated memory array.
        Once a set of images have been acquired by HermesSnapAcquire(), a single image can be exported from the SDK image buffer.

        Parameters:
            Img: Pointer to the output image array. The size of the array must be at least 4kB.
            Position: Index of the image to save.  Accepted values: 1 ... Number of acquired images
            counter: Number of the desired counter. Accepted values: 1 ... Number of used counters
        Error codes:
            NULL_POINTER The provided Hermes_H or Img point to an empty memory location
            OUT_OF_BOUND Parameters are out of bound.
        """
        data = np.zeros(self.row_size * self._num_rows, dtype=np.float64)

        f = self.dll.SPC3_Get_Img_Position
        f.argtypes = [
            SPC3_H,
            np.ctypeslib.ndpointer(dtype=np.float64, ndim=1, flags="C_CONTIGUOUS"),
            c_uint32,
            c_uint16,
        ]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSnapGetImgPosition(Hermes_H Hermes, uint16_t* Img, uint32_t Position, uint16_t counter);
        ec = f(self.c_handle, data, Position, counter)
        self._checkError(ec)

        frames = self.BufferToFrames(
            data, self._num_pixels, 1
        )  # expect data of just one counter!
        return frames[0]  # strip away dimension of frame index

    def ContAcqToFileStart(self, filename):
        """ContAcqToFileStart - Put the camera in "continuous acquisition" mode. Compatible with FLIM mode. If the camera was set to wait for an external sync, the acquisition will start as soon as a pulse is detected on the Sync input, otherwise it
        will start immediately.  The output file name must be provided when calling this function. Data are stored in the camera internal memory and must be downloaded calling the HermesContAcqToFileGetMemory() function
        as soon as possible, in order to avoid data loss.

        Parameters:
            filename: Full path of the output file. The string length must not exceed 1024 characters.
        Error codes:
            NULL_POINTER The provided Hermes_H or BUFFER_H point to an empty memory location
            UNABLE_CREATE_FILE It was not possible to create the output file.
        """
        f = self.dll.SPC3_Start_ContAcq
        f.argtypes = [SPC3_H, c_char_p]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesContAcqToFileStart(Hermes_H Hermes, char* filename);
        ec = f(self.c_handle, filename.encode("utf-8"))
        self._checkError(ec)
        return

    def ContAcqToFileGetMemory(self):
        """ContAcqToFileGetMemory - Dump the camera memory to the PC and save data to the file specified with the HermesContAcqToFileStart() function in Hermes file format (for details on the format see function HermesSaveImgDisk).
        This function must be repeatedly called, as fast as possible, in order to free the camera
        internal memory and keep the acquisition going. If the internal camera memory get full during acquisition an error is generated. \b WARNING The camera can generate data with very high throughput,
        up to about 205MB/s. Be sure to have enough disk space for your measurement.

        Parameters:
            None.
        Returns:
            Total number of bytes read.

        Error codes:
            NULL_POINTER The provided Hermes_H or BUFFER_H point to an empty memory location
            UNABLE_CREATE_FILE It was not possible to access the output file.
            INVALID_OP Continuous acquisition was not yet started. Use Hermes_HermesContAcqToFileStart() before calling this function.
            COMMUNICATION_ERROR Communication error during data download.
            Hermes_MEMORY_FULL Camera internal memory got full during data download. Data loss occurred. Reduce frame-rate or optimize your software to reduce dead-time between subsequent calling of the function.
        """
        f = self.dll.SPC3_Get_Memory
        f.argtypes = [SPC3_H, POINTER(c_double)]
        f.restype = SPC3Return
        total_bytes = c_double(0)
        #  DllSDKExport HermesReturn HermesContAcqToFileGetMemory(Hermes_H Hermes, double* total_bytes);
        ec = f(self.c_handle, byref(total_bytes))
        self._checkError(ec)
        return int(total_bytes.value)

    def ContAcqToFileStop(self):
        """ContAcqToFileStop - Stop the continuous acquisition of data and close the output file. This function must be called at the end of the continuous acquisition, in order to properly close the file. \b WARNING If not called,
        the output file may be unreadable, and camera may have unexpected behavior if other functions are called beforehand.

        Parameters:
            None.
        Error codes:
            NULL_POINTER The provided Hermes_H or BUFFER_H point to an empty memory location
            UNABLE_CREATE_FILE It was not possible to access the output file.
        """
        f = self.dll.SPC3_Stop_ContAcq
        f.argtypes = [SPC3_H]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesContAcqToFileStop(Hermes_H Hermes);
        ec = f(self.c_handle)
        self._checkError(ec)
        return

    def ContAcqToMemoryStart(self):
        """ContAcqToMemoryStart - Put the camera in "continuous acquisition" mode. Compatible with FLIM mode. If the camera was set to wait for an external sync, the acquisition will start as soon as a pulse is detected on the Sync input, otherwise it
        will start immediately. Data are stored in the camera internal memory and must be downloaded calling the HermesContAcqToMemoryGetBuffer() function as soon as possible, in order to avoid data loss.

        Parameters:
            None.
        Error codes:
            NULL_POINTER The provided Hermes_H or BUFFER_H point to an empty memory location
        """
        f = self.dll.SPC3_Start_ContAcq_in_Memory
        f.argtypes = [SPC3_H]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesContAcqToMemoryStart(Hermes_H Hermes);
        ec = f(self.c_handle)
        self._checkError(ec)
        return

    def ContAcqToMemoryGetBuffer(self):
        """ContAcqToMemoryGetBuffer - Get data from camera during a continuous acquisition.
        Parameters:
            None.
        Error codes:
            NULL_POINTER The provided Hermes_H or BUFFER_H point to an empty memory location
            INVALID_OP Continues acquisition was not yet started. Use HermesContAcqToMemoryStart() before calling this function.
            COMMUNICATION_ERROR Communication error during data download.
            Hermes_MEMORY_FULL Camera internal memory got full during data download. Data loss occurred. Reduce frame-rate or optimize your software to reduce deadtime between subsequent calling of the function.
        """

        buf = POINTER(c_uint8)()
        total_bytes = c_double()

        f = self.dll.SPC3_Get_Memory_Buffer
        f.argtypes = [SPC3_H, POINTER(c_double), POINTER(POINTER(c_uint8))]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesContAcqToMemoryGetBuffer(Hermes_H Hermes, double* total_bytes, BUFFER_H* buffer);
        ec = f(self.c_handle, byref(total_bytes), byref(buf))
        self._checkError(ec)

        size = int(total_bytes.value)
        if self._data_bits == 16:
            buf = cast(buf, POINTER(c_uint16))
            count = size // 2
        else:
            count = size
        data = np.ctypeslib.as_array(
            buf, shape=(count,)
        )  # automatically deduces dtype from c_types POINTER(c_xyz)

        return data

    def ContAcqToMemoryStop(self):
        """ContAcqToMemoryStop - Stop the continuous acquisition of data. This function must be called at the end of the continuous acquisition. \b WARNING If not called, camera may have unexpected behavior if other functions are called.


        Parameters:
            None.
        Error codes:
            NULL_POINTER The provided Hermes_H point to an empty memory location
        """
        f = self.dll.SPC3_Stop_ContAcq_in_Memory
        f.argtypes = [SPC3_H]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesContAcqToMemoryStop(Hermes_H Hermes);
        ec = f(self.c_handle)
        self._checkError(ec)
        return

    def GetDeadTime(self, Val):
        """GetDeadTime - Get the calibrated dead-time value.
        This function provides the closest calibrated dead-time value to Val.

        Parameters:
            Val: Desired dead-time value in ns. No error is generated when the value is above MAX_DEAD_TIME.

        Returns:
            Closest dead-time value possible.

        Error codes:
            NULL_POINTER The provided Hermes_H or ReturnVal point to an empty memory location
        """
        f = self.dll.SPC3_Get_DeadTime
        f.argtypes = [SPC3_H, c_uint16, POINTER(c_uint16)]
        f.restype = SPC3Return

        ReturnVal = c_uint16(0)
        #  DllSDKExport HermesReturn HermesGetDeadTime(Hermes_H Hermes, uint16_t Val, uint16_t* ReturnVal);
        ec = f(self.c_handle, Val, byref(ReturnVal))
        self._checkError(ec)
        return ReturnVal.value

    def GetGateWidth(self, counter, Val):
        """GetGateWidth - Get the calibrated gate width value.
        This function provides the closest calibrated gate-width value to Val.

        Parameters:
            counter: Counter for which the gate width is requested. Accepted values: 1..3
            Val: Desired gate-width value in percentage of 20ns. No error is generated when the value is out of range, instead the real boundaries are forced on ReturnVal.

        Returns:
            Closest gate-width value possible.

        Error codes:
            NULL_POINTER The provided Hermes_H or ReturnVal point to an empty memory location
        """
        f = self.dll.SPC3_Get_GateWidth
        f.argtypes = [SPC3_H, c_uint16, c_int16, POINTER(c_double)]
        f.restype = SPC3Return
        ReturnVal = c_double(0)
        #  DllSDKExport HermesReturn HermesGetGateWidth(Hermes_H Hermes, uint16_t counter, int16_t Val, double* ReturnVal);
        ec = f(self.c_handle, counter, Val, byref(ReturnVal))
        self._checkError(ec)
        return ReturnVal.value

    def GetGateShift(self, counter, Val):
        """GetGateShift - Get the calibrated gate shift value.
        This function provides the closest calibrated gate shift value to Val.

        Parameters:
            counter: Counter for which the gate shift is requested. Accepted values: 1..3
            Val: Desired gate shift value in thousandths of 20ns. No error is generated when the value out of range, instead the real boundaries are forced on ReturnVal.
        Returns:
            Closest gate-shift value possible.
        Error codes:
            NULL_POINTER The provided Hermes_H or ReturnVal point to an empty memory location
        """
        f = self.dll.SPC3_Get_GateShift
        f.argtypes = [SPC3_H, c_uint16, c_int16, POINTER(c_int16)]
        f.restype = SPC3Return

        ReturnVal = c_uint16(0)
        #  DllSDKExport HermesReturn HermesGetGateShift(Hermes_H Hermes, uint16_t counter, int16_t Val, int16_t* ReturnVal);
        ec = f(self.c_handle, counter, Val, byref(ReturnVal))
        self._checkError(ec)
        return ReturnVal.value

    def Is16Bit(self):
        """Is16Bit - Get the actual bit depth of acquired data.
        Data from the camera will be 16-bit per pixel, if NFramesInteg > 1, or DTC is enabled, or background subtraction is enabled, or 8-bit per pixel otherwise.
        This function provides actual bit depth with the current settings.

        Parameters:
            None.

        Returns:
            Returns True if the camera is setp to provide 16-bit data, False otherwise.

        Error codes:
            NULL_POINTER The provided Hermes_H or is16bit pointers point to an empty memory location
        """
        f = self.dll.SPC3_Is16Bit
        f.argtypes = [SPC3_H, POINTER(c_short)]
        f.restype = SPC3Return

        is16bit = c_short(0)
        #  DllSDKExport HermesReturn HermesIs16Bit(Hermes_H Hermes, short* is16bit);
        ec = f(self.c_handle, byref(is16bit))
        self._checkError(ec)
        return bool(is16bit.value)

    def IsTriggered(self):
        """IsTriggered - Poll the camera for external trigger status.
        Poll the camera in order to know if an external sync pulse was detected. The result is meaningful only if the camera was previously set to wait for an external sync.

        Parameters:
            None.
        Returns:
            Actual status. The value is 0 if no sync pulse was detected so far, 1 otherwise.
        Error codes:
            NULL_POINTER The provided Hermes_H or is Triggered pointers point to an empty memory location
        """
        f = self.dll.SPC3_IsTriggered
        f.argtypes = [SPC3_H, POINTER(c_short)]
        f.restype = SPC3Return

        isTriggered = c_short(0)
        #  DllSDKExport HermesReturn HermesIsTriggered(Hermes_H Hermes, short* isTriggered);
        ec = f(self.c_handle, byref(isTriggered))
        self._checkError(ec)
        # BUG FIX: original code returned bool(c_short.value) which evaluates the ctypes
        # class descriptor (always truthy) instead of the local instance. Changed to
        # bool(isTriggered.value) to correctly read the SDK output value.
        return bool(isTriggered.value)

    def GetVersion(self):
        """GetVersion - Get the SDK and camera firmware version

        Parameters:
            None.
        Returns:
            Firmware_Version: Version of the camera firmare in the format x.xx.
            Software_Version: Version of the SDK in the format x.xx.
            Custom_version: Customization version of the firmware and SDK. For standard model "A" is returned.
        Error codes:
            NULL_POINTER The provided handle or pointers point to an empty memory location
        """
        f = self.dll.SPC3_GetVersion
        f.argtypes = [SPC3_H, POINTER(c_double), POINTER(c_double), c_char_p]
        f.restype = SPC3Return

        Firmware_Version = c_double(0)
        Software_Version = c_double(0)
        Custom_version = c_char(0)
        #  DllSDKExport HermesReturn HermesGetVersion(Hermes_H Hermes, double* Firmware_Version, double* Software_Version, char* Custom_version);
        ec = f(
            self.c_handle,
            byref(Firmware_Version),
            byref(Software_Version),
            byref(Custom_version),
        )
        self._checkError(ec)
        return (
            Firmware_Version.value,
            Software_Version.value,
            Custom_version.value.decode("utf-8"),
        )

    def GetSerial(self):
        """GetSerial - Get the camera serial number and ID

        Parameters:
            None.
        Returns:
            Camera_ID: Unique camera ID. A string of at least 11 character is required as parameter.
            Camera_serial: Hermes camera serial number. A string of at least 33 character is required as parameter.
        Error codes:
            NULL_POINTER The provided handle or pointers point to an empty memory location.
        """
        f = self.dll.SPC3_GetSerial
        f.argtypes = [SPC3_H, c_char_p, c_char_p]
        f.restype = SPC3Return

        Camera_ID = create_string_buffer(11)
        Camera_serial = create_string_buffer(33)
        #  DllSDKExport HermesReturn HermesGetSerial(Hermes_H Hermes, char* Camera_ID, char* Camera_serial);
        ec = f(self.c_handle, Camera_ID, Camera_serial)
        self._checkError(ec)
        return Camera_ID.value.decode("utf-8"), Camera_serial.value.decode("utf-8")

    # def DeviceInfo(self, Device_ID):
    #             """DeviceInfo - Get device info.
    #     It gets device serial number, Unique ID, version and SDK version, without constructing an Hermes object. Useful when constructor fails due to incompatible firmware and SDK versions or for powering issues.
    #
    #     Parameters:
    #         Device_ID: Hermes camera Unique ID. A string of at least 11 character is required as parameter. If multiple devices are connected to the computer, a unique camera ID should be provided to correctly identify the camera. The camera ID can be found in the camera documentation.
    #         Camera_serial: Hermes camera serial number. A string of at least 33 character is required as parameter. This parameter is referenced.
    #         Firmware_Version: Version of the camera firmare in the format x.xx. This parameter is referenced.
    #         Software_Version: Version of the SDK in the format x.xx. This parameter is referenced.
    #         Firmware_Custom_Version: Customization version of the firmware. For standard model "A" is returned. This parameter is referenced.
    #         Software_Custom_Version: Customization version of the software. For standard model "A" is returned. This parameter is referenced.
    #     Error codes:
    #     """
    #     f = self.dll.HermesDeviceInfo
    #     f.argtypes = [c_char_p, c_char_p, POINTER(c_double), POINTER(c_double), c_char_p, c_char_p]
    #     f.restype = HermesReturn
    #
    #     Camera_serial = create_string_buffer(33)
    #     Firmware_Version = c_double(0)
    #     Software_Version = c_double(0)
    #     Firmware_Custom_Version = c_char(0)
    #     Software_Custom_Version = c_char(0)
    #     #  DllSDKExport HermesReturn HermesDeviceInfo(char* Device_ID, char* Camera_serial, double* Firmware_Version, double* Software_Version, char* Firmware_Custom_Version, char* Software_Custom_Version);
    #     ec = f(Device_ID.encode('utf-8'), Camera_serial, byref(Firmware_Version), byref(Software_Version), byref(Firmware_Custom_Version), byref(Software_Custom_Version))
    #     self._checkError(ec)
    #     return Camera_serial.decode('utf-8'), Firmware_Version.value, Software_Version.value, Firmware_Custom_Version.value.decode('utf-8'), byref(Software_Custom_Version).value.decode('utf-8')

    def SaveImgDisk(self, Start_Img, End_Img, filename, mode):
        """SaveImgDisk - Save the selected images on the hard disk.
        This function saves the acquired images on the hard disk. The output file format can be either a multipage TIFF with embedded acquisition metadata according to the OME-TIFF format or the proprietary Hermes format. For FLIM measurements, use the HermesSaveFlimDisk() function.
        If TIFF format is selected, the desired images will be saved in a file for each enabled counter. If Hermes format is selected a single Hermes file will be created for all the counters.
        OME-TIFF file could be opened with any image reader compatible with TIFF file, since metadata are saved into the Image Description tag in XML format.
        In order to decode OME-TIFF metadata, it is possible to use a free OME-TIFF reader, such as OMERO or the Bio-Formats plugin for ImageJ. For more details see the OME-TIFF web site:
        http://www.openmicroscopy.org/site/support/ome-model/ome-tiff/. If subarray acquisition is enabled and the number of pixels is not an integer multiple of 32,
        the TIFF files will have as much rows of 32 pixels as needed to accommodate all pixels, and the missing pixels will be put to 0, e.g.  if 67 pixels are acquired, the TIFF image will be 32x3, with the last 29 pixels of the 3rd row set to 0.
        Hermes file are binary files composed by a header with acquisition metadata followed by raw image data, containing the 8/16 bit (integer) or 64 bit (double precision) pixel values in row-major order (refer to Figure 7 of the User Manual for pixels position and order). The byte order is little-endian for the 16 or 64 bit images.
        In case more counters are used, data are interlaced, i.e. the sequence of frames is the following:
        1st frame of 1st counter, 1st frame of 2nd counter, 1st frame of 3rd counter, 2nd frame of 1st counter, etc.
        The header is composed by a signature of 8 byte (0x4d5044ff04000000, starting with 4d on byte 0), and a metadata section of 1024 byte, as follows (multibyte fields are little-endian):

        | Byte offset   | Number of bytes   | Description                                    |
        | --------------|-------------------|-----------------------------------------------|
        | 0             | 10                   | Unique camera ID (string)                        |
        | 10            | 32                | Hermes serial number (string)                    |
        | 42            | 2                    | Firmware version (x.xx saved as xxx)            |
        | 44            | 1                    | Firmware custom version (standard = 0)        |
        | 45            | 20                | Acquisition data&time (string)                |
        | 65            | 35                | Unused                                        |
        | 100           | 1                 | Number of rows                                |
        | 101           | 1                 | Number of columns                                |
        | 102           | 1                 | Bit per pixel                                    |
        | 103           | 1                 | Counters in use                                |
        | 104           | 2                 | Hardware integration time    (multiples of 10ns)    |
        | 106           | 2                 | Summed frames                                     |
        | 108           | 1                 | Dead time correction enabled                    |
        | 109           | 1                 | Internal gate duty-cycle for counter1 (0-100%)|
        | 110           | 2                 | Hold-off time (ns)                            |
        | 112            | 1                 | Background subtraction enabled                |
        | 113           | 1                 | Data for counters 1 and 2 are signed            |
        | 114            | 4                    | Number of frames in the file                    |
        | 118            | 1                    | Image is averaged                                |
        | 119            | 1                    | Counter which is averaged                        |
        | 120            | 2                    | Number of averaged images                        |
        | 122           | 1                 | Internal gate duty-cycle for counter2 (0-100%)|
        | 123           | 1                 | Internal gate duty-cycle for counter3 (0-100%)|
        | 124            | 2                    | Frames per sync-in pulse                        |
        | 126            | 2                    | Number of pixels                                |
        | 128            | 72                | Unused                                        |
        | 200           | 1                 | FLIM enabled                                    |
        | 201           | 2                 | FLIM shift (thousandths of gate period)        |
        | 203           | 2                 | FLIM steps                                    |
        | 205           | 4                 | FLIM frame length (multiples of 10ns)            |
        | 209           | 2                 | FLIM bin width (fs)                           |
        | 211            | 9                    | Unused                                        |
        | 220            | 1                    | Multi gate mode: 2 = dual, 3 = triple            |
        | 221            | 2                    | Multi gate mode: start position (-500 - +500)    |
        | 223            | 1                    | Multi gate mode: first gate width (0-100%)    |
        | 224            | 1                    | Multi gate mode: second gate width (0-100%)     |
        | 225            | 1                    | Multi gate mode: third gate width (0-100%)     |
        | 226            | 2                    | Multi gate mode: gap1 (0-800)                    |
        | 228            | 2                    | Multi gate mode: gap2 (0-800)                    |
        | 230            | 2                    | Multi gate mode: calibrated bin-width in fs    |
        | 232            | 1                    | Coarse gate 1 enabled                            |
        | 233            | 2                    | Coarse gate 1 start                            |
        | 235            | 2                    | Coarse gate 1 stop                            |
        | 237            | 1                    | Coarse gate 2 enabled                            |
        | 238            | 2                    | Coarse gate 2 start                            |
        | 240            | 2                    | Coarse gate 2 stop                            |
        | 242            | 1                    | Coarse gate 3 enabled                            |
        | 243            | 2                    | Coarse gate 3 start                            |
        | 245            | 2                    | Coarse gate 3 stop                            |
        | 247            | 53                | Unused                                        |
        | 300           | 1                 | PDE measurement                                |
        | 301           | 2                 | Start wavelength (nm)                            |
        | 303           | 2                 | Stop wavelength (nm)                            |
        | 305           | 2                 | Step (nm)                                        |
        | 307           | 717               | unused                                        |

        Hermes file can be read using the provided ImageJ/Fiji plugin.

        Parameters:
            Start_Img: Index of the first image to save.  Accepted values: 1 ... Number of acquired images
            End_Img: Index of the last image to save.  Accepted values: Start_Img ... Number of acquired images
            filename: Full path of the output file.
            mode: File format of the output images
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP No images were acquired or the selected range of images is not valid
            UNABLE_CREATE_FILE Unable to create the output file
        """
        f = self.dll.SPC3_Save_Img_Disk
        f.argtypes = [SPC3_H, c_uint32, c_uint32, c_char_p, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSaveImgDisk(Hermes_H Hermes, uint32_t Start_Img, uint32_t End_Img, char* filename, OutFileFormat mode);
        ec = f(self.c_handle, Start_Img, End_Img, filename.encode("utf-8"), mode)
        self._checkError(ec)
        return

    def SaveAveragedImgDisk(self, counter, filename, mode, is_double):
        """SaveAveragedImgDisk - Save the selected images on the hard disk.
        This function saves the average of the images acquired by a specified counter on the hard disk. File format can be proprietary Hermes or TIFF, as explained in HermesSaveImgDisk() function.

        Parameters:
            counter: Number of the counter to be saved. Accepted values: 1..3
            filename: Full path of the output file.
            mode: File format of the output images
            isDouble: Number format. 0 for Uint16, 1 for Double
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP No images were acquired or the selected range of images is not valid
            UNABLE_CREATE_FILE Unable to create the output file
        """
        f = self.dll.SPC3_Save_Averaged_Img_Disk
        f.argtypes = [SPC3_H, c_uint16, c_char_p, c_int, c_short]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSaveAveragedImgDisk(Hermes_H Hermes, uint16_t counter, char* filename, OutFileFormat mode, short isDouble);
        ec = f(self.c_handle, counter, filename.encode("utf-8"), mode, is_double)
        self._checkError(ec)
        return

    def SaveFlimDisk(self, filename, mode):
        """SaveFlimDisk - Save the FLIM acquisition on the hard disk.
        This function saves the acquired FLIM images on the hard disk. The output file format can be either a multipage TIFF with embedded acquisition metadata according to the OME-TIFF format or the proprietary Hermes format. For standard measurements, use the Hermes_Save_Img__Disk() function.
        For both formats, image data is composed by a set of images following a "FLIM first, time second scheme", i.e. with the following frame sequence:
        1st gate shift of 1st FLIM measurement, 2nd gate shift of 1st FLIM measurement,...,nth gate shift of 1st FLIM measurement,1st gate shift of 2nd FLIM measurement, 2nd gate shift of 2nd FLIM measurement,...,nth gate shift of 2nd FLIM measurement,etc.
        OME-TIFF file could be opened with any image reader compatible with TIFF file, since metadata are saved into the Image Description tag in XML format.
        In order to decode OME-TIFF metadata, it is possible to use a free OME-TIFF reader, such as OMERO or the Bio-Formats plugin for ImageJ. For more details see the OME-TIFF web site:
        http://www.openmicroscopy.org/site/support/ome-model/ome-tiff/. OME-TIFF metadata include the ModuloAlongT tag, which allows the processing of FLIM data with dedicated FLIM software such as FLIMfit (see http://www.openmicroscopy.org/site/products/partner/flimfit).
        Hermes file are binary files composed by a header with acquisition metadata followed by raw image data, containing the 8/16 bit pixel values in row-major order (refer to Figure 7 of the User Manual for pixels position and order). The byte order is little-endian for the 16 bit images.
        The header is composed by a signature of 8 byte (0x4d5044ff03000001, starting with 4d on byte 0), and a metadata section of 1024 byte described in function HermesSaveImgDisk().
        Hermes file can be read using the provided ImageJ/Fiji plugin.

        Parameters:
            filename: Full path of the output file.
            mode: File format of the output images
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP No images were acquired or the selected range of images is not valid
            UNABLE_CREATE_FILE Unable to create the output file
        """
        f = self.dll.SPC3_Save_FLIM_Disk
        f.argtypes = [SPC3_H, c_char_p, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSaveFlimDisk(Hermes_H Hermes, char* filename, OutFileFormat mode);
        ec = f(self.c_handle, filename.encode("utf-8"), mode)
        self._checkError(ec)
        return

    def ReadHermesFileFormatImage(self, filename, ImgIdx, counter):
        """ReadHermesFileFormatImage - Read an integer (8 - 16 bit) Hermes image from file.
        Read the image at the ImgIdx position and for desired counter in the given Hermes file from the hard disk.

        Parameters:
            filename: Full path of the output file.
            ImgIdx:    Image index in the file. Accepted values: 1 ... 65534
            counter: Desired counter. Accepted values: 1 ... 3
            Img: Pointer to the output image array. The size of the array must be at least 2 kiB.
            header: Array in which the header of Hermes file is saved.
        Error codes:
            UNABLE_READ_FILE Unable to read the input file. Is it a Hermes file?
            OUT_OF_BOUND The desired counter or image exceeds the file size.
            NOT_EN_MEMORY Not enough memory to store the data contained in the file
            NULL_POINTER The provided provided handle or pointers point to an empty memory location.
        """
        f = self.dll.SPC3_ReadSPC3FileFormatImage
        f.argtypes = [
            c_char_p,
            c_uint32,
            c_uint16,
            np.ctypeslib.ndpointer(dtype=np.float64, ndim=1, flags="C_CONTIGUOUS"),
            c_char_p,
        ]
        f.restype = SPC3Return

        Img = np.zeros(self.row_size * self._num_rows, dtype=np.uint16)
        header = create_string_buffer(1024)
        #  DllSDKExport HermesReturn HermesReadHermesFileFormatImage(char* filename, uint32_t ImgIdx, uint16_t counter, uint16_t* Img, char header[1024]);
        ec = f(filename.encode("utf-8"), ImgIdx, counter, Img, header)
        self._checkError(ec)

        return Img, header.decode("utf-8")

    def AverageImg(self, counter):
        """AverageImg - Gets the average image.
        Once a set of images have been acquired by HermesSnapAcquire(), an image which contains for each pixel the average value over all the acquired images is
        calculated.

        Parameters:
            counter: Desired counter. Accepted values: 1..3
        Returns:
            The average image.
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP No images were acquired
        """

        f = self.dll.SPC3_Average_Img
        f.argtypes = [
            SPC3_H,
            np.ctypeslib.ndpointer(dtype=np.float64, ndim=1, flags="C_CONTIGUOUS"),
            c_uint16,
        ]
        f.restype = SPC3Return

        data = np.zeros(self.row_size * self._num_rows, dtype=np.float64)
        #  DllSDKExport HermesReturn HermesAverageImg(Hermes_H Hermes, double* Img, uint16_t counter);
        ec = f(self.c_handle, data, counter)
        self._checkError(ec)

        frames = self.BufferToFrames(
            data, self._num_pixels, 1
        )  # expect data of just one counter!
        return frames[0][0]  # strip away dimensions of frame index and counter index

    def StDevImg(self, counter):
        """StDevImg - Calculate the standard deviation image.
        Once a set of images have been acquired by HermesSnapAcquire(), an image which contains for each pixel the standard deviation over all the acquired images is
        calculated.

        Parameters:
            counter: Desired counter. Accepted values: 1..3
        Returns:
            The standard deviation image.
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP No images were acquired
        """
        f = self.dll.SPC3_StDev_Img
        f.argtypes = [
            SPC3_H,
            np.ctypeslib.ndpointer(dtype=np.float64, ndim=1, flags="C_CONTIGUOUS"),
            c_uint16,
        ]
        f.restype = SPC3Return

        data = np.zeros(self.row_size * self._num_rows, dtype=np.float64)
        #  DllSDKExport HermesReturn HermesStDevImg(Hermes_H Hermes, double* Img, uint16_t counter);
        ec = f(self.c_handle, data, counter)
        self._checkError(ec)

        frames = self.BufferToFrames(
            data, self._num_pixels, 1
        )  # expect data of just one counter!
        return frames[0][0]  # strip away dimensions of frame index and counter index

    def SetCorrelationMode(self, CM, NCorrChannels, s):
        """SetCorrelationMode - Enable the correlation mode.
        This function must be called before invoking HermesCorrelationImg(). When this function is called, the memory required to save the
        new data is allocated in the heap and the previously stored data are cancelled. The deallocation of this memory is automatically performed
        when the Hermes_destr() function is called or by setting the State s equal to Disabled.

        Parameters:
            CM: Selected autocorrelation algorithm
            NCorrChannels: Number of global lag channels. When the linear correlation algorithm is selected, the first NChannel lags are calculated, where NChannel
            s: Enable or Disable the correlation mode
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            OUT_OF_BOUND NCorrChannels must be greater than zero for the Multi-tau algorithm and greater than 2 for the Linear one
            NOT_EN_MEMORY There is not enough memory to enable the correlation mode
        """
        f = self.dll.SPC3_Set_Correlation_Mode
        f.argtypes = [SPC3_H, c_int, c_int, c_int]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSetCorrelationMode(Hermes_H Hermes, CorrelationMode CM, int NCorrChannels, State s);
        ec = f(self.c_handle, CM, NCorrChannels, s)
        self._checkError(ec)
        return

    def CorrelationImg(self, counter):
        """CorrelationImg - Calculate the autocorrelation function.
        The autocorrelation function is estimated for each pixel. This function requires that a set of
        images have been previously acquired by HermesSnapAcquire() and that the correlation mode is set to Enabled. Depending on the selected algorithm and the
        total number of collected images, this function can take several tens of seconds.

        Parameters:
            counter: Desired counter. Accepted values: 1..3
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP No images were acquired or the correlation mode was not enabled
            NOT_EN_MEMORY Not enough memory to calculate the correlation function
            INVALID_NIMG_CORRELATION The required number of time lags of the correlation function can not be calculated from the available number of images
        """
        f = self.dll.SPC3_Correlation_Img
        f.argtypes = [SPC3_H, c_uint16]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesCorrelationImg(Hermes_H Hermes, uint16_t counter);
        ec = f(self.c_handle, counter)
        self._checkError(ec)
        return

    def SaveCorrelationImg(self, filename):
        """SaveCorrelationImg - Save the autocorrelation functions on the hard disk.
        This function requires that HermesSetCorrelationMode() and HermesCorrelationImg() have been previously called. The autocorrelation data are stored in a .hrmc binary
        file.
        The hrmc binary file is organized as follows:

        Byte offset               | Type           |Number of bytes | Description                                            |
        --------------------------|----------------|----------------|-------------------------------------------------------|
        0                         | int            |4               | Number of lag-times (NLag)                            |
        4                         | int            |4               | Number of pixels. This value must be 1024 (NPix)        |
        8                         | int            |4               | Selected algorithm: 0 Linear, 1 Multi-tau                |
        12                        | double         |8 * NLag        | Autocorrelation values of the first pixel                |
        12 + 8 * NLag             | double         |8 * NLag        | Autocorrelation values of the second pixel            |
        ...                       | double         |8 * NLag        | Autocorrelation values of the  N<SUP>th</SUP> pixel    |
        12 + 8 * (NPix-1) * NLag  | double         |8 * NLag        | Autocorrelation values of the last pixel                |
        12 + 8 * NPix * NLag      | double         |8 * NLag        | Lag times                                                |

        A simple Matlab script can be used to read the data for further processing or visualization.

        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~{.mat}
        %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
        MPD .HRMC file reader
        %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

        function data = Read_HRMC(fname)
        f = fopen(fname,'rb');
        buf = fread(f,3,'int32');
        data.NChannel = buf(1);
        data.NPixel = buf(2);
        data.IsMultiTau = (buf(3) == 1);

        data.CorrelationImage = reshape(fread(f,data.NPixel*data.NChannel,'float64'), ...
                    data.NChannel,32,32);
        data.CorrelationImage = permute(data.CorrelationImage,[2 3 1]);
        data.t=fread(f, data.NChannel,'float64');
        fclose(f);
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        Parameters:
            filename: Full path of the output file
        Error codes:
            NULL_POINTER The provided Hermes_H points to an empty memory location
            INVALID_OP The autocorrelation, which has been calculated, is not valid
            UNABLE_CREATE_FILE Unable to create the output file
        """
        f = self.dll.SPC3_Save_Correlation_Img
        f.argtypes = [SPC3_H, c_char_p]
        f.restype = SPC3Return
        #  DllSDKExport HermesReturn HermesSaveCorrelationImg(Hermes_H Hermes, char* filename);
        ec = f(self.c_handle, filename.encode("utf-8"))
        self._checkError(ec)
        return

    # def ResetOverilluminationProtection(self):
    #     """ResetOverilluminationProtection - Reset the internal overillumination protection circuit.
    #     This function resets the Hermes internal protection triggered by excessive illumination. It is mandatory to check that the overillumination condition is removed
    #     before calling this function. In any case, in order to avoid damage to the Hermes, up to 3 reset cycles are allowed before having to disconnect the camera from the power supply.

    #     Parameters:
    #         None.
    #     Error codes:
    #         TOO_MUCH_LIGHT The protection was reset, but the illumination is still too much!
    #         PERSISTING_TOO_MUCH_LIGHT The number of allowed reset cycles has been exceeded!
    #     """
    #     f = self.dll.HermesResetOverilluminationProtection
    #     f.argtypes = [Hermes_H]
    #     f.restype = HermesReturn
    #     #  DllSDKExport HermesReturn HermesResetOverilluminationProtection(Hermes_H Hermes);
    #     ec = f(self.c_handle)
    #     self._checkError(ec)
    #     return

    @staticmethod
    def BufferToFrames(data, num_pixels, num_counters):
        """BufferToFrames - Converts a plain flat SPC3 data array such as data from ContAcqToMemoryGetBuffer()
        or "raw" data read from spc3 data files to a more structured data set containing multiple frames

        Parameters
            data: 1-d array containing SPC3 pixel data such as data returned by SnapGetImageBuffer(), ContAcqToMemoryGetBuffer()
            num_pixels: number of active pixels
            num_counters: number of active counters
        Returns:
            Data reshaped as frames.
        """
        if not isinstance(data, np.ndarray):
            raise ValueError("Expected numpy-ndarray, got {}".format(type(data)))

        if data.size % (num_pixels * num_counters) != 0:
            raise ValueError(
                "The number of elements of data must be a multiple of (num_pixels * num_counters)"
            )

        row_size = 32

        num_full_rows = int(num_pixels + row_size - 1) // row_size  # ceil division

        # reshape data in 'flat' blocks each containing all pixels of a frame
        all_flat_frames = data.reshape((data.size // num_pixels, num_pixels))

        # number of frames per counter
        num_frames = (
            len(all_flat_frames) // num_counters
        )  # number of frames per counter

        if num_pixels % row_size == 0:
            frames = all_flat_frames.reshape(
                (num_frames, num_counters, num_full_rows, row_size)
            )
        else:
            # pad frames so that the number of pixels is a multiple of 32 pixels (i.e. one row)
            num_pad_pixels = row_size * num_full_rows - num_pixels
            all_flat_frames_padded = np.pad(
                all_flat_frames, ((0, 0), (0, num_pad_pixels))
            )
            frames = all_flat_frames_padded.reshape(
                (num_frames, num_counters, num_full_rows, row_size)
            )

        # swap frame indexes and counter dimensions
        frames = np.swapaxes(frames, 0, 1)
        # rotate by 90 degrees (swap rows and cols)
        frames = np.swapaxes(frames, frames.ndim - 2, frames.ndim - 1)

        return frames

    @staticmethod
    def ReadSPC3DataFile(path):
        """ReadSPC3DataFile - reads .spc acquisition files
        or "raw" data read from spc data files to a more structured data set containing multiple frames

        Parameters
            path: path to the .spc3 data file
        Returns:
            data file header and frames
        """

        def readfield(inf, count, c_type):
            if c_type is None:
                inf.read(count)
                return
            bs = bytearray(inf.read(count * sizeof(c_type)))
            if count > 1:
                return (c_type * count).from_buffer_copy(bs).value
            else:
                return c_type.from_buffer_copy(bs).value

        inf = open(path, "rb")

        file_meta_stuff = readfield(inf, 8, c_char)

        class SPC3FileHeader:
            pass

        header = SPC3FileHeader

        header.camera_id = readfield(inf, 10, c_char).decode("utf-8")

        header.SN = readfield(inf, 32, c_char).decode("utf-8")

        header.FW_VER = readfield(inf, 1, c_uint16) / 100
        header.custom_ver = chr(
            readfield(inf, 1, c_uint8) + ord("A")
        )  # 0 = A, 1 = B, etc
        header.date_time = readfield(inf, 20, c_char).decode("utf-8")

        readfield(inf, 35, None)

        header.N_rows = readfield(inf, 1, c_uint8)
        header.N_cols = readfield(inf, 1, c_uint8)
        header.bit_x_pix = readfield(inf, 1, c_uint8)
        header.N_counters = readfield(inf, 1, c_uint8)
        header.HwIntTime = readfield(inf, 1, c_uint16) * 10e-9
        header.SummedFrames = readfield(inf, 1, c_uint16)
        header.DeadTimeCorrectionON = readfield(inf, 1, c_uint8) != 0
        header.GateDuty_C1 = readfield(inf, 1, c_uint8)
        header.HoldOff = readfield(inf, 1, c_uint16) * 1e-9
        header.BKGsubON = readfield(inf, 1, c_uint8) != 0
        header.C1_2_signed = readfield(inf, 1, c_uint8) != 0
        header.N_frames = readfield(inf, 1, c_uint32)
        header.ImgAveraged = readfield(inf, 1, c_uint8) != 0
        header.Caveraged = readfield(inf, 1, c_uint8)
        header.N_ave = readfield(inf, 1, c_uint16)
        header.GateDuty_C2 = readfield(inf, 1, c_uint8)
        header.GateDuty_C3 = readfield(inf, 1, c_uint8)
        header.Frames_x_syncIn = readfield(inf, 1, c_uint16)
        header.N_pix = readfield(inf, 1, c_uint16)
        readfield(inf, 72, None)

        header.FLIM_ON = readfield(inf, 1, c_uint8) != 0
        header.FLIM_shift_pct = readfield(inf, 1, c_uint16)
        header.FLIM_steps = readfield(inf, 1, c_uint16)
        header.FLIM_frameLen = readfield(inf, 1, c_uint32) * 10e-9
        header.FLIM_binWidth = readfield(inf, 1, c_uint16) * 1e-15
        readfield(inf, 9, None)

        header.MultiGate_mode = readfield(inf, 1, c_uint8)
        header.MultiGate_start_pos = readfield(inf, 1, c_int16)
        header.MultiGate_widthC1 = readfield(inf, 1, c_uint8)
        header.MultiGate_widthC2 = readfield(inf, 1, c_uint8)
        header.MultiGate_widthC3 = readfield(inf, 1, c_uint8)
        header.MultiGate_gapC1_2 = readfield(inf, 1, c_uint16)
        header.MultiGate_gapC2_3 = readfield(inf, 1, c_uint16)
        header.MultiGate_binWidth = readfield(inf, 1, c_uint16) * 1e-15

        header.CoarseGate_C1_ON = readfield(inf, 1, c_uint8) != 0
        header.CoarseGate_C1_startPos = readfield(inf, 1, c_uint16) * 10e-9
        header.CoarseGate_C1_stopPos = readfield(inf, 1, c_uint16) * 10e-9
        header.CoarseGate_C2_ON = readfield(inf, 1, c_uint8) != 0
        header.CoarseGate_C2_startPos = readfield(inf, 1, c_uint16) * 10e-9
        header.CoarseGate_C2_stopPos = readfield(inf, 1, c_uint16) * 10e-9
        header.CoarseGate_C3_ON = readfield(inf, 1, c_uint8) != 0
        header.CoarseGate_C3_startPos = readfield(inf, 1, c_uint16) * 10e-9
        header.CoarseGate_C3_stopPos = readfield(inf, 1, c_uint16) * 10e-9
        readfield(inf, 53, None)

        header.PDE_ON = readfield(inf, 1, c_uint8) != 0
        header.PDE_startWave = readfield(inf, 1, c_uint16) * 1e-9
        header.PDE_stopWave = readfield(inf, 1, c_uint16) * 1e-9
        header.PDE_step = readfield(inf, 1, c_uint16) * 1e-9

        data_count = header.N_cols * header.N_rows * header.N_frames * header.N_counters

        if header.bit_x_pix == 16:
            dtype = np.uint16
        elif header.bit_x_pix == 8:
            dtype = np.uint8
        else:
            raise ValueError("invalid bit width, got {}".format(str(header.bit_x_pix)))
        inf.seek(0)
        data = np.fromfile(inf, offset=1024 + 8, count=data_count, dtype=dtype)

        num_pixels = header.N_pix
        num_counters = header.N_counters

        # Debug: log what we read from file
        #print(f"ReadSPC3DataFile DEBUG:")
        #print(
        #    f"  Header: N_rows={header.N_rows}, N_cols={header.N_cols}, N_frames={header.N_frames}, N_counters={header.N_counters}"
        #)
        #print(f"  Header: N_pix={header.N_pix}, bit_x_pix={header.bit_x_pix}")
        #print(f"  Calculated data_count={data_count}, dtype={dtype}")
        #print(f"  Read data.size={data.size}, data.dtype={data.dtype}")
        #print(f"  Using num_pixels={num_pixels}, num_counters={num_counters}")
        #print(
        #    f"  Check: data.size % (num_pixels * num_counters) = {data.size % (num_pixels * num_counters)}"
        #)

        frames = SPC3.BufferToFrames(data, num_pixels, num_counters)

        return frames, header


# if __name__ == "__main__":
# plt.close('all')
# s = SPC3(SPC3.CameraMode.NORMAL)
# num_counters = 2
# s.SetCameraPar(100, 1, 1000, num_counters, SPC3.State.DISABLED, SPC3.State.DISABLED, SPC3.State.DISABLED)
# s.ApplySettings()
# s.LiveSetModeON()

# plot_img = plt.imshow(np.zeros((32, 64)))
# plt.set_cmap('gray')

# for k in range(1000):
#     live_frames = s.LiveGetImg()
#     counter1_frame = live_frames[0]
#     # frames[0] contains live image for counter 1 (shown below)
#     # frames[1] contains live image for counter 2
#     plt.clim(np.min(counter1_frame), np.max(counter1_frame))
#     plt.title('frame {}'.format(k))
#     plot_img.set_data(counter1_frame)
#     plt.pause(0.010)
