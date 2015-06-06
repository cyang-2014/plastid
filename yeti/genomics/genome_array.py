#!/usr/bin/env python
"""Implements array-like data structures called |GenomeArray|, that map numerical values 
to genomic positions (nucleotides) under various configurable mapping rules.
Several different implementations are provided, depending on how the alignment
and/or count data is stored:

Important classes
-----------------
    
    |AbstractGenomeArray|
        Base class for all genome arrays, array-like data structures mapping
        read counts to specific nucleotide positions.  
        
        Defines interfaces for:
            - retrieving vectors (as :py:class:`numpy.ndarray` s) of read counts
              at each position of a |GenomicSegment|
              
            - getting the sum of read counts in a dataset
            
            - toggling normalization of fetched counts to reads-per-million
    
    |MutableAbstractGenomeArray|
        Base class for |GenomeArray| and |SparseGenomeArray|. Contains all interfaces
        from |AbstractGenomeArray|, and additionally defines interfaces for:
        
            - setting values, manually or mathematically, over regions of the
              genome, or over the entire genome, element wise
            
    |GenomeArray|
        A |MutableAbstractGenomeArray| that provides convenience functions for import of
        data from `Wiggle`_ files, `bedGraph`_ files, and `bowtie`_ alignments, as well
        as export to `Wiggle`_ and `bedGraph`_ formats.
        
        Additionally supplies interfaces for mathematical operations such as
        addition and subtraction with scalar values or other |GenomeArray| s,
        both over the entire arrray or at specific |GenomicSegment| s within the
        array. 

        When importing from a `bowtie`_ file, the user must specify a *mapping rule*
        to determine how each read alignment should be converted into a sequencing
        count. Available mapping rules are specified in
        :py:mod:`yeti.readers.bowtie`. These include:
        
            #.  *Fiveprime end mapping*
                    Each read alignment is mapped at a fixed distance from its fiveprime end
            
            #.  *Variable fiveprime end mapping*
                    Each read alignment is mapped at a fixed distance from its
                    fiveprime end, the distance determined by the length of the read
                    alignment
            
            #.  *Threeprime end mapping*
                    Each read alignment is mapped at a fixed distance from its threeprime end
            
            #.  *Center* or *nibble mapping*
                    A fixed number of positions is trimmed from each end of the read alignment,
                    and the remaining N positions in the alignment are incremented by 1/N
                    read counts
    
    |SparseGenomeArray|
        A slower but more memory-efficient implementation of |GenomeArray|,
        useful for large genomes or on computers with limited memory.
    
    |BAMGenomeArray|
        An |AbstractGenomeArray| for alignments in one or more `BAM`_ files. Because
        `BAM`_ files are randomly-accessible, this implementation is much faster
        to instantiate and far more memory-efficient than other |GenomeArray| 
        or |SparseGenomeArray|. This yields several advantages:
        
            #.  Mapping functions may be changed at runtime, with no speed cost,
                rather than having to be set at import time (as is the case for
                import of `bowtie`_ files into a |GenomeArray|).
                
                All of the same mapping strategies for |GenomeArray| are available
                here via :py:meth:`BAMGenomeArray.set_mapping`.
                See the section `Mapping and filtering factories`_.
        
            #.  Because `BAM`_ files include rich descriptions of each read alignment
                (e.g. mismatches, read lengths, et c), reads may be filtered or
                transformed before assignment to genomic positions by the
                use of arbitrary filter functions. These filter functions,
                like mapping rules, are also changeable at run time. See
                :py:meth:`.BAMGenomeArray.add_filter` below
        
        Because a |BAMGenomeArray| is a view of the data in an
        underlying `BAM`_ file (rather than a collection of counts imported from
        one or more `Wiggle`_ or `bowtie`_ files), |BAMGenomeArray| s do not support
        "setter" operations. Mathematical operations, if desired, must be
        applied to vectors of counts, once fetched.


Mapping and filtering factories
-------------------------------

For |BAMGenomeArray|, several factories are provided to generate mapping functions
similar to those used in import of `bowtie`_ files for a |GenomeArray|. As mentioned
above, an advantage of |BAMGenomeArray| s is that these mapping and filter functions
may be changed at arbitrarily after the |BAMGenomeArray| is created, as opposed
to being unchangeable after import.

All mapping functions must take two parameters: a list of :py:class:`pysam.AlignedSegment`,
and a |GenomicSegment|, and must return two values: a list of :py:class:`pysam.AlignedSegment` s
included in the count data, and a vector of counts corresponding to the values at
each genomic position specified by the |GenomicSegment|.

Sample mapping factories include:

    :py:func:`FivePrimeMapFactory`
        Returns a mapping function in which reads are mapped at a user-configurable
        offset (default: 0), from the fiveprime end of the read alignment.
    
    :py:func:`VariableFivePrimeMapFactory`
        Returns a mapping function in which reads are mapped at different user-configurable
        offsets as a function of the read length.

    :py:func:`ThreePrimeMapFactory`
        Returns a mapping function in which reads are mapped at a user-configurable
        offset (default: 0), from the threeprime end of the read alignment.

    :py:func:`NibbleMapFactory`
        Returns a mapping function in which a user-specified number of positions
        is removed from each side of the read alignment, and the N remaining
        bases are each apportioned 1/N of a read count.


Filter functions must take and return the same data type, a list of
:py:class:`pysam.AlignedSegment` s. This allows multiple filters to be composed.
The following filter factories are provided:

    :py:func:`SizeFilterFactory`
        Returns a filter function that can low-pass, high-pass, or band-pass
        read alignments based upon alignment length
    

Finally
-------
This module also contains dictionaries mapping chromosome names to chromosome lengths
for genome builds of several model organisms (e.g. hg19, dm3, sgd2013)
"""
__date__ =  "May 3, 2011"
__author__ = "joshua"
from abc import abstractmethod
import itertools
import operator
import copy
import warnings
import numpy
import scipy.sparse
from collections import OrderedDict
from yeti.readers.wiggle import WiggleReader
from yeti.readers.bowtie import BowtieReader, TagalignReader
from yeti.genomics.roitools import GenomicSegment
from yeti.genomics.splicing import junction_pat
from yeti.util.services.mini2to3 import xrange, ifilter

MIN_CHR_SIZE = 10*1e6 # 10 Mb minimum size for unspecified chromosomes 


#===============================================================================
# Factories for mapping functions for BAMGenomeArray or other structures
# Each factory returns a function that takes a list of pysam.AlignedSegments
# and a GenomicSegment, and returns a list of pysam.AlignedSegments that mapped
# to that interval under the mapping rules specified in that function, and
# a corresponding vector of read counts mapping to each nucleotide position
# in the GenomicSegment
#===============================================================================

def NibbleMapFactory(nibble=0):
    """Returns a mapping function for :py:meth:`BAMGenomeArray.set_mapping`.
    A user-specified number of bases is removed from each side of each
    read alignment, and the N remaining bases are each apportioned 1/N
    of the read count.
     
    Parameters
    ----------
    nibble : int
        Number of bases to remove from each side of read (Default: *0*)
    
    Returns
    -------
    function
        Mapping function
    """
    # docstring of function we will return.
    # set it up here so we can put the string substitution in it
    docstring = """Returns reads covering a region, and a count vector mapping reads
        to specific positions in the region. %s bases are trimmed from each
        side of the read, and each of the N remaining alignment positions
        are incremented by 1/N
        
        Parameters
        ----------
        iv : |GenomicSegment|
            Region of interest
        
        reads : list<:py:class:`pysam.AlignedSegment`>
            Reads to map
            
        Returns
        -------
        :py:class:`numpy.ndarray`
            Vector of counts at each position in ``iv``
        """ % nibble
 
    def map_func(reads,iv):
        iv_positions = set(range(iv.start,iv.end))
        reads_out = []
        count_array = numpy.zeros(len(iv))
        
        #reads = ifilter(lambda x: len(x.positions) > 2*nibble,reads)
        for read in reads:
            if len(read.positions) <= 2*nibble:
                warnings.warn("Read alignment length %s nt is less than 2*'nibble' value of %s nt. Ignoring." % (len(read.positions),2*nibble),
                              UserWarning)
                continue
            
            if nibble == 0:
                read_positions = read.positions
            else:
                read_positions = read.positions[nibble:-nibble]
 
            overlap = set(read_positions) & iv_positions
            if len(overlap) > 0:
                overlap_array_coordinates = [X-iv.start for X in overlap]
                reads_out.append(read)
                val = 1.0 / len(read_positions)
                count_array[overlap_array_coordinates] += val
                 
        return reads_out, count_array
     
    map_func.__doc__ = docstring
    map_func.__mapping__ = "center"
    return map_func
 
 
