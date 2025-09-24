class Update:
    def __init__(self, device):
        self.device = device
        self.interface_name = "com.attocube.system.update"

    def getSwUpdateProgress(self):
        # type: () -> (int)
        """
        Get the progress of running update
        Returns:
            value_errNo: errNo errorCode
            value_int: int: progress in percent
                    
        """
        
        response = self.device.request(self.interface_name + ".getSwUpdateProgress")
        self.device.handleError(response)
        return response[1]                

    def getLicenseUpdateProgress(self):
        # type: () -> (int)
        """
        Get the progress of running license update
        Returns:
            value_errNo: errNo errorCode
            value_int: int: progress in percent
                    
        """
        
        response = self.device.request(self.interface_name + ".getLicenseUpdateProgress")
        self.device.handleError(response)
        return response[1]                

    def softwareUpdateBase64(self):
        # type: () -> ()
        """
        Execute the update with base64 file uploaded.
        """
        
        response = self.device.request(self.interface_name + ".softwareUpdateBase64")
        self.device.handleError(response)
        return                 

    def uploadSoftwareImageBase64(self, offset, b64Data):
        # type: (int, str) -> ()
        """
        Upload new firmware image in format base 64

        Parameters:
            offset: int: offset of the data
            b64Data: string: base64 data
                    
        """
        
        response = self.device.request(self.interface_name + ".uploadSoftwareImageBase64", [offset, b64Data, ])
        self.device.handleError(response)
        return                 

    def uploadLicenseBase64(self, offset, b64Data):
        # type: (int, str) -> ()
        """
        Upload new license file in format base 64

        Parameters:
            offset: int: offset of the data
            b64Data: string: base64 data
                    
        """
        
        response = self.device.request(self.interface_name + ".uploadLicenseBase64", [offset, b64Data, ])
        self.device.handleError(response)
        return                 

    def licenseUpdateBase64(self):
        # type: () -> ()
        """
        Execute the license update with base64 file uploaded.
        """
        
        response = self.device.request(self.interface_name + ".licenseUpdateBase64")
        self.device.handleError(response)
        return                 

