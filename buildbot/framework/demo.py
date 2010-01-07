import os
import subprocess
import threading
import logging
import time
import signal
import sys
import random
import commands
import copy
import shutil
import glob

log = logging.getLogger("demo")

import paramiko
import socket
import select
import SocketServer

class FileNotFoundError(Exception):
    def __init__(self, p_path):
        super(FileNotFoundError, self).__init__("File not found: %s" % p_path)
    
class NodeConnectionProblemError(Exception): pass

#Classes for doing port tunnelling, taken from the paramiko example forward.py (http://github.com/robey/paramiko)
class ForwardServer(threading.Thread, SocketServer.ThreadingTCPServer):
    """
    This class has been modified from the example so that it runs in its own thread
    """
    daemon_threads = True
    allow_reuse_address = True
    
    def __init__(self, *args, **kwargs):
        threading.Thread.__init__(self)
        SocketServer.ThreadingTCPServer.__init__(self, *args, **kwargs)
        self.socket.settimeout(1)
        self.done = False
        self.setDaemon(True)
        
    def run(self):
        """TODO: Make threadsafe"""
        self.running = True
        while not self.done:
            self.handle_request()
        self.running = False
            
    def stopServer(self):
        log.debug("Stopping tunnel server")
        self.done = True
        #Wait for the thread to stop running
        while self.running:
            time.sleep(1)
        self.socket.close()
        log.debug("Tunnel server stopped")
 
class Handler(SocketServer.BaseRequestHandler):
    def handle(self):
        try:
            chan = self.ssh_transport.open_channel('direct-tcpip',
                                                   (self.chain_host, self.chain_port),
                                                   self.request.getpeername())
        except Exception, e:
            log.debug('Incoming request to %s:%d failed: %s' % (self.chain_host,
                                                              self.chain_port,
                                                              repr(e)))
            return
        if chan is None:
            log.debug('Incoming request to %s:%d was rejected by the SSH server.' %
                    (self.chain_host, self.chain_port))
            return
 
        log.info('Connected! Tunnel open %r -> %r -> %r' % (self.request.getpeername(),
                                                            chan.getpeername(), (self.chain_host, self.chain_port)))
        while True:
            r, w, x = select.select([self.request, chan], [], [])
            if self.request in r:
                data = self.request.recv(1024)
                if len(data) == 0:
                    break
                chan.send(data)
            if chan in r:
                data = chan.recv(1024)
                if len(data) == 0:
                    break
                self.request.send(data)
        chan.close()
        log.debug('Tunnel closed from %r' % (self.request.getpeername(),))
        self.request.close()


class RemoteCommandManager(threading.Thread):
    def __init__(self, p_command, p_node, p_fileDescriptors):
        threading.Thread.__init__(self)
        self.setDaemon(True)
        self.command = p_command
        self.node = p_node
        self.fileDescriptors = p_fileDescriptors
        self.doStop = False
        self.running = False
        self._output = ""
        self._pos = 0
        self._readPos = 0
        
    def run(self):      
        """TODO: Make threadsafe"""
        stdin, stdout, stderr = self.fileDescriptors
        stdout.channel.settimeout(0.5)
        print "Channel for %s: %r, %r" % (self.command, stdout.channel, stdin.channel)
        self.running = True
        channel = stdout.channel
        while not self.doStop and not channel.exit_status_ready():
            try:
                self._output += stdout.read()
            except socket.timeout, e:
                pass
            try:
                self._output += stderr.read()
            except socket.timeout, e:
                pass
            if len(self._output) > self._pos:
                log.debug("Command output: %s", self._output[self._pos:])
                self._pos = len(self._output)
        log.debug("%s finished" % self.command)
        stdout.channel.close()
        self.running = False
        
    def read(self, p_maxRead = None):
        if len(self._output) == self._readPos:
            return None
        if p_maxRead:
            if p_maxRead > len(self._output) - self._readPos:
                p_maxRead = len(self._output) - self._readPos
            return self._output[self._readPos:self._readPos+p_maxRead]
        else:
            return self._output[self._readPos:]
        
    def stop(self):
        log.debug("Stopping %s" % self.command)
        self.doStop = True
        while self.running:
            time.sleep(0.1)

