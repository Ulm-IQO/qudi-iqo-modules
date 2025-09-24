class Rotcomp:
    def __init__(self, device):
        self.device = device
        self.interface_name = "com.attocube.amc.rotcomp"

    def getControlTargetRanges(self):
        # type: () -> (bool)
        """
        Checks if all three axis are in target range.
        Returns:
            errNo: Error code, if there was an error, otherwise 0 for ok
            in_target_range: true all three axes are in target range, false at least one axis is not in target range
                    
        """
        
        response = self.device.request(self.interface_name + ".getControlTargetRanges")
        self.device.handleError(response)
        return response[1]                

    def getEnabled(self):
        # type: () -> (bool)
        """
        Gets the enabled status of the rotation compensation
        Returns:
            errNo: Error code, if there was an error, otherwise 0 for ok
            enabled: true Rotation compensation is enabled, false Rotation compensation is disabled
                    
        """
        
        response = self.device.request(self.interface_name + ".getEnabled")
        self.device.handleError(response)
        return response[1]                

    def getLUT(self):
        # type: () -> (str)
        """
        Gets the LUT file as JSON string
        Returns:
            errNo: Error code, if there was an error, otherwise 0 for ok
            lut: JSON string of the LUT file for the rotation compensation
                    
        """
        
        response = self.device.request(self.interface_name + ".getLUT")
        self.device.handleError(response)
        return response[1]                

    def setEnabled(self, enabled):
        # type: (bool) -> ()
        """
        Enables and disables the rotation compensation

        Parameters:
            enabled: true Rotation compensation is enabled, false Rotation compensation is disabled
                    
        """
        
        response = self.device.request(self.interface_name + ".setEnabled", [enabled, ])
        self.device.handleError(response)
        return                 

    def setLUT(self, lut_string):
        # type: (str) -> ()
        """
        Sets the LUT file from a JSON string

        Parameters:
            lut_string: JSON string of the LUT file for the rotation compensation
                    
        """
        
        response = self.device.request(self.interface_name + ".setLUT", [lut_string, ])
        self.device.handleError(response)
        return                 

    def updateOffsets(self, offset_axis0, offset_axis1, offset_axis2):
        # type: (int, int, int) -> ()
        """
        Updates the start offsets of the axes

        Parameters:
            offset_axis0: Offset of axis 1 in [nm]
            offset_axis1: Offset of axis 2 in [nm]
            offset_axis2: Offset of axis 3 in [nm]
                    
        """
        
        response = self.device.request(self.interface_name + ".updateOffsets", [offset_axis0, offset_axis1, offset_axis2, ])
        self.device.handleError(response)
        return                 

