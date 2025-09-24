class Description:
    def __init__(self, device):
        self.device = device
        self.interface_name = "com.attocube.amc.description"

    def checkChassisNbr(self):
        # type: () -> (int, int)
        """
        Get Chassis and Slot Number, only works when AMC is within a Rack
        Returns:
            value_errNo: errNo errorCode
            slotNbr: slotNbr
            chassisNbr: chassisNbr
                    
        """
        
        response = self.device.request(self.interface_name + ".checkChassisNbr")
        self.device.handleError(response)
        return response[1], response[2]                

    def getPositionersList(self):
        # type: () -> (str)
        """
        This function reads the actor names that can be connected to the device.
        Returns:
            errNo: errNo
            PositionersList: PositionersList
                    
        """
        
        response = self.device.request(self.interface_name + ".getPositionersList")
        self.device.handleError(response)
        return response[1]                

    def getDeviceType(self):
        # type: () -> (str)
        """
        This function gets the device type based on its EEPROM configuration.
        Returns:
            errNo: errNo
            value_devicetype: devicetype Device name (AMC100, AMC150, AMC300) with attached feature ( AMC100/NUM, AMC100/NUM/PRO)
                    
        """
        
        response = self.device.request(self.interface_name + ".getDeviceType")
        self.device.handleError(response)
        return response[1]                

    def getFeaturesActivated(self):
        # type: () -> (str)
        """
        Get the activated features and return as a string
        Returns:
            errNo: errNo
            value_features: features activated on device concatenated by comma e.g. Closed loop Operation, Pro, Wireless Controller, IO
                    
        """
        
        response = self.device.request(self.interface_name + ".getFeaturesActivated")
        self.device.handleError(response)
        return response[1]                

