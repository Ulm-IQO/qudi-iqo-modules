class Move:
    def __init__(self, device):
        self.device = device
        self.interface_name = "com.attocube.amc.move"

    def setSingleStep(self, axis, backward):
        # type: (int, bool) -> ()
        """
        This function triggers one step on the selected axis in desired direction.

        Parameters:
            axis: [0|1|2]
            backward: Selects the desired direction. False triggers a forward step, true a backward step
                    
        """
        
        response = self.device.request(self.interface_name + ".setSingleStep", [axis, backward, ])
        self.device.handleError(response)
        return                 

    def setNSteps(self, axis, backward, step):
        # type: (int, bool, int) -> ()
        """
        This function triggers n steps on the selected axis in desired direction.

        Parameters:
            axis: [0|1|2]
            backward: Selects the desired direction. False triggers a forward step, true a backward step
            step: number of step
                    
        """
        
        response = self.device.request(self.interface_name + ".setNSteps", [axis, backward, step, ])
        self.device.handleError(response)
        return                 

    def writeNSteps(self, axis, step):
        # type: (int, int) -> ()
        """
        Sets the number of steps to perform on stepwise movement.

        Parameters:
            axis: [0|1|2]
            step: number of step
                    
        """
        
        response = self.device.request(self.interface_name + ".writeNSteps", [axis, step, ])
        self.device.handleError(response)
        return                 

    def performNSteps(self, axis, backward):
        # type: (int, bool) -> ()
        """
        Perform the OL command for N steps

        Parameters:
            axis: [0|1|2]
            backward: Selects the desired direction. False triggers a forward step, true a backward step
                    
        """
        
        response = self.device.request(self.interface_name + ".performNSteps", [axis, backward, ])
        self.device.handleError(response)
        return                 

    def getNSteps(self, axis):
        # type: (int) -> (int)
        """
        This function gets the number of Steps in desired direction.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            nbrstep: nbrstep
                    
        """
        
        response = self.device.request(self.interface_name + ".getNSteps", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlContinuousFwd(self, axis, enable):
        # type: (int, bool) -> ()
        """
        This function sets a continuous movement on the selected axis in positive direction.

        Parameters:
            axis: [0|1|2]
            enable: If enabled a present movement in the opposite direction is stopped. The parameter "false" stops all movement of the axis regardless its direction.
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlContinuousFwd", [axis, enable, ])
        self.device.handleError(response)
        return                 

    def getControlContinuousFwd(self, axis):
        # type: (int) -> (bool)
        """
        This function gets the axis’ movement status in positive direction.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_enabled: enabled true if movement Fwd is active, false otherwise
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlContinuousFwd", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlContinuousBkwd(self, axis, enable):
        # type: (int, bool) -> ()
        """
        This function sets a continuous movement on the selected axis in backward direction.

        Parameters:
            axis: [0|1|2]
            enable: If enabled a present movement in the opposite direction is stopped. The parameter "false" stops all movement of the axis regardless its direction
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlContinuousBkwd", [axis, enable, ])
        self.device.handleError(response)
        return                 

    def getControlContinuousBkwd(self, axis):
        # type: (int) -> (bool)
        """
        This function gets the axis’ movement status in backward direction.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_enabled: enabled true if movement backward is active , false otherwise
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlContinuousBkwd", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getControlTargetPosition(self, axis):
        # type: (int) -> (float)
        """
        This function gets the target position for the movement on the selected axis.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_position: position defined in nm for goniometer an rotator type actors it is µ°.
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlTargetPosition", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlTargetPosition(self, axis, target):
        # type: (int, float) -> ()
        """
        This function sets the target position for the movement on the selected axis.

        Parameters:
            axis: [0|1|2]
            target: absolute position : For linear type actors the position is defined in nm for goniometer an rotator type actors it is µ°.
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlTargetPosition", [axis, target, ])
        self.device.handleError(response)
        return                 

    def moveReference(self, axis):
        # type: (int) -> ()
        """
        This function starts an approach to the reference position.

        Parameters:
            axis: [0|1|2]
                    
        """
        
        response = self.device.request(self.interface_name + ".moveReference", [axis, ])
        self.device.handleError(response)
        return                 

    def getPosition(self, axis):
        # type: (int) -> (float)
        """
        This function gets the current position of the positioner on the selected axis.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_position: position defined in nm for goniometer an rotator type actors it is µ°.
                    
        """
        
        response = self.device.request(self.interface_name + ".getPosition", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getPositionWithTime(self, axis):
        # type: (int) -> (float, float)
        """
        This function gets the current position of the positioner and provides time-information to the position.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_Monotnoic_time_usec: Monotnoic_time_usec: elapsed time in microseconds since last reboot of device
            value_position: position defined in nm for goniometer an rotator type actors it is µ°.
                    
        """
        
        response = self.device.request(self.interface_name + ".getPositionWithTime", [axis, ])
        self.device.handleError(response)
        return response[1], response[2]                

    def getPositionWithTime_32Bit(self, axis):
        # type: (int) -> (int, int, float)
        """
        This function gets the current position of the positioner and provides time-information to the position.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_Monotonic_time_sec: Monotonic_time_sec: seconds passed since last reboot of device
            value_Monotonic_time_nsec: Monotonic_time_nsec: fractional seconds of Monotonic_time_sec
            value_position: position defined in nm for goniometer an rotator type actors it is µ°.
                    
        """
        
        response = self.device.request(self.interface_name + ".getPositionWithTime_32Bit", [axis, ])
        self.device.handleError(response)
        return response[1], response[2], response[3]                

    def getControlEotOutputDeactive(self, axis):
        # type: (int) -> (bool)
        """
        This function gets the output applied to the selected axis on the end of travel.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_enabled: enabled If true, the output of the axis will be deactivated on positive EOT detection.
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlEotOutputDeactive", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlEotOutputDeactive(self, axis, enable):
        # type: (int, bool) -> ()
        """
        This function sets the output applied to the selected axis on the end of travel.

        Parameters:
            axis: [0|1|2]
            enable: if enabled, the output of the axis will be deactivated on positive EOT detection.
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlEotOutputDeactive", [axis, enable, ])
        self.device.handleError(response)
        return                 

    def setGroundAxis(self, axis, enabled):
        # type: (int, bool) -> ()
        """
        Pull axis piezo drive to GND actively only in AMC300 this is used in MIC-Mode

        Parameters:
            axis: motion controler axis [0|1|2]
            enabled: true or false
                    
        """
        
        response = self.device.request(self.interface_name + ".setGroundAxis", [axis, enabled, ])
        self.device.handleError(response)
        return                 

    def getGroundAxis(self, axis):
        # type: (int) -> (bool)
        """
        Checks if the axis piezo drive is actively grounded only in AMC300

        Parameters:
            axis: montion controler axis [0|1|2]
                    
        Returns:
            value_errNo: errNo 0 or error
            value_grounded: grounded true or false
                    
        """
        
        response = self.device.request(self.interface_name + ".getGroundAxis", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setGroundAxisAutoOnTarget(self, axis, enabled):
        # type: (int, bool) -> ()
        """
        Pull axis piezo drive to GND actively if positioner is in ground target range only in AMC300 this is used in MIC-Mode

        Parameters:
            axis: montion controler axis [0|1|2]
            enabled: true or false
                    
        """
        
        response = self.device.request(self.interface_name + ".setGroundAxisAutoOnTarget", [axis, enabled, ])
        self.device.handleError(response)
        return                 

    def getGroundAxisAutoOnTarget(self, axis):
        # type: (int) -> (bool)
        """
        Pull axis piezo drive to GND if positioner is in ground target range only in AMC300

        Parameters:
            axis: montion controler axis [0|1|2]
                    
        Returns:
            value_errNo: errNo 0 or error
            value_value: value true or false
                    
        """
        
        response = self.device.request(self.interface_name + ".getGroundAxisAutoOnTarget", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getGroundTargetRange(self, axis):
        # type: (int) -> (int)
        """
        Retrieves the range around the target position in which the auto grounding becomes active.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_targetrange: targetrange in nm
                    
        """
        
        response = self.device.request(self.interface_name + ".getGroundTargetRange", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setGroundTargetRange(self, axis, range):
        # type: (int, int) -> ()
        """
        Set  the range around the target position in which the auto grounding becomes active.

        Parameters:
            axis: [0|1|2]
            range: in nm
                    
        """
        
        response = self.device.request(self.interface_name + ".setGroundTargetRange", [axis, range, ])
        self.device.handleError(response)
        return                 

