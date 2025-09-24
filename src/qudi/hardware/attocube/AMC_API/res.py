class Res:
    def __init__(self, device):
        self.device = device
        self.interface_name = "com.attocube.amc.res"

    def setMode(self, mode):
        # type: (int) -> ()
        """
        Sets the mode of the RES position measurement This selects which frequency/ies are used for the lock-in measurement of the RES position, currently there are two possibilities: 1: Individual per axis: each axis is measured on a different frequency; this mode reduces noise coupling between axes, while requiring more wiring 2: Shared line/MIC-Mode: each axis is measured on the same frequency, which reduces the number of required wires while more coupling noise is excpected 3: Same as 1, but with overall lower frequencies.

        Parameters:
            mode: 1: Individual per axis, 2: Shared line mode, 3: Individual per axis (low frequency), 4: Shared line mode (low frequency)
                    
        """
        
        response = self.device.request(self.interface_name + ".setMode", [mode, ])
        self.device.handleError(response)
        return                 

    def getMode(self):
        # type: () -> (int)
        """
        Get mode of RES application, see setMode for the description of possible parameters
        Returns:
            errNo: errNo
            mode: mode
                    
        """
        
        response = self.device.request(self.interface_name + ".getMode")
        self.device.handleError(response)
        return response[1]                

    def setChainGain(self, axis, gainconfig):
        # type: (int, int) -> ()
        """
        Set signal chain gain to control overall power

        Parameters:
            axis: number of axis
            gainconfig: 0: 0dB ( power 600mVpkpk^2/R), 1 : -10 dB , 2 : -15 dB , 3 : -20 dB
                    
        """
        
        response = self.device.request(self.interface_name + ".setChainGain", [axis, gainconfig, ])
        self.device.handleError(response)
        return                 

    def getChainGain(self, axis):
        # type: (int) -> (int)
        """
        Get chain gain, see setChainGain for parameter description

        Parameters:
            axis: number of axis
                    
        Returns:
            errNo: errNo
            gaincoeff: gaincoeff
                    
        """
        
        response = self.device.request(self.interface_name + ".getChainGain", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getLutSn(self, axis):
        # type: (int) -> (str)
        """
        get the identifier of the loaded lookuptable (will be empty if disabled)

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_string: string : identifier
                    
        """
        
        response = self.device.request(self.interface_name + ".getLutSn", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setConfigurationFile(self, axis, content):
        # type: (int, str) -> ()
        """
        Load configuration file which either contains a JSON dict with parameters for the positioner on the axis or the LUT file itself (as legacy support for ANC350 .aps files)

        Parameters:
            axis: [0|1|2]
            content: JSON Dictionary or .aps File.
 The JSON Dictonary can/must contain the following keys:
 'type': mandatory This field has to be one of the positioner list (see getPositionersList)
 'lut': optional, contains an array of 1024 LUT values that are a mapping between ratio of the RES element travelled (0 to 1) and the corresponding absolute value at this ratio given in [nm].
 Note: when generating these tables with position data in absolute units, the scaling of the travel ratio with the current sensor range has to be reversed.
 'lut_sn': optional, a string to uniquely identify the loaded LUT
                    
        """
        
        response = self.device.request(self.interface_name + ".setConfigurationFile", [axis, content, ])
        self.device.handleError(response)
        return                 

    def getLinearization(self, axis):
        # type: (int) -> (bool)
        """
        Gets wether linearization is enabled or not

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_enabled: enabled true when enabled
                    
        """
        
        response = self.device.request(self.interface_name + ".getLinearization", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def setLinearization(self, axis, enable):
        # type: (int, bool) -> ()
        """
        Control if linearization is enabled or not

        Parameters:
            axis: [0|1|2]
            enable: boolean ( true: enable linearization)
                    
        """
        
        response = self.device.request(self.interface_name + ".setLinearization", [axis, enable, ])
        self.device.handleError(response)
        return                 

    def getSensorStatus(self, axis):
        # type: (int) -> (bool)
        """
        Gets wether a valid RES position signal is present (always true for a disabled sensor and for rotators)

        Parameters:
            axis: [0|1|2]
                    
        Returns:
            errNo: errNo
            value_present: present true when present
                    
        """
        
        response = self.device.request(self.interface_name + ".getSensorStatus", [axis, ])
        self.device.handleError(response)
        return response[1]                

