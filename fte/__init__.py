#!/usr/bin/env python
# -*- coding: utf-8 -*-

# This file is part of fteproxy.
#
# fteproxy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# fteproxy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with fteproxy.  If not, see <http://www.gnu.org/licenses/>.

import socket
import string

import fte.network_io
import fte.conf
import fte.defs
import fte.encoder
import fte.encrypter
import fte.record_layer


class InvalidRoleException(Exception):
    pass


class NegotiationFailedException(Exception):
    pass


class ChannelNotReadyException(Exception):
    pass


class NegotiateTimeoutException(Exception):

    """Raised when negotiation fails to complete after """ + str(fte.conf.getValue('runtime.fte.negotiate.timeout')) + """ seconds.
    """
    pass


class NegotiateCell(object):
    _CELL_SIZE = 64
    _PADDING_LEN = 32
    _PADDING_CHAR = '\x00'
    _DATE_FORMAT = 'YYYYMMDD'

    def __init__(self):
        self._def_file = ""
        self._language = ""

    def setDefFile(self, def_file):
        self._def_file = def_file

    def getDefFile(self):
        return self._def_file

    def setLanguage(self, language):
        self._language = language

    def getLanguage(self):
        return self._language

    def toString(self):
        retval = ''
        retval += self._def_file
        retval += self._language
        retval = string.rjust(
            retval, NegotiateCell._CELL_SIZE, NegotiateCell._PADDING_CHAR)
        assert retval[:NegotiateCell._PADDING_LEN] == NegotiateCell._PADDING_CHAR * \
            NegotiateCell._PADDING_LEN
        return retval

    def fromString(self, negotiate_cell_str):
        assert len(negotiate_cell_str) == NegotiateCell._CELL_SIZE
        assert negotiate_cell_str[
            :NegotiateCell._PADDING_LEN] == NegotiateCell._PADDING_CHAR * NegotiateCell._PADDING_LEN
        negotiate_cell_str = negotiate_cell_str.strip(
            NegotiateCell._PADDING_CHAR)
        # 8==len(YYYYMMDD)
        def_file = negotiate_cell_str[:len(NegotiateCell._DATE_FORMAT)]
        language = negotiate_cell_str[len(NegotiateCell._DATE_FORMAT):]
        negotiate_cell = NegotiateCell()
        negotiate_cell.setDefFile(def_file)
        negotiate_cell.setLanguage(language)
        return negotiate_cell


