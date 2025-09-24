class Network:
    def __init__(self, device):
        self.device = device
        self.interface_name = "com.attocube.system.network"

    def getRealIpAddress(self):
        # type: () -> (str)
        """
        Get the real IP address of the device set to the network interface (br0, eth1 or eth0)
        Returns:
            value_errNo: errNo errorCode
            value_IP: IP address as string
                    
        """
        
        response = self.device.request(self.interface_name + ".getRealIpAddress")
        self.device.handleError(response)
        return response[1]                

    def getIpAddress(self):
        # type: () -> (str)
        """
        Get the IP address of the device
        Returns:
            value_errNo: errNo errorCode
            value_IP: IP address as string
                    
        """
        
        response = self.device.request(self.interface_name + ".getIpAddress")
        self.device.handleError(response)
        return response[1]                

    def setIpAddress(self, address):
        # type: (str) -> ()
        """
        Set the IP address of the device

        Parameters:
            address: IP address as string
                    
        """
        
        response = self.device.request(self.interface_name + ".setIpAddress", [address, ])
        self.device.handleError(response)
        return                 

    def getSubnetMask(self):
        # type: () -> (str)
        """
        Get the subnet mask of the device
        Returns:
            value_errNo: errNo errorCode
            value_Subnet: Subnet mask as string
                    
        """
        
        response = self.device.request(self.interface_name + ".getSubnetMask")
        self.device.handleError(response)
        return response[1]                

    def setSubnetMask(self, netmask):
        # type: (str) -> ()
        """
        Set the subnet mask of the device

        Parameters:
            netmask: Subnet mask as string
                    
        """
        
        response = self.device.request(self.interface_name + ".setSubnetMask", [netmask, ])
        self.device.handleError(response)
        return                 

    def getDefaultGateway(self):
        # type: () -> (str)
        """
        Get the default gateway of the device
        Returns:
            value_errNo: errNo errorCode
            value_Default: Default gateway
                    
        """
        
        response = self.device.request(self.interface_name + ".getDefaultGateway")
        self.device.handleError(response)
        return response[1]                

    def setDefaultGateway(self, gateway):
        # type: (str) -> ()
        """
        Set the default gateway of the device

        Parameters:
            gateway: Default gateway as string
                    
        """
        
        response = self.device.request(self.interface_name + ".setDefaultGateway", [gateway, ])
        self.device.handleError(response)
        return                 

    def getDnsResolver(self, priority):
        # type: (int) -> (str)
        """
        Get the DNS resolver

        Parameters:
            priority: of DNS resolver (Usually: 0 = Default, 1 = Backup)
                    
        Returns:
            value_errNo: errNo errorCode
            value_IP: IP address of DNS resolver
                    
        """
        
        response = self.device.request(self.interface_name + ".getDnsResolver", [priority, ])
        self.device.handleError(response)
        return response[1]                

    def setDnsResolver(self, priority, resolver):
        # type: (int, str) -> ()
        """
        Set the DNS resolver

        Parameters:
            priority: of DNS resolver (Usually: 0 = Default, 1 = Backup)
            resolver: The resolver's IP address as string
                    
        """
        
        response = self.device.request(self.interface_name + ".setDnsResolver", [priority, resolver, ])
        self.device.handleError(response)
        return                 

    def getProxyServer(self):
        # type: () -> (str)
        """
        Get the proxy settings of the devide
        Returns:
            value_errNo: errNo errorCode
            value_Proxy: Proxy Server String, empty for no proxy
                    
        """
        
        response = self.device.request(self.interface_name + ".getProxyServer")
        self.device.handleError(response)
        return response[1]                

    def setProxyServer(self, proxyServer):
        # type: (str) -> ()
        """
        Set the proxy server of the device

        Parameters:
            proxyServer: Proxy Server Setting as string
                    
        """
        
        response = self.device.request(self.interface_name + ".setProxyServer", [proxyServer, ])
        self.device.handleError(response)
        return                 

    def getEnableDhcpServer(self):
        # type: () -> (bool)
        """
        Get the state of DHCP server
        Returns:
            value_errNo: errNo errorCode
            value_boolean: boolean: true = DHCP server enable, false = DHCP server disable
                    
        """
        
        response = self.device.request(self.interface_name + ".getEnableDhcpServer")
        self.device.handleError(response)
        return response[1]                

    def setEnableDhcpServer(self, enable):
        # type: (bool) -> ()
        """
        Enable or disable DHCP server

        Parameters:
            enable: boolean: true = enable DHCP server, false = disable DHCP server
                    
        """
        
        response = self.device.request(self.interface_name + ".setEnableDhcpServer", [enable, ])
        self.device.handleError(response)
        return                 

    def getEnableDhcpClient(self):
        # type: () -> (bool)
        """
        Get the state of DHCP client
        Returns:
            value_errNo: errNo errorCode
            value_boolean: boolean: true = DHCP client enable, false = DHCP client disable
                    
        """
        
        response = self.device.request(self.interface_name + ".getEnableDhcpClient")
        self.device.handleError(response)
        return response[1]                

    def setEnableDhcpClient(self, enable):
        # type: (bool) -> ()
        """
        Enable or disable DHCP client

        Parameters:
            enable: boolean: true = enable DHCP client, false = disable DHCP client
                    
        """
        
        response = self.device.request(self.interface_name + ".setEnableDhcpClient", [enable, ])
        self.device.handleError(response)
        return                 

    def apply(self):
        # type: () -> ()
        """
        Apply temporary IP configuration and load it
        """
        
        response = self.device.request(self.interface_name + ".apply")
        self.device.handleError(response)
        return                 

    def verify(self):
        # type: () -> ()
        """
        Verify that temporary IP configuration is correct
        """
        
        response = self.device.request(self.interface_name + ".verify")
        self.device.handleError(response)
        return                 

    def discard(self):
        # type: () -> ()
        """
        Discard temporary IP configuration
        """
        
        response = self.device.request(self.interface_name + ".discard")
        self.device.handleError(response)
        return                 

    def getWifiPresent(self):
        # type: () -> (bool)
        """
        Returns is a Wifi interface is present
        Returns:
            value_errNo: errNo errorCode
            value_True: True, if interface is present
                    
        """
        
        response = self.device.request(self.interface_name + ".getWifiPresent")
        self.device.handleError(response)
        return response[1]                

    def setWifiMode(self, mode):
        # type: (int) -> ()
        """
        Change the operation mode of the wifi adapter

        Parameters:
            mode: 0: Access point, 1: Wifi client
                    
        """
        
        response = self.device.request(self.interface_name + ".setWifiMode", [mode, ])
        self.device.handleError(response)
        return                 

    def getWifiMode(self):
        # type: () -> (int)
        """
        Get the operation mode of the wifi adapter
        Returns:
            value_errNo: errNo errorCode
            value_mode: mode 0: Access point, 1: Wifi client
                    
        """
        
        response = self.device.request(self.interface_name + ".getWifiMode")
        self.device.handleError(response)
        return response[1]                

    def setWifiSSID(self, ssid):
        # type: (str) -> ()
        """
        Change the SSID of the network hosted (mode: Access point) or connected to (mode: client)

        Parameters:
            ssid: 
                    
        """
        
        response = self.device.request(self.interface_name + ".setWifiSSID", [ssid, ])
        self.device.handleError(response)
        return                 

    def getWifiSSID(self):
        # type: () -> (str)
        """
        Get the the SSID of the network hosted (mode: Access point) or connected to (mode: client)
        Returns:
            value_errNo: errNo errorCode
            SSID: SSID
                    
        """
        
        response = self.device.request(self.interface_name + ".getWifiSSID")
        self.device.handleError(response)
        return response[1]                

    def setWifiPassphrase(self, psk):
        # type: (str) -> ()
        """
        Change the passphrase of the network hosted (mode: Access point) or connected to (mode: client)

        Parameters:
            psk: Pre-shared key
                    
        """
        
        response = self.device.request(self.interface_name + ".setWifiPassphrase", [psk, ])
        self.device.handleError(response)
        return                 

    def getWifiPassphrase(self):
        # type: () -> (str)
        """
        Get the the passphrase of the network hosted (mode: Access point) or connected to (mode: client)
        Returns:
            value_errNo: errNo errorCode
            value_psk: psk Pre-shared key
                    
        """
        
        response = self.device.request(self.interface_name + ".getWifiPassphrase")
        self.device.handleError(response)
        return response[1]                

    def configureWifi(self, mode, ssid, psk):
        # type: (int, str, str) -> ()
        """
        Change the wifi configuration and applies it

        Parameters:
            mode: 0: Access point, 1: Wifi client
            ssid: 
            psk: Pre-shared key
                    
        """
        
        response = self.device.request(self.interface_name + ".configureWifi", [mode, ssid, psk, ])
        self.device.handleError(response)
        return                 

