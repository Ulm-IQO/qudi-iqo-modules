class Access:
    def __init__(self, device):
        self.device = device
        self.interface_name = ""

    def grantAccess(self, password):
        # type: (str) -> ()
        """
        Grants access to a locked device for the requesting IP by checking against the password

        Parameters:
            password: string the current password
                    
        """
        
        response = self.device.request(self.interface_name + "grantAccess", [password, ])
        self.device.handleError(response)
        return                 

    def lock(self, password):
        # type: (str) -> ()
        """
        This function locks the device with a password, so the calling of functions is only possible with this password. The locking IP is automatically added to the devices which can access functions

        Parameters:
            password: string the password to be set
                    
        """
        
        response = self.device.request(self.interface_name + "lock", [password, ])
        self.device.handleError(response)
        return                 

    def unlock(self, password):
        # type: (str) -> ()
        """
        This function unlocks the device, so it will not be necessary to execute the grantAccess function to run any function

        Parameters:
            password: string the current password
                    
        """
        
        response = self.device.request(self.interface_name + "unlock", [password, ])
        self.device.handleError(response)
        return                 

    def getLockStatus(self):
        # type: () -> (bool, bool)
        """
        This function returns if the device is locked and if the current client is authorized to use the device.
        Returns:
            value_errNo: errNo errorCode
            value_locked: locked Is the device locked?
            value_authorized: authorized Is the client authorized?
                    
        """
        
        response = self.device.request(self.interface_name + "getLockStatus")
        self.device.handleError(response)
        return response[1], response[2]                