class NegotiationManager(object):

    def __init__(self):
        self._negotiationComplete = False

    def getNegotiationComplete(self):
        return self._negotiationComplete

    def _acceptNegotiation(self, encrypter, data):

        languages = fte.defs.load_definitions()
        for incoming_language in languages.keys():
            try:
                if incoming_language.endswith('response'):
                    continue

                incoming_regex = fte.defs.getRegex(incoming_language)
                incoming_fixed_slice = fte.defs.getFixedSlice(incoming_language)

                incoming_decoder = fte.encoder.RegexEncoder(incoming_regex,
                                                            incoming_fixed_slice)
                decoder = fte.record_layer.Decoder(decrypter=encrypter,
                                                   decoder=incoming_decoder)

                decoder.push(data)
                negotiate_cell = decoder.pop(oneCell=True)
                NegotiateCell().fromString(negotiate_cell)

                return [negotiate_cell, decoder._buffer]
            except:
                continue

        raise NegotiationFailedException()

    def _init_encoders(self, encrypter,
                       outgoing_regex, outgoing_fixed_slice,
                       incoming_regex, incoming_fixed_slice):

        encoder = None
        decoder = None

        if outgoing_regex != None and outgoing_fixed_slice != -1:
            outgoing_encoder = fte.encoder.RegexEncoder(outgoing_regex,
                                                        outgoing_fixed_slice)
            encoder = fte.record_layer.Encoder(encrypter=encrypter,
                                               encoder=outgoing_encoder)

        if incoming_regex != None and incoming_fixed_slice != -1:
            incoming_decoder = fte.encoder.RegexEncoder(incoming_regex,
                                                        incoming_fixed_slice)
            decoder = fte.record_layer.Decoder(decrypter=encrypter,
                                               decoder=incoming_decoder)

        return [encoder, decoder]

    def _makeNegotiationCell(self, encoder):
        negotiate_cell = NegotiateCell()
        def_file = fte.conf.getValue('fte.defs.release')
        negotiate_cell.setDefFile(def_file)
        language = fte.conf.getValue('runtime.state.upstream_language')
        language = language[:-len('-request')]
        negotiate_cell.setLanguage(language)
        encoder.push(negotiate_cell.toString())
        data = encoder.pop()
        return data

    def makeClientNegotiationCell(self, encrypter,
                                  outgoing_regex, outgoing_fixed_slice,
                                  incoming_regex, incoming_fixed_slice):
        [encoder, decoder] = self._init_encoders(
            encrypter, outgoing_regex, outgoing_fixed_slice, incoming_regex, incoming_fixed_slice)
        return self._makeNegotiationCell(encoder)

    def doServerSideNegotiation(self, encrypter, data):
        [negotiate_cell, remaining_buffer] = self._acceptNegotiation(
            encrypter, data)

        negotiate = NegotiateCell().fromString(negotiate_cell)

        outgoing_language = negotiate.getLanguage() + '-response'
        incoming_language = negotiate.getLanguage() + '-request'

        outgoing_regex = fte.defs.getRegex(outgoing_language)
        outgoing_fixed_slice = fte.defs.getFixedSlice(outgoing_language)
        incoming_regex = fte.defs.getRegex(incoming_language)
        incoming_fixed_slice = fte.defs.getFixedSlice(incoming_language)

        [encoder, decoder] = self._init_encoders(
            encrypter, outgoing_regex, outgoing_fixed_slice, incoming_regex, incoming_fixed_slice)

        decoder.push(remaining_buffer)

        return [encoder, decoder]


class FTEHelper(object):

    def _processRecv(self, data):
        retval = data
        if self._isServer and not self._negotiationComplete:
            try:
                self._preNegotiationBuffer_incoming += data
                [encoder, decoder] = self._negotiation_manager.doServerSideNegotiation(
                    self._encrypter, self._preNegotiationBuffer_incoming)
                self._encoder = encoder
                self._decoder = decoder
                self._preNegotiationBuffer_incoming = ''
                self._negotiationComplete = True
                retval = ''
            except:
                raise ChannelNotReadyException()

        return retval

    def _processSend(self):
        retval = ''
        if self._isClient and not self._negotiationComplete:
            [encoder, decoder] = self._negotiation_manager._init_encoders(
                self._encrypter,
                self._outgoing_regex,
                self._outgoing_fixed_slice,
                self._incoming_regex,
                self._incoming_fixed_slice)
            self._encoder = encoder
            self._decoder = decoder
            negotiation_cell = self._negotiation_manager.makeClientNegotiationCell(
                self._encrypter,
                self._outgoing_regex, self._outgoing_fixed_slice,
                self._incoming_regex, self._incoming_fixed_slice)
            retval = negotiation_cell
            self._negotiationComplete = True
        return retval


