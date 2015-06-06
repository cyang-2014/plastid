#!/usr/bin/env python
"""Readers for fixedStep wiggle, variableStep wiggle, and bedGraph files.

See Also
--------
`UCSC file format FAQ <http://genome.ucsc.edu/FAQ/FAQformat.html>`_.
    UCSC Wiggle and bedGraph file specification
"""

class WiggleReader(object):
    """Reads wiggle and bedGraph files entry-by-entry, returning tuples
    of (chromosome, start position, stop position, value), where all positions
    are zero-indexed and half-open (as opposed to fixedStep or variableStep
    wiggles, which are 1-indexed).

    See the `UCSC file format FAQ <http://genome.ucsc.edu/FAQ/FAQformat.html>`_ for details.
    """
    def __init__(self,fh):
        self.fh = fh
        self.data_format = "bedGraph"
        self._reset()
    
    def _reset(self):
        """Used internally. Resets positional counters"""
        self.chrom   = None
        self.step    = 1
        self.span    = 1 #default span is 1 (e.g. things span 1 base)
        self.counter = 1 #chromosomal positions are 1-indexed
        
    def __iter__(self):
        return self
    
    def _get_lineinfo(self,line):
        """Determines line type & returns a dictionary
        containing key-value pairs of any parameters
        defined in the line (valid for header lines only)

        Line type is returned under the key "line_step"
        
        Parameters
        ----------
        line : str
            A line of Wiggle or bedGraph file
        
        Returns
        -------
        dict
            line-type key describes the type of line

            If a header line, key-value pairs are returned for parsing 
            ``stepsize`` and ``span``.
        """
        dReturn   = {}
        items     = line.split()
        line_type = None
        if items[0] == "track":
            line_type = "fileHeader"
        elif items[0] == "variableStep":
            line_type = "dataHeader"
        elif items[0] == "fixedStep":
            line_type = "dataHeader"
        elif len(items) == 4:
            line_type = "bedGraph"
        else:
            line_type = "data"
        dReturn["line_type"] = line_type
        if "Header" in line_type:
            for item in items[1:]:
                if "description" in item:
                    break
                elif "=" in item:
                    key,val = item.split("=")
                    dReturn[key] = val
                else:
                    dReturn[key] = 'true'
        return dReturn

    def _next_line(self):
        return next(self.fh)

    def __next__(self):
        return self.next()
    
    def next(self):
        """Yields a tuple of (chromosome, start, stop, value)  for each data line.
        Header lines are processed internally and not exposed to the user.
        
        All coordinates are returned as 0-based, half-open intervals,
        following Python conventions.
                      
        Returns
        -------
        str
            chromosome name

        int
            start position, 0-indexed

        int
            end position, 0-indexed, half-open

        float
            value on chromosome between start and end
        """
        while True:
            line = self._next_line() #self.fh.next()
            if line.isspace():
                continue
            if line[0] == "#":
                continue

            line_info = self._get_lineinfo(line)
            line_type = line_info["line_type"]
            line_items = line.split()

            if line_type == "fileHeader":
                self.file_info = line_info
                continue
            elif line_type == "bedGraph":
                self._reset()
                self.data_format = "bedGraph"
                chrom = line_items[0]
                start = int(line_items[1]) #bedGraph is zero-based half open already. no corrections!
                stop  = int(line_items[2])
                val   = float(line_items[3])
                return (chrom, start, stop, val)
            elif line_type == "dataHeader":
                self._reset()
                self.data_format = line_items[0]
                if "chrom" in line_info:
                    self.chrom = line_info["chrom"]
                if "span" in line_info:
                    self.span = int(line_info["span"])
                if "step" in line_info:
                    self.step = int(line_info["step"])
                if "start" in line_info:
                    self.counter = int(line_info["start"])
                continue
            elif line_type == "data":
                if self.data_format == "variableStep":
                    start = int(line_items[0]) - 1 #move to 0-based index
                    stop  = start + self.span      #leave alone. this will make half-open interval
                    val   = float(line_items[1])
                    return (self.chrom, start, stop, val)
                if self.data_format == "fixedStep":
                    start = self.counter - 1 # move to 0-based index
                    stop  = start + self.span
                    val   = float(line.strip())
                    self.counter += self.step
                    return (self.chrom, start, stop, val)


class UnbufferedWiggleReader(WiggleReader):
    """Similar to |WiggleReader| but uses an unbuffered iterator.
    This means slower disk access, but allows use of `fh.seek()`
    and `fh.tell()` to randomly-access sections of wiggles.
    
    Reads wiggle and bedGraph files entry-by-entry, returning tuples
    of (chromosome, start position, stop position, value), where all positions
    are zero-indexed and half-open (as opposed to fixedStep or variableStep
    wiggles, which are 1-indexed).
    
    See the `UCSC file format FAQ <http://genome.ucsc.edu/FAQ/FAQformat.html>`_ for details.
    """
    def _next_line(self):
        return self.fh.readline().strip("\n")
