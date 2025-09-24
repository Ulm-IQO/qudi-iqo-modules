from . import ACS
from .test import Test
from .system_service import System_service
from .diagnostic import Diagnostic
from .network import Network
from .res import Res
from .description import Description
from .access import Access
from .move import Move
from .rotcomp import Rotcomp
from .update import Update
from .status import Status
from .control import Control
from .amcids import Amcids
from .rtin import Rtin
from .about import About
from .rtout import Rtout


class Device(ACS.Device):
    def __init__(self, address):
        super().__init__(address)

        self.test = Test(self)
        self.system_service = System_service(self)
        self.diagnostic = Diagnostic(self)
        self.network = Network(self)
        self.res = Res(self)
        self.description = Description(self)
        self.access = Access(self)
        self.move = Move(self)
        self.rotcomp = Rotcomp(self)
        self.update = Update(self)
        self.status = Status(self)
        self.control = Control(self)
        self.amcids = Amcids(self)
        self.rtin = Rtin(self)
        self.about = About(self)
        self.rtout = Rtout(self)
        
        

def discover():
    return Device.discover("amc")