class _FTESocketWrapper(FTEHelper, object):

    def __init__(self, _socket,
                 outgoing_regex=None, outgoing_fixed_slice=-1,
                 incoming_regex=None, incoming_fixed_slice=-1,
                 K1=None, K2=None):

        self._socket = _socket
        self._outgoing_regex = outgoing_regex
        self._outgoing_fixed_slice = outgoing_fixed_slice
        self._incoming_regex = incoming_regex
        self._incoming_fixed_slice = incoming_fixed_slice
        self._K1 = K1
        self._K2 = K2

        self._encrypter = fte.encrypter.Encrypter(K1=self._K1,
                                                  K2=self._K2)

        self._negotiation_manager = NegotiationManager()
        self._negotiationComplete = False
        self._isServer = (outgoing_regex is None and incoming_regex is None)
        self._isClient = (
            outgoing_regex is not None and incoming_regex is not None)
        self._incoming_buffer = ''
        self._preNegotiationBuffer_outgoing = ''
        self._preNegotiationBuffer_incoming = ''

    def fileno(self):
        return self._socket.fileno()

    def recv(self, bufsize):
        ### <HACK>
        # Required to deal with case when client attempts to recv
        # before sending. This checks to ensure that a negotiate
        # cell is sent no matter what the client does first.
        to_send = self._processSend()
        if to_send:
            numbytes = self._socket.send(to_send)
            assert numbytes == len(to_send)
        ### </HACK>
            
        try:
            while True:
                data = self._socket.recv(bufsize)
                noData = (data == '')
                data = self._processRecv(data)

                if noData and not self._incoming_buffer and not self._decoder._buffer:
                    return ''

                self._decoder.push(data)

                while True:
                    frag = self._decoder.pop()
                    if not frag:
                        break
                    self._incoming_buffer += frag

                if self._incoming_buffer:
                    break

            retval = self._incoming_buffer[:bufsize]
            self._incoming_buffer = self._incoming_buffer[bufsize:]
        except ChannelNotReadyException:
            raise socket.timeout

        return retval

    def send(self, data):
        to_send = self._processSend()
        if to_send:
            self._socket.sendall(to_send)

        self._encoder.push(data)
        while True:
            to_send = self._encoder.pop()
            if not to_send:
                break
            self._socket.sendall(to_send)
        return len(data)

    def sendall(self, data):
        self.send(data)
        return None

    def gettimeout(self):
        return self._socket.gettimeout()

    def settimeout(self, val):
        return self._socket.settimeout(val)

    def shutdown(self, flags):
        return self._socket.shutdown(flags)

    def close(self):
        return self._socket.close()

    def connect(self, addr):
        return self._socket.connect(addr)

    def accept(self):
        conn, addr = self._socket.accept()
        conn = _FTESocketWrapper(conn,
                                 self._outgoing_regex, self._outgoing_fixed_slice,
                                 self._incoming_regex, self._incoming_fixed_slice,
                                 self._K1, self._K2)

        return conn, addr

    def bind(self, addr):
        return self._socket.bind(addr)

    def listen(self, N):
        return self._socket.listen(N)


def wrap_socket(sock,
                outgoing_regex=None, outgoing_fixed_slice=-1,
                incoming_regex=None, incoming_fixed_slice=-1,
                K1=None, K2=None):
    """``fte.wrap_socket`` turns an existing socket into an fteproxy socket.

    The input parameter ``sock`` is the socket to wrap.
    The parameter ``outgoing_regex`` specifies the format of the messages
    to send via the socket. The ``outgoing_fixed_slice`` parameter specifies the
    maximum length of the strings in ``outgoing_regex``.
    The parameters ``incoming_regex`` and ``incoming_fixed_slice`` are defined
    similarly.
    The optional parameters ``K1`` and ``K2`` specify 128-bit keys to be used
    in FTE's underlying AE scheme. If specified, these values must be 16-byte
    hex strings.
    """

    assert K1 == None or len(K1) == 16
    assert K2 == None or len(K2) == 16

    socket_wrapped = _FTESocketWrapper(
        sock,
        outgoing_regex, outgoing_fixed_slice,
        incoming_regex, incoming_fixed_slice,
        K1, K2)
    return socket_wrapped


import obfsproxy.network.network as network
import obfsproxy.network.socks as socks
import obfsproxy.network.extended_orport as extended_orport
import obfsproxy.transports.base

import twisted.internet


