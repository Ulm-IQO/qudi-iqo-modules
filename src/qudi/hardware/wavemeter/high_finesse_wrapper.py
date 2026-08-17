######################################################################################################
# @package wlmData
# @file wlmData.py
# @copyright HighFinesse GmbH.
# @date 2020.06.02
# @version 0.4
#
# Homepage: http://www.highfinesse.com/
#
# @brief Python wrapper for wlmData.dll.
#
# Changelog:
# ----------
# 2018.09.12
# v0.1 - Initial release
# 2018.09.14
# v0.2 - Constant values added
# 2018.09.15
# v0.3 - Constant values separated to wlmConst.py, LoadDLL() added
# 2020.06.02
# v0.4 - GetPattern... and GetAnalysisData argtypes adapted
# /

import warnings
from ctypes import c_bool, c_double, c_char_p, c_long, c_longlong, c_short, c_ulong, c_ushort, POINTER, windll

MIN_VERSION = 6491


def load_dll(dll_path='wlmData.dll'):
    dll = windll.LoadLibrary(dll_path)
    return dll


def setup_dll(dll):
    # LONG_PTR Instantiate(long RFC, long Mode, LONG_PTR P1, long P2)
    dll.Instantiate.argtypes = [c_long, c_long, POINTER(c_long), c_long]
    dll.Instantiate.restype = POINTER(c_long)

    # long WaitForWLMEvent(lref Mode, lref IntVal, dref DblVal)
    dll.WaitForWLMEvent.argtypes = [POINTER(c_long), POINTER(c_long), POINTER(c_double)]
    dll.WaitForWLMEvent.restype = c_long

    # long WaitForWLMEventEx(lref Ver, lref Mode, lref IntVal, dref DblVal, lref Res1)
    dll.WaitForWLMEventEx.argtypes = [POINTER(c_long), POINTER(c_long), POINTER(c_long), POINTER(c_double),
                                      POINTER(c_long)]
    dll.WaitForWLMEventEx.restype = c_long

    # long WaitForNextWLMEvent(lref Mode, lref IntVal, dref DblVal)
    dll.WaitForNextWLMEvent.argtypes = [POINTER(c_long), POINTER(c_long), POINTER(c_double)]
    dll.WaitForNextWLMEvent.restype = c_long

    # long WaitForNextWLMEventEx(lref Ver, lref Mode, lref IntVal, dref DblVal, lref Res1)
    dll.WaitForNextWLMEventEx.argtypes = [POINTER(c_long), POINTER(c_long), POINTER(c_long), POINTER(c_double),
                                          POINTER(c_long)]
    dll.WaitForNextWLMEventEx.restype = c_long

    # void ClearWLMEvents(void)
    dll.ClearWLMEvents.argtypes = []
    dll.ClearWLMEvents.restype = None

    # long ControlWLM(long Action, LONG_PTR App, long Ver)
    dll.ControlWLM.argtypes = [c_long, POINTER(c_long), c_long]
    dll.ControlWLM.restype = c_long

    # long ControlWLMEx(long Action, LONG_PTR App, long Ver, long Delay, long Res)
    dll.ControlWLMEx.argtypes = [c_long, POINTER(c_long), c_long, c_long, c_long]
    dll.ControlWLMEx.restype = c_long

    # __int64 SynchroniseWLM(long Mode, __int64 TS)
    dll.SynchroniseWLM.argtypes = [c_long, c_longlong]
    dll.SynchroniseWLM.restype = c_longlong

    # long SetMeasurementDelayMethod(long Mode, long Delay)
    dll.SetMeasurementDelayMethod.argtypes = [c_long, c_long]
    dll.SetMeasurementDelayMethod.restype = c_long

    # long SetWLMPriority(long PPC, long Res1, long Res2)
    dll.SetWLMPriority.argtypes = [c_long, c_long, c_long]
    dll.SetWLMPriority.restype = c_long

    # long PresetWLMIndex(long Ver)
    dll.PresetWLMIndex.argtypes = [c_long]
    dll.PresetWLMIndex.restype = c_long

    # long GetWLMVersion(long Ver)
    dll.GetWLMVersion.argtypes = [c_long]
    dll.GetWLMVersion.restype = c_long

    # long GetWLMIndex(long Ver)
    dll.GetWLMIndex.argtypes = [c_long]
    dll.GetWLMIndex.restype = c_long

    # long GetWLMCount(long V)
    dll.GetWLMCount.argtypes = [c_long]
    dll.GetWLMCount.restype = c_long

    # double GetWavelength(double WL)
    dll.GetWavelength.argtypes = [c_double]
    dll.GetWavelength.restype = c_double

    # double GetWavelength2(double WL2)
    dll.GetWavelength2.argtypes = [c_double]
    dll.GetWavelength2.restype = c_double

    # double GetWavelengthNum(long num, double WL)
    dll.GetWavelengthNum.argtypes = [c_long, c_double]
    dll.GetWavelengthNum.restype = c_double

    # double GetCalWavelength(long ba, double WL)
    dll.GetCalWavelength.argtypes = [c_long, c_double]
    dll.GetCalWavelength.restype = c_double

    # double GetCalibrationEffect(double CE)
    dll.GetCalibrationEffect.argtypes = [c_double]
    dll.GetCalibrationEffect.restype = c_double

    # double GetFrequency(double F)
    dll.GetFrequency.argtypes = [c_double]
    dll.GetFrequency.restype = c_double

    # double GetFrequency2(double F2)
    dll.GetFrequency2.argtypes = [c_double]
    dll.GetFrequency2.restype = c_double

    # double GetFrequencyNum(long num, double F)
    dll.GetFrequencyNum.argtypes = [c_long, c_double]
    dll.GetFrequencyNum.restype = c_double

    # double GetLinewidth(long Index, double LW)
    dll.GetLinewidth.argtypes = [c_long, c_double]
    dll.GetLinewidth.restype = c_double

    # double GetLinewidthNum(long num, double LW)
    dll.GetLinewidthNum.argtypes = [c_long, c_double]
    dll.GetLinewidthNum.restype = c_double

    # double GetDistance(double D)
    dll.GetDistance.argtypes = [c_double]
    dll.GetDistance.restype = c_double

    # double GetAnalogIn(double AI)
    dll.GetAnalogIn.argtypes = [c_double]
    dll.GetAnalogIn.restype = c_double

    # double GetTemperature(double T)
    dll.GetTemperature.argtypes = [c_double]
    dll.GetTemperature.restype = c_double

    # long SetTemperature(double T)
    dll.SetTemperature.argtypes = [c_double]
    dll.SetTemperature.restype = c_long

    # double GetPressure(double P)
    dll.GetPressure.argtypes = [c_double]
    dll.GetPressure.restype = c_double

    # long SetPressure(long Mode, double P)
    dll.SetPressure.argtypes = [c_long, c_double]
    dll.SetPressure.restype = c_long

    # double GetExternalInput(long Index, double I)
    dll.GetExternalInput.argtypes = [c_long, c_double]
    dll.GetExternalInput.restype = c_double

    # long SetExternalInput(long Index, double I)
    dll.SetExternalInput.argtypes = [c_long, c_double]
    dll.SetExternalInput.restype = c_long

    # long GetExtraSetting(long Index, lref lGet, dref dGet, sref sGet)
    dll.GetExtraSetting.argtypes = [c_long, POINTER(c_long), POINTER(c_double), c_char_p]
    dll.GetExtraSetting.restype = c_long

    # long SetExtraSetting(long Index, long lSet, double dSet, sref sSet)
    dll.SetExtraSetting.argtypes = [c_long, c_long, c_double, c_char_p]
    dll.SetExtraSetting.restype = c_long

    # unsigned short GetExposure(unsigned short E)
    dll.GetExposure.argtypes = [c_ushort]
    dll.GetExposure.restype = c_ushort

    # long SetExposure(unsigned short E)
    dll.SetExposure.argtypes = [c_ushort]
    dll.SetExposure.restype = c_long

    # unsigned short GetExposure2(unsigned short E2)
    dll.GetExposure2.argtypes = [c_ushort]
    dll.GetExposure2.restype = c_ushort

    # long SetExposure2(unsigned short E2)
    dll.SetExposure2.argtypes = [c_ushort]
    dll.SetExposure2.restype = c_long

    # long GetExposureNum(long num, long arr, long E)
    dll.GetExposureNum.argtypes = [c_long, c_long, c_long]
    dll.GetExposureNum.restype = c_long

    # long SetExposureNum(long num, long arr, long E)
    dll.SetExposureNum.argtypes = [c_long, c_long, c_long]
    dll.SetExposureNum.restype = c_long

    # double GetExposureNumEx(long num, long arr, double E)
    dll.GetExposureNumEx.argtypes = [c_long, c_long, c_double]
    dll.GetExposureNumEx.restype = c_double

    # long SetExposureNumEx(long num, long arr, double E)
    dll.SetExposureNumEx.argtypes = [c_long, c_long, c_double]
    dll.SetExposureNumEx.restype = c_long

    # bool GetExposureMode(bool EM)
    dll.GetExposureMode.argtypes = [c_bool]
    dll.GetExposureMode.restype = c_bool

    # long SetExposureMode(bool EM)
    dll.SetExposureMode.argtypes = [c_bool]
    dll.SetExposureMode.restype = c_long

    # long GetExposureModeNum(long num, bool EM)
    dll.GetExposureModeNum.argtypes = [c_long, c_bool]
    dll.GetExposureModeNum.restype = c_long

    # long SetExposureModeNum(long num, bool EM)
    dll.SetExposureModeNum.argtypes = [c_long, c_bool]
    dll.SetExposureModeNum.restype = c_long

    # long GetExposureRange(long ER)
    dll.GetExposureRange.argtypes = [c_long]
    dll.GetExposureRange.restype = c_long

    # long GetAutoExposureSetting(long num, long AES, lref iVal, dref dVal)
    dll.GetAutoExposureSetting.argtypes = [c_long, c_long, POINTER(c_long), POINTER(c_double)]
    dll.GetAutoExposureSetting.restype = c_long

    # long SetAutoExposureSetting(long num, long AES, long iVal, double dVal)
    dll.SetAutoExposureSetting.argtypes = [c_long, c_long, c_long, c_double]
    dll.SetAutoExposureSetting.restype = c_long

    # unsigned short GetResultMode(unsigned short RM)
    dll.GetResultMode.argtypes = [c_ushort]
    dll.GetResultMode.restype = c_ushort

    # long SetResultMode(unsigned short RM)
    dll.SetResultMode.argtypes = [c_ushort]
    dll.SetResultMode.restype = c_long

    # unsigned short GetRange(unsigned short R)
    dll.GetRange.argtypes = [c_ushort]
    dll.GetRange.restype = c_ushort

    # long SetRange(unsigned short R)
    dll.SetRange.argtypes = [c_ushort]
    dll.SetRange.restype = c_long

    # unsigned short GetPulseMode(unsigned short PM)
    dll.GetPulseMode.argtypes = [c_ushort]
    dll.GetPulseMode.restype = c_ushort

    # long SetPulseMode(unsigned short PM)
    dll.SetPulseMode.argtypes = [c_ushort]
    dll.SetPulseMode.restype = c_long

    # long GetPulseDelay(long PD)
    dll.GetPulseDelay.argtypes = [c_long]
    dll.GetPulseDelay.restype = c_long

    # long SetPulseDelay(long PD)
    dll.SetPulseDelay.argtypes = [c_long]
    dll.SetPulseDelay.restype = c_long

    # unsigned short GetWideMode(unsigned short WM)
    dll.GetWideMode.argtypes = [c_ushort]
    dll.GetWideMode.restype = c_ushort

    # long SetWideMode(unsigned short WM)
    dll.SetWideMode.argtypes = [c_ushort]
    dll.SetWideMode.restype = c_long

    # long GetDisplayMode(long DM)
    dll.GetDisplayMode.argtypes = [c_long]
    dll.GetDisplayMode.restype = c_long

    # long SetDisplayMode(long DM)
    dll.SetDisplayMode.argtypes = [c_long]
    dll.SetDisplayMode.restype = c_long

    # bool GetFastMode(bool FM)
    dll.GetFastMode.argtypes = [c_bool]
    dll.GetFastMode.restype = c_bool

    # long SetFastMode(bool FM)
    dll.SetFastMode.argtypes = [c_bool]
    dll.SetFastMode.restype = c_long

    # bool GetLinewidthMode(bool LM)
    dll.GetLinewidthMode.argtypes = [c_bool]
    dll.GetLinewidthMode.restype = c_bool

    # long SetLinewidthMode(bool LM)
    dll.SetLinewidthMode.argtypes = [c_bool]
    dll.SetLinewidthMode.restype = c_long

    # bool GetDistanceMode(bool DM)
    dll.GetDistanceMode.argtypes = [c_bool]
    dll.GetDistanceMode.restype = c_bool

    # long SetDistanceMode(bool DM)
    dll.SetDistanceMode.argtypes = [c_bool]
    dll.SetDistanceMode.restype = c_long

    # long GetSwitcherMode(long SM)
    dll.GetSwitcherMode.argtypes = [c_long]
    dll.GetSwitcherMode.restype = c_long

    # long SetSwitcherMode(long SM)
    dll.SetSwitcherMode.argtypes = [c_long]
    dll.SetSwitcherMode.restype = c_long

    # long GetSwitcherChannel(long CH)
    dll.GetSwitcherChannel.argtypes = [c_long]
    dll.GetSwitcherChannel.restype = c_long

    # long SetSwitcherChannel(long CH)
    dll.SetSwitcherChannel.argtypes = [c_long]
    dll.SetSwitcherChannel.restype = c_long

    # long GetSwitcherSignalStates(long Signal, lref Use, lref Show)
    dll.GetSwitcherSignalStates.argtypes = [c_long, POINTER(c_long), POINTER(c_long)]
    dll.GetSwitcherSignalStates.restype = c_long

    # long SetSwitcherSignalStates(long Signal, long Use, long Show)
    dll.SetSwitcherSignalStates.argtypes = [c_long, c_long, c_long]
    dll.SetSwitcherSignalStates.restype = c_long

    # long SetSwitcherSignal(long Signal, long Use, long Show)
    dll.SetSwitcherSignal.argtypes = [c_long, c_long, c_long]
    dll.SetSwitcherSignal.restype = c_long

    # long GetAutoCalMode(long ACM)
    dll.GetAutoCalMode.argtypes = [c_long]
    dll.GetAutoCalMode.restype = c_long

    # long SetAutoCalMode(long ACM)
    dll.SetAutoCalMode.argtypes = [c_long]
    dll.SetAutoCalMode.restype = c_long

    # long GetAutoCalSetting(long ACS, lref val, long Res1, lref Res2)
    dll.GetAutoCalSetting.argtypes = [c_long, POINTER(c_long), c_long, POINTER(c_long)]
    dll.GetAutoCalSetting.restype = c_long

    # long SetAutoCalSetting(long ACS, long val, long Res1, long Res2)
    dll.SetAutoCalSetting.argtypes = [c_long, c_long, c_long, c_long]
    dll.SetAutoCalSetting.restype = c_long

    # long GetActiveChannel(long Mode, lref Port, long Res1)
    dll.GetActiveChannel.argtypes = [c_long, POINTER(c_long), c_long]
    dll.GetActiveChannel.restype = c_long

    # long SetActiveChannel(long Mode, long Port, long CH, long Res1)
    dll.SetActiveChannel.argtypes = [c_long, c_long, c_long, c_long]
    dll.SetActiveChannel.restype = c_long

    # long GetChannelsCount(long C)
    dll.GetChannelsCount.argtypes = [c_long]
    dll.GetChannelsCount.restype = c_long

    # unsigned short GetOperationState(unsigned short OS)
    dll.GetOperationState.argtypes = [c_ushort]
    dll.GetOperationState.restype = c_ushort

    # long Operation(unsigned short Op)
    dll.Operation.argtypes = [c_ushort]
    dll.Operation.restype = c_long

    # long SetOperationFile(sref lpFile)
    dll.SetOperationFile.argtypes = [c_char_p]
    dll.SetOperationFile.restype = c_long

    # long Calibration(long Type, long Unit, double Value, long Channel)
    dll.Calibration.argtypes = [c_long, c_long, c_double, c_long]
    dll.Calibration.restype = c_long

    # long RaiseMeasurementEvent(long Mode)
    dll.RaiseMeasurementEvent.argtypes = [c_long]
    dll.RaiseMeasurementEvent.restype = c_long

    # long TriggerMeasurement(long Action)
    dll.TriggerMeasurement.argtypes = [c_long]
    dll.TriggerMeasurement.restype = c_long

    # long GetTriggerState(long TS)
    dll.GetTriggerState.argtypes = [c_long]
    dll.GetTriggerState.restype = c_long

    # long GetInterval(long I)
    dll.GetInterval.argtypes = [c_long]
    dll.GetInterval.restype = c_long

    # long SetInterval(long I)
    dll.SetInterval.argtypes = [c_long]
    dll.SetInterval.restype = c_long

    # bool GetIntervalMode(bool IM)
    dll.GetIntervalMode.argtypes = [c_bool]
    dll.GetIntervalMode.restype = c_bool

    # long SetIntervalMode(bool IM)
    dll.SetIntervalMode.argtypes = [c_bool]
    dll.SetIntervalMode.restype = c_long

    # long GetBackground(long BG)
    dll.GetBackground.argtypes = [c_long]
    dll.GetBackground.restype = c_long

    # long SetBackground(long BG)
    dll.SetBackground.argtypes = [c_long]
    dll.SetBackground.restype = c_long

    # long GetAveragingSettingNum(long num, long AS, long Value)
    dll.GetAveragingSettingNum.argtypes = [c_long, c_long, c_long]
    dll.GetAveragingSettingNum.restype = c_long

    # long SetAveragingSettingNum(long num, long AS, long Value)
    dll.SetAveragingSettingNum.argtypes = [c_long, c_long, c_long]
    dll.SetAveragingSettingNum.restype = c_long

    # bool GetLinkState(bool LS)
    dll.GetLinkState.argtypes = [c_bool]
    dll.GetLinkState.restype = c_bool

    # long SetLinkState(bool LS)
    dll.SetLinkState.argtypes = [c_bool]
    dll.SetLinkState.restype = c_long

    # void LinkSettingsDlg(void)
    dll.LinkSettingsDlg.argtypes = []
    dll.LinkSettingsDlg.restype = None

    # long GetPatternItemSize(long Index)
    dll.GetPatternItemSize.argtypes = [c_long]
    dll.GetPatternItemSize.restype = c_long

    # long GetPatternItemCount(long Index)
    dll.GetPatternItemCount.argtypes = [c_long]
    dll.GetPatternItemCount.restype = c_long

    # ULONG_PTR GetPattern(long Index)
    dll.GetPattern.argtypes = [c_long]
    dll.GetPattern.restype = POINTER(c_ulong)

    # ULONG_PTR GetPatternNum(long Chn, long Index)
    dll.GetPatternNum.argtypes = [c_long, c_long]
    dll.GetPatternNum.restype = POINTER(c_ulong)

    # long GetPatternData(long Index, ULONG_PTR PArray)
    dll.GetPatternData.argtypes = [c_long, POINTER(c_short)]
    dll.GetPatternData.restype = c_long

    # long GetPatternDataNum(long Chn, long Index, ULONG_PTR PArray)
    dll.GetPatternDataNum.argtypes = [c_long, c_long, POINTER(c_short)]
    dll.GetPatternDataNum.restype = c_long

    # long SetPattern(long Index, long iEnable)
    dll.SetPattern.argtypes = [c_long, c_long]
    dll.SetPattern.restype = c_long

    # long SetPatternData(long Index, ULONG_PTR PArray)
    dll.SetPatternData.argtypes = [c_long, POINTER(c_ulong)]
    dll.SetPatternData.restype = c_long

    # bool GetAnalysisMode(bool AM)
    dll.GetAnalysisMode.argtypes = [c_bool]
    dll.GetAnalysisMode.restype = c_bool

    # long SetAnalysisMode(bool AM)
    dll.SetAnalysisMode.argtypes = [c_bool]
    dll.SetAnalysisMode.restype = c_long

    # long GetAnalysisItemSize(long Index)
    dll.GetAnalysisItemSize.argtypes = [c_long]
    dll.GetAnalysisItemSize.restype = c_long

    # long GetAnalysisItemCount(long Index)
    dll.GetAnalysisItemCount.argtypes = [c_long]
    dll.GetAnalysisItemCount.restype = c_long

    # ULONG_PTR GetAnalysis(long Index)
    dll.GetAnalysis.argtypes = [c_long]
    dll.GetAnalysis.restype = POINTER(c_ulong)

    # long GetAnalysisData(long Index, ULONG_PTR PArray)
    dll.GetAnalysisData.argtypes = [c_long, POINTER(c_double)]
    dll.GetAnalysisData.restype = c_long

    # long SetAnalysis(long Index, long iEnable)
    dll.SetAnalysis.argtypes = [c_long, c_long]
    dll.SetAnalysis.restype = c_long

    # long GetMinPeak(long M1)
    dll.GetMinPeak.argtypes = [c_long]
    dll.GetMinPeak.restype = c_long

    # long GetMinPeak2(long M2)
    dll.GetMinPeak2.argtypes = [c_long]
    dll.GetMinPeak2.restype = c_long

    # long GetMaxPeak(long X1)
    dll.GetMaxPeak.argtypes = [c_long]
    dll.GetMaxPeak.restype = c_long

    # long GetMaxPeak2(long X2)
    dll.GetMaxPeak2.argtypes = [c_long]
    dll.GetMaxPeak2.restype = c_long

    # long GetAvgPeak(long A1)
    dll.GetAvgPeak.argtypes = [c_long]
    dll.GetAvgPeak.restype = c_long

    # long GetAvgPeak2(long A2)
    dll.GetAvgPeak2.argtypes = [c_long]
    dll.GetAvgPeak2.restype = c_long

    # long SetAvgPeak(long PA)
    dll.SetAvgPeak.argtypes = [c_long]
    dll.SetAvgPeak.restype = c_long

    # long GetAmplitudeNum(long num, long Index, long A)
    dll.GetAmplitudeNum.argtypes = [c_long, c_long, c_long]
    dll.GetAmplitudeNum.restype = c_long

    # double GetIntensityNum(long num, double I)
    dll.GetIntensityNum.argtypes = [c_long, c_double]
    dll.GetIntensityNum.restype = c_double

    # double GetPowerNum(long num, double P)
    dll.GetPowerNum.argtypes = [c_long, c_double]
    dll.GetPowerNum.restype = c_double

    # unsigned short GetDelay(unsigned short D)
    dll.GetDelay.argtypes = [c_ushort]
    dll.GetDelay.restype = c_ushort

    # long SetDelay(unsigned short D)
    dll.SetDelay.argtypes = [c_ushort]
    dll.SetDelay.restype = c_long

    # unsigned short GetShift(unsigned short S)
    dll.GetShift.argtypes = [c_ushort]
    dll.GetShift.restype = c_ushort

    # long SetShift(unsigned short S)
    dll.SetShift.argtypes = [c_ushort]
    dll.SetShift.restype = c_long

    # unsigned short GetShift2(unsigned short S2)
    dll.GetShift2.argtypes = [c_ushort]
    dll.GetShift2.restype = c_ushort

    # long SetShift2(unsigned short S2)
    dll.SetShift2.argtypes = [c_ushort]
    dll.SetShift2.restype = c_long

    # bool GetDeviationMode(bool DM)
    dll.GetDeviationMode.argtypes = [c_bool]
    dll.GetDeviationMode.restype = c_bool

    # long SetDeviationMode(bool DM)
    dll.SetDeviationMode.argtypes = [c_bool]
    dll.SetDeviationMode.restype = c_long

    # double GetDeviationReference(double DR)
    dll.GetDeviationReference.argtypes = [c_double]
    dll.GetDeviationReference.restype = c_double

    # long SetDeviationReference(double DR)
    dll.SetDeviationReference.argtypes = [c_double]
    dll.SetDeviationReference.restype = c_long

    # long GetDeviationSensitivity(long DS)
    dll.GetDeviationSensitivity.argtypes = [c_long]
    dll.GetDeviationSensitivity.restype = c_long

    # long SetDeviationSensitivity(long DS)
    dll.SetDeviationSensitivity.argtypes = [c_long]
    dll.SetDeviationSensitivity.restype = c_long

    # double GetDeviationSignal(double DS)
    dll.GetDeviationSignal.argtypes = [c_double]
    dll.GetDeviationSignal.restype = c_double

    # double GetDeviationSignalNum(long Port, double DS)
    dll.GetDeviationSignalNum.argtypes = [c_long, c_double]
    dll.GetDeviationSignalNum.restype = c_double

    # long SetDeviationSignal(double DS)
    dll.SetDeviationSignal.argtypes = [c_double]
    dll.SetDeviationSignal.restype = c_long

    # long SetDeviationSignalNum(long Port, double DS)
    dll.SetDeviationSignalNum.argtypes = [c_long, c_double]
    dll.SetDeviationSignalNum.restype = c_long

    # double RaiseDeviationSignal(long iType, double dSignal)
    dll.RaiseDeviationSignal.argtypes = [c_long, c_double]
    dll.RaiseDeviationSignal.restype = c_double

    # long GetPIDCourse(sref PIDC)
    dll.GetPIDCourse.argtypes = [c_char_p]
    dll.GetPIDCourse.restype = c_long

    # long SetPIDCourse(sref PIDC)
    dll.SetPIDCourse.argtypes = [c_char_p]
    dll.SetPIDCourse.restype = c_long

    # long GetPIDCourseNum(long Port, sref PIDC)
    dll.GetPIDCourseNum.argtypes = [c_long, c_char_p]
    dll.GetPIDCourseNum.restype = c_long

    # long SetPIDCourseNum(long Port, sref PIDC)
    dll.SetPIDCourseNum.argtypes = [c_long, c_char_p]
    dll.SetPIDCourseNum.restype = c_long

    # long GetPIDSetting(long PS, long Port, lref iSet, dref dSet)
    dll.GetPIDSetting.argtypes = [c_long, c_long, POINTER(c_long), POINTER(c_double)]
    dll.GetPIDSetting.restype = c_long

    # long SetPIDSetting(long PS, long Port, long iSet, double dSet)
    dll.SetPIDSetting.argtypes = [c_long, c_long, c_long, c_double]
    dll.SetPIDSetting.restype = c_long

    # long GetLaserControlSetting(long PS, long Port, lref iSet, dref dSet, sref sSet)
    dll.GetLaserControlSetting.argtypes = [c_long, c_long, POINTER(c_long), POINTER(c_double), c_char_p]
    dll.GetLaserControlSetting.restype = c_long

    # long SetLaserControlSetting(long PS, long Port, long iSet, double dSet, sref sSet)
    dll.SetLaserControlSetting.argtypes = [c_long, c_long, c_long, c_double, c_char_p]
    dll.SetLaserControlSetting.restype = c_long

    # long ClearPIDHistory(long Port)
    dll.ClearPIDHistory.argtypes = [c_long]
    dll.ClearPIDHistory.restype = c_long

    # double ConvertUnit(double Val, long uFrom, long uTo)
    dll.ConvertUnit.argtypes = [c_double, c_long, c_long]
    dll.ConvertUnit.restype = c_double

    # double ConvertDeltaUnit(double Base, double Delta, long uBase, long uFrom, long uTo)
    dll.ConvertDeltaUnit.argtypes = [c_double, c_double, c_long, c_long, c_long]
    dll.ConvertDeltaUnit.restype = c_double

    # bool GetReduced(bool R)
    dll.GetReduced.argtypes = [c_bool]
    dll.GetReduced.restype = c_bool

    # long SetReduced(bool R)
    dll.SetReduced.argtypes = [c_bool]
    dll.SetReduced.restype = c_long

    # unsigned short GetScale(unsigned short S)
    dll.GetScale.argtypes = [c_ushort]
    dll.GetScale.restype = c_ushort

    # long SetScale(unsigned short S)
    dll.SetScale.argtypes = [c_ushort]
    dll.SetScale.restype = c_long
