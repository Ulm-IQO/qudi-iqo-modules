class System_service:
    def __init__(self, device):
        self.device = device
        self.interface_name = "com.attocube.system"

    def apply(self, key):
        # type: (int) -> ()
        """
        Apply temporary system configuration

        Parameters:
            key: 
                    
        """
        
        response = self.device.request(self.interface_name + ".apply", [key, ])
        self.device.handleError(response)
        return                 

    def setDeviceName(self, name):
        # type: (str) -> ()
        """
        Set custom name for the device

        Parameters:
            name: string: device name
                    
        """
        
        response = self.device.request(self.interface_name + ".setDeviceName", [name, ])
        self.device.handleError(response)
        return                 

    def getDeviceName(self):
        # type: () -> (str)
        """
        Get the actual device name
        Returns:
            value_errNo: errNo errorCode
            value_string: string: actual device name
                    
        """
        
        response = self.device.request(self.interface_name + ".getDeviceName")
        self.device.handleError(response)
        return response[1]                

    def rebootSystem(self):
        # type: () -> ()
        """
        Reboot the system
        """
        
        response = self.device.request(self.interface_name + ".rebootSystem")
        self.device.handleError(response)
        return                 

    def factoryReset(self):
        # type: () -> ()
        """
        Turns on the factory reset flag.
        """
        
        response = self.device.request(self.interface_name + ".factoryReset")
        self.device.handleError(response)
        return                 

    def softReset(self):
        # type: () -> ()
        """
        Performs a soft reset (Reset without deleting the network settings).
        """
        
        response = self.device.request(self.interface_name + ".softReset")
        self.device.handleError(response)
        return                 

    def errorNumberToString(self, language, errNbr):
        # type: (int, int) -> (str)
        """
        Get a description of an error code

        Parameters:
            language: integer: Language code 0 for the error name, 1 for a more user friendly error message
            errNbr: interger: Error code to translate
                    
        Returns:
            value_errNo: errNo errorCode
            value_string: string: Error description
                    
        """
        
        response = self.device.request(self.interface_name + ".errorNumberToString", [language, errNbr, ])
        self.device.handleError(response)
        return response[1]                

    def errorNumberToRecommendation(self, language, errNbr):
        # type: (int, int) -> (str)
        """
        Get a recommendation for the error code

        Parameters:
            language: integer: Language code
            errNbr: interger: Error code to translate
                    
        Returns:
            value_errNo: errNo errorCode
            value_string: string: Error recommendation (currently returning an int = 0 until we have recommendations)
                    
        """
        
        response = self.device.request(self.interface_name + ".errorNumberToRecommendation", [language, errNbr, ])
        self.device.handleError(response)
        return response[1]                

    def getFirmwareVersion(self):
        # type: () -> (str)
        """
        Get the firmware version of the system
        Returns:
            value_errNo: errNo errorCode
            value_string: string: The firmware version
                    
        """
        
        response = self.device.request(self.interface_name + ".getFirmwareVersion")
        self.device.handleError(response)
        return response[1]                

    def getHostname(self):
        # type: () -> (str)
        """
        Return device hostname
        Returns:
            value_errNo: errNo errorCode
            available: available
                    
        """
        
        response = self.device.request(self.interface_name + ".getHostname")
        self.device.handleError(response)
        return response[1]                

    def getMacAddress(self):
        # type: () -> (str)
        """
        Get the mac address of the system
        Returns:
            value_errNo: errNo errorCode
            value_string: string: Mac address of the system
                    
        """
        
        response = self.device.request(self.interface_name + ".getMacAddress")
        self.device.handleError(response)
        return response[1]                

    def getSerialNumber(self):
        # type: () -> (str)
        """
        Get the serial number of the system
        Returns:
            value_errNo: errNo errorCode
            value_string: string: Serial number
                    
        """
        
        response = self.device.request(self.interface_name + ".getSerialNumber")
        self.device.handleError(response)
        return response[1]                

    def getFluxCode(self):
        # type: () -> (str)
        """
        Get the flux code of the system
        Returns:
            value_errNo: errNo errorCode
            value_string: string: flux code
                    
        """
        
        response = self.device.request(self.interface_name + ".getFluxCode")
        self.device.handleError(response)
        return response[1]                

    def updateTimeFromInternet(self):
        # type: () -> ()
        """
        Update system time by querying attocube.com
        """
        
        response = self.device.request(self.interface_name + ".updateTimeFromInternet")
        self.device.handleError(response)
        return                 

    def setTime(self, day, month, year, hour, minute, second):
        # type: (int, int, int, int, int, int) -> ()
        """
        Set system time manually

        Parameters:
            day: integer: Day (1-31)
            month: integer: Day (1-12)
            year: integer: Day (eg. 2021)
            hour: integer: Day (0-23)
            minute: integer: Day (0-59)
            second: integer: Day (0-59)
                    
        """
        
        response = self.device.request(self.interface_name + ".setTime", [day, month, year, hour, minute, second, ])
        self.device.handleError(response)
        return                 

