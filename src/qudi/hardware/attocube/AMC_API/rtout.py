class Rtout:
    def __init__(self, device):
        self.device = device
        self.interface_name = "com.attocube.amc.rtout"

    def setMode(self, axis, mode):
        # type: (int, int) -> ()
        """
        Set the real time output signal mode

        Parameters:
            axis: [0|1|2]
            mode: 0: Off, 1: AquadB, 2: Trigger
                    
        """
        
        response = self.device.request(self.interface_name + ".setMode", [axis, mode, ])
        if response["result"][0] == 0:
            self.apply()
        else:
            self.discard()
        self.device.handleError(response)
        return                 

    def getMode(self, axis):
        # type: (int) -> (int)
        """
        Get Mode

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_mode: mode 0: Off, 1: AquadB, 2: Trigger
                    
        """
        
        response = self.device.request(self.interface_name + ".getMode", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setSignalMode(self, mode):
        # type: (int) -> ()
        """
        This function sets the real time output mode for the selected axis.

        Parameters:
            mode: 0: TTL, 1: LVDS
                    
        """
        
        response = self.device.request(self.interface_name + ".setSignalMode", [mode, ])
        self.device.handleError(response)
        return                 

    def getSignalMode(self, tempvalue):
        # type: (int) -> (int)
        """
        This function gets the real time output mode for the selected axis.

        Parameters:
            tempvalue: 
                    
        Returns:
            errNo: errNo
            value_mode: mode 0: TTL, 1: LVDS
                    
        """
        
        response = self.device.request(self.interface_name + ".getSignalMode", [tempvalue, ])
        self.device.handleError(response)
        return response[1]                

    def setTriggerConfig(self, axis, higher, lower, epsilon, polarity):
        # type: (int, int, int, int, int) -> ()
        """
        Control the real time output trigger config

        Parameters:
            axis: [0|1|2]
            higher: upper limit in nm / µdeg
            lower: lower limit in nm / µdeg
            epsilon: hysteresis in nm / µdeg
            polarity: 0: active high, 1: active low
                    
        """
        
        response = self.device.request(self.interface_name + ".setTriggerConfig", [axis, higher, lower, epsilon, polarity, ])
        self.device.handleError(response)
        return                 

    def getTriggerConfig(self, axis):
        # type: (int) -> (int, int, int, int)
        """
        Get the real time output trigger config

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_higher: higher upper limit in nm / µdeg
            value_lower: lower lower limit in nm / µdeg
            value_epsilon: epsilon hysteresis in nm / µdeg
            value_polarity: polarity 0: active high, 1: active low
                    
        """
        
        response = self.device.request(self.interface_name + ".getTriggerConfig", [axis, ])
        self.device.handleError(response)
        return response[1], response[2], response[3], response[4]                

    def getControlAQuadBOut(self, axis):
        # type: (int) -> (bool)
        """
        This function gets if of AQuadB output for position indication is enabled

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_enabled: enabled boolean
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlAQuadBOut", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getControlAQuadBOutResolution(self, axis):
        # type: (int) -> (int)
        """
        This function gets the AQuadB output resolution for position indication.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_resolution: resolution in nm
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlAQuadBOutResolution", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlAQuadBOutResolution(self, axis, resolution):
        # type: (int, int) -> ()
        """
        This function sets the AQuadB output resolution for position indication.

        Parameters:
            axis: [0|1|2]
            resolution: in nm; range [1 ... 16777]
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlAQuadBOutResolution", [axis, resolution, ])
        if response["result"][0] == 0:
            self.apply()
        else:
            self.discard()
        self.device.handleError(response)
        return                 

    def getControlAQuadBOutClock(self, axis):
        # type: (int) -> (int)
        """
        This function gets the clock for AQuadB output.

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_clock_in_ns: clock_in_ns Clock in multiples of 20ns. Minimum 2 (40ns), maximum 65535 (1,310700ms)
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlAQuadBOutClock", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setControlAQuadBOutClock(self, axis, clock):
        # type: (int, int) -> ()
        """
        This function sets the clock for AQuadB output.

        Parameters:
            axis: [0|1|2]
            clock: Clock in multiples of 20ns. Minimum 2 (40ns), maximum 65535 (1,310700ms)
                    
        """
        
        response = self.device.request(self.interface_name + ".setControlAQuadBOutClock", [axis, clock, ])
        if response["result"][0] == 0:
            self.apply()
        else:
            self.discard()
        self.device.handleError(response)
        return                 

    def applyAxis(self, axis):
        # type: (int) -> ()
        """
        Apply for rtout function of specific axis

        Parameters:
            axis: [0|1|2]
                    
        """
        
        response = self.device.request(self.interface_name + ".applyAxis", [axis, ])
        self.device.handleError(response)
        return                 

    def apply(self):
        # type: () -> ()
        """
        Apply for all rtout function
        """
        
        response = self.device.request(self.interface_name + ".apply")
        self.device.handleError(response)
        return                 

    def discardAxis(self, axis):
        # type: (int) -> ()
        """
        Discard rtout value of specific axis set by the set function(not applied yet)

        Parameters:
            axis: [0|1|2]
                    
        """
        
        response = self.device.request(self.interface_name + ".discardAxis", [axis, ])
        self.device.handleError(response)
        return                 

    def discardSignalMode(self):
        # type: () -> ()
        """
        Discard value set by setSignalMode
        """
        
        response = self.device.request(self.interface_name + ".discardSignalMode")
        self.device.handleError(response)
        return                 

    def discard(self):
        # type: () -> ()
        """
        Discard all rtout value set by the set function(not applied yet)
        """
        
        response = self.device.request(self.interface_name + ".discard")
        self.device.handleError(response)
        return                 