class FTETransport(FTEHelper, obfsproxy.transports.base.BaseTransport):

    def __init__(self, pt_config):
        self._isClient = (fte.conf.getValue('runtime.mode') == 'client')
        self._isServer = not self._isClient
        if self._isClient:
            outgoing_language = fte.conf.getValue(
                'runtime.state.upstream_language')
            incoming_language = fte.conf.getValue(
                'runtime.state.downstream_language')
            self._outgoing_regex = fte.defs.getRegex(outgoing_language)
            self._outgoing_fixed_slice = fte.defs.getFixedSlice(outgoing_language)
            self._incoming_regex = fte.defs.getRegex(incoming_language)
            self._incoming_fixed_slice = fte.defs.getFixedSlice(incoming_language)
        else:
            self._outgoing_regex = None
            self._outgoing_fixed_slice = -1
            self._incoming_regex = None
            self._incoming_fixed_slice = -1

        self._K1 = fte.conf.getValue('runtime.fte.encrypter.key')[0:16]
        self._K2 = fte.conf.getValue('runtime.fte.encrypter.key')[16:32]
        self._encrypter = fte.encrypter.Encrypter(K1=self._K1,
                                                  K2=self._K2)

        self._negotiation_manager = NegotiationManager()
        self._negotiationComplete = False
        self._incoming_buffer = ''
        self._preNegotiationBuffer_outgoing = ''
        self._preNegotiationBuffer_incoming = ''

    def receivedDownstream(self, data, circuit):
        """decode fteproxy stream"""

        try:
            data = data.read()
            data = self._processRecv(data)

            self._decoder.push(data)

            while True:
                frag = self._decoder.pop()
                if not frag:
                    break
                circuit.upstream.write(frag)

        except ChannelNotReadyException:
            pass

    def receivedUpstream(self, data, circuit):
        """encode fteproxy stream"""
        to_send = self._processSend()
        if to_send:
            circuit.downstream.write(to_send)

        data = data.read()
        self._encoder.push(data)
        while True:
            to_send = self._encoder.pop()
            if not to_send:
                break
            circuit.downstream.write(to_send)


class FTETransportClient(FTETransport):
    pass


class FTETransportServer(FTETransport):
    pass


def launch_transport_listener(transport, bindaddr, role, remote_addrport, pt_config, ext_or_cookie_file=None):
    """
    Launch a listener for 'transport' in role 'role' (socks/client/server/ext_server).

    If 'bindaddr' is set, then listen on bindaddr. Otherwise, listen
    on an ephemeral port on localhost.
    'remote_addrport' is the TCP/IP address of the other end of the
    circuit. It's not used if we are in 'socks' role.

    'pt_config' contains configuration options (such as the state location)
    which are of interest to the pluggable transport.

    'ext_or_cookie_file' is the filesystem path where the Extended
    ORPort Authentication cookie is stored. It's only used in
    'ext_server' mode.

    Return a tuple (addr, port) representing where we managed to bind.

    Throws obfsproxy.transports.transports.TransportNotFound if the
    transport could not be found.

    Throws twisted.internet.error.CannotListenError if the listener
    could not be set up.
    """

    listen_host = bindaddr[0] if bindaddr else 'localhost'
    listen_port = int(bindaddr[1]) if bindaddr else 0

    if role == 'socks':
        transport_class = FTETransportClient
        factory = socks.SOCKSv4Factory(transport_class, pt_config)
    elif role == 'ext_server':
        assert(remote_addrport and ext_or_cookie_file)
        transport_class = FTETransportServer
        factory = extended_orport.ExtORPortServerFactory(
            remote_addrport, ext_or_cookie_file, transport, transport_class, pt_config)
    elif role == 'client':
        assert(remote_addrport)
        transport_class = FTETransportClient
        factory = network.StaticDestinationServerFactory(
            remote_addrport, role, transport_class, pt_config)
    elif role == 'server':
        assert(remote_addrport)
        transport_class = FTETransportServer
        factory = network.StaticDestinationServerFactory(
            remote_addrport, role, transport_class, pt_config)
    else:
        raise InvalidRoleException()

    addrport = twisted.internet.reactor.listenTCP(
        listen_port, factory, interface=listen_host)

    return (addrport.getHost().host, addrport.getHost().port)