class Node(object):
    def __init__(self, p_host=None, p_username=None, p_password=None, p_port=22, 
                 p_relayNode=None, p_platform=None):
        self.host = p_host
        self.username = p_username
        self.password = p_password
        self.port = p_port
        self.relayNode = p_relayNode
        self.platform = p_platform or {"darwin":"OSX", "win32":"Windows", "linux2":"Linux"}.get(sys.platform, "Linux")

        self.tunnels = []
        self.__client = None
        self.localRelayPort = 0
        self.clientLock = threading.Lock()
        
    def getDemoPath(self, *components):
        path = os.path.join(*components)
        path = path % {"os":self.platform}
        if os.path.isabs(path):
            return path
        return os.path.join("SparkDemo2009", path)
        
    def getOSName(self):
        return self.platform
    
    def getRemoteExecutable(self, p_base, p_node):
        class RemoteExecutable(p_base):
            node = p_node

            def __init__(self, p_path, *args, **kwargs):
                try:
                    super(RemoteExecutable, self).__init__(p_path, *args, **kwargs)
                except FileNotFoundError, e:
                    pass
                self.filePath = p_path

            def run(self, p_monitor = None):
                self.commandManager = self.node.runCommand(" ".join(self._prepareArgs()))
                
            def gracefulStop(self):
                self.commandManager.stop()
                
        return RemoteExecutable
    
    def getExecutable(self, *components):
        path = self.getDemoPath(os.path.join(*components))
        #TODO: glob.glob should be replaced with something that does the proper equivalent for remote nodes
        possibilities = glob.glob(path + "*")
        base = None
        log.debug("possibilities = %s for %s", str(possibilities), path)
        for possibility in possibilities:    
            if os.path.splitext(possibility)[1] == ".app":
                base = ExecutableApp
            elif os.path.splitext(possibility)[1] == ".jar":
                base = ExecutableJar
            elif self.platform == "Windows":
                base = WindowsExecutable
        if base is None:
            base = Executable
            
        if self.host:
            return self.getRemoteExecutable(base, self)(path)
        else:
            return base(path)
    
    def __connect(self):
        self.clientLock.acquire()
        try:
            log.info("Connecting via SSH to %s@%s on port %d", self.username, self.host, self.port)
            if self.relayNode is not None:
                self.localRelayPort = random.randint(1025,65535)
                #TODO: Should probably deal with port collisions here
                self.relayNode.startTunnel(self.localRelayPort, self.host, self.port)
                log.info("Using a tunnel to localhost on port %d", self.localRelayPort)
                self.__client.connect("127.0.0.1", self.localRelayPort, self.username, self.password)
            else:
                self.__client.connect(self.host, self.port, self.username, self.password)
        except Exception, e:
            log.exception("Exception while connecting")
        self.clientLock.release()
            
    def __getClient(self):
        """
        TODO: Add support for private keys
        """
        if self.__client is None:
            if self.host is None:
                raise NodeConnectionProblemError("Node does not have a hostname set")
            self.__client = paramiko.SSHClient()
            self.__client.load_system_host_keys()
            self.__client.set_missing_host_key_policy(paramiko.WarningPolicy())
            self.__connect()
        return self.__client
    client = property(__getClient)
    
    def isConnected(self):
        return self.__client is not None
    
    def restartSSH(self):
        log.debug("Restarting SSH")
        self.__connect()
        self.clientLock.acquire()
        try:
            tunnels = copy.copy(self.tunnels)
            for localPort, remoteHost, remotePort, tunnelServer in tunnels:
                tunnelServer.stopServer()
                self.startTunnel(localPort, remoteHost, remotePort)
        except Exception, e:
            log.exception("Exception while restoring tunnels")
        self.clientLock.release()
    
    def startTunnel(self, p_localPort, p_remoteHost, p_remotePort):
        transport = self.client.get_transport()
        #Example taken from the paramiko forward.py example
        class SubHander(Handler):
            chain_host = p_remoteHost
            chain_port = p_remotePort
            ssh_transport = transport
        tunnelServer = ForwardServer(('', p_localPort), SubHander)
        tunnelServer.start()
        self.tunnels.append((p_localPort, p_remoteHost, p_remotePort, tunnelServer))
    
    def runCommand(self, command):
        if self.host:
            log.debug("command = %s", command)
            try:
                stdin, stdout, stderr = self.client.exec_command(command)
            except Exception, e:
                log.exception(e)
                self.restartSSH()
                stdin, stdout, stderr = self.client.exec_command(command)
            output = RemoteCommandManager(command, self, (stdin, stdout, stderr))
            output.start()
        else:
            output = commands.getoutput(command)
        return output
        
    def writeFile(self, p_file, p_destination):
        """
        p_file should be a file object
        p_destination should be the location on this node to write the file to
        """
        source_handle = p_file.open()
        if self.host:
            sftp_client = self.client.open_sftp()
            destination_handle = sftp_client.open(p_destination, 'w')
        else:
            destination_handle = open(p_destination, 'w')
        destination_handle.write(source_handle.read())
        destination_handle.close()
        source_handle.close()
        if self.host:
            sftp_client.close()
        
    def cleanup(self):
        if self.isConnected():
            self.client.close()
        

class File(object):
    filePath = ""
    
    def __init__(self, p_path):
        if os.path.exists(p_path):
            self.filePath = p_path
        else:
            raise FileNotFoundError(p_path)
            
    def open(self, *p_args):
        return open(self.filePath, *p_args)

