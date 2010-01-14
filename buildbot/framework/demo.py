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
from subprocess import Popen, PIPE

log = logging.getLogger("demo")

import paramiko
import socket
import select
import SocketServer

class FileNotFoundError(Exception):
    def __init__(self, p_path):
        super(FileNotFoundError, self).__init__("File not found: %s" % p_path)
    
class NodeConnectionProblemError(Exception): pass
        
# scp.py
# Copyright (C) 2008 James Bardin <jbar...@bu.edu>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.


"""
Utilities for sending files over ssh using the scp1 protocol.
"""

class scpClient(threading.Thread):
    """
    An scp1 implementation, compatible with openssh scp.
    Raises scpException for all transport related errors. Local filesystem
    and OS errors pass through. 

    Main public methods are .put and .get 
    The get method is controlled by the remote scp instance, and behaves 
    accordingly. This means that symlinks are resolved, and the transfer is
    halted after too many levels of symlinks are detected.
    The put method uses os.walk for recursion, and sends files accordingly.
    Since scp doesn't support symlinks, we send file symlinks as the file
    (matching scp behaviour), but we make no attempt at symlinked directories.
    """
    def __init__(self, p_hostName, p_userName, p_password, buff_size = 16384, socket_timeout = 5.0):
        """
        Create an scp1 client.

        @param transport: an existing paramiko L{Transport}
        @type transport: L{Transport}
        @param buff_size: size of the scp send buffer.
        @type buff_size: int
        @param socket_timeout: channel socket timeout in seconds
        @type socket_timeout: float
        """
        threading.Thread.__init__(self)
        self.running = False
        self.callback = None
        self.exit_code = 0
        
        self.m_hostName = p_hostName
        self.m_userName = p_userName
        self.m_password = p_password
        self.buff_size = buff_size
        self.socket_timeout = socket_timeout
        self.channel = None
        self.preserve_times = False
        self._recv_dir = ''
        self._utime = None
        self._dirtimes = {}
    
    def scp(self, p_put = True, recursive = False, local_path = '',
            remote_path = '.', preserve_times = False, callback = None):
        self.m_put = p_put
        self.local_path = local_path
        self.remote_path = remote_path
        self.recursive = recursive
        self.preserve_times = preserve_times
        self.callback = callback
        self.running = True; 
        self.start()   
            
    def run(self):
        try:
            if self.m_put:
                self._put(self.local_path, self.remote_path, self.recursive, self.preserve_times)
            else:
                self._get(self.remote_path, self.local_path, self.recursive, self.preserve_times)
        except:
            log.error('scp exception.')
            self.exit_code = 1
        else:
            self.exit_code = 0
        finally:
            self.running = False
            if self.callback:
                self.callback()

    def _put(self, files, remote_path = '.', 
            recursive = False, preserve_times = False):
        """
        Transfer files to remote host.

        @param files: A single path, or a list of paths to be transfered.
            recursive must be True to transfer directories.
        @type files: string OR list of strings
        @param remote_path: path in which to receive the files on the remote
            host. defaults to '.'
        @type remote_path: str
        @param recursive: transfer files and directories recursively
        @type recursive: bool
        @param preserve_times: preserve mtime and atime of transfered files
            and directories.
        @type preserve_times: bool
        """
        # Socket connection to remote host
        self.l_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.l_sock.connect((self.m_hostName, 22))
        # Build a SSH transport
        self.l_transport = paramiko.Transport(self.l_sock)
        self.l_transport.start_client()
        self.l_transport.auth_password(self.m_userName, self.m_password)
        
        self.preserve_times = preserve_times
        self.channel = self.l_transport.open_session()
        self.channel.settimeout(self.socket_timeout)
        scp_command = ('scp -t %s\n', 'scp -r -t %s\n')[recursive]
        self.channel.exec_command(scp_command % remote_path)
        self._recv_confirm()

        if not isinstance(files, (list, tuple)):
            files = [files]

        if recursive:
            self._send_recursive(files)
        else:
            self._send_files(files)

        if self.channel:
            self.channel.close()
        if self.l_transport:
            self.l_transport.close()
        if self.l_sock:
            self.l_sock.close()

    def _get(self, remote_path, local_path = '',
            recursive = False, preserve_times = False):
        """
        Transfer files from remote host to localhost

        @param remote_path: path to retreive from remote host. since this is
            evaluated by scp on the remote host, shell wildcards and 
            environment variables may be used.
        @type remote_path: str
        @param local_path: path in which to receive files locally
        @type local_path: str
        @param recursive: transfer files and directories recursively
        @type recursive: bool
        @param preserve_times: preserve mtime and atime of transfered files
            and directories.
        @type preserve_times: bool
        """
        # Socket connection to remote host
        self.l_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.l_sock.connect((self.m_hostName, 22))
        # Build a SSH transport
        self.l_transport = paramiko.Transport(self.l_sock)
        self.l_transport.start_client()
        self.l_transport.auth_password(self.m_userName, self.m_password)
        
        self._recv_dir = local_path or os.getcwd() 
        rcsv = ('', ' -r')[recursive]
        prsv = ('', ' -p')[preserve_times]
        self.channel = self.l_transport.open_session()
        self.channel.settimeout(self.socket_timeout)
        self.channel.exec_command('scp%s%s -f %s' % (rcsv, prsv, remote_path))
        self._recv_all()

        if self.channel:
            self.channel.close()
        if self.l_transport:
            self.l_transport.close()
        if self.l_sock:
            self.l_sock.close()

    def _read_stats(self, name):
        """return just the file stats needed for scp"""
        stats = os.stat(name)
        mode = oct(stats.st_mode)[-4:]
        size = stats.st_size
        atime = int(stats.st_atime)
        mtime = int(stats.st_mtime)
        return (mode, size, mtime, atime)

    def _send_files(self, files): 
        for name in files:
            basename = os.path.basename(name)
            (mode, size, mtime, atime) = self._read_stats(name)
            if self.preserve_times:
                self._send_time(mtime, atime)
            file_hdl = file(name, 'rb')
            self.channel.sendall('C%s %d %s\n' % (mode, size, basename))
            self._recv_confirm()
            file_pos = 0
            buff_size = self.buff_size
            chan = self.channel
            while file_pos < size:
                chan.sendall(file_hdl.read(buff_size))
                file_pos = file_hdl.tell()
            chan.sendall('\x00')
            file_hdl.close()

    def _send_recursive(self, files):
        for base in files:
            lastdir = base
            for root, dirs, fls in os.walk(base):
                # pop back out to the next dir in the walk
                while lastdir != os.path.commonprefix([lastdir, root]):
                    self._send_popd()
                    lastdir = os.path.split(lastdir)[0]
                self._send_pushd(root)
                lastdir = root
                self._send_files([os.path.join(root, f) for f in fls])

    def _send_pushd(self, directory):
        (mode, size, mtime, atime) = self._read_stats(directory)
        basename = os.path.basename(directory)
        if self.preserve_times:
            self._send_time(mtime, atime)
        self.channel.sendall('D%s 0 %s\n' % (mode, basename))
        self._recv_confirm()

    def _send_popd(self):
        self.channel.sendall('E\n')
        self._recv_confirm()

    def _send_time(self, mtime, atime):
        self.channel.sendall('T%d 0 %d 0\n' % (mtime, atime))
        self._recv_confirm()

    def _recv_confirm(self):
        # read scp response
        msg = ''
        try:
            msg = self.channel.recv(512)
        except socket.timeout:
            raise scpException('Timout waiting for scp response')
        if msg and msg[0] == '\x00':
            return
        elif msg and msg[0] == '\x01':
            raise scpException(msg[1:])
        elif self.channel.recv_stderr_ready():
            msg = self.channel.recv_stderr(512)
            raise scpException(msg)
        elif not msg:
            raise scpException('No response from server')
        else:
            raise scpException('Invalid response from server: ' + msg)

    def _recv_all(self):
        # loop over scp commands, and recive as necessary
        command = {'C': self._recv_file,
                   'T': self._set_time,
                   'D': self._recv_pushd,
                   'E': self._recv_popd}
        while not self.channel.closed:
            # wait for command as long as we're open
            self.channel.sendall('\x00')
            msg = self.channel.recv(1024)
            if not msg: # chan closed while recving
                break
            code = msg[0]
            try:
                command[code](msg[1:])
            except KeyError:
                raise scpException(repr(msg))
        # directory times can't be set until we're done writing files
        self._set_dirtimes()

    def _set_time(self, cmd):
        try:
            times = cmd.split()
            mtime = int(times[0])
            atime = int(times[2]) or mtime
        except:
            self.channel.send('\x01')
            raise scpException('Bad time format')
        # save for later
        self._utime = (mtime, atime)

    def _recv_file(self, cmd):
        chan = self.channel
        parts = cmd.split()
        try:
            mode = int(parts[0], 8)
            size = int(parts[1])
            path = os.path.join(self._recv_dir, parts[2])
        except:
            chan.send('\x01')
            chan.close()
            raise scpException('Bad file format')

        try:
            file_hdl = file(path, 'wb')
        except IOError, e:
            chan.send('\x01'+e.message)
            chan.close()
            raise

        buff_size = self.buff_size
        pos = 0
        chan.send('\x00')
        try:
            while pos < size:
                # we have to make sure we don't read the final byte
                if size - pos <= buff_size:
                    buff_size = size - pos
                file_hdl.write(chan.recv(buff_size))
                pos = file_hdl.tell()

            msg = chan.recv(512)
            if msg and msg[0] != '\x00':
                raise scpException(msg[1:])
        except socket.timeout:
            chan.close()
            raise scpException('Error receiving, socket.timeout')

        file_hdl.truncate()
        try:
            os.utime(path, self._utime)
            self._utime = None
            os.chmod(path, mode)
            # should we notify the other end?
        finally:
            file_hdl.close()
        # '\x00' confirmation sent in _recv_all

    def _recv_pushd(self, cmd):
        parts = cmd.split()
        try:
            mode = int(parts[0], 8)
            path = os.path.join(self._recv_dir, parts[2])
        except:
            self.channel.send('\x01')
            raise scpException('Bad directory format')
        try:
            if not os.path.exists(path):
                os.mkdir(path, mode)
            elif os.path.isdir(path):
                os.chmod(path, mode)
            else:
                raise scpException('%s: Not a directory' % path)
            self._dirtimes[path] = (self._utime)
            self._utime = None
            self._recv_dir = path
        except (OSError, scpException), e:
            self.channel.send('\x01'+e.message)
            raise

    def _recv_popd(self, *cmd):
        self._recv_dir = os.path.split(self._recv_dir)[0]

    def _set_dirtimes(self):
        try:
            for d in self._dirtimes:
                os.utime(d, self._dirtimes[d])
        finally:
            self._dirtimes = {}


