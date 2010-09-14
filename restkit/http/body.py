# -*- coding: utf-8 -
#
# This file is part of restkit released under the MIT license. 
# See the NOTICE for more information.

import sys
import zlib

try:
    from cStringIO import StringIO
except ImportError:
    from StringIO import StringIO

from restkit.errors import NoMoreData, ChunkMissingTerminator, \
InvalidChunkSize

class ChunkedReader(object):
    def __init__(self, req, unreader):
        self.unreader = unreader
        self.req = req
        self.parser = self.parse_chunked(unreader)
        self.buf = StringIO()
    
    def read(self, size):
        if not isinstance(size, (int, long)):
            raise TypeError("size must be an integral type")
        if size <= 0:
            raise ValueError("Size must be positive.")
        if size == 0:
            return ""

        if self.parser:
            while self.buf.tell() < size:
                try:
                    self.buf.write(self.parser.next())
                except StopIteration:
                    self.parser = None
                    break

        data = self.buf.getvalue()
        ret, rest = data[:size], data[size:]
        self.buf.truncate(0)
        self.buf.write(rest)
        return ret
    
    def parse_trailers(self, unreader, data, eof=False):
        buf = StringIO()
        buf.write(data)
        
        idx = buf.getvalue().find("\r\n\r\n")
        done = buf.getvalue()[:2] == "\r\n"

        while idx < 0 and not done:
            self.get_data(unreader, buf)  
            idx = buf.getvalue().find("\r\n\r\n")
            done = buf.getvalue()[:2] == "\r\n"
        if done:
            unreader.unread(buf.getvalue()[2:])
            return ""
        self.req.trailers = self.req.parse_headers(buf.getvalue()[:idx])
        unreader.unread(buf.getvalue()[idx+4:])

    def parse_chunked(self, unreader):
        (size, rest) = self.parse_chunk_size(unreader)
        while size > 0:
            while size > len(rest):
                size -= len(rest)
                yield rest
                rest = unreader.read()
                if not rest:
                    raise NoMoreData()
            yield rest[:size]
            # Remove \r\n after chunk
            rest = rest[size:]
            while len(rest) < 2:
                rest += unreader.read()
            if rest[:2] != '\r\n':
                raise ChunkMissingTerminator(rest[:2])
            (size, rest) = self.parse_chunk_size(unreader, data=rest[2:])          

    def parse_chunk_size(self, unreader, data=None):
        buf = StringIO()
        if data is not None:
            buf.write(data)

        idx = buf.getvalue().find("\r\n")
        while idx < 0:
            self.get_data(unreader, buf)
            idx = buf.getvalue().find("\r\n")

        data = buf.getvalue()
        line, rest_chunk = data[:idx], data[idx+2:]
    
        chunk_size = line.split(";", 1)[0].strip()
        try:
            chunk_size = int(chunk_size, 16)
        except ValueError:
            raise InvalidChunkSize(chunk_size)

        if chunk_size == 0:
            try:
                self.parse_trailers(unreader, rest_chunk)
            except NoMoreData:
                pass
            return (0, None)
        return (chunk_size, rest_chunk)

    def get_data(self, unreader, buf):
        data = unreader.read()
        if not data:
            raise NoMoreData()
        buf.write(data)

class LengthReader(object):
    def __init__(self, req, unreader, length):
        self.req = req
        self.unreader = unreader
        self.length = length
    
    def read(self, size):
        if not isinstance(size, (int, long)):
            raise TypeError("size must be an integral type")
            
        size = min(self.length, size)
        if size < 0:
            raise ValueError("Size must be positive.")
        if size == 0:
            return ""       

        buf = StringIO()
        data = self.unreader.read()
        while data:
            buf.write(data)
            if buf.tell() >= size:
                break
            data = self.unreader.read()
        
        
        buf = buf.getvalue()
        ret, rest = buf[:size], buf[size:]
        self.unreader.unread(rest)
        self.length -= size
        return ret

class EOFReader(object):
    def __init__(self, req, unreader):
        self.req = req
        self.unreader = unreader
        self.buf = StringIO()
        self.finished = False
    
    def read(self, size):
        if not isinstance(size, (int, long)):
            raise TypeError("size must be an integral type")
        if size < 0:
            raise ValueError("Size must be positive.")
        if size == 0 or self.finished:
            return ""

        
        data = self.unreader.read()
        while data:
            self.buf.write(data)
            if self.buf.tell() > size:
                break
            data = self.unreader.read()

        if not data:
            self.finished = True
            return self.buf.getvalue()
            
        data = self.buf.getvalue()
        ret, rest = data[:size], data[size:]
        self.buf.truncate(0)
        self.buf.write(rest)
        return ret