def FivePrimeMapFactory(offset=0):
    """Returns a mapping function for :py:meth:`BAMGenomeArray.set_mapping`.
    Reads are mapped at a specified offset from the fiveprime end of the alignment
     
    Parameters
    ----------
    offset : int, optional
        Offset from 5' end of read, in direction of threeprime end, at which 
        reads should be counted (Default: *0*)
    
    Returns
    -------
    function
        Mapping function
    """
     
    # docstring of function we will return.
    # set it up here so we can put the string substitution in it
    docstring = """Returns reads covering a region, and a count vector mapping reads
        to specific positions in the region, mapping reads at %s
        from the fiveprime end of each read.
 
        Parameters
        ----------
        iv : |GenomicSegment|
            Region of interest
        
        reads : list<:py:class:`pysam.AlignedSegment`>
            Reads to map
            
        Returns
        -------
        :py:class:`numpy.ndarray`
            Vector of counts at each position in ``iv``
        """ % offset
         
    def map_func(reads,iv):
        reads_out = []         
        count_array = numpy.zeros(len(iv))
        for read in reads:
            if offset > len(read.positions):
                warnings.warn("Offset %snt greater than read length %snt. Ignoring." % (offset,len(read)),
                              UserWarning)
                continue
            if iv.strand == "+":
                p_site = read.positions[offset] # read.pos + self.offset
            else:
                p_site = read.positions[-offset - 1] #read.pos + read.rlen - self.offset - 1
             
            if p_site >= iv.start and p_site < iv.end:
                reads_out.append(read)
                count_array[p_site - iv.start] += 1
        return reads_out, count_array
     
    map_func.__doc__ = docstring
    map_func.__mapping__ = "fiveprime"    
    return map_func
 
 
def ThreePrimeMapFactory(offset=0):
    """Returns a mapping function for :py:meth:`BAMGenomeArray.set_mapping`.
    Reads are mapped at a specified offset from the threeprime end of the alignment,
    in the direction of the fiveprime end
     
    Parameters
    ----------
    offset : int, optional
        Offset from 3' end of read, in direction of 5' end, at which 
        reads should be counted (Default: *0*)
    
    Returns
    -------
    function
        Mapping function
    """
     
    # docstring of function we will return.
    # set it up here so we can put the string substitution in it
    docstring = """Returns reads covering a region, and a count vector mapping reads
        to specific positions in the region, mapping reads at %s
        from the threeprime end of each read.
 
        Parameters
        ----------
        iv : |GenomicSegment|
            Region of interest
        
        reads : list<:py:class:`pysam.AlignedSegment`>
            Reads to map
            
        Returns
        -------
        :py:class:`numpy.ndarray`
            Vector of counts at each position in ``iv``
        """ % offset
    def map_func(reads,iv):
        reads_out = []
        count_array = numpy.zeros(len(iv))
        for read in reads:
            if offset > len(read.positions):
                warnings.warn("Offset %snt greater than read length %snt. Ignoring." % (offset,len(read)),
                              UserWarning)
                continue
            if iv.strand == "+":
                p_site = read.positions[-offset - 1] #read.pos + read.rlen - 1 - self.offset
            else:
                p_site = read.positions[offset] #read.pos + self.offset
                 
            if p_site >= iv.start and p_site < iv.end:
                reads_out.append(read)
                count_array[p_site - iv.start] += 1
        return reads_out, count_array
     
    map_func.__doc__ = docstring
    map_func.__mapping__ = "threeprime"    
    return map_func
 
# JGD modified 2013-12-16
# APF 2013-11-18
def VariableFivePrimeMapFactory(offset_dict):
    """Returns a mapping function for :py:meth:`BAMGenomeArray.set_mapping`.
    Reads are mapped at a specified offset from the fiveprime end of the alignment,
    which can vary with the length of the read according to offset[readlen]
     
    Parameters
    ----------
    offset_dict : dict
        Dictionary mapping read lengths to offsets that should be applied
        to reads of that length when mapping. A special key, *default* may be supplied
        to provide a default value for lengths not specifically enumerated
        in the dict
    
    Returns
    -------
    function
        Mapping function    
    """
     
    # docstring of function we will return.
    docstring = """Returns reads covering a region, and a count vector mapping reads
        to specific positions in the region, mapping reads at possibly varying
        offsets from the 5' end of each read.
 
        Parameters
        ----------
        iv : |GenomicSegment|
            Region of interest
        
        reads : list<:py:class:`pysam.AlignedSegment`>
            Reads to map
            
        Returns
        -------
        :py:class:`numpy.ndarray`
            Vector of counts at each position in ``iv``
        """
         
    def map_func(reads,iv):
        reads_out = []         
        count_array = numpy.zeros(len(iv))
        for read in reads:
            # Get offset from dict. If not present, ask for default offset
            # If no default, this will throw a KeyError, which users can
            # deal with.
            read_length = len(read.positions) 
            if read_length not in offset_dict:
                offset = offset_dict["default"]
            else:
                offset = offset_dict[read_length]
                 
            if iv.strand == "+":
                p_site = read.positions[offset] # read.pos + self.offset
            else:
                p_site = read.positions[-offset - 1] #read.pos + read.rlen - self.offset - 1
             
            if p_site >= iv.start and p_site < iv.end:
                reads_out.append(read)
                count_array[p_site - iv.start] += 1
        return reads_out, count_array
     
    map_func.__doc__ = docstring
    map_func.__mapping__ = "fiveprime_variable"    
    return map_func
 
 
 
 
 
# Default mapping option: map entire read
map_entire = NibbleMapFactory()


#===============================================================================
# Factory functions to filter reads for BAMGenomeArrays
#===============================================================================

def SizeFilterFactory(min=1,max=numpy.inf):
    """Factory to produce size filters, which can be applied at runtime 
    to the :py:meth:`BAMGenomeArray.add_filter`
    
    Parameters
    ----------
    min : int
        Minimum read length to pass filter (Default: *1*)
    
    max : int or numpy.inf
        Maximum read length to pass filter (Default: infinity)
    
    Returns
    -------
    function
    """
    assert max > min
    def my_func(x):
        my_length = len(x.positions)
        return True if my_length >= min and my_length <= max else False
    
    return my_func



#===============================================================================
# INDEX: Mapping functions for GenomeArray and SparseGenomeArray.
#        these are used by add_from_bowtie() and add_from_tagalign()
#        to map read alignments to specific sites.
#
#        See function documentation for more details
#===============================================================================


def five_prime_variable_map(feature,**kwargs):
    """Transformation used by :py:meth:`GenomeArray.add_from_bowtie`
    to map 5' positions of reads with variable offset
    dependent upon read length
    
    Parameters
    ----------
    feature : |SegmentChain|
        Ungapped genomic alignment
        
    kwargs['value'] : float or int
        Value to apportion (Default: *1*)
        
    kwargs['offset'] : dict<int,int>
        Dictionary mapping read lengths to offsets
    
    Returns
    -------
    list<tuple<|GenomicSegment|,float>>
    """
    chrom  = feature.spanning_segment.chrom
    strand = feature.spanning_segment.strand
    value   = kwargs.get("value",1.0)
    offset  = kwargs["offset"].get(len(feature.spanning_segment),kwargs["offset"]["default"])
    if strand in ("+","."):
        start = feature.spanning_segment.start + offset
    else:
        start = feature.spanning_segment.end - 1 - offset
    iv = GenomicSegment(chrom,start,start+1,strand)
    return [(iv,value)]    

def five_prime_map(feature,**kwargs):
    """Transformation used by :py:meth:`GenomeArray.add_from_bowtie` to map reads to 5' positions
    
    Parameters
    ----------
    feature : |SegmentChain|
        Ungapped genomic alignment
        
    kwargs['value'] : float or int
        Value to apportion Ddefault: *1*)
        
    kwargs['offset'] : int
        Mapping offset, if any, from 5' toward 3' end of read
    
    Returns
    -------
    list<tuple<|GenomicSegment|,float>>
    """
    chrom  = feature.spanning_segment.chrom
    strand = feature.spanning_segment.strand
    value   = kwargs.get("value",1.0)
    offset  = kwargs.get("offset",0)
    if strand in ("+","."):
        start = feature.spanning_segment.start + offset
    else:
        start = feature.spanning_segment.end - 1 - offset
    iv = GenomicSegment(chrom,start,start+1,strand)
    return [(iv,value)]

def three_prime_map(feature,**kwargs):
    """Transformation used by :py:meth:`GenomeArray.add_from_bowtie` to map reads to 3' positions
    
    Parameters
    ----------
    feature : |SegmentChain|
        Ungapped genomic alignment
        
    kwargs['value'] : float or int
        Value to apportion (Default: *1*)
        
    kwargs['offset'] : int
        Mapping offset, if any, from 3' toward 5' end of read.
    
    Returns
    -------
    list<tuple<|GenomicSegment|,float>>
    """
    chrom  = feature.spanning_segment.chrom
    strand = feature.spanning_segment.strand
    value   = kwargs.get("value",1.0)
    offset  = kwargs.get("offset",0)
    if strand in ("+","."):
        start = feature.spanning_segment.end - 1 - offset
    else:
        start = feature.spanning_segment.start + offset
    iv = GenomicSegment(chrom,start,start+1,strand)
    return [(iv,value)]

