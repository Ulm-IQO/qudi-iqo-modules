import serial
import serial.tools.list_ports

from qudi.util.mutex import Mutex

class MOGLABSDeviceFinder:
    _instance = None
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self._lock = Mutex()
            self.initialized = True
            self.baudrate=115200
            self.bytesize=8
            self.parity='N'
            self.stopbits=1
            self.timeout=1
            self.writeTimeout=0
            self.cem = None
            self.ldd = None
            self.fzw = None
            self.find_devices()

    def find_devices(self):
        with self._lock:
            ports = serial.tools.list_ports.grep("VID:PID=0483:5740")
            for port in ports:
                try:
                    s = serial.Serial(port.device,
                                      baudrate=self.baudrate,
                                      bytesize=self.bytesize,
                                      parity=self.parity,
                                      stopbits=self.stopbits,
                                      timeout=self.timeout,
                                      writeTimeout=self.writeTimeout
                                      )
                    s.write("info\r\n".encode("utf8"))
                    info = s.readline().decode("utf8")
                    device,*_ = info.split()
                    device = device.lower()
                    if device == "cem":
                        self.cem = s
                    elif device == "mld":
                        self.ldd = s
                    elif device == "fzw":
                        self.fzw = s
                    s.close()
                except Exception as e:
                    print(f"Error while scanning for devices {e}")