class Body(object):
    def __init__(self, reader):
        self.reader = reader
        self.req = reader.req
        self.buf = StringIO()
        self.closed = False
        
    def close(self):
       """ Close the socket if needed """
       if self.req.should_close():
           self.req.unreader.close()
       elif not self.closed:
           # release connection
           self.req.unreader.release()
       self.closed = True
            
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, traceback):
        if exc_type is None:
            """ close on exit and release connection if needed """
            self.close()

    def __iter__(self):
        return self
    
    def next(self):
        ret = self.readline()
        if not ret:
            raise StopIteration()
        return ret

    def getsize(self, size):
        if size is None:
            return sys.maxint
        elif not isinstance(size, (int, long)):
            raise TypeError("size must be an integral type")
        elif size < 0:
            return sys.maxint
        return size
    
    def read(self, size=None):
        size = self.getsize(size)
        if size == 0:
            return ""

        if size < self.buf.tell():
            data = self.buf.getvalue()
            ret, rest = data[:size], data[size:]
            self.buf.truncate(0)
            self.buf.write(rest)
            return ret

        while size > self.buf.tell():
            data = self.reader.read(1024)
            if not len(data):
                self.close()
                break
            self.buf.write(data)

        data = self.buf.getvalue()
        ret, rest = data[:size], data[size:]
        self.buf.truncate(0)
        self.buf.write(rest)
        return ret
    
    def readline(self, size=None):
        size = self.getsize(size)
        if size == 0:
            return ""
        
        idx = self.buf.getvalue().find("\n")
        while idx < 0:
            data = self.reader.read(1024)

            if not len(data):
                self.close()
                break
            self.buf.write(data)
            idx = self.buf.getvalue().find("\n")
            if size < self.buf.tell():
                break
        
        # If we didn't find it, and we got here, we've
        # exceeded size or run out of data.
        if idx < 0:
            rlen = min(size, self.buf.tell())
        else:
            rlen = idx + 1

            # If rlen is beyond our size threshold, trim back
            if rlen > size:
                rlen = size
        
        data = self.buf.getvalue()
        ret, rest = data[:rlen], data[rlen:]
        
        self.buf.truncate(0)
        self.buf.write(rest)
        return ret
    
    def readlines(self, size=None):
        ret = []
        data = self.read()
        while len(data):
            pos = data.find("\n")
            if pos < 0:
                ret.append(data)
                data = ""
            else:
                line, data = data[:pos+1], data[pos+1:]
                ret.append(line)
        return ret
        

class GzipBody(Body):
    def __init__(self, reader):
        super(GzipBody, self).__init__(reader)
        self._d = zlib.decompressobj(16+zlib.MAX_WBITS)
        
    def _decompress(self, data):
        return self._d.decompress(data) 
        
    def read(self, size=None):
        size = self.getsize(size)
        if size == 0:
            return ""

        if size < self.buf.tell():
            data = self.buf.getvalue()
            ret, rest = data[:size], data[size:]
            self.buf.truncate(0)
            self.buf.write(rest)
            return self._decompress(ret)

        while size > self.buf.tell():
            data = self.reader.read(1024)
            if not len(data):
                self.close()
                break
            self.buf.write(data)

        data = self.buf.getvalue()
        ret, rest = data[:size], data[size:]
        self.buf.truncate(0)
        self.buf.write(rest)
        return self._decompress(ret)
    
    def readline(self, size=None):
        size = self.getsize(size)
        if size == 0:
            return ""
        
        idx = self.buf.getvalue().find("\n")
        while idx < 0:
            data = self.reader.read(1024)
            if not len(data):
                self.close()
                break
            self.buf.write(self._decompress(data))
            idx = self.buf.getvalue().find("\n")
            if size < self.buf.tell():
                break
        
        # If we didn't find it, and we got here, we've
        # exceeded size or run out of data.
        if idx < 0:
            rlen = min(size, self.buf.tell())
        else:
            rlen = idx + 1

            # If rlen is beyond our size threshold, trim back
            if rlen > size:
                rlen = size
        
        data = self.buf.getvalue()
        ret, rest = data[:rlen], data[rlen:]
        
        self.buf.truncate(0)
        self.buf.write(rest)
        return ret


class DeflateBody(GzipBody):
    def __init__(self, reader):
        super(DeflateBody, self).__init__(reader)
        self._d = zlib.decompressobj()

