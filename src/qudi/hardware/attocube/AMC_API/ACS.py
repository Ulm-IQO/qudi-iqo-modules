# -*- coding: utf-8 -*-

import sys
import socket
import json

from time import time, sleep

from random import randint

from threading import Thread, Lock
try:
    import netifaces
except:
    pass

import urllib.request
import xml.dom.minidom as minidom

class AttoException(Exception):
    def __init__(self, errorText = None, errorNumber = 0):
        self.errorText = errorText
        self.errorNumber = errorNumber

class AttoResult():
    def __init__(self, resultDict):
        self.resultDict = resultDict

    def __getitem__(self, index):
        if "error" in self.resultDict:
            raise AttoException("JSON error in %s" % self.resultDict['error'])

        resultList = self.resultDict.get("result", [])
        if len(resultList) <= index:
            raise AttoException(errorText="Unknown error occured", errorNumber=-1)
        return resultList[index]

    def __repr__(self):
        return json.dumps(self.resultDict)

    def __str__(self):
        return self.__repr__()


class Device(object):
    TCP_PORT        = 9090
    is_open         = False
    request_id      = randint(0, 1000000)
    request_id_lock = Lock()
    response_buffer = {}

    def __init__(self, address):
        self.address        = address
        self.language       = 0
        self.apiversion     = 2
        self.response_lock  = Lock()

    def __del__(self):
        self.close()

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def connect(self):
        """
            Initializes and connects the selected AMC device.
        """
        if not self.is_open:
            tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp.settimeout(10)
            tcp.connect((self.address, self.TCP_PORT))
            self.tcp = tcp
            if sys.version_info[0] > 2:
                self.bufferedSocket = tcp.makefile("rw", newline='\r\n')
            else:
                self.bufferedSocket = tcp.makefile("rw")
            self.is_open = True

    def close(self):
        """
            Closes the connection to the device.
        Returns
        -------
        """
        if self.is_open:
            self.bufferedSocket.close()
            self.tcp.close()
            self.is_open = False

    def sendRequest(self, method, params=False):
        req = {
                "jsonrpc": "2.0",
                "method": method,
                "api": self.apiversion
              }
        if params:
            req["params"] = params
        with Device.request_id_lock:
            req["id"] = Device.request_id
            self.bufferedSocket.write(json.dumps(req))
            self.bufferedSocket.flush()
            Device.request_id = Device.request_id + 1
            return req["id"]

    def getResponse(self, request_id):
        start_time = time()
        while True:
            if request_id in self.response_buffer:
                response = self.response_buffer[request_id]
                del self.response_buffer[request_id]
                return response
            if time() - start_time > 10:
                raise TimeoutError("No result")

            # Only one thread is allowed to read buffer
            # Otherwise, deadlock is possible
            if self.response_lock.acquire(blocking=False):
                try:
                    response = self.bufferedSocket.readline()
                    parsed = json.loads(response)
                    if parsed["id"] == request_id:
                        return AttoResult(parsed)
                    else:
                        self.response_buffer[parsed["id"]] = AttoResult(parsed)
                finally:
                    self.response_lock.release()
            else:
                # Sleep to unblock scheduler
                sleep(0.01)


    def request(self,method,params=False):
        """ Synchronous request.
        """
        if not self.is_open:
            raise AttoException("not connected, use connect()");
        request_id = self.sendRequest(method, params)
        return self.getResponse(request_id)

    def printError(self, errorNumber):
        """ Converts the errorNumber into an error string an prints it to the
        console.
        Parameters
        ----------
        errorNumber : int
        """
        print("Error! " + str(self.system_service.errorNumberToString(self.language, errorNumber)[1]))

    def handleError(self, response, ignoreFunctionError=False):
        errNo = response[0]
        if (errNo != 0 and errNo != 'null' and not ignoreFunctionError):
            raise AttoException(("Error! " + str(self.system_service.errorNumberToString(self.language ,errNo))), errNo)
        return errNo

    @staticmethod
    def discover(cls):
        try:
            network_ifaces = netifaces.interfaces()
        except NameError:
            print("Install netifaces for discovery")
            print("Python:")
            print("pip install netifaces")
            print("\nPython3:")
            print("pip3 install netifaces")
            return {}

        msg = \
           'M-SEARCH * HTTP/1.1\r\n' \
           'HOST:239.255.255.250:1900\r\n' \
           'ST:urn:schemas-attocube-com:device:' + str(cls) + ':1\r\n' \
           'MX:2\r\n' \
           'MAN:"ssdp:discover"\r\n' \
           '\r\n'

        def send_and_recv(iface, devices, devices_lock):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.bind((iface, 0))
            s.settimeout(2)
            s.sendto(str.encode(msg), ('239.255.255.250', 1900))
            try:
                while True:
                    _, addr = s.recvfrom(65507)
                    with devices_lock:
                        devices.append(addr[0])
            except socket.timeout:
                pass

        thread_pool = []
        devices = []
        devices_lock = Lock()

        for iface in network_ifaces:
            addr = netifaces.ifaddresses(iface)
            if netifaces.AF_INET not in addr:
                continue
            for ip in addr[netifaces.AF_INET]:
                if "addr" not in ip:
                    continue
                thread_pool.append(Thread(target=send_and_recv, args=(ip["addr"], devices, devices_lock)))
                thread_pool[-1].start()

        for thread in thread_pool:
            thread.join()

        def getElementData(xmlNode, tag):
            tagNodes = xmlNode.getElementsByTagName(tag)
            if len(tagNodes) == 0:
                return None
            childNodes = tagNodes[0].childNodes
            if len(childNodes) == 0:
                return None
            return childNodes[0].data

        deviceInfos = {}
        for ip in devices:
            try:
                location = "http://" + ip + ":49000/upnp.xml"
                response = urllib.request.urlopen(location)
                response = response.read()
                xmlNode = minidom.parseString(response)

                serialNumber = getElementData(xmlNode, 'serialNumber')
                ipAddress = getElementData(xmlNode, 'ipAddress')
                macAddress = getElementData(xmlNode, 'macAddress')
                friendlyName = getElementData(xmlNode, 'friendlyName')
                modelName = getElementData(xmlNode, 'modelName')
                lockedStatus = getElementData(xmlNode, 'lockedStatus')

                deviceInfos[ip] = (
                    serialNumber,
                    ipAddress,
                    macAddress,
                    friendlyName,
                    modelName,
                    lockedStatus
                )
            except:
                pass

        return deviceInfos