def center_map(feature,**kwargs):
    """Transformation used by :py:meth:`GenomeArray.add_from_bowtie` to trim 5' and 3' ends of reads
    for center-weighted mapping
    
    Parameters
    ----------
    feature : |SegmentChain|
        Ungapped genomic alignment
        
    kwargs['value'] : float
        Total value to divide over aligning positions (Default: *1.0*)
        
    kwargs['nibble'] : int
        Positions to remove from each end before mapping
        
    kwargs['offset'] : int
        Mapping offset, if any, from 5' end of read
    
    Returns
    -------
    list<tuple<|GenomicSegment|,float>> : offset
        = kwargs.get("value",0)
    """
    chrom  = feature.spanning_segment.chrom
    strand = feature.spanning_segment.strand
    offset = kwargs.get("offset",0)
    value  = float(kwargs.get("value",1.0))
    sign   = -1 if strand == "-" else 1
    start  = feature.spanning_segment.start + kwargs['nibble'] + sign*offset
    end    = feature.spanning_segment.end   - kwargs['nibble'] + sign*offset
    iv = GenomicSegment(chrom,start,end,strand)
    frac = value/len(iv)
    return [(iv,frac)]
    
def entire_map(feature,**kwargs):
    """Transformation used by :py:meth:`GenomeArray.add_from_bowtie` to plot an entire read.
    Equivalent to :py:func:`center_map` with ``nibble`` set to *0*.
    
    Parameters
    ----------
    feature : |SegmentChain|
        Ungapped genomic alignment
        
    kwargs['value'] : float
        Total value to divide over aligning positions (default: 1.0)
        
    kwargs['offset'] : int
        Mapping offset, if any, from 5' end of read
        Mapping offset, if any
    
    Returns
    -------
    list<tuple<|GenomicSegment|,float>>
    """
    kwargs["nibble"] = 0
    return center_map(feature,**kwargs)


#===============================================================================
# GenomeArray classes
#===============================================================================

class AbstractGenomeArray(object):
    """Base class for all |GenomeArray|-like objects
    """
    def __init__(self,chr_lengths=None,strands=("+","-")):
        """
        Parameters
        ----------
        chr_lengths : dict
            Dictionary mapping chromosome names to lengths.
            Suppyling this in advance yields considerable
            speed improvements, as memory can be pre-allocated
            for each chromosomal position. If not provided,
            a minimum chromosome size will be guessed, and
            chromosomes re-sized as needed.
        
        strands : list<str>
            Sequence<strands> for the |AbstractGenomeArray|
        """
        self._chroms       = {}
        self._strands      = strands
        self._sum          = None
        self._normalize    = False

    def __str__(self):
        return repr(self)
    
    def __repr__(self):
        stmp = "<%s len=%s sum=%s chroms=" % (self.__class__.__name__, len(self), self.sum())
        stmp += ",".join(self.chroms())
        stmp += " strands=%s" % ",".join(self.strands())
        stmp += ">"
        return stmp

    def __contains__(self,key):
        return key in self._chroms

    def __len__(self):
        """Returns size of |AbstractGenomeArray| in nucleotide positions (not base pairs).
        To obtain size in base pairs, divide length by two.
        
        Returns
        -------
        int
        """
        return len(self._strands)*sum(self.lengths().values())
    
    @abstractmethod
    def __getitem__(self,genomic_interval):
        """Retrieve array of counts from a region of interest. Coordinates
        in the array are in the order of the chromosome (leftmost-first), and
        are NOT reversed for negative strand features.
        
        Parameters
        ----------
        genomic_interval : |GenomicSegment| 
            Region of interest in genome
        
        Returns
        -------
        list<Number>
            vector of numbers, each position corresponding to a position in the
            region of interest 
        """
        pass
    
    @abstractmethod    
    def reset_sum(self):
        """Resets sum to total mapped reads in the |GenomeArray|
        """

    def set_sum(self,val):
        """Set sum used for normalization to an arbitrary value (e.g. from another
        dataset)
        
        Parameters
        ----------
        val: int or float
            a number
        """
        self._sum = val
        
    def sum(self):
        """Returns the total number of aligned reads in the |GenomeArray|
        
        The true (i.e. unnormalized) sum is always reported, even if 
        set_normalize() is set to True
        
        Returns
        -------
        int or float
        """
        if self._sum is None:
            self.reset_sum()
        
        return self._sum

    def set_normalize(self,value=True):
        """Toggle normalization of reported values to reads per million mapped
        in the dataset.
        
        Parameters
        ----------
        value : bool
            If True, all values fetched will be normalized to reads
            per million. If false, all values will not be normalized.
        """
        assert value in (True,False)
        self._normalize = value

    def chroms(self):
        """Returns a list of chromosomes in the |GenomeArray|
        
        Returns
        -------
        list<string>
        """
        return self._chroms.keys()
    
    def strands(self):
        """Returns a tuple of strands in the |GenomeArray|
        
        Returns
        -------
        tuple<string>
        """
        return self._strands

    def lengths(self):
        """Returns a dictionary mapping chromosome names to lengths. In the
        case where two strands report different lengths for a chromosome, the
        max length is taken.
        
        Returns
        -------
        dict
        """
        d_out = {}.fromkeys(self.keys())
        for key in d_out:
            d_out[key] = max([len(self._chroms[key][X]) for X in self.strands()])
        return d_out


class MutableAbstractGenomeArray(AbstractGenomeArray):
    """Abstract base class for |GenomeArray|-like objects whose values can be
    changed at runtime (for example by mathematical operations) 
    """
    @abstractmethod
    def __setitem__(self,genomic_interval,val):
        """Set values in |MutableAbstractGenomeArray| over a region of interest.
        
        Parameters
        ----------
        genomic_interval : |GenomicSegment| 
            Region of interest

        val : int or float
            Scalar value
        """
        pass


