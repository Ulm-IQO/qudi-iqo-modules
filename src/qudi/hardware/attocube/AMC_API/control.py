class Control:
    def __init__(self, device):
        self.device = device
        self.interface_name = "com.attocube.amc.control"

    def setActorParametersJson(self, axis, json_dict):
        # type: (int, str) -> ()
        """
        Select and override a positioner out of the Current default list only override given parameters set others default

        Parameters:
            axis: [0|1|2]
            json_dict: dict with override params
                    
        """
        
        response = self.device.request(self.interface_name + ".setActorParametersJson", [axis, json_dict, ])
        self.device.handleError(response)
        return                 

    def setEoTParameters(self, axis, minAvgStepSize_nm, numOfAvgedSteps):
        # type: (int, int, int) -> ()
        """
        Sets the two parameters, that define the behavior of the eot detection (how sensitive respectively how robust it works)

        Parameters:
            axis: [0|1|2] (will be ignored, if minAvgStepSize equals nil)
            minAvgStepSize_nm: [type=int] this correpsonds to the "eot_threshold"-parameter
            numOfAvgedSteps: [type=int] this defines the number of steps, over which the average step size is calculated
                    
        """
        
        response = self.device.request(self.interface_name + ".setEoTParameters", [axis, minAvgStepSize_nm, numOfAvgedSteps, ])
        self.device.handleError(response)
        return                 

    def getEoTParameters(self, axis):
        # type: (int) -> (int, int)
        """
        Gets the two parameters, that define the behavior of the eot detection (how sensitive respectively how robust it works)

        Parameters:
            axis: [0|1|2] (will be ignored, if minAvgStepSize equals nil)
                    
        Returns:
            err: err
            value_minAvgStepSize_nmtypeint: minAvgStepSize_nm[type=int] this correpsonds to the "eot_threshold"-parameter
            value_numOfAvgedStepstypeint: numOfAvgedSteps[type=int] this defines the number of steps, over which the average step size is calculated
                    
        """
        
        response = self.device.request(self.interface_name + ".getEoTParameters", [axis, ])
        self.device.handleError(response)
        return response[1], response[2]                

    def getMotionControlThreshold(self, axis):
        # type: (int) -> (int)
        """
        This function gets the threshold range within the closed-loop controlled movement stops to regulate.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_threshold: threshold in pm
                    
        """
        
        response = self.device.request(self.interface_name + ".getMotionControlThreshold", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setMotionControlThreshold(self, axis, threshold):
        # type: (int, int) -> ()
        """
        This function sets the threshold range within the closed-loop controlled movement stops to regulate.

        Parameters:
            axis: [0|1|2]
            threshold: in pm
                    
        """
        
        response = self.device.request(self.interface_name + ".setMotionControlThreshold", [axis, threshold, ])
        self.device.handleError(response)
        return                 

    def getCrosstalkThreshold(self, axis):
        # type: (int) -> (int, int)
        """
        This function gets the threshold range and slip phase time which is used while moving another axis

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_range: range in pm
            value_time: time after slip phase which is waited until the controller is acting again in microseconds
                    
        """
        
        response = self.device.request(self.interface_name + ".getCrosstalkThreshold", [axis, ])
        self.device.handleError(response)
        return response[1], response[2]                

    def setCrosstalkThreshold(self, axis, threshold, slipphasetime):
        # type: (int, int, int) -> ()
        """
        This function sets the threshold range to avoid axis-crosstalk and slip phase time which is used while moving another axis

        Parameters:
            axis: [0|1|2]
            threshold: [max:2147483647][pm]; has to be greater than the motion-control-threshold
            slipphasetime: [min=0,max=65535][us] time after slip phase which is waited until the controller acts again
                    
        """
        
        response = self.device.request(self.interface_name + ".setCrosstalkThreshold", [axis, threshold, slipphasetime, ])
        self.device.handleError(response)
        return                 

    def getSensorDirection(self, axis):
        # type: (int) -> (bool)
        """
        This function gets whether the IDS sensor source of closed loop is inverted It is only available when the feature AMC/IDS closed loop has been activated

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_inverted: inverted boolen
                    
        """
        
        response = self.device.request(self.interface_name + ".getSensorDirection", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setSensorDirection(self, axis, inverted):
        # type: (int, bool) -> ()
        """
        This function sets the IDS sensor source of closed loop to inverted when true.

        Parameters:
            axis: [0|1|2]
            inverted: 
                    
        """
        
        response = self.device.request(self.interface_name + ".setSensorDirection", [axis, inverted, ])
        self.device.handleError(response)
        return                 

    def getExternalSensor(self, axis):
        # type: (int) -> (bool)
        """
        This function gets whether the sensor source of closed loop is IDS It is only available when the feature AMC/IDS closed loop has been activated

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            enabled: enabled
                    
        """
        
        response = self.device.request(self.interface_name + ".getExternalSensor", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setExternalSensor(self, axis, enabled, ignoreFunctionError=True):
        # type: (int, bool) -> ()
        """
        This function sets the sensor source of closed loop to the IDS when enabled.

        Parameters:
            axis: [0|1|2]
            enabled: 
                    
        """
        
        response = self.device.request(self.interface_name + ".setExternalSensor", [axis, enabled, ])
        self.device.handleError(response, ignoreFunctionError)
        return response[0]                

    def getControlOutput(self, axis):
        # type: (int) -> (bool)
        """
        This function gets the status of the output relays of the selected axis.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_enabled: enabled power status (true = enabled,false = disabled)
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlOutput", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlOutput(self, axis, enable):
        # type: (int, bool) -> ()
        """
        This function sets the status of the output relays of the selected axis.

        Parameters:
            axis: [0|1|2]
            enable: true: enable drives, false: disable drives
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlOutput", [axis, enable, ])
        self.device.handleError(response)
        return                 

    def setAutoMeasure(self, axis, enable):
        # type: (int, bool) -> ()
        """
        This function enables/disables the automatic C/R measurement on axis enable

        Parameters:
            axis: [0|1|2]
            enable: true: enable automeasurement, false: disable automeasurement
                    
        """
        
        response = self.device.request(self.interface_name + ".setAutoMeasure", [axis, enable, ])
        self.device.handleError(response)
        return                 

    def getAutoMeasure(self, axis):
        # type: (int) -> (bool)
        """
        This function returns if the automeasurement on axis enable is enabled

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_enable: enable true: enable automeasurement, false: disable automeasurement
                    
        """
        
        response = self.device.request(self.interface_name + ".getAutoMeasure", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setActorSensitivity(self, axis, sensitivity):
        # type: (int, int) -> ()
        """
        Control the actor parameter closed loop sensitivity

        Parameters:
            axis: [0|1|2]
            sensitivity: 
                    
        """
        
        response = self.device.request(self.interface_name + ".setActorSensitivity", [axis, sensitivity, ])
        self.device.handleError(response)
        return                 

    def getActorSensitivity(self, axis):
        # type: (int) -> (int)
        """
        Get the setting for the actor parameter sensitivity

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            sensitivity: sensitivity
                    
        """
        
        response = self.device.request(self.interface_name + ".getActorSensitivity", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getActorParametersActorName(self, axis):
        # type: (int) -> (str)
        """
        Control the actors parameter: actor name

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            actorname: actorname
                    
        """
        
        response = self.device.request(self.interface_name + ".getActorParametersActorName", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setActorParametersByName(self, axis, actorname):
        # type: (int, str) -> ()
        """
        This function sets the name for the positioner on the selected axis.

        Parameters:
            axis: [0|1|2]
            actorname: name of the actor
                    
        """
        
        response = self.device.request(self.interface_name + ".setActorParametersByName", [axis, actorname, ])
        self.device.handleError(response)
        return                 

    def getCurrentOutputVoltage(self, axis):
        # type: (int) -> (int)
        """
        This function gets the current Voltage which is applied to the Piezo

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_amplitude: amplitude in mV
                    
        """
        
        response = self.device.request(self.interface_name + ".getCurrentOutputVoltage", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getControlAmplitude(self, axis):
        # type: (int) -> (int)
        """
        This function gets the amplitude of the actuator signal of the selected axis.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_amplitude: amplitude in mV
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlAmplitude", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlAmplitude(self, axis, amplitude):
        # type: (int, int) -> ()
        """
        This function sets the amplitude of the actuator signal of the selected axis.

        Parameters:
            axis: [0|1|2]
            amplitude: in mV
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlAmplitude", [axis, amplitude, ])
        self.device.handleError(response)
        return                 

    def setControlFrequency(self, axis, frequency):
        # type: (int, int) -> ()
        """
        This function sets the frequency of the actuator signal of the selected axis.

        Parameters:
            axis: [0|1|2]
            frequency: in  mHz
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlFrequency", [axis, frequency, ])
        self.device.handleError(response)
        return                 

    def getControlFrequency(self, axis):
        # type: (int) -> (int)
        """
        This function gets the frequency of the actuator signal of the selected axis.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_frequency: frequency in mHz
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlFrequency", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getActorType(self, axis):
        # type: (int) -> (int)
        """
        This function gets the type of the positioner of the selected axis.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_actor_type: actor_type  0: linear, 1: rotator, 2: goniometer
                    
        """
        
        response = self.device.request(self.interface_name + ".getActorType", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getActorName(self, axis):
        # type: (int) -> (str)
        """
        This function gets the name of the positioner of the selected axis.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            actor_name: actor_name
                    
        """
        
        response = self.device.request(self.interface_name + ".getActorName", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setReset(self, axis):
        # type: (int) -> ()
        """
        This function resets the actual position of the selected axis given by the NUM sensor to zero and marks the reference position as invalid.

        Parameters:
            axis: [0|1|2]
                    
        """
        
        response = self.device.request(self.interface_name + ".setReset", [axis, ])
        self.device.handleError(response)
        return                 

    def setControlMove(self, axis, enable):
        # type: (int, bool) -> ()
        """
        This function sets the approach of the selected axis’ positioner to the target position.

        Parameters:
            axis: [0|1|2]
            enable: boolean true: eanble the approach , false: disable the approach
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlMove", [axis, enable, ])
        self.device.handleError(response)
        return                 

    def getControlMove(self, axis):
        # type: (int) -> (bool)
        """
        This function gets the approach of the selected axis’ positioner to the target position.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_enable: enable boolean true: closed loop control enabled, false: closed loop control disabled
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlMove", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def searchReferencePosition(self, axis):
        # type: (int) -> ()
        """
        This function searches for the reference position of the selected axis.

        Parameters:
            axis: [0|1|2]
                    
        """
        
        response = self.device.request(self.interface_name + ".searchReferencePosition", [axis, ])
        self.device.handleError(response)
        return                 

    def getReferencePosition(self, axis):
        # type: (int) -> (int)
        """
        This function gets the reference position of the selected axis.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_position: position: For linear type actors the position is defined in nm for goniometer an rotator type actors it is µ°.
                    
        """
        
        response = self.device.request(self.interface_name + ".getReferencePosition", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getControlReferenceAutoUpdate(self, axis):
        # type: (int) -> (bool)
        """
        This function gets the status of whether the reference position is updated when the reference mark is hit.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_enabled: enabled boolen
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlReferenceAutoUpdate", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlReferenceAutoUpdate(self, axis, enable):
        # type: (int, bool) -> ()
        """
        This function sets the status of whether the reference position is updated when the reference mark is hit.

        Parameters:
            axis: [0|1|2]
            enable: boolean
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlReferenceAutoUpdate", [axis, enable, ])
        self.device.handleError(response)
        return                 

    def getControlAutoReset(self, axis):
        # type: (int) -> (bool)
        """
        This function resets the position every time the reference position is detected.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_enabled: enabled boolean
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlAutoReset", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlAutoReset(self, axis, enable):
        # type: (int, bool) -> ()
        """
        This function resets the position every time the reference position is detected.

        Parameters:
            axis: [0|1|2]
            enable: boolean
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlAutoReset", [axis, enable, ])
        self.device.handleError(response)
        return                 

    def getControlTargetRange(self, axis):
        # type: (int) -> (int)
        """
        This function gets the range around the target position in which the flag "In Target Range" becomes active.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_targetrange: targetrange in nm
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlTargetRange", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlTargetRange(self, axis, range):
        # type: (int, int) -> ()
        """
        This function sets the range around the target position in which the flag "In Target Range" (see VIII.7.a) becomes active.

        Parameters:
            axis: [0|1|2]
            range: in nm
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlTargetRange", [axis, range, ])
        self.device.handleError(response)
        return                 

    def MultiAxisPositioning(self, set1, set2, set3, target1, target2, target3):
        # type: (bool, bool, bool, int, int, int) -> (bool, bool, bool, int, int, int, int, int, int)
        """
        Simultaneously set 3 axes positions and get positions to minimize network latency

        Parameters:
            set1: axis1 otherwise pos1 target is ignored
            set2: axis2 otherwise pos2 target is ignored
            set3: axis3 otherwise pos3 target is ignored
            target1: target position of axis 1
            target2: target position of axis 2
            target3: target position of axis 3
                    
        Returns:
            errNo: errNo
            value_ref1: ref1 Status of axis 1
            value_ref2: ref2 Status of axis 2
            value_ref3: ref3 Status of axis 3
            value_refpos1: refpos1 reference Position of axis 1
            value_refpos2: refpos2 reference Position of axis 2
            value_refpos3: refpos3 reference Position of axis 3
            value_pos1: pos1 position of axis 1
            value_pos2: pos2 position of axis 2
            value_pos3: pos3 position of axis 3
                    
        """
        
        response = self.device.request(self.interface_name + ".MultiAxisPositioning", [set1, set2, set3, target1, target2, target3, ])
        self.device.handleError(response)
        return response[1], response[2], response[3], response[4], response[5], response[6], response[7], response[8], response[9]                

    def MultiAxisPositioningWithTime(self, set1, set2, set3, target1, target2, target3):
        # type: (bool, bool, bool, int, int, int) -> (bool, bool, bool, int, int, int, int, int, int, float, float, float)
        """
        Simultaneously set 3 axes positions and get positions to minimize network latency

        Parameters:
            set1: axis1 otherwise pos1 target is ignored
            set2: axis2 otherwise pos2 target is ignored
            set3: axis3 otherwise pos3 target is ignored
            target1: target position of axis 1
            target2: target position of axis 2
            target3: target position of axis 3
                    
        Returns:
            errNo: errNo
            value_ref1: ref1 Status of axis 1
            value_ref2: ref2 Status of axis 2
            value_ref3: ref3 Status of axis 3
            value_refpos1: refpos1 reference Position of axis 1
            value_refpos2: refpos2 reference Position of axis 2
            value_refpos3: refpos3 reference Position of axis 3
            value_pos1: pos1 position of axis 1
            value_pos2: pos2 position of axis 2
            value_pos3: pos3 position of axis 3
            value_time1: time1 timestamp of axis 1
            value_time2: time2 timestamp of axis 2
            value_time3: time3 timestamp of axis 3
                    
        """
        
        response = self.device.request(self.interface_name + ".MultiAxisPositioningWithTime", [set1, set2, set3, target1, target2, target3, ])
        self.device.handleError(response)
        return response[1], response[2], response[3], response[4], response[5], response[6], response[7], response[8], response[9], response[10], response[11], response[12]                

    def getPositionsAndVoltages(self):
        # type: () -> (int, int, int, int, int, int)
        """
        Simultaneously get 3 axes positions as well as the DC offset to maximize sampling rate over network
        Returns:
            errNo: errNo
            value_pos1: pos1 position of axis 1
            value_pos2: pos2 position of axis 2
            value_pos3: pos3 position of axis 3
            value_val1: val1 dc voltage of of axis 1 in mV
            value_val2: val2 dc voltage of of axis 2 in mV
            value_val3: val3 dc voltage of of axis 3 in mV
                    
        """
        
        response = self.device.request(self.interface_name + ".getPositionsAndVoltages")
        self.device.handleError(response)
        return response[1], response[2], response[3], response[4], response[5], response[6]                

    def getStatusMovingAllAxes(self):
        # type: () -> (int, int, int)
        """
        Get Status of all axes, see getStatusMoving for coding of the values
        Returns:
            errNo: errNo
            value_moving1: moving1 status of axis 1
            value_moving2: moving2 status of axis 2
            value_moving3: moving3 status of axis 3
                    
        """
        
        response = self.device.request(self.interface_name + ".getStatusMovingAllAxes")
        self.device.handleError(response)
        return response[1], response[2], response[3]                

    def setControlFixOutputVoltage(self, axis, amplitude_mv):
        # type: (int, int) -> ()
        """
        This function sets the DC level output of the selected axis.

        Parameters:
            axis: [0|1|2]
            amplitude_mv: in mV
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlFixOutputVoltage", [axis, amplitude_mv, ])
        self.device.handleError(response)
        return                 

    def getControlFixOutputVoltage(self, axis):
        # type: (int) -> (int)
        """
        This function gets the DC level output of the selected axis.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_amplitude_mv: amplitude_mv in mV
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlFixOutputVoltage", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setSensorEnabled(self, axis, value):
        # type: (int, bool) -> ()
        """
        Set sensor power supply status, can be switched off to save heat generated by sensor [NUM or RES] Positions retrieved will be invalid when activating this, so closed-loop control should be switched off beforehand

        Parameters:
            axis: [0|1|2]
            value: true if enabled, false otherwise
                    
        """
        
        response = self.device.request(self.interface_name + ".setSensorEnabled", [axis, value, ])
        self.device.handleError(response)
        return                 

    def getSensorEnabled(self, axis):
        # type: (int) -> (bool)
        """
        Get sensot power supply status

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_value: value true if enabled, false otherwise
                    
        """
        
        response = self.device.request(self.interface_name + ".getSensorEnabled", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getFinePositioningRange(self, axis):
        # type: (int) -> (int)
        """
        This function gets the fine positioning DC-range

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_range: range in nm
                    
        """
        
        response = self.device.request(self.interface_name + ".getFinePositioningRange", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setFinePositioningRange(self, axis, range):
        # type: (int, int) -> ()
        """
        This function sets the fine positioning DC-range

        Parameters:
            axis: [0|1|2]
            range: in nm
                    
        """
        
        response = self.device.request(self.interface_name + ".setFinePositioningRange", [axis, range, ])
        self.device.handleError(response)
        return                 

    def getFinePositioningSlewRate(self, axis):
        # type: (int) -> (int)
        """
        This function gets the fine positioning slew rate

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_slewrate: slewrate [0|1|2|3]
                    
        """
        
        response = self.device.request(self.interface_name + ".getFinePositioningSlewRate", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setFinePositioningSlewRate(self, axis, slewrate):
        # type: (int, int) -> ()
        """
        This function sets the fine positioning slew rate

        Parameters:
            axis: [0|1|2]
            slewrate: [0|1|2|3]
                    
        """
        
        response = self.device.request(self.interface_name + ".setFinePositioningSlewRate", [axis, slewrate, ])
        self.device.handleError(response)
        return                 