class Executable(File):
    cwd = None
    environment = None
    parameters = []
    process = None
    monitor = None
    
    def __init__(self, *args, **kwargs):
        self.cleanupFunc = None
        super(Executable, self).__init__(*args, **kwargs)
    
    def setCWD(self, p_cwd):
        self.cwd = p_cwd
    
    def setEnvironment(self, **p_kwargs):
        self.environment = p_kwargs
    
    def setParameters(self, *p_args):
        self.parameters = p_args
    
    def _prepareArgs(self):
        return [self.filePath] + list(self.parameters)
    
    def run(self, p_monitor = None):
        l_args = self._prepareArgs()
        if self.environment is not None:
            l_environment = os.environ
            l_environment.update(self.environment)
        else:
            l_environment = None
        
        l_stdin = None
        l_stdout = None
        l_stderr = None
        if p_monitor != self.monitor:
            l_stdin = subprocess.PIPE
            l_stdout, l_stderr = p_monitor.output
            log.debug("l_stdout = %s, l_stderr = %s", l_stdout, l_stderr)
            p_monitor.setExecutable(self)
            p_monitor.begin()
            self.monitor = p_monitor
        
        log.debug("Running %s", l_args)
        self.process = subprocess.Popen(
            args = l_args,
            stdin = l_stdin,
            stdout = l_stdout,
            stderr = l_stderr,
            cwd = self.cwd,
            env = l_environment
        )
    
    def setCleanupFunc(self, p_func):
        self.cleanupFunc = p_func
    
    def gracefulStop(self):
        if self.process != None:
            if "cleanupFunc" in dir(self) and self.cleanupFunc:
                self.cleanupFunc(self)
            try:
                self.process.terminate()
            except AttributeError, e:
                log.debug("process.terminate() failed")
                os.kill(self.process.pid, signal.SIGTERM)
            except WindowsError, e:
                log.exception("Stupid windows")
            
    
    def kill(self):
        if self.process != None:
            try:
                self.process.kill()
            except AttributeError, e:
                os.kill(self.process.pid, signal.SIGKILL)
                
    def restart(self):
        if self.process != None:
            self.gracefulStop()
        self.run(self.monitor)

class WindowsExecutable(Executable):
    def __init__(self, p_path):
        p_path = p_path + ".exe"
        super(WindowsExecutable, self).__init__(p_path)
            
class ExecutableApp(Executable):
    def __init__(self, p_path):
        p_path = p_path + ".app"
        if os.path.exists(p_path) and os.path.isdir(p_path):
            l_appName = os.path.basename(os.path.splitext(p_path)[0])
            log.debug("p_path = %s, l_appName = %s", p_path, l_appName)
            self.filePath = "%s/Contents/MacOS/%s" % (p_path, l_appName)
            log.debug("filePath = %s", self.filePath)
        else:
            raise FileNotFoundError(p_path)

class ExecutableJar(Executable):
    java = "java"
    def __init__(self, p_path, p_java="java"):
        p_path = p_path + ".jar"
        if os.path.exists(p_path):
            self.filePath = p_path
            self.java = p_java
        else:
            raise FileNotFoundError(p_path)
    
    def _prepareArgs(self):
        swigDir = os.path.join(os.path.dirname(self.filePath), "SWIG")
        return [self.java, "-Djava.library.path=%s" % swigDir, "-jar", self.filePath] + list(self.parameters)
        
class ExecutablePy(Executable):
    python = "python"

    def __init__(self, p_path, p_python="python"):
        p_path = p_path + ".py"
        if os.path.exists(p_path):
            self.filePath = p_path
            self.python = p_python
        else:
            raise FileNotFoundError(p_path)

    def _prepareArgs(self):
        return [self.python, self.filePath] + list(self.parameters)
        
class ExecutableEgg(ExecutablePy):
    def _prepareArgs(self):
        l_dir = os.path.dirname(self.filePath)
        l_module = os.path.basename(self.filePath).split('-')[0]
        return [self.python, "-c", "import sys, os; sys.path.insert(0, os.path.abspath('%s')); from %s import main; sys.exit(main())" % (l_dir, l_module)] + list(self.parameters)

class Monitor(object):
    def __init__(self, p_output = None):
        """
        p_output is either a tuple of the stdout and stderr handles, or
        a single handle to be used for both stdout and stderr
        specifying None for output will prevent either from being displayed
        specifying (None, None) will cause both to be displayed
        """
        if isinstance(p_output, tuple):
            self.output = p_output
        elif p_output is None:
            self.output = p_output
        else:
            self.output = (p_output, p_output)
        log.debug(self.output)
        
        if self.output is None:
            log.debug("Using monitor defaults")
            self.output = (subprocess.PIPE, subprocess.PIPE)
    
    def setExecutable(self, p_exec):
        self.executable = p_exec

    def begin(self):
        pass
        
class RestartMonitor(Monitor, threading.Thread):
    def __init__(self, p_output = None):
        Monitor.__init__(self, p_output)
        threading.Thread.__init__(self)
        self.setDaemon(True)
        
    def run(self):
        pass
        
    def begin(self):
        self.start()

class CommandLineInterface(object):
    commands = {}
    
    def __init__(self, cleanupHandler = None):
        self.cleanupHandler = cleanupHandler
    
    def prompt(self, text, expected_reply = None):
        try:
            cmd = raw_input(text + ": ")
        except KeyboardInterrupt, e:
            if self.cleanupHandler:
                self.cleanupHandler()
            else:
                sys.exit(0)
            
        if expected_reply and cmd in expected_reply:
            return cmd
        for command, handler in self.commands.items():
            if cmd == command:
                return handler()
        return None
        
    