class BAMGenomeArray(AbstractGenomeArray):
    """Immutable data structure mapping read counts to specific nucleotide
    positions in chromosomes and strands, given a BAM file of alignments.
    
    Provides convenience methods for retrieving reads that map to
    regions of interest in the genomes, as well as reduced arrays of
    counts (e.g. for mapping ribosomal P-sites, RNA-seq reads, et c)
    corresponding to the reads in those regions.
    
    Mapping rules are changeable at runtime, as is normalization.
    
    If mutability is required, the |BAMGenomeArray| can be converted
    to a |GenomeArray| or |SparseGenomeArray|.
    """
    
    def __init__(self,bamfiles,mapping=map_entire):
        """Create |BAMGenomeArray|
        
        Parameters
        ----------
        bamfile : list
            An list of open :py:class:`pysam.AlignmentFile` s.
        
        mapping : func
            Function that determines how each read alignment is mapped to a 
            count at a position in the |BAMGenomeArray|. Examples include
            mapping reads to their fiveprime or threeprime ends, with or
            without offsets. Factories to produce such functions are provided.
            See references below. Default: map reads along entire length


        Notes
        -----
        BAM files must be first sorted and indexed via ``samtools``. Otherwise,
        ValueErros will be raised when reads or counts are fetched.
        
            
        See Also
        --------
        FivePrimeMapFactory
            map reads to 5' ends, with or without offset
        
        VariableFivePrimeMapFactory
            map reads to 5' ends, chosing an offset determined by read length
        
        ThreePrimeMapFactory
            map reads to 3' ends, with or without offset
        
        NibbleMapFactory
            map each read fractionally to every position in the read, optionally trimming positions from the ends first
        """
        self.bamfiles     = bamfiles
        self.map_fn       = mapping
        self._strands     = ("+","-")
        self._normalize   = False
        
        self._chr_lengths = {}
        for bamfile in self.bamfiles:
            for k,v in zip(bamfile.references,bamfile.lengths):
                self._chr_lengths[k] = max(self._chr_lengths.get(k,0),v)
                
        self._filters     = OrderedDict()
        self._update()

    def __del__(self):
        for bamfile in self.bamfiles:
            bamfile.close()

    def reset_sum(self):
        """Reset the sum to the total number of mapped reads in the |BAMGenomeArray|
        
        Notes
        -----
        Filters are not applied in this summation
        """
        self._sum = sum([X.mapped for X in self.bamfiles])
        
    def _update(self):
        """Updates mapping function to suit mapping rules
        """
        self.reset_sum()

    def add_filter(self,name,func):
        """Apply a function to filter reads retrieved from regions before they
        are counted (e.g. a size filter returned by BAMGenomeNDarray.SizeFilter)
        
        Parameters
        ----------
        name : str
            A name for the filter. If not unique, will overwrite
            previous filter
        
        func : func
            Filter function. Function must take a
            pysam.AlignedSegment as a single parameter, and return
            True if that read should be included in output.
        
        Notes
        -----
        In Python lambda functions do NOT have their own scope! We strongly
        recomend defining filter functions using the 'def' syntax to avoid
        namespace collisions.
        
        See also
        --------
        SizeFilterFactory
            generate filter functions that gate read alignments on size
        """
        self._filters[name] = func
    
    def remove_filter(self,name):
        """Remove a generic filter
        
        Parameters
        ----------
        name : str
            A name for the filter
        
        Returns
        -------
        func
            the removed filter function
        """
        retval = self._filters.pop(name)
        return retval
    
    def chroms(self):
        """Returns a list of chromosomes
        
        Returns
        -------
        list<string>
            chromosome names
        """
        return self._chr_lengths.keys()

    def lengths(self):
        """Returns a dictionary mapping chromosome names to lengths. 
        
        Returns
        -------
        dict
            mapping chromosome names to lengths
        """
        return self._chr_lengths
        
    def get_reads_and_counts(self,iv):
        """Returns reads covering a region, and a count vector mapping reads
        to specific positions in the region, following rules specified by 
        self.mapping, self.offset, and self.nibble. To obtain unstranded reads,
        set the value of iv.strand to "."
        
        Parameters
        ----------
        iv : |GenomicSegment|
            Region of interest

        
        Returns
        -------
        list<pysam.AlignedSegment>
            List of reads covering region of interest

        numpy.ndarray
            Counts at each position of ``iv``, under whatever mapping rules
            were set by :py:meth:`~BAMGenomeArray.set_mapping`


        Raises
        ------
        ValueError
            if bamfile not sorted or not indexed
        """
        # fetch reads
        if iv.chrom not in self.chroms():
            return [], numpy.zeros(len(iv))

        reads = itertools.chain.from_iterable((X.fetch(reference=iv.chrom,
                                          start=iv.start,
                                          end=iv.end,
                                         # until_eof=True, # this could speed things up. need to test/investigate
                                          ) for X in self.bamfiles))
            
        # filter by strand
        if iv.strand == "+":
            reads = ifilter(lambda x: x.is_reverse is False,reads)
        elif iv.strand == "-":
            reads = ifilter(lambda x: x.is_reverse is True,reads)
        
        # Pass through additional filters (e.g. size filters, if they have
        # been added)
        for my_filter in self._filters.values():
            reads = ifilter(my_filter,reads)
        
        # retrieve selected parts of regions
        reads,count_array = self.map_fn(reads,iv)
        
        # normalize to reads per million if normalization flag is set
        if self._normalize is True:
            count_array = count_array / float(self.sum()) * 1e6
        
        return reads, count_array

    def get_reads(self,iv):
        """Returns reads covering a  |GenomicSegment|. To obtain unstranded reads,
        set the value of iv.strand to "."
        
        Parameters
        ----------
        iv
            |GenomicSegment| specifying region of interest

        
        Returns
        -------
        reads
            list<pysam.AlignedSegment>


        Raises
        ------
        ValueError
            if bamfile not sorted or not indexed
        """
        reads, _ = self.get_reads_and_counts(iv)
        return reads

    def __getitem__(self,iv):
        """Returns a count array mapping reads to specific positions
        in the a region of interest, following rules specified by 
        self.map_fn.  Coordinates in the returned array are in the order
        of the chromosome (leftmost-first), and are NOT reversed
        for negative strand features. To obtain unstranded reads, set the 
        value of iv.strand to "."
        
        Parameters
        ----------
        iv
            |GenomicSegment| specifying region of interest
        
        
        Returns
        -------
        reads
            list<:py:class:`pysam.AlignedSegment`> of reads passing all filters
        
        count_array
            :py:class:`numpy.ndarray` of counts at each position from left-to-right
            in genomic coordinates, generated from reads passing all filters


        Raises
        ------
        ValueError
            if bamfile not sorted or not indexed
        """
        _, count_array = self.get_reads_and_counts(iv)
        return count_array

    def get_mapping(self):
        """Returns the docstring of the mapping function, in case you forgot
        in interactive mode
        """
        return self.map_fn.__doc__
    
    def set_mapping(self,mapping_function):
        """Change the mapping rules.
        
        Parameters
        ----------
        mapping : func
            Function that determines how each read alignment is mapped to a 
            count at a position in the |BAMGenomeArray|. Examples include
            mapping reads to their fiveprime or threeprime ends, with or
            without offsets. Factories to produce such functions are provided.
            See references below. Default: map reads along entire length

            
        See Also
        --------
        FivePrimeMapFactory
            map reads to 5' ends, with or without offset
        
        VariableFivePrimeMapFactory
            map reads to 5' ends, chosing an offset determined by read length
        
        ThreePrimeMapFactory
            map reads to 3' ends, with or without offset
        
        NibbleMapFactory
            map each read fractionally to every position in the read, optionally trimming positions from the ends first
        """
        self.map_fn = mapping_function
        self._update()
    
    def to_genome_array(self,array_type=None):
        """Converts |BAMGenomeArray| into a |MutableAbstractGenomeArray|

        Parameters
        ----------
        array_type : Subclass of |MutableAbstractGenomeArray|
            Type of |MutableAbstractGenomeArray| to return (default: |GenomeArray|)
        
        Returns
        -------
        |MutableAbstractGenomeArray|
        """
        if array_type is None:
            array_type = GenomeArray
            
        gnd = array_type(chr_lengths=self.lengths(),strands=self.strands())
        for chrom in self.chroms():
            for strand in self.strands():
                iv = GenomicSegment(chrom,0,self.lengths()[chrom]-1,strand)
                gnd[iv] = self[iv]

        return gnd

    def to_variable_step(self,fh,trackname,strand,window_size=100000,**kwargs):
        """Write the contents of the |BAMGenomeArray| to a variableStep wiggle file.
        These contain 1-based coordinates.
        
        See http://genome.ucsc.edu/goldenpath/help/wiggle.html
        
        Parameters
        ----------
        fh : file-like
            Filehandle to write to
        
        trackname : str
            Name of browser track
          
        strand : str
            Strand of |BAMGenomeArray| to export. "+", "-", or "."
        
        window_size : int
            Size of chromosome/contig to process at a time.
            Larger values are faster but less memory-efficient
        
        **kwargs
            Any other key-value pairs to include in track definition line
        """
        assert strand in self.strands()
        fh.write("track type=wiggle_0 name=%s" % trackname)
        if kwargs is not None:
            for k,v in sorted(kwargs.items(),key = lambda x: x[0]):
                fh.write(" %s=%s" % (k,v))
        fh.write("\n")

        for chrom in sorted(self.chroms()):
            fh.write("variableStep chrom=%s span=1\n" % chrom)
            window_starts = xrange(0,self._chr_lengths[chrom],window_size)
            for i in range(len(window_starts)):
                my_start = window_starts[i]
                my_end   = window_starts[i+1] if i + 1 < len(window_starts) else int(self._chr_lengths[chrom])
                my_counts = self[GenomicSegment(chrom,my_start,my_end,strand)]
                if my_counts.sum() > 0:
                    for idx in my_counts.nonzero()[0]:
                        genomic_x = my_start + idx
                        val = my_counts[idx]
                        fh.write("%s\t%s\n" % (genomic_x + 1,val))

            
        
    def to_bedgraph(self,fh,trackname,strand,window_size=100000,**kwargs):
        """Write the contents of the |BAMGenomeArray| to a bedgraph file.
        
        These contain 0-based, half-open coordinates
        
        See https://cgwb.nci.nih.gov/goldenPath/help/bedgraph.html
            
        Parameters
        ----------
        fh : file-like
            Filehandle to write to
        
        trackname : str
            Name of browser track
          
        strand : str
            Strand of |BAMGenomeArray| to export. "+", "-", or "."
        
        window_size : int
            Size of chromosome/contig to process at a time.
            Larger values are faster but less memory-efficient
        
        **kwargs
            Any other key-value pairs to include in track definition line
        """
        assert strand in self.strands()
        assert window_size > 0
        # write header
        fh.write("track type=bedGraph name=%s" % trackname)
        if kwargs is not None:
            for k,v in sorted(kwargs.items(),key = lambda x: x[0]):
                fh.write(" %s=%s" % (k,v))
        fh.write("\n")
        
        for chrom in sorted(self.chroms()):
            window_starts = xrange(0,self._chr_lengths[chrom],window_size)
            for i in range(len(window_starts)):
                my_start = window_starts[i]
                my_end   = window_starts[i+1] if i + 1 < len(window_starts) else int(self._chr_lengths[chrom])
                my_reads, my_counts = self.get_reads_and_counts(GenomicSegment(chrom,my_start,my_end,strand))
                
                if len(my_reads) > 0:
                    genomic_start_x = window_starts[i]
                    last_val        = my_counts[0]

                    for x, val in enumerate(my_counts[1:]):
                        if val != last_val:
                            genomic_end_x = 1 + x + my_start
                            #write line: chrom chromStart chromEnd dataValue. 0-based half-open
                            fh.write("%s\t%s\t%s\t%s\n" % (chrom,genomic_start_x,genomic_end_x,last_val))
                            #update variables
                            last_val = val
                            genomic_start_x = genomic_end_x
                        else:
                            continue
                    # write out last values for window
                    fh.write("%s\t%s\t%s\t%s\n" % (chrom,genomic_start_x,
                                                   my_end,
                                                   last_val))
        return