class scpException(Exception):
    """scp exception class"""
    pass


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


class CommandManager(threading.Thread):
    def __init__(self, p_command, p_node, p_callback=None, p_fileDescriptors=None):
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
        self.exit_code = 0
        self.callback = p_callback
        
    def start(self):
        threading.Thread.start(self)
        self.running = True

    def run(self): 
        """TODO: Make threadsafe"""
        p = Popen(self.command, shell=True, bufsize=2048, stdout=PIPE, stderr=PIPE, stdin=PIPE)
        log.debug("Running %s" % self.command)
        stdout, stderr, stdin = p.stdout, p.stderr, p.stdin
        while not self.doStop and not p.poll() and p.returncode is None:
            try:
                self._output += stdout.read()
            except Exception, e:
                log.debug('Command timeout: %s' % e.message)
                break
            try:
                self._output += stderr.read()
            except Exception, e:
                log.debug('Command timeout: %s' % e.message)
                break
            if len(self._output) > self._pos:
                log.debug("Command output: %s", self._output[self._pos:])
                self._pos = len(self._output)
        stdin.close()
        self.exit_code = p.returncode
        log.debug("%s finished" % self.command)
        self.running = False
        if self.callback:
            self.callback()
        
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

class RemoteCommandManager(CommandManager):
    def run(self): 
        """TODO: Make threadsafe"""
        stdin, stdout, stderr = self.fileDescriptors
        stdout.channel.settimeout(5)
        log.info("Channel for %s: %r, %r" % (self.command, stdout.channel, stdin.channel))
        channel = stdout.channel
        while not self.doStop and not channel.exit_status_ready():
            try:
                self._output += stdout.read()
            except socket.timeout, e:
                log.debug('Command timeout: %s' % repr(e))
                break
            try:
                self._output += stderr.read()
            except socket.timeout, e:
                log.debug('Command timeout: %s' % repr(e))
                break
            if len(self._output) > self._pos:
                log.debug("Command output: %s", self._output[self._pos:])
                self._pos = len(self._output)
        if(channel):
            self.exit_code = channel.recv_exit_status()
            stdout.channel.close()
        else:
            self.exit_code = -1
        log.debug("%s finished" % self.command)
        self.running = False
        if self.callback:
            self.callback()

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
    
    def runCommand(self, command, callback=None):
        if self.host:
            log.debug("command = %s", command)
            try:
                stdin, stdout, stderr = self.client.exec_command(command)
            except Exception, e:
                log.exception(e)
                self.restartSSH()
                stdin, stdout, stderr = self.client.exec_command(command)
            output = RemoteCommandManager(command, self, callback, (stdin, stdout, stderr))
            output.start()
        else:
            output = CommandManager(command, self, callback)
            output.start()
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
        
    
