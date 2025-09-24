class Test:
    def __init__(self, device):
        self.device = device
        self.interface_name = "com.attocube.amc.test"

    def clearLog(self, axis, testname):
        # type: (int, str) -> ()
        """
        Resets the log    For debugging only.

        Parameters:
            axis: Axis of the AMC
            testname: Name of the test
                    
        """
        
        response = self.device.request(self.interface_name + ".clearLog", [axis, testname, ])
        self.device.handleError(response)
        return                 

    def execute(self, name, parameters):
        # type: (str, str) -> ()
        """
        Starts a test run            For debugging only.                Error codes:                ERR_OK = 0                ERR_TEST_DOES_NOT_EXIST = -1                ERR_TEST_RUNNING = -2

        Parameters:
            name: Name of the test, see getAllTest()
            parameters: Parameters object as stringified JSON object, where "default" is the applied value
Example
        "{
            "axis": {
                "friendlyName": "Axis",
                "default": "0"
            },
            "mode": {
                "friendlyName": "Mode",
                "default": 0
            },
            "amplitude": {
                "friendlyName": "Amplitude (V)",
                "default": "45"
            },
            "frequency": {
                "friendlyName": "Frequency (Hz)",
                "default": "2000"
            },
            "cycles": {
                "friendlyName": "Cycles",
                "default": "3"
            },
            "random_range": {
                "friendlyName": "Random range",
                "default": 500000
            },
            "buffer": {
                "friendlyName": "Buffer",
                 "default": 2000
            },
            "sample_period": {
                "friendlyName": "Sample period (ms)",
                "default": "100"
            }
        }"
                    
        """
        
        response = self.device.request(self.interface_name + ".execute", [name, parameters, ])
        self.device.handleError(response)
        return                 

    def getAllTests(self, axis):
        # type: (int) -> (str, str)
        """
        Request all names of the registered tests            For debugging only.

        Parameters:
            axis: 
                    
        Returns:
            error_code: Error code
            tests: Jsonified list with all automatic tests
Example
"[
    {
        "name": "Velocity Test",
        "parameters": {
            "axis": {
                "friendlyName": "Axis",
                "default": "0"
            },
            "mode": {
                "friendlyName": "Mode",
                "default": 0
            },
            "amplitude": {
                "friendlyName": "Amplitude (V)",
                "default": "45"
            },
            "frequency": {
                "friendlyName": "Frequency (Hz)",
                "default": "2000"
            },
            "cycles": {
                "friendlyName": "Cycles",
                "default": "3"
            },
            "random_range": {
                "friendlyName": "Random range",
                "default": 500000
            },
            "buffer": {
                "friendlyName": "Buffer",
                 "default": 2000
            },
            "sample_period": {
                "friendlyName": "Sample period (ms)",
                "default": "100"
            }
        },
         "version": "1.0.0",
         "stoppable": true
    }
]"

, "version": "1.0.0", "stoppable": true}]"
"stoppable" tells the user if this test can be aborted while running
            manualTests: Jsonified list with all manual tests
Example
"[{"name": "Capacity Test", "parameters": {"capacity": "Capacity (nF)"}, "version": "1.0.0"}]"
                    
        """
        
        response = self.device.request(self.interface_name + ".getAllTests", [axis, ])
        self.device.handleError(response)
        return response[1], response[2]                

    def getLog(self, axis):
        # type: (int) -> (str)
        """
        Gets the complete log    For debugging only.

        Parameters:
            axis: Axis of the AMC
                    
        Returns:
            error_code: Error code
            logs: Log string json-encoded ({testname: log-str})
                    
        """
        
        response = self.device.request(self.interface_name + ".getLog", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getPositionerSN(self, axis):
        # type: (int) -> (str)
        """
        Gets the serial number of the positioner connected to a given axis.    For debugging only.

        Parameters:
            axis: 
                    
        Returns:
            error_code: Error code
            sn: Serial number of positioner
                    
        """
        
        response = self.device.request(self.interface_name + ".getPositionerSN", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getReport(self, axis, name):
        # type: (int, str) -> (str)
        """
        Get test report of last test run of specific test    name == "all": the test reports of all tests from last test run will be returned    For debugging only.

        Parameters:
            axis: 
            name: Name of the test or "all"
                    
        Returns:
            error_code: Error code
            report: Test report json-serialized
                    
        """
        
        response = self.device.request(self.interface_name + ".getReport", [axis, name, ])
        self.device.handleError(response)
        return response[1]                

    def getStatus(self, axis):
        # type: (int) -> (int, str)
        """
        Get the current execution status of the test sequencer    For debugging only.        Status:        IDLE = 0        RUNNING = 1        FINISHED = 2        FINISHED_CYCLE = 3

        Parameters:
            axis: 
                    
        Returns:
            error_code: Error code
            status: Status
            test: Name of test which ran last
                    
        """
        
        response = self.device.request(self.interface_name + ".getStatus", [axis, ])
        self.device.handleError(response)
        return response[1], response[2]                

    def getTestParameters(self, axis):
        # type: (int) -> (str)
        """
        Get test parameters the current test on the given axis is executed with            For debugging only.

        Parameters:
            axis: 
                    
        Returns:
            error_code: Error code
            parameters: Parameters object as stringified JSON object where "default" is the applied parameter
Example
"{"axis":{"friendlyName":"Axis","default":0},"min_amplitude":{"friendlyName":"Min. amplitude (V)","default":"45"},"max_amplitude":{"friendlyName":"Max. amplitude (V)","default":"60"},"start_position":{"friendlyName":"Start position","default":0}}"
                    
        """
        
        response = self.device.request(self.interface_name + ".getTestParameters", [axis, ])
        self.device.handleError(response)
        return response[1]                

    def getTestplatz(self):
        # type: () -> (int)
        """
        Gets the number of the Testplatz where the AMC belongs to    For debugging only.
        Returns:
            error_code: Error code
            testplatz: Number of Testplatz
                    
        """
        
        response = self.device.request(self.interface_name + ".getTestplatz")
        self.device.handleError(response)
        return response[1]                

    def setPositionerSN(self, axis, sn):
        # type: (int, str) -> ()
        """
        Sets the serial number of the positioner connected to a given axis.    For debugging only.

        Parameters:
            axis: Axis the positioner is connected to
            sn: Serial number of the positioner
                    
        """
        
        response = self.device.request(self.interface_name + ".setPositionerSN", [axis, sn, ])
        self.device.handleError(response)
        return                 

    def setTestplatz(self, testplatz):
        # type: (int) -> ()
        """
        Sets the number of the Testplatz where the AMC belongs to    For debugging only.

        Parameters:
            testplatz: Number of Testplatz
                    
        """
        
        response = self.device.request(self.interface_name + ".setTestplatz", [testplatz, ])
        self.device.handleError(response)
        return                 

    def stopCurrentTest(self, axis):
        # type: (int) -> ()
        """
        Stops the current test if it is stoppable    For debugging only.

        Parameters:
            axis: 
                    
        """
        
        response = self.device.request(self.interface_name + ".stopCurrentTest", [axis, ])
        self.device.handleError(response)
        return                 