class GenomeArray(MutableAbstractGenomeArray):
    """Mutable data structure mapping read counts to specific nucleotide
    positions in chromosomes and strands.
    
    Supports basic mathematical operations elementwise, where elements are
    nucleotide positions. Can read from and write to several formats,
    including Wiggle, BedGraph, and bowtie alignments.
    """
    
    def __init__(self,chr_lengths=None,strands=("+","-"),
                 min_chr_size=MIN_CHR_SIZE):
        """Create a |GenomeArray|
        
        Parameters
        ----------
        chr_lengths : dict or None
            Dictionary mapping chromosome names to lengths.
            Suppyling this in advance yields considerable
            speed improvements, as memory can be pre-allocated
            for each chromosomal position. If not provided,
            a minimum chromosome size will be guessed, and
            chromosomes re-sized as needed.
        
        min_chr_size : int
            If chr_lengths is not supplied, min_chr_size is 
            the default first guess of a chromosome size. If
            your genome has large chromosomes, it is much
            much better, speed-wise, to provide a chr_lengths
            dict than to provide a guess here that is too small.
        
        strands : list<str>
            Sequence of strands for the |GenomeArray|
        """
        self._chroms       = {}
        self._strands      = strands
        self.min_chr_size  = min_chr_size
        self._sum          = None
        self._normalize    = False
        if chr_lengths is not None:
            for chrom in chr_lengths.keys():
                self._chroms[chrom] = {}
                for strand in self._strands:
                    l = chr_lengths[chrom]
                    self._chroms[chrom][strand] = numpy.zeros(l)

    def reset_sum(self):
        """Reset the sum of the |GenomeArray| to the total number of mapped reads
        """
        self._sum = sum([X.sum() for X in self.iterchroms()])
        
    def _has_same_dimensions(self,other):
        """Determines whether self & other have the chromosomes,
        strands, and that these are all of the same length
        
        Parameters
        ----------
        other : |GenomeArray|
        
        
        Returns
        -------
        bool
        """
        assert set(self.keys()) == set(other.keys())
        assert self.strands() == other.strands()
        assert self.lengths() == other.lengths()

    def __eq__(self,other,tol=1e-10):
        """Tests for equality between self and other.
        
        To be equal, both |GenomeArray| s do not have to have identical 
        chromosome lengths, but do have to have identical values at all
        nonzero positions in each chromosome.
        
        Parameters
        ----------
        other : |GenomeArray|
        
        
        Returns
        -------
        bool
        """
        snz = self.nonzero()
        onz = other.nonzero()
        for chrom in set(self.chroms()) | set(other.chroms()):
            for strand in set(self.strands()) | set(other.strands()):
                self_nonzero_vec  = snz.get(chrom,{K : numpy.array([]) for K in self.strands()}).get(strand,numpy.array([]))
                other_nonzero_vec = onz.get(chrom,{K : numpy.array([]) for K in other.strands()}).get(strand,numpy.array([]))

                if len(self_nonzero_vec) != len(other_nonzero_vec):
                    return False
                
                # indices of nonzero positions must be same
                if (self_nonzero_vec != other_nonzero_vec).any():
                    return False
                
                # values of nonzero positions must be same
                if len(self_nonzero_vec) > 0:
                    test_iv = GenomicSegment(chrom,self_nonzero_vec.min(),self_nonzero_vec.max(),strand)
                    if not (self[test_iv] - other[test_iv] <= tol).all():
                        return False

        return True
        
    def __getitem__(self,iv):
        """Retrieve array of counts from a region of interest. Coordinates
        in the array are in the order of the chromosome (leftmost-first), and
        are NOT reversed for negative strand features.
        
        If the ROI is on a valid chromosome and strand but outside the
        bounds of the current |GenomeArray| (e.g. on on coordinates that exceed
        the existing length of a chromosome), the |GenomeArray| will be
        automatically expanded to fit that size. This is useful in circumstances
        where chromosome sizes aren't known ahead of time (for example, when
        reading wiggle files when users don't explicitly specify chromosome sizes
        at runtime). This behavior is different from what an IndexError or
        KeyError that some users might expect.
        
        Parameters
        ----------
        iv : |GenomicSegment|
            Region of interest
        
        Returns
        -------
        numpy.array
            vector of counts at each position in region of interest
        """
        assert isinstance(iv,GenomicSegment)
        assert iv.start >= 0
        assert iv.end >= iv.start
        try:
            assert iv.end < len(self._chroms[iv.chrom][iv.strand])
        except AssertionError:
            my_len = len(self._chroms[iv.chrom][iv.strand])
            new_size = max(my_len + 10000,iv.end + 10000)
            for strand in self.strands():
                new_strand = copy.deepcopy(self._chroms[iv.chrom][strand])
                new_strand.resize(new_size)
                self._chroms[iv.chrom][strand] = new_strand
        except KeyError:
            assert iv.strand in self.strands()
            if iv.chrom not in self.keys():
                self._chroms[iv.chrom] = {}
                for strand in self.strands():
                    self._chroms[iv.chrom][strand] = numpy.zeros(self.min_chr_size)
        
        vals = self._chroms[iv.chrom][iv.strand][iv.start:iv.end]
        if self._normalize is True:
            vals = 1e6 * vals / self.sum()
            
        return vals
    
    def __setitem__(self,iv,val):
        """Set values in |GenomeArray| over a region of interest (ROI).
        If the ROI is on a valid chromosome and strand but outside the
        bounds of the current |GenomeArray| (e.g. on on coordinates that exceed
        the existing length of a chromosome), the |GenomeArray| will be
        automatically expanded to fit that size. This is useful in circumstances
        where chromosome sizes aren't known ahead of time (for example, when
        reading wiggle files when users don't explicitly specify chromosome sizes
        at runtime). This behavior is different from what an IndexError or
        KeyError that some users might expect.

        Parameters
        ----------
        iv : |GenomicSegment|
            Region of interest
        
        val : int, float, or :py:class:`numpy.ndarray`
            Values to set over region of interest
        
        
        Notes
        -----
        If :meth:`set_normalize` is set to *True*, it is temporarily set to *False*
        while values are being set. It is then returned to *True*.
        
        
        Raises
        ------
        AssertionError
            if ``self._normalize`` is True, or if ``iv`` is invalid
        """
        self._sum = None
        assert isinstance(iv,GenomicSegment)
        assert iv.start >= 0
        assert iv.end >= iv.start

        old_normalize = self._normalize
        if old_normalize == True:
            warnings.warn("Temporarily turning off normalization during value set. It will be re-enabled automatically when complete.",UserWarning)

        self.set_normalize(False)
        try:
            assert iv.end < len(self._chroms[iv.chrom][iv.strand])
        except AssertionError:
            my_len = len(self._chroms[iv.chrom][iv.strand])
            new_size = max(my_len + 10000,iv.end + 10000)
            for strand in self.strands():
                new_strand = copy.deepcopy(self._chroms[iv.chrom][strand])
                new_strand.resize(new_size)
                self._chroms[iv.chrom][strand] = new_strand
        except KeyError:
            assert iv.strand in self.strands()
            if iv.chrom not in self.keys():
                self._chroms[iv.chrom] = {}
                for strand in self.strands():
                    self._chroms[iv.chrom][strand] = numpy.zeros(self.min_chr_size) 
            
        self._chroms[iv.chrom][iv.strand][iv.start:iv.end] = val
        self.set_normalize(old_normalize)

    def keys(self):
        return self.chroms()
    
    def iterchroms(self):
        """Returns an iterator, by strand, over each chromosome
        
        Yields
        ------
        numpy.ndarray
            Positionwise counts on a chromosome strand
        """
        for chrom in self.keys():
            for strand in self.strands():
                yield self._chroms[chrom][strand]
    
    # no unit test for this
    def plot(self,chroms=None,strands=None,**plot_kw):
        """Creates a plot of coverage along tracks or chromosomes
        
        Parameters
        ----------
        chroms : list<str>
            Chromosomes to plot (default: all)
            
        strands : list<str>
            Strands to plot (default: all)
            
        plot_kw
            A dictionary of keywords to pass to matplotlib
        
        
        Returns
        -------
        :py:class:`matplotlib.figure.Figure`
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig_kw = { 'figsize' : (8,10) }
        colors = { "+" : "blue", "-" : "orange", "." : "darkgreen" }        
        
        if chroms is None:
            chroms = self.keys()
        
        if strands is None:
            strands = self.strands()
            
        fig_kw.update(plot_kw)
        f, axes = plt.subplots(nrows=len(chroms),**fig_kw)
        plt.subplots_adjust(hspace=0.3)
        for n,chrom in enumerate(sorted(chroms)):
            x = numpy.arange(0,self.lengths()[chrom])
            for strand in strands:
                multiplier = -1 if strand == "-" else 1
                axes[n].plot(x,self._chroms[chrom][strand]*multiplier,
                             label=strand,
                             color=colors[strand])
                axes[n].set_title(chrom)
                axes[n].set_ylabel("Counts")
                axes[n].set_xlabel("Position (nt)")
                axes[n].ticklabel_format(useOffset=1,style='plain',axis='x')
        
        return f
    
    def nonzero(self):
        """Return the indices of each chromosome/strand pair that are non-zero.
        
        Returns
        -------
        dict[chrom][strand] = :py:class:`numpy.ndarray` of nonzero positions
        """
        d_out = {}
        for key in self.keys():
            d_out[key] = {}
            for strand in self.strands():
                d_out[key][strand] = self._chroms[key][strand].nonzero()[0]
        return d_out
    
    def apply_operation(self,other,func,mode="same"):
        """Applies a binary operator either to two |GenomeArray| s, elementwise,
        or to some other quantity and this |GenomeArray|. In both cases, a new
        |GenomeArray| is returned. If :meth:`set_normalize` is set to *True*,
        it is disabled during the operation.
        
        Parameters
        ----------
        other : float, int, or |MutableAbstractGenomeArray|
        
        func : func
            Function to perform. This must take two arguments.
            a :py:class:`numpy.ndarray` (chromosome-strand) from the
            |GenomeArray| will be supplied as the first argument, and ``other``
            as the second.
            
            If mode is set to "all", and a chromosome or strand is not
            present in one of self or other, zero will be supplied
            as the missing argument to func. func must handle this
            gracefully.
        
        mode : str
            Only relevant if ``other`` is a |MutableAbstractGenomeArray|
        
            If "same" each set of corresponding chromosomes must
            have the same dimensions in both parent |GenomeArray| s.
            In addition, both |GenomeArray| s must have the same
            chromosomes, and the same strands.

            If "all", corresponding chromosomes in the parent
            |GenomeArray| s will be resized to the dimensions of
            the larger chromosome before the operation is applied.
            Parents are not required to have the same chromosomes
            or strands; all chromosomes and strands will be
            included in output. 
                    
            If "truncate",corresponding chromosomes in the parent
            |GenomeArray| s will be resized to the dimensions of
            the smaller chromosome before the operation is applied.
            Parents are not required to have the same chromosomes
            or strands; chromosomes or strands not common
            to both parents will be ignored.    
        
        Returns
        -------
        |GenomeArray|
            new |GenomeArray| after the operation is applied
        """
        new_array = GenomeArray.like(self)
        old_normalize = self._normalize
        if old_normalize == True:
            warnings.warn("Temporarily turning off normalization during value set. It will be re-enabled automatically when complete.",UserWarning)

        if type(other) == self.__class__:
            if mode == "same":       
                self._has_same_dimensions(other)
                for chrom in self.keys():
                    for strand in self.strands():
                        iv = GenomicSegment(chrom,0,len(self._chroms[chrom][strand]),strand)
                        new_array[iv] = func(self[iv],other[iv])               
            elif mode == "all":
                chroms    = {}.fromkeys(set(self.keys()) | set(other.keys()))
                strands   = set(self.strands()) | set(other.strands())
                new_array = GenomeArray(chroms,strands=strands)
                for chrom in chroms:
                    if chrom in self.keys() and chrom in other.keys():
                        for strand in strands:
                            if strand in self.strands() and strand in other.strands():
                                sc = copy.deepcopy(self._chroms[chrom][strand])
                                oc = copy.deepcopy(other._chroms[chrom][strand])
                                if len(sc) > len(oc):
                                    oc.resize(len(sc),refcheck=False)
                                elif len(oc) > len(sc):
                                    sc.resize(len(oc),refcheck=False)
                                new_array._chroms[chrom][strand] = func(sc,oc)
                            elif strand in self.strands():
                                new_array._chroms[chrom][strand] = func(copy.deepcopy(self._chroms[chrom][strand]),0)
                            else:
                                new_array._chroms[chrom][strand] = func(copy.deepcopy(other._chroms[chrom][strand]),0)
                    elif chrom in self.keys():
                        for strand in strands:
                            new_array._chroms[chrom][strand] = func(copy.deepcopy(self[chrom][strand]),0)
                    else:
                        for strand in strands:
                            new_array._chroms[chrom][strand] = func(copy.deepcopy(other[chrom][strand]),0)
            elif mode == "truncate":
                my_strands = set(self.strands()) & set(other.strands())
                my_chroms  = set(self.chroms())  & set(other.chroms())
                for chrom in my_chroms:
                    for strand in my_strands:
                        sc = copy.deepcopy(self._chroms[chrom][strand])
                        oc = copy.deepcopy(other._chroms[chrom][strand])
                        if len(sc) > len(oc):
                            sc.resize(len(oc),refcheck=False)
                        elif len(oc) > len(sc):
                            oc.resize(len(sc),refcheck=False)
                        new_array._chroms[chrom][strand] = func(sc,oc)
            else:
                raise KeyError("Mode not understood")
        else:
            for chrom in self.keys():
                for strand in self.strands():
                    new_array._chroms[chrom][strand] = func(self._chroms[chrom][strand],other)

        self.set_normalize(old_normalize)
        return new_array
        
    def __add__(self,other,mode="all"):
        """Adds a scalar or another another |GenomeArray| to |GenomeArray|,
        elementwise.
        
        Parameters
        ----------
        other : float, int, or |MutableAbstractGenomeArray|
        
        mode : str
            Only relevant if ``other`` is a |MutableAbstractGenomeArray|
        
            If "same" each set of corresponding chromosomes must
            have the same dimensions in both parent |GenomeArray| s.
            In addition, both |GenomeArray| s must have the same
            chromosomes, and the same strands.

            If "all", corresponding chromosomes in the parent
            |GenomeArray| s will be resized to the dimensions of
            the larger chromosome before the operation is applied.
            Parents are not required to have the same chromosomes
            or strands; all chromosomes and strands will be
            included in output. 
                    
            If "truncate",corresponding chromosomes in the parent
            |GenomeArray| s will be resized to the dimensions of
            the smaller chromosome before the operation is applied.
            Parents are not required to have the same chromosomes
            or strands; chromosomes or strands not common
            to both parents will be ignored.     
        
        Returns
        -------
        |GenomeArray|
        """
        return self.apply_operation(other, operator.add, mode=mode)
        
    def __mul__(self,other,mode="all"):
        """Multiplies |GenomeArray| by a scalar or by another |GenomeArray|,
        elementwise.
        
        Parameters
        ----------
        other : float, int, or |MutableAbstractGenomeArray|
        
        mode : str
            Only relevant if ``other`` is a |MutableAbstractGenomeArray|
        
            If "same" each set of corresponding chromosomes must
            have the same dimensions in both parent |GenomeArray| s.
            In addition, both |GenomeArray| s must have the same
            chromosomes, and the same strands.

            If "all", corresponding chromosomes in the parent
            |GenomeArray| s will be resized to the dimensions of
            the larger chromosome before the operation is applied.
            Parents are not required to have the same chromosomes
            or strands; all chromosomes and strands will be
            included in output. 
                    
            If "truncate",corresponding chromosomes in the parent
            |GenomeArray| s will be resized to the dimensions of
            the smaller chromosome before the operation is applied.
            Parents are not required to have the same chromosomes
            or strands; chromosomes or strands not common
            to both parents will be ignored.               
        
        Returns
        -------
        |GenomeArray|
        """
        return self.apply_operation(other, operator.mul, mode=mode)
        
    def __sub__(self,other,mode="all"):
        """Subtracts a scalar or another another |GenomeArray| from |GenomeArray|,
        elementwise.
        
        Parameters
        ----------
        other : float, int, or |MutableAbstractGenomeArray|
        
        mode : str
            Only relevant if ``other`` is a |MutableAbstractGenomeArray|
        
            If "same" each set of corresponding chromosomes must
            have the same dimensions in both parent |GenomeArray| s.
            In addition, both |GenomeArray| s must have the same
            chromosomes, and the same strands.

            If "all", corresponding chromosomes in the parent
            |GenomeArray| s will be resized to the dimensions of
            the larger chromosome before the operation is applied.
            Parents are not required to have the same chromosomes
            or strands; all chromosomes and strands will be
            included in output. 
                    
            If "truncate",corresponding chromosomes in the parent
            |GenomeArray| s will be resized to the dimensions of
            the smaller chromosome before the operation is applied.
            Parents are not required to have the same chromosomes
            or strands; chromosomes or strands not common
            to both parents will be ignored.            
        
        Returns
        -------
        |GenomeArray|
        """
        return self.__add__(other*-1)
    
    def add_from_bowtie(self,fh,transformation,min_length=25,
                          max_length=numpy.inf,**trans_args):
        """Adds data from a bowtie alignment to current |GenomeArray|
        
        Parameters
        ----------
        fh : file-like
            filehandle pointing to bowtie file
        
        min_length : int
            minimum length for read to be counted
            (default: 25)
        
        max_length : int or numpy.inf
            maximum length for read to be counted
            (default: infinity)
        
        transformation : func
            a function that transforms alignment
            coordinates into a sequence of
            (|GenomicSegment|,value) pairs
        
        trans_args
            Keyword arguments to pass to transformation
            function  
        """
        for feature in BowtieReader(fh):
            if len(feature.spanning_segment) >= min_length and len(feature.spanning_segment) <= max_length:
                iv = feature.spanning_segment
                tuples = transformation(feature,**trans_args)
                for iv, val in tuples:
                    self[iv] += val
        
        self._sum = None

    def add_from_wiggle(self,fh,strand):
        """Adds data from a wiggle file or bedGraph file to current |GenomeArray|
        
        Parameters
        ----------
        fh : file-like
            filehandle pointing to wiggle file
        
        strand : str
            Strand to which data should be added. "+", "-", or "."
        """
        assert strand in self.strands()
        for chrom,start,stop,val in WiggleReader(fh):
            iv = GenomicSegment(chrom,start,stop,strand)
            self[iv] += val

        self._sum = None
        
    def to_variable_step(self,fh,trackname,strand,**kwargs):
        """Write the contents of the |GenomeArray| to a variable step
        wiggle file. Note for most purposes, bedgraph is a more
        efficient format.
        
        See http://genome.ucsc.edu/goldenpath/help/wiggle.html
        
        Parameters
        ----------
        fh : file-like
            Filehandle to write to
        
        trackname : str
            Name of browser track
        
        strand : str
            Strand of |GenomeArray| to export. "+", "-", or "."
        
        **kwargs
            Any other key-value pairs to include in track definition line
        """
        assert strand in self.strands()
        fh.write("track type=wiggle_0 name=%s" % trackname)
        if kwargs is not None:
            for k,v in sorted(kwargs.items(),key = lambda x: x[0]):
                fh.write(" %s=%s" % (k,v))
        fh.write("\n")
        nonzero = self.nonzero()
        for chrom in sorted(nonzero):
            fh.write("variableStep chrom=%s span=1\n" % chrom)
            indices = nonzero[chrom][strand]
            for idx in indices:
                val = self._chroms[chrom][strand][self._slicewrap(idx)]
                fh.write("%s\t%s\n" % (idx+1, val))
                
    def to_bedgraph(self,fh,trackname,strand,**kwargs):
        """Write the contents of the |GenomeArray| to a bedGraph file
        These contain 0-based, half-open coordinates
        
        See https://cgwb.nci.nih.gov/goldenPath/help/bedgraph.html
            
        Parameters
        ----------
        fh : file-like
            Filehandle to write to
        
        trackname : str
            Name of browser track
        
        strand : str
            Strand of |GenomeArray| to export. "+", "-", or "."
        
        **kwargs
            Any other key-value pairs to include in track definition line
        """
        assert strand in self.strands()
        fh.write("track type=bedGraph name=%s" % trackname)
        if kwargs is not None:
            for k,v in sorted(kwargs.items(),key = lambda x: x[0]):
                fh.write(" %s=%s" % (k,v))
        fh.write("\n")
        nonzero = self.nonzero()
        for chrom in sorted(nonzero):
            if self._chroms[chrom][strand].sum() > 0:
                last_val = 0
                last_x   = 0
                nz = nonzero[chrom][strand]
                for x in range(nz.min(),nz.max()+1):
                    val = self._chroms[chrom][strand][self._slicewrap(x)]
                    if val != last_val:
                        #write line: chrom chromStart chromEnd dataValue
                        fh.write("%s\t%s\t%s\t%s\n" % (chrom,last_x,x,last_val))
                        #update variables
                        last_val = val
                        last_x = x
                    else:
                        continue
                # write last line
                fh.write("%s\t%s\t%s\t%s\n" % (chrom,last_x,x+1,last_val))
        
    @staticmethod
    def like(other):
        """Returns a |GenomeArray| of same dimension as the input array
        
        Parameters
        ----------
        other : |GenomeArray|
        
        Returns
        -------
        GenomeArray
            empty |GenomeArray| of same size as ``other``
        """
        return GenomeArray(other.lengths(),strands=other.strands())

    def _slicewrap(self,x):
        """Helper function to wrap coordinates for VariableStep/Bedgraph export"""
        return x


class SparseGenomeArray(GenomeArray):
    """A memory-efficient, |MutableAbstractGenomeArray| using sparse internal representation.
    Note, savings in memory may come at a cost in performance when setting/getting values
    from a |SparseGenomeArray| relative to a |GenomeArray|.
    """
    def __init__(self,chr_lengths=None,strands=("+","-"),min_chr_size=MIN_CHR_SIZE):
        """Create a |SparseGenomeArray|

        Parameters
        ----------
        chr_lengths : dict, optional
            Dictionary mapping chromosome names to lengths.
            Suppyling this parameter yields considerable
            speed improvements, as memory can be pre-allocated
            for each chromosomal position. If not provided,
            a minimum chromosome size will be guessed, and
            chromosomes re-sized as needed (Default: *{}* )
        
        min_chr_size : int,optional
            If ``chr_lengths`` is not supplied, ``min_chr_size`` is 
            the default first guess of a chromosome size. If
            your genome has large chromosomes, it is much
            much better, speed-wise, to provide a chr_lengths
            dict than to provide a guess here that is too small.
            (Default: %s)
        
        strands : list<str>
            Sequence of strands for the |GenomeArray| (Default *['+','-']*)
        """ % MIN_CHR_SIZE
        self._chroms       = {}
        self._strands      = strands
        self._sum          = None
        self._normalize    = False
        self.min_chr_size = min_chr_size
        if chr_lengths is not None:
            for chrom in chr_lengths.keys():
                self._chroms[chrom] = {}
                for strand in self._strands:
                    l = chr_lengths[chrom]
                    self._chroms[chrom][strand] = scipy.sparse.dok_matrix((1,l))

    def lengths(self):
        """Returns a dictionary mapping chromosome names to lengths. In the
        case where two strands report different lengths for a chromosome, the
        max length is taken.
        
        Returns
        -------
        dict
            mapping chromosome names to lengths
        """
        d_out = {}.fromkeys(self.keys())
        for key in d_out:
            d_out[key] = max([self._chroms[key][X].shape[1] for X in self.strands()])
        
        return d_out

    def __getitem__(self,iv):
        """Retrieve array of counts from a region of interest. Coordinates
        in the array are in the order of the chromosome (leftmost-first), and
        are NOT reversed for negative strand features.
        
        Parameters
        ----------
        iv : |GenomicSegment|
            Region of interest
        
        Returns
        -------
        numpy.ndarray
            counts at each position in region of interest
        """
        assert isinstance(iv,GenomicSegment)
        assert iv.start >= 0
        assert iv.end >= iv.start

        if iv.chrom not in self:
            self._chroms[iv.chrom] = { K : copy.deepcopy(scipy.sparse.dok_matrix((1,self.min_chr_size)))
                                       for K in self.strands()
                                      }
        if iv.end > self._chroms[iv.chrom][iv.strand].shape[1]:
            for strand in self.strands():
                self._chroms[iv.chrom][strand].resize((1,iv.end+10000))

        vals = self._chroms[iv.chrom][iv.strand][0,iv.start:iv.end]
        if self._normalize is True:
            vals = 1e6 * vals / self.sum()
            
        return vals.toarray().reshape(vals.shape[1],)

    def __setitem__(self,iv,val):
        """Set values in |SparseGenomeArray| over a region of interest (ROI).
        If the ROI is on a valid chromosome and strand but outside the
        bounds of the current |SparseGenomeArray| (e.g. on on coordinates that exceed
        the existing length of a chromosome), the |SparseGenomeArray| will be
        automatically expanded to fit that size. This is useful in circumstances
        where chromosome sizes aren't known ahead of time (for example, when
        reading wiggle files when users don't explicitly specify chromosome sizes
        at runtime). This behavior is different from what an IndexError or
        KeyError that some users might expect.

        Parameters
        ----------
        iv : |GenomicSegment|
            Region of interest
        
        val : int, float, or :py:class:`numpy.ndarray` of same length as ``iv``
            Value(s) to set in region specifed by interval ``iv``


        Notes
        -----
        If :meth:`set_normalize` is set to *True*, it is temporarily set to *False*
        while values are being set. It is then returned to *True*.
       
        
        Raises
        ------
        AssertionError
            if iv is invalid
        """
        self._sum = None
        assert isinstance(iv,GenomicSegment)
        assert iv.start >= 0
        assert iv.end >= iv.start
        
        old_normalize = self._normalize
        if old_normalize == True:
            warnings.warn("Temporarily turning off normalization during value set. It will be re-enabled automatically when complete.",UserWarning)
        self.set_normalize(False)

        if iv.chrom not in self:
            self._chroms[iv.chrom] = { K : copy.deepcopy(scipy.sparse.dok_matrix((1,self.min_chr_size)))
                                       for K in self.strands()
                                      }
        if iv.end > self._chroms[iv.chrom][iv.strand].shape[1]:
            for strand in self.strands():
                self._chroms[iv.chrom][strand].resize((1,iv.end+10000))
        
        self._chroms[iv.chrom][iv.strand][0,iv.start:iv.end] = val
        self.set_normalize(old_normalize)

    def __mul__(self,other,mode=None):
        """Multiplies |SparseGenomeArray| by a scalar or by another |SparseGenomeArray|,
        elementwise.
        """
        if isinstance(other,GenomeArray):
            new_array = SparseGenomeArray.like(self)
            chroms    = set(self.keys()) & set(other.keys())
            strands   = set(self.strands()) & set(other.strands())
            for chrom in chroms:
                for strand in strands:
                    new_array._chroms[chrom][strand] = self._chroms[chrom][strand].multiply(other._chroms[chrom][strand])
                    
            return new_array
        else:
            return self.apply_operation(other,operator.mul,mode=mode)
        
    def apply_operation(self,other,func,mode=None):
        """Applies a binary operator either to two |MutableAbstractGenomeArray| s, elementwise,
        or to some other quantity and this |MutableAbstractGenomeArray|. In both cases, a new
        |MutableAbstractGenomeArray| is returned.
        
        Parameters
        ----------
        other : scalar or |MutableAbstractGenomeArray|
        
        func : func
            Function to perform. This must take two arguments.
            a numpy.array (chromosome-strand) from self will be
            supplied as the first argument, and a numpy.array
            (chromosome-strand) other as the second. If mode
            is set to "all", and a chromosome or strand is not
            present in one of self or other, zero will be supplied
            as the missing argument to func. func must handle this
            gracefully.
        
        mode : str
            Only relevant if ``other`` is a |MutableAbstractGenomeArray|
        
            If "same" each set of corresponding chromosomes must
            have the same dimensions in both parent |GenomeArray| s.
            In addition, both |GenomeArray| s must have the same
            chromosomes, and the same strands.

            If "all", corresponding chromosomes in the parent
            |GenomeArray| s will be resized to the dimensions of
            the larger chromosome before the operation is applied.
            Parents are not required to have the same chromosomes
            or strands; all chromosomes and strands will be
            included in output. 
                    
            If "truncate",corresponding chromosomes in the parent
            |GenomeArray| s will be resized to the dimensions of
            the smaller chromosome before the operation is applied.
            Parents are not required to have the same chromosomes
            or strands; chromosomes or strands not common
            to both parents will be ignored.     
        
        Returns
        -------
        |GenomeArray|
        """        
        new_array = SparseGenomeArray.like(self)

        old_normalize = self._normalize
        if old_normalize == True:
            warnings.warn("Temporarily turning off normalization during value set. It will be re-enabled automatically when complete.",UserWarning)
        self.set_normalize(False)

        if isinstance(other,GenomeArray):
            chroms    = {}.fromkeys(set(self.keys()) | set(other.keys()),10)
            strands   = set(self.strands()) | set(other.strands())
            new_array = SparseGenomeArray(chroms,strands=strands)
            for chrom in chroms:
                if chrom in self.keys() and chrom in other.keys():
                    for strand in strands:
                        if strand in self.strands() and strand in other.strands():
                            sc = self._chroms[chrom][strand]
                            oc = other._chroms[chrom][strand]
                            new_array._chroms[chrom][strand] = func(sc,oc)
                        elif strand in self.strands():
                            new_array._chroms[chrom][strand] = func(copy.deepcopy(self._chroms[chrom][strand]),0)
                        else:
                            new_array._chroms[chrom][strand] = func(copy.deepcopy(other._chroms[chrom][strand]),0)
                elif chrom in self.keys():
                    for strand in strands:
                        new_array._chroms[chrom][strand] = func(copy.deepcopy(self[chrom][strand]),0)
                else:
                    for strand in strands:
                        new_array._chroms[chrom][strand] = func(copy.deepcopy(other[chrom][strand]),0)
        else:
            for chrom in self.keys():
                for strand in self.strands():
                    new_array._chroms[chrom][strand] = func(self._chroms[chrom][strand],other)

        self.set_normalize(old_normalize)
        return new_array    
    
    def nonzero(self):
        """Return the indices of each chromosome/strand pair that are non-zero.
        
        Returns
        -------
        dict
            dict[chrom][strand] = numpy.ndarray of indices
        """
        d_out = {}
        for key in self.keys():
            d_out[key] = {}
            for strand in self.strands():
                # need to sort because dok doens't guarantee sorted indices in nonzero()                
                d_out[key][strand] = numpy.array(sorted(self._chroms[key][strand].nonzero()[1]))
                
        return d_out

    def _slicewrap(self,x):
        """Helper function to wrap coordinates for VariableStep/Bedgraph export"""
        return (0,x)

    @staticmethod
    def like(other):
        """Returns a |SparseGenomeArray| of same dimension as the input array
        
        Parameters
        ----------
        other : |MutableAbstractGenomeArray|
        
        Returns
        -------
        |SparseGenomeArray|
            of same size as ``other``
        """
        return SparseGenomeArray(other.lengths(),strands=other.strands())





#===============================================================================
# Genome sizes for various assemblies
#===============================================================================

human = hg19 = {'chr1': 249250621,
 'chr10': 135534747,
 'chr11': 135006516,
 'chr11_gl000202_random': 40103,
 'chr12': 133851895,
 'chr13': 115169878,
 'chr14': 107349540,
 'chr15': 102531392,
 'chr16': 90354753,
 'chr17': 81195210,
 'chr17_ctg5_hap1': 1680828,
 'chr17_gl000203_random': 37498,
 'chr17_gl000204_random': 81310,
 'chr17_gl000205_random': 174588,
 'chr17_gl000206_random': 41001,
 'chr18': 78077248,
 'chr18_gl000207_random': 4262,
 'chr19': 59128983,
 'chr19_gl000208_random': 92689,
 'chr19_gl000209_random': 159169,
 'chr1_gl000191_random': 106433,
 'chr1_gl000192_random': 547496,
 'chr2': 243199373,
 'chr20': 63025520,
 'chr21': 48129895,
 'chr21_gl000210_random': 27682,
 'chr22': 51304566,
 'chr3': 198022430,
 'chr4': 191154276,
 'chr4_ctg9_hap1': 590426,
 'chr4_gl000193_random': 189789,
 'chr4_gl000194_random': 191469,
 'chr5': 180915260,
 'chr6': 171115067,
 'chr6_apd_hap1': 4622290,
 'chr6_cox_hap2': 4795371,
 'chr6_dbb_hap3': 4610396,
 'chr6_mann_hap4': 4683263,
 'chr6_mcf_hap5': 4833398,
 'chr6_qbl_hap6': 4611984,
 'chr6_ssto_hap7': 4928567,
 'chr7': 159138663,
 'chr7_gl000195_random': 182896,
 'chr8': 146364022,
 'chr8_gl000196_random': 38914,
 'chr8_gl000197_random': 37175,
 'chr9': 141213431,
 'chr9_gl000198_random': 90085,
 'chr9_gl000199_random': 169874,
 'chr9_gl000200_random': 187035,
 'chr9_gl000201_random': 36148,
 'chrM': 16571,
 'chrUn_gl000211': 166566,
 'chrUn_gl000212': 186858,
 'chrUn_gl000213': 164239,
 'chrUn_gl000214': 137718,
 'chrUn_gl000215': 172545,
 'chrUn_gl000216': 172294,
 'chrUn_gl000217': 172149,
 'chrUn_gl000218': 161147,
 'chrUn_gl000219': 179198,
 'chrUn_gl000220': 161802,
 'chrUn_gl000221': 155397,
 'chrUn_gl000222': 186861,
 'chrUn_gl000223': 180455,
 'chrUn_gl000224': 179693,
 'chrUn_gl000225': 211173,
 'chrUn_gl000226': 15008,
 'chrUn_gl000227': 128374,
 'chrUn_gl000228': 129120,
 'chrUn_gl000229': 19913,
 'chrUn_gl000230': 43691,
 'chrUn_gl000231': 27386,
 'chrUn_gl000232': 40652,
 'chrUn_gl000233': 45941,
 'chrUn_gl000234': 40531,
 'chrUn_gl000235': 34474,
 'chrUn_gl000236': 41934,
 'chrUn_gl000237': 45867,
 'chrUn_gl000238': 39939,
 'chrUn_gl000239': 33824,
 'chrUn_gl000240': 41933,
 'chrUn_gl000241': 42152,
 'chrUn_gl000242': 43523,
 'chrUn_gl000243': 43341,
 'chrUn_gl000244': 39929,
 'chrUn_gl000245': 36651,
 'chrUn_gl000246': 38154,
 'chrUn_gl000247': 36422,
 'chrUn_gl000248': 39786,
 'chrUn_gl000249': 38502,
 'chrX': 155270560,
 'chrY': 59373566}


drosophila = dm3 = {'2L': 23011544,
 '2LHet': 368872,
 '2R': 21146708,
 '2RHet': 3288761,
 '3L': 24543557,
 '3LHet': 2555491,
 '3R': 27905053,
 '3RHet': 2517507,
 '4': 1351857,
 'U': 10049037,
 'Uextra': 29004656,
 'X': 22422827,
 'XHet': 204112,
 'YHet': 347038,
 'dmel_mitochondrion_genome': 19517}

yeast = sgd2013 = saccer10 = sgdR64 = {'chrI': 230218,
 'chrII': 813184,
 'chrIII': 316620,
 'chrIV': 1531933,
 'chrIX': 439888,
 'chrV': 576874,
 'chrVI': 270161,
 'chrVII': 1090940,
 'chrVIII': 562643,
 'chrX': 745751,
 'chrXI': 666816,
 'chrXII': 1078177,
 'chrXIII': 924431,
 'chrXIV': 784333,
 'chrXV': 1091291,
 'chrXVI': 948066,
 'chrmt': 85779}



