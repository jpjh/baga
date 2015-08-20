#! /usr/bin/env python2
# -*- coding: utf-8 -*-@language python
#
# This file is part of the Bacterial and Archaeal Genome Analyser
# Copyright (C) 2015 David Williams
# david.williams.at.liv.d-dub.org.uk
# License GPLv3+: GNU GPL version 3 or later
# This is free software: you are free to change and redistribute it
# There is NO WARRANTY, to the extent permitted by law
# 
# Work on this software was started at The University of Liverpool, UK 
# with funding from The Wellcome Trust (093306/Z/10) awarded to:
# Dr Steve Paterson (The University of Liverpool, UK)
# Dr Craig Winstanley (The University of Liverpool, UK)
# Dr Michael A Brockhurst (University of York, UK)
#
'''
Structure module from the Bacterial and Archaeal Genome (BAG) Analyzer.

This module contains functions to detect structural rearrangements between a 
reference genome sequence and a query genome for which paired end reads for 
chromosome fragements are available. The intended use is to mark the regions 
likely affected by structural rearrangements for exclusion from a short read 
mapping experiment. Structurally altered regions, for example where a prophage 
has integrated into one genome but not the other, violate the assumption of the 
short read mapping method that reference and query sequences share 1-to-1 
orthology or homologous replacement.
'''

# stdlib
from baga import _os
from baga import _cPickle
from baga import _gzip
from baga import _tarfile
from baga import _StringIO
from baga import _json

from array import array as _array
import operator as _operator
import time as _time
from itertools import izip as _izip

# external Python modules
import pysam as _pysam

from baga import report_time as _report_time
def main():
    pass


def moving_stats(values, window = 500, step = 1, resolution = 10):
    '''
    calculate:
        mean, 
        (commented out:
        sample variance (/(n-1)), 
        standard deviation)
    
    over window of specified width moving by specified step size. Input can have 
    a lower than 1:1 resolution e.g., 1 in 10 positions with resolution = 10
    '''
    mean = _array('f')
    #variance = _array('f')
    #st_dev = _array('f')
    for i in range(0, len(values) * resolution - window, resolution)[::step]:  #break
        # collect nt range allowing for resolution of ratios
        these = values[(i / resolution) : ((i + window) / resolution)]
        # exclude zero ratios from calculation of mean: 
        # associated with areas of low quality read alignments
        # also present at edges of large deletions so prevent them lowering window mean prematurely
        # require minimum half window length 
        these = [t for t in these if t > 0]
        if len(these) <= window / 2.0 / resolution:
            # when length of non-zero window is less than half that specified,
            # make up with zeros to decrease mean moderately close to edge of 
            # reference chromosome region with reads mapped
            these += [0] * int(window / 2.0 / resolution - len(these))
        
        # if len(these) >= 3:
            # these_mean = 0
        # else:
        these_mean = sum(these) / float(len(these))
        #these_variance = sum([(t - these_mean)**2 for t in these]) / (float(len(these) - 1))
        #these_st_dev = these_variance**0.5
        mean.append(these_mean)
        #variance.append(these_variance)
        #st_dev.append(these_st_dev)
    
    #return(mean, variance, st_dev)
    return(mean)


def loadCheckerInfo(filein):
    checker_info = {}
    with _tarfile.open(filein, "r:gz") as tar:
        for member in tar:
            #print(member.name)
            contents = _StringIO(tar.extractfile(member).read())
            try:
                # either json serialised conventional objects
                contents = _json.loads(contents.getvalue())
                
            except ValueError:
                # or longer python array.array objects
                if member.name == 'sequence':
                    contents = _array('c', contents.getvalue())
                elif 'ratio' in member.name:
                    contents = _array('f', contents.getvalue())
                else:
                    contents = _array('i', contents.getvalue())
            
            checker_info[member.name] = contents
    
    return(checker_info)
def checkStructure(BAMs, mean_param = 5, min_mapping_quality = 5, resolution = 10, step = 1):
    '''
    check for structural rearrangements . . .
    '''
    # instantiate Structure Checkers
    checkers = {}
    for BAM in BAMs:
        this_checker = Checker(BAM)
        checkers[this_checker.reads_name] = this_checker

    # mapped read pair inclusion:
    # not is_duplicate
    # not is_secondary
    # not is_qcfail

    start_time = _time.time()
    for cnum,(sample,checker) in enumerate(sorted(checkers.items())):
        print('Collecting coverage for {} reads aligned to {}'.format(sample, checker.genome_name))
        checker.getCoverageDepths(min_mapping_quality = min_mapping_quality)
        print('Calculating mean insert size for {} reads aligned to {}'.format(sample, checker.genome_name))
        checker.getMeanInsertSize()
        print('mean insert size == {:.1f}'.format(checker.mean_insert_size))
        print('Collecting non-proper : proper pair ratios for {} reads aligned to {}'.format(sample, checker.genome_name))
        checker.getProperRatios(resolution)
        print('Calculating smoothed ratios for {} reads aligned to {}'.format(sample, checker.genome_name))
        checker.getSmoothedRatios(window = int(round(checker.mean_insert_size)), step = 1, resolution = 10)
        # omit zero depths and zero/infinite ratios for mean (~ omit NAs)
        use_smoothed_ratios = [m for m in checker.smoothed_ratios if m != 0]
        mean_smoothed_ratios = sum(use_smoothed_ratios) / len(use_smoothed_ratios)
        
        window = int(round(checker.mean_insert_size))
        checker.threshold = mean_smoothed_ratios * mean_param
        
        filter_name = 'rearrangements'
        test = '>='
        checker.scan_threshold( checker.smoothed_ratios, 
                                threshold = checker.threshold, 
                                offset = window / 2 / step, 
                                test = test, 
                                name = filter_name,
                                resolution = resolution, 
                                buffer_distance = int(round(checker.mean_insert_size)))
        
        t = len(checker.suspect_regions[filter_name])
        s = sum([(e - s) for s,e in checker.suspect_regions[filter_name]])
        print('For {} at {} {}*mean nonprop/prop ({:.3f}), excludes {} regions spanning {:,} basepairs'.format(
                                                                        sample, 
                                                                        test, 
                                                                        mean_param, 
                                                                        checker.threshold, 
                                                                        t, 
                                                                        s))
        
        # extend regions affected by sequence translocations if adjacent regions are 
        # without aligned reads or anomolous read alignment i.e., all proper-pairs
        filter_name = 'rearrangements_extended'
        filter_to_extend = 'rearrangements'
        # this was set to less than 0.5 because certain regions with read mapping gaps were 
        # not collected that were between two close disrupted regions
        checker.extend_regions(filter_name, filter_to_extend, resolution = resolution, threshold = 0.15)
        t = len(checker.suspect_regions[filter_name])
        s = sum([(e - s) for s,e in checker.suspect_regions[filter_name]])
        print('For {} between high non-proper proportions, excludes {} regions spanning {:,} basepairs'.format(sample, t, s))
        
        ## prepare to save checker info
        # save dicts, floats, strings and arrays for plotting later
        filename = checker.reads.filename.split(_os.path.sep)[-1]
        name = _os.path.extsep.join(filename.split(_os.path.extsep)[:-1])
        if sample == name:
            # per BAM sample name not known (non-baga prepared BAMs)
            # so just use filename
            checker.saveLocal(sample)
        else:
            # sample and reference genome know (in baga pipeline)
            checker.saveLocal()
        
        # report durations, time left etc
        _report_time(start_time, cnum, len(checkers))

    return(checkers)
class Checker:
    '''
    The Checker class of the Structure module contains a method to check for 
    structural rearrangements between a reference genome sequence and a query 
    genome from which a set of paired end short reads are provided. Structural 
    rearrangements are detected by changes in the proportion of mapped reads 
    that were assigned to proper pairs by Burrows-Wheeler Aligner (BWA).
    '''

    def __init__(self, path_to_bam):
        '''
        A structure checker object must be instantiated with:
            
            - path to a sorted bam file containing short paired end reads aligned 
        to the genome (e.g. via Reads and SAMs classes in CallVariants module)
        '''
        
        e = 'Could not find %s.\nPlease ensure all BAM files exist'
        assert _os.path.exists(path_to_bam), e % path_to_bam
        
        self.reads = _pysam.Samfile(path_to_bam, 'rb')
        self.reads_name = self.reads.header['RG'][0]['ID']
        self.genome_length = self.reads.lengths[0]
        self.genome_name = self.reads.references[0]



    def regionProperProportion(self, start, end):
        '''of all fragments with at least two reads, what proportion does BWA consider "proper"?'''
        reads_iter = self.reads.fetch( self.reads.references[0], start, end)
        both_mapped_proper = set()
        both_mapped_notproper = set()
        for r in reads_iter:

            if not r.is_duplicate and not r.mate_is_unmapped:
                if r.is_proper_pair:
                    both_mapped_proper.add(r.query_name)
                else:
                    both_mapped_notproper.add(r.query_name)

        proportion = float(len(both_mapped_proper))/ (len(both_mapped_proper)+len(both_mapped_notproper))
        #print('%s / %s = %.03f' % (len(both_mapped_proper), (len(both_mapped_proper)+len(both_mapped_notproper)), proportion))
        return(proportion)

    def getCoverageDepths(self, resolution = 10, min_mapping_quality = 30, load = True, save = True):
        '''
        collect per position along genome, aligned read coverage depth that pass a 
        quality standard and those that BWA considers additionally to be in a 
        "proper" pair.
        '''
        found_depths = False
        if load:
            use_name = _os.path.extsep.join(self.reads.filename.split(_os.path.extsep)[:-1])
            use_name = use_name + '_depths'
            filein = '{}.baga'.format(use_name)
            # could save this with file?
            array_types = {}
            array_types['depth_total'] = 'i'
            array_types['depth_proper'] = 'i'
            try:
                with _tarfile.open(filein, "r:gz") as tar:
                    for member in tar:
                        contents = _StringIO(tar.extractfile(member).read())
                        try:
                            # either json serialised conventional objects
                            contents = _json.loads(contents.getvalue())
                        except ValueError:
                            # or longer python array.array objects
                            contents = _array(array_types[member.name], contents.getvalue())
                        
                        setattr(self, member.name, contents)
                
                print("Found and loaded previously scanned coverage depths from {} which saves time!".format(filein))
                found_depths = True
            except IOError:
                print("Couldn't find previously scanned coverages at {}".format(filein))

        if not found_depths:
            num_ref_positions = self.reads.header['SQ'][0]['LN']
            depth_total = _array('i', (0,) * (int(num_ref_positions / resolution) + 1))
            depth_proper = _array('i', (0,) * (int(num_ref_positions / resolution) + 1))
            pl_iter = self.reads.pileup( self.reads.references[0] )
            for x in pl_iter:
                # Coordinates in pysam are always 0-based.
                # SAM text files use 1-based coordinates.
                if x.pos % resolution == 0:
                    read_count_proper_pair = 0
                    read_count_non_dup = 0
                    for r in x.pileups:
                        if \
                          not r.alignment.is_qcfail and \
                          not r.alignment.is_duplicate and \
                          not r.alignment.is_secondary and \
                          r.alignment.mapping_quality >= min_mapping_quality:
                            
                            read_count_non_dup += 1
                            
                            if r.alignment.is_proper_pair:
                                
                                read_count_proper_pair += 1
                    
                    pos_in_arrays = x.pos / resolution
                    depth_total[pos_in_arrays] = read_count_non_dup
                    depth_proper[pos_in_arrays] = read_count_proper_pair
            
            self.depth_total = depth_total
            self.depth_proper = depth_proper

        if save and not found_depths:
            use_name = _os.path.extsep.join(self.reads.filename.split(_os.path.extsep)[:-1])
            use_name = use_name + '_depths'
            fileout = '{}.baga'.format(use_name)
            try:
                print("Saving scanned coverage depths at {} to save time if reanalysing".format(fileout))
                def add_array(obj, name):
                    io = _StringIO(obj.tostring())
                    io.seek(0, _os.SEEK_END)
                    length = io.tell()
                    io.seek(0)
                    info = _tarfile.TarInfo(name = name)
                    info.size = length
                    tar.addfile(tarinfo = info, fileobj = io)
                
                with _tarfile.open(fileout, "w:gz") as tar:
                    print('Writing to {} . . . '.format(fileout))
                    add_array(depth_total, 'depth_total')
                    add_array(depth_proper, 'depth_proper')
                    
            except IOError:
                print("Attempt to save scanned coverage depths at {} failed . . .".format(fileout))


    def getMeanInsertSize(self, upper_limit = 10000, min_mapping_quality = 30, load = True, save = True):
        '''
        omits pairs at either end of sequence break point of 
        circular chromosome (by ampplying an upper limit) that 
        would cause a few chromosome length insert sizes.
        '''
        use_name = _os.path.extsep.join(self.reads.filename.split(_os.path.extsep)[:-1])
        use_name = use_name + '_mean_insert_size'
        filein = '{}.baga'.format(use_name)
        if load:
            try:
                mean_insert_size = float(open(filein).read())
                loaded_from_file = True
                print('Loaded insert size from {}'.format(filein))
            except ValueError:
                mean_insert_size = False
                loaded_from_file = False
                pass
            except IOError:
                mean_insert_size = False
                loaded_from_file = False
                pass

        if not mean_insert_size:
            print('Calculating mean insert size from BAM file . . .')
            alignment = self.reads.fetch(self.reads.references[0])
            isizes = _array('i')
            for read in alignment:
                if read.is_proper_pair and \
                           read.is_read1 and \
                           not read.is_duplicate and \
                           not read.is_qcfail and \
                           read.mapping_quality >= min_mapping_quality:
                    
                    insert_length = abs(read.template_length)
                    if insert_length < upper_limit:
                        isizes.append(insert_length)
            
            mean_insert_size = sum(isizes) / float(len(isizes))

        if save and not loaded_from_file:
            try:
                open(filein, 'w').write(str(mean_insert_size))
            except IOError:
                print('Could not save mean insert size to {}'.format(mean_insert_size))

        self.mean_insert_size = mean_insert_size


    def getProperRatios(self, resolution = 10, include_zeros = False):
        '''
        Infers ratios non-proper pair to proper pair per position. Optionally 
        including regions with zero depths as zero ratio, defaults to excluding.
        Relatively more non-proper pair gives a higher ratio.
        '''
        # could pre-declare and use
        # n, total, proper in _izip(xrange(self.depth_total), self.depth_total, self.depth_proper)
        # ratios = _array('f', (-1,) * len(self.depth_total))

        # currently appending
        #a, b, c = 0, 0, 0
        ratios = _array('f')
        for total, proper in _izip(self.depth_total, self.depth_proper):
            if total >= proper > 0:
                #a += 1
                # both total aligned and proper pairs positive (present) and at least some nonpropers
                # typically proper > nonproper, ratio 0.1-0.3
                # as total non-proper approaches proper pairs, 
                # within an insert length of disturbance,
                # ratio approaches 1 (50:50 nonproper:proper)
                # all proper, ratio == 0
                ratios.append((total - proper) / float(proper))
            elif total > proper == 0:
                #b += 1
                # no proper, set upper limit of ratio == total reads
                # not important here because we are interested exceeding a 
                # threshold of relatively many non-proper pair reads which this
                # would.
                ratios.append(total)
            else:
                #c += 1
                # total == proper == 0
                # zero aligned reads
                # avoid zero division and distinguish from all proper pairs i.e., 0 / 100 == 0
                ratios.append(-1)

        #print(a,b,c)
        self.ratios = ratios



    def getSmoothedRatios(self, window = 500, step = 1, resolution = 10):
        '''
        calculate:
            mean, 

        over window of specified width moving by specified step size. Input can have 
        a lower than 1:1 resolution e.g., 1 in 10 positions with resolution = 10
        '''
        # set zero depths to zero ratios for mean (~ omit NAs)
        # eventually omit from actual calculation (below)
        #use_ratios = _array('f',[r if r != -1 else 0 for r in self.ratios])

        # omit -1 no depths from ratios below (but include zeros)
        use_ratios = self.ratios
        means = _array('f')
        for i in range(0, len(use_ratios) * resolution - window, resolution)[::step]:  #break
            # collect nt range allowing for resolution of ratios
            these = use_ratios[(i / resolution) : ((i + window) / resolution)]
            # exclude zero ratios from calculation of mean: 
            # associated with areas of low quality read alignments
            # also present at edges of large deletions so prevent them lowering window mean prematurely
            # require minimum half window length 
            these = [t for t in these if t >= 0]
            if len(these) <= window / 2.0 / resolution:
                # when length of non-zero window is less than half that specified,
                # make up with zeros to decrease mean moderately close to edge of 
                # reference chromosome region with reads mapped
                these += [0] * int(window / 2.0 / resolution - len(these))
            
            these_mean = sum(these) / float(len(these))
            means.append(these_mean)

        self.smoothed_ratios = means




    def scan_threshold(self, values, threshold, offset, 
                             test = '>=', 
                             name = 'threshold', 
                             resolution = 10, 
                             buffer_distance = 0):
        '''
        Reports ranges for regions that exceed a threshold.
        Values given as list, resolution and offset determines positions they 
        correspond to.
        Offset should be half the width of moving window, if used.
        Buffer_distance is excluded region at each end of chromosome sequence if 
        known to be circular.
        Threshold testing can be '>', '>=', '<' or '<='
        '''
        # sort out test function
        if test == '>=':
            
            def threshold_crossed(value):
                return(_operator.ge(value, threshold))
            
        elif test == '<=':
            
            def threshold_crossed(value):
                return(_operator.le(value, threshold))

        elif test == '<':
            
            def threshold_crossed(value):
                return(_operator.lt(value, threshold))

        elif test == '>':
            
            def threshold_crossed(value):
                return(_operator.gt(value, threshold))

        else:
            print('Warning: "test" can be ">", ">=", "<" or "<=", not "{}"'.format(test))
            
            def threshold_crossed(value):
                return(False)


        # if input is a (sparse) dict, make it list-like
        if isinstance(values, dict):
            data_type = str(type(values.values()[0])).split("<type '")[1][0]
            use_values = _array(data_type)
            for pos1 in range(min(values), max(values), resolution):
                try:
                    use_values.append(values[pos1])
                except KeyError:
                    use_values.append(0)
        else:
            use_values = values

        suspect_regions = []
        collecting = False
        for pos,v in zip(range(offset, len(values)*resolution + offset, resolution), use_values):
            if threshold_crossed(v):
                if not collecting:
                    # exceeds limit and not collecting so start
                    collecting = True
                    suspect_regions += [pos]
            else:
                if collecting:
                    # below limit and collecting so stop
                    collecting = False
                    suspect_regions += [pos]

        if len(suspect_regions) % 2 != 0:
            # complete terminal range
            suspect_regions += [self.genome_length]

        suspect_regions = [suspect_regions[n:n+2] for n in range(0,len(suspect_regions),2)]

        # omit either end according to buffer_distance
        suspect_regions = [(s,e) for s,e in suspect_regions if s > buffer_distance and e < (len(values)*resolution + offset - buffer_distance)]

        if hasattr(self, 'suspect_regions'):
            self.suspect_regions[name] = suspect_regions
        else:
            self.suspect_regions = {name : suspect_regions}




    def extend_regions(self, filter_name, filter_to_extend, resolution = 10, threshold = 0.5):
        '''
        Given suspect regions for filtering, extend if adjacent windows have 
        majority positions with ratio -1 for no reads at default settings with
        threshold = 0.5.
        Lowering threshold so that fewer positions must have no reads makes filter
        more greedy.
        '''
        extensions = []
        for n in range(len(self.suspect_regions[filter_to_extend]) - 1 ):
            right_edge = self.suspect_regions[filter_to_extend][n][1]
            next_left_edge = self.suspect_regions[filter_to_extend][n + 1][0]
            window_size = int(round(self.mean_insert_size))
            # extend from left to right
            join = True
            if right_edge < next_left_edge - window_size:
                for p in range(right_edge, next_left_edge - window_size):
                    these_ratios = self.ratios[p / resolution: (p + window_size) / resolution]
                    if len([r for r in these_ratios if r < 0]) < window_size / resolution * threshold:
                        join = False
                        break
            else:
                these_ratios = self.ratios[right_edge / resolution: next_left_edge / resolution]
                effective_window_size = next_left_edge - right_edge
                if len([r for r in these_ratios if r <= 0]) > effective_window_size / resolution * threshold:
                    extensions += [[right_edge,next_left_edge]]
                
                continue
            
            if join:
                # got to next disrupted region: add joining non-aligned region
                extensions += [[right_edge,next_left_edge]]
            else:
                if p > right_edge:
                    # got past at least first window: store non-aligned region
                    extension_right_edge = p + int(round(window_size / 2.0))
                    extensions += [[right_edge, extension_right_edge]]
                    end = extension_right_edge
                    
                else:
                    end = right_edge
                
                # extend from right to left (if necessary)
                join = True
                for p in range(end, next_left_edge - window_size)[::-1]:  #break
                    these_ratios = self.ratios[p / resolution: (p + window_size) / resolution]
                    if len([r for r in these_ratios if r < 0]) < window_size / resolution * threshold:
                        join = False
                        break
                if join:
                    # got to back next disrupted region: add joining non-aligned region
                    # (unlikely if join False above)
                    extensions += [[end, next_left_edge]]
                    
                elif p < next_left_edge - window_size - 1:
                    # got past at least first window: store non-aligned region
                    extensions += [[p + int(round(window_size / 2.0)), next_left_edge]]

        if hasattr(self, 'suspect_regions'):
            self.suspect_regions[filter_name] = extensions
        else:
            self.suspect_regions = {filter_name : extensions}
    def saveLocal(self, name = False):
        '''
        Save additional info needed for plotting the Structure.Checker analysis
        'filename' can exclude extension: .baga will be added.
        A .baga file is mostly Python dictionaries in JSON strings and
        array.array objects in a tar.gz or pickled dictionaries in .gz format.
        '''
        if name:
            fileout = 'baga.Structure.CheckerInfo-{}.baga'.format(name)
        else:
            fileout = 'baga.Structure.CheckerInfo-{}__{}.baga'.format(self.reads_name, self.genome_name)

        # for simplicity, this also includes reads depths which may
        # have been saved in a _depths.baga file.
        with _tarfile.open(fileout, "w:gz") as tar:
            print('Writing to {} . . . '.format(fileout))
            for att_name, att in self.__dict__.items():
                if isinstance(att, _array):
                    io = _StringIO(att.tostring())
                    io.seek(0, _os.SEEK_END)
                    length = io.tell()
                    io.seek(0)
                    thisone = _tarfile.TarInfo(name = att_name)
                    thisone.size = length
                    tar.addfile(tarinfo = thisone, fileobj = io)
                elif isinstance(att, dict) or isinstance(att, str) or isinstance(att, float):
                    # ensure only dicts, strings, floats (or arrays, above) are saved
                    io = _StringIO()
                    _json.dump(att, io)
                    io.seek(0, _os.SEEK_END)
                    length = io.tell()
                    io.seek(0)
                    thisone = _tarfile.TarInfo(name = att_name)
                    thisone.size = length
                    tar.addfile(tarinfo = thisone, fileobj = io)


class Plotter:
    '''
    Plotter class of the Structure module contains methods to plot the regions likely 
    to have undergone structural rearrangements as found by an instance of the 
    Checker class.
    '''
    def __init__(self, checker_info, genome, plot_output_path,
        width_cm = 30, height_cm = 10, 
        viewbox_width_px = 1800, viewbox_height_px = 600,
        plot_width_prop = 0.8, plot_height_prop = 0.8, 
        white_canvas = True):
        '''
        Plot pairs of aligned homologous chromosome regions with percent 
        identity calculated over a moving window.
        
        genome: an instance of Genome from RepeatFinder for which repeats 
        have been inferred.
        
        plot_width_prop and plot_height_prop: Proportion of whole plot 
        area covered by actual plot, to allow space for labels.
        '''
        
        e = 'The provided genome ({}) does not seem to match the reference \
    sequence of the provided read alignment ({})'.format(
                                        genome.id, 
                                        checker_info['genome_name'])
        
        assert genome.id == checker_info['genome_name'], e
        
        self.genome = genome
        
        import svgwrite as _svgwrite
        dwg = _svgwrite.Drawing(plot_output_path, width='%scm' % width_cm, height='%scm' % height_cm,
                                profile='full', debug=True)
        
        dwg.viewbox(width = viewbox_width_px, height = viewbox_height_px)
        
        if white_canvas:
            dwg.add(_svgwrite.shapes.Rect(insert=(0, 0), size=(viewbox_width_px, viewbox_height_px), fill = _svgwrite.rgb(100, 100, 100, '%')))
        
        self.viewbox_width_px = viewbox_width_px
        self.viewbox_height_px = viewbox_height_px
        self.plot_width_prop = plot_width_prop
        self.plot_height_prop = plot_height_prop
        self.checker_info = checker_info
        self.dwg = dwg
        self.rgb = _svgwrite.rgb

    def chrom2plot_x(self, pos_chrom, start, end):
        '''convert chromosome position to plotting position in canvas'''
        plotlen_chrom = end - start
        pos_plot = (pos_chrom - start) * ((self.viewbox_width_px * self.plot_width_prop) / plotlen_chrom)
        return(pos_plot)

    def plot_scale(self,    start, 
                            end, 
                            panel, 
                            num_ticks = 5,
                            tick_len_px = 20, 
                            colour=(0,0,0,'%'), 
                            stroke_width=3, 
                            font_size = '20pt', 
                            use_fontfamily = 'Nimbus Sans L',
                            plot_label = True):
        '''given the real position ordinate on chromosome, and range of plotting window, plot ticks with position'''

        # get tick positions on chromosomes to plot
        if end - start <= 2000:
            tick_rounding = 100
        elif end - start <= 20000:
            tick_rounding = 1000
        else:
            tick_rounding = 5000
            
        tick_dist = (end - start) / num_ticks
        if tick_rounding > tick_dist:
            tick_dist = tick_rounding
        else:
            rm = tick_dist % tick_rounding
            if (tick_rounding / 2.0) < rm:
                tick_dist += (tick_rounding - rm)
            else:
                tick_dist -= rm

        plotpositions_chrom = []
        for pos in range(0, len(self.genome.sequence), tick_dist):
            if start <= pos <= end:
                plotpositions_chrom += [pos]

        # convert to x-positions for plotting
        plotlen_chrom = end - start

        # plot into full viewbox or other specified panel
        ((this_x_panel, num_x_panels),(this_y_panel, num_y_panels)) = panel

        # calculate plotting area within viewbox
        # currently only single x panel implemented (full width of view box)
        plotstart_x = (self.viewbox_width_px - (self.viewbox_width_px * self.plot_width_prop)) / 2
        plotend_x = plotstart_x + (self.viewbox_width_px * self.plot_width_prop)
        if num_x_panels > 1:
            print('>1 x panel not implemented')
            return(False)

        # this area is just the data i.e., depth line plot
        # from which feature y positions calculated
        # start with pre-calculated
        plottop_y = self.plottop_y
        plotbottom_y = self.plotbottom_y
        if num_y_panels > 1:
            # update existing plot area in y direction
            # this uses num_y_panels for overall shape and this_y_panel for target
            y_per_panel = (plotbottom_y - plottop_y) / num_y_panels
            plottop_y = plotbottom_y - y_per_panel * this_y_panel
            plotbottom_y -= y_per_panel * (this_y_panel - 1)

        # get tick positions on x-axis to plot
        plotpositions_x = [self.chrom2plot_x(pos_chrom, start, end) for pos_chrom in plotpositions_chrom]
        # make tick font size slightly smaller than labels
        font_size_int = int(font_size.replace('pt',''))
        tick_font_size = '{}pt'.format(font_size_int * 0.85)

        for n,x in enumerate(plotpositions_x):
            MoveTo = "M %s %s" % (x + plotstart_x, plotbottom_y)
            Line = "L %s %s" % (x + plotstart_x, plotbottom_y + tick_len_px)
            # use 'z' to close path with a line
            self.dwg.add(
                self.dwg.path(
                    d="%s %s z" % (MoveTo, Line), 
                    stroke = self.rgb(*colour), 
                    stroke_linecap='round', 
                    fill = 'none', stroke_width = stroke_width
                    )
            )
            textanc = 'middle'
            if tick_rounding == 100:
                fmt = '%.01f kb'
            else:
                fmt = '%d kb'
            
            tiplabel = self.dwg.text(
                                fmt % (plotpositions_chrom[n]/1000.0), 
                                insert = (x + plotstart_x,plotbottom_y + tick_len_px * 2.1),
                                fill='black', 
                                font_family = use_fontfamily, 
                                text_anchor = textanc, 
                                font_size = tick_font_size)
            
            self.dwg.add(tiplabel)

        if plot_label:
            # label x-axis
            
            xaxislabel = self.dwg.text(
                                "Reference Chromosome Position", 
                                insert = ((plotstart_x + plotend_x)/2, plotbottom_y + tick_len_px * 2.1 * 2),
                                fill = 'black', 
                                font_family = use_fontfamily, 
                                text_anchor = "middle", 
                                font_size = font_size)
            
            self.dwg.add(xaxislabel)


    def plot_ORFs(self, start, end, 
                        panel = ((1,1),(1,1)), 
                        stroke_width = 40, 
                        colour = (0,0,0,'%'), 
                        font_size = 15, 
                        use_fontfamily = 'Nimbus Sans L'):
        
        # upper < lower because SVG upside down <<<<<<<<<<<<<<<
        # plot into full viewbox or other specified panel
        ((this_x_panel, num_x_panels),(this_y_panel, num_y_panels)) = panel

        # calculate plotting area within viewbox
        # currently only single x panel implemented (full width of view box)
        plotstart_x = (self.viewbox_width_px - (self.viewbox_width_px * self.plot_width_prop)) / 2
        plotend_x = plotstart_x + (self.viewbox_width_px * self.plot_width_prop)
        if num_x_panels > 1:
            print('>1 x panel not implemented')
            return(False)

        # this area is just the data i.e., depth line plot
        # from which feature y positions calculated
        # start with pre-calculated
        plottop_y = self.plottop_y
        plotbottom_y = self.plotbottom_y
        if num_y_panels > 1:
            # update existing plot area in y direction
            # this uses num_y_panels for overall shape and this_y_panel for target
            y_per_panel = (plotbottom_y - plottop_y) / num_y_panels
            plottop_y = plotbottom_y - y_per_panel * this_y_panel
            plotbottom_y -= y_per_panel * (this_y_panel - 1)

        # collect names, strand and ranges of ORFs
        ORF_plot_info = []
        for ID,(s,e,d,name) in self.genome.ORF_ranges.items():
            status = False
            if start <= s and e < end:
                plot_x_s = self.chrom2plot_x(s, start, end) + plotstart_x
                plot_x_e = self.chrom2plot_x(e, start, end) + plotstart_x
                status = 'complete'
            elif s < start < e:
                plot_x_s = self.chrom2plot_x(start, start, end) + plotstart_x
                plot_x_e = self.chrom2plot_x(e, start, end) + plotstart_x
                status = 'left cut'
            elif s < end < e:
                plot_x_s = self.chrom2plot_x(s, start, end) + plotstart_x
                plot_x_e = self.chrom2plot_x(end, start, end) + plotstart_x
                status = 'right cut'
            
            if status:
                if len(name):
                    use_name = '{} ({})'.format(ID, name)
                else:
                    use_name = ID
                
                ORF_plot_info += [(plot_x_s, plot_x_e, d, use_name, status)]

        # some additional parameters here for tweaking layout of features
        feature_thickness = (plotbottom_y - plottop_y) * 0.07
        point_width = (plotend_x - plotstart_x) * 0.008
        # half a feature thickness above scale, one feature thickness for reverse strand, half a feature thickness above reverse strand
        forward_y_offset = feature_thickness * 0.5 + feature_thickness + feature_thickness * 0.5
        reverse_y_offset = feature_thickness * 0.5

        # plot feature lane guide lines
        commands = ["M %s %s" % (
                                            plotstart_x, 
                                            plotbottom_y - reverse_y_offset - feature_thickness * 0.5
                                            )]
        commands += ["L %s %s" % (
                                            plotend_x, 
                                            plotbottom_y - reverse_y_offset - feature_thickness * 0.5
                                            )]
        self.dwg.add(
            self.dwg.path(
                d=' '.join(commands), 
                stroke=self.rgb(70, 70, 70,'%'), stroke_linecap='round', 
                fill = 'none', stroke_width = 5
            )
        )

        commands = ["M %s %s" % (
                                            plotstart_x, 
                                            plotbottom_y - forward_y_offset - feature_thickness * 0.5
                                            )]
        commands += ["L %s %s" % (
                                            plotend_x, 
                                            plotbottom_y - forward_y_offset - feature_thickness * 0.5
                                            )]
        self.dwg.add(
            self.dwg.path(
                d=' '.join(commands), 
                stroke=self.rgb(70, 70, 70,'%'), stroke_linecap='round', 
                fill = 'none', stroke_width = 5
            )
        )

        # plot labels
        textanc = 'end'
        horizontal_offset = 20
        label = "Forward strand"
        feature_lane_label = self.dwg.text(
                                            label, 
                                            insert = (
                                                    plotstart_x - horizontal_offset, 
                                                    plotbottom_y - (forward_y_offset + feature_thickness * 0.5)
                                                    ), 
                                             fill = 'black', font_family = use_fontfamily, 
                                             text_anchor = textanc, font_size = '%dpt' % font_size, 
                                             baseline_shift='-50%'
                                             )
        self.dwg.add(feature_lane_label)

        label = "Reverse strand"
        feature_lane_label = self.dwg.text(
                                            label, 
                                            insert = (
                                                plotstart_x - horizontal_offset, 
                                                plotbottom_y - (reverse_y_offset + feature_thickness * 0.5)
                                                ), 
                                             fill='black', font_family=use_fontfamily, 
                                             text_anchor=textanc, font_size = '%dpt' % font_size,
                                             baseline_shift='-50%'
                                             )
        self.dwg.add(feature_lane_label)

        ORF_plot_info_forward_strand = sorted(
                                                [o for o in ORF_plot_info if o[2] == 1],
                                                reverse = True
                                                )
        ORF_plot_info_reverse_strand = sorted(
                                                [o for o in ORF_plot_info if o[2] == -1],
                                                reverse = False
                                                )

        for s,e,d,name,state in ORF_plot_info_forward_strand + ORF_plot_info_reverse_strand:
            if d == 1:
                start,end = s,e
                # forward strand
                #if start ==  plotstart_x:
                if state == 'left cut':
                    # 'left cut', start lower left plus a bit for cut angle
                    commands = ["M %s %s" % (
                                                start - point_width, 
                                                plotbottom_y - forward_y_offset
                                                )]
                else:
                    # start lower left at feature start
                    commands = ["M %s %s" % (
                                                start, 
                                                plotbottom_y - forward_y_offset
                                                )]
                
                #if end == plotend_x:
                if state == 'right cut':
                    # 'right cut', go to lower right then top right plus a bit for angle
                    commands += ["L %s %s" % (
                                                end, 
                                                plotbottom_y - forward_y_offset
                                                )]
                    commands += ["L %s %s" % (
                                                end + point_width, 
                                                plotbottom_y - (forward_y_offset + feature_thickness * 1)
                                                )]
                else:
                    # else point on right for forward strand
                    commands += ["L %s %s" % (
                                                end - point_width, 
                                                plotbottom_y - forward_y_offset
                                                )]
                    commands += ["L %s %s" % (
                                                end, 
                                                plotbottom_y - (forward_y_offset + feature_thickness * 0.5)
                                                )]
                    commands += ["L %s %s" % (
                                                end - point_width, 
                                                plotbottom_y - (forward_y_offset + feature_thickness * 1)
                                                )]
                
                commands += ["L %s %s z" % (
                                                start, 
                                                plotbottom_y - (forward_y_offset + feature_thickness * 1)
                                                )]
                use_y_offset = forward_y_offset
                
            else:
                # reverse strand
                start,end = e,s
                #if start == plotend_x:
                if state == 'right cut':
                    # 'right cut', go upper right plus a bit for cut angle
                    commands = ["M %s %s" % (
                                                start + point_width, 
                                                plotbottom_y - (reverse_y_offset + feature_thickness * 1)
                                                )]
                else:
                    # start upper right plus a bit for cut angle
                    commands = ["M %s %s" % (
                                                start,
                                                plotbottom_y - (reverse_y_offset + feature_thickness * 1)
                                                )]
                
                #if end == plotstart_x:
                if state == 'left cut':
                    # 'left cut', go to upper left
                    commands += ["L %s %s" % (
                                                end, 
                                                plotbottom_y - (reverse_y_offset + feature_thickness * 1)
                                                )]
                    # plus a bit lower left
                    commands += ["L %s %s" % (
                                                end - point_width, 
                                                plotbottom_y - reverse_y_offset
                                                )]
                else:
                    # else point on left for reverse strand
                    commands += ["L %s %s" % (
                                                end + point_width, 
                                                plotbottom_y - (reverse_y_offset + feature_thickness * 1)
                                                )]
                    commands += ["L %s %s" % (
                                                end, 
                                                plotbottom_y - (reverse_y_offset + feature_thickness * 0.5)
                                                )]
                    commands += ["L %s %s" % (
                                                end + point_width, 
                                                plotbottom_y - reverse_y_offset
                                                )]
                
                commands += ["L %s %s z" % (
                                                start, 
                                                plotbottom_y - reverse_y_offset
                                                )]
                
                use_y_offset = reverse_y_offset
                
            self.dwg.add(
                self.dwg.path(
                    d=' '.join(commands), stroke='white', stroke_linecap='round', 
                    fill = self.rgb(*colour), stroke_width = 1
                )
            )
            added_label = self.dwg.add(
                self.dwg.text(
                    name,
                    insert = (
                        (start+end)/2,
                        plotbottom_y - (use_y_offset + feature_thickness * 0.5)
                        ), 
                    fill='white', stroke='black', stroke_width=0.4, 
                    font_family=use_fontfamily, font_weight='bold',
                    text_anchor='middle', font_size = '%dpt' % font_size, 
                    baseline_shift='-50%'
                )
            )
            # this would vary with font and font size
            max_length_for_unrotated_label = self.viewbox_width_px * 1.0/18
            if max(start,end) - min(start,end) < max_length_for_unrotated_label:
                x = (start+end)/2
                y = plotbottom_y - (use_y_offset + feature_thickness * 0.5)
                added_label.rotate(-25, center = (x, y))

    def plot_LargeFeatures(self, start, end, panel = ((1,1),(1,1)),
                          stroke_width = 40, colour = (20, 20, 20,'%'), 
                          font_size = 15, use_fontfamily = 'Nimbus Sans L'):
        
        # plot into full viewbox or other specified panel
        ((this_x_panel, num_x_panels),(this_y_panel, num_y_panels)) = panel

        # calculate plotting area within viewbox
        # currently only single x panel implemented (full width of view box)
        plotstart_x = (self.viewbox_width_px - (self.viewbox_width_px * self.plot_width_prop)) / 2
        plotend_x = plotstart_x + (self.viewbox_width_px * self.plot_width_prop)
        if num_x_panels > 1:
            print('>1 x panel not implemented')
            return(False)


        # this area is just the data i.e., depth line plot
        # from which feature y positions calculated
        # start with pre-calculated
        plottop_y = self.plottop_y
        plotbottom_y = self.plotbottom_y
        if num_y_panels > 1:
            # update existing plot area in y direction
            # this uses num_y_panels for overall shape and this_y_panel for target
            y_per_panel = (plotbottom_y - plottop_y) / num_y_panels
            plottop_y = plotbottom_y - y_per_panel * this_y_panel
            plotbottom_y -= y_per_panel * (this_y_panel - 1)


        Features_to_plot = []
        #for feat_chrom_start,feat_chrom_end,n in Features:
        for name, (feat_chrom_start, feat_chrom_end) in self.genome.large_mobile_element_ranges.items():
            # don't plot unless part of feature is within plotting range
            if feat_chrom_start < end and feat_chrom_end > start:
                s = self.chrom2plot_x(max( start, feat_chrom_start), start, end ) + plotstart_x
                e = self.chrom2plot_x(min( end, feat_chrom_end), start, end ) + plotstart_x
                Features_to_plot += [(s, e, name)]


        # some more parameters for tweaking feature layout here
        # same set in ORF function
        feature_thickness = (plotbottom_y - plottop_y) * 0.07
        # used if feature cut at either end
        point_width = (plotend_x - plotstart_x) * 0.008
        # half a feature thickness above scale, 
        # two feature thickness for strands plus a half inbetween, 
        # half a feature thickness above forward strand
        y_offset = feature_thickness * 0.5 + feature_thickness + \
                         feature_thickness * 0.5 + feature_thickness + \
                         feature_thickness * 0.5

        # plot feature lane guide lines
        commands = ["M %s %s" % (
                                    plotstart_x, 
                                    plotbottom_y - y_offset - feature_thickness * 0.5
                                    )]
        commands += ["L %s %s" % (
                                    plotend_x, 
                                    plotbottom_y - y_offset - feature_thickness * 0.5
                                    )]
        self.dwg.add(
            self.dwg.path(
                d=' '.join(commands), stroke=self.rgb(70, 70, 70,'%'), 
                stroke_linecap='round', fill = 'none', stroke_width = 5
            )
        )

        # plot labels
        textanc = 'end'
        horizontal_offset = 20
        label = "Large Features"
        feature_lane_label = self.dwg.text(
                            label, 
                            insert = (
                                plotstart_x - horizontal_offset, 
                                plotbottom_y - (y_offset + feature_thickness * 0.5)
                            ), 
                            fill='black', font_family=use_fontfamily, 
                            text_anchor=textanc, 
                            font_size = '%dpt' % font_size, 
                            baseline_shift='-50%'
                        )
        self.dwg.add(feature_lane_label)

        # plot feature
        for s, e, name in Features_to_plot:
            start,end = s,e
            if start ==  plotstart_x:    # 'left cut'
                commands = ["M %s %s" % (
                                            start - point_width, 
                                            plotbottom_y - y_offset
                                            )]
            else:
                commands = ["M %s %s" % (
                                            start, 
                                            plotbottom_y - y_offset
                                            )]
            
            commands += ["L %s %s" % (
                                            end, 
                                            plotbottom_y - y_offset
                                            )]
            
            if end == plotend_x:   # 'right cut':
                commands += ["L %s %s" % (
                                            end + point_width, 
                                            plotbottom_y - (y_offset + feature_thickness * 1)
                                            )]
            else:
                commands += ["L %s %s" % (
                                            end, 
                                            plotbottom_y - (y_offset + feature_thickness * 1)
                                            )]
            
            commands += ["L %s %s z" % (
                                            start, 
                                            plotbottom_y - (y_offset + feature_thickness * 1)
                                            )]
            
            self.dwg.add(
                self.dwg.path(
                    d=' '.join(commands), 
                    stroke='white', stroke_linecap='round', 
                    fill = self.rgb(*colour), stroke_width = 1
                )
            )
            self.dwg.add(
                self.dwg.text(
                    name, 
                    insert = (
                        (start+end)/2, 
                        plotbottom_y - (y_offset + feature_thickness * 0.5)
                    ), 
                    fill='white', stroke='black', stroke_width=0.2, 
                    font_family=use_fontfamily, font_weight='bold',
                    text_anchor='middle', font_size = '%dpt' % font_size, 
                    baseline_shift='-50%'
                    ))

    def calc_plot_region(self, start, 
                          end, 
                          panel = ((1,1),(1,1)),
                          values_upper_prop = 0.75,
                          values_lower_prop = 0.4):
        
        '''
        given the real position ordinate on chromosome and panel for plotting
        calculate region for plotting.
        '''
        
        # plot into full viewbox or other specified panel
        ((this_x_panel, num_x_panels), (this_y_panel, num_y_panels)) = panel

        # calculate plotting area within viewbox
        # currently only single x panel implemented (full width of view box)
        # this area includes all: depth, scale, ORFs, features etc
        plotstart_x = (self.viewbox_width_px - (self.viewbox_width_px * self.plot_width_prop)) / 2
        plotend_x = plotstart_x + (self.viewbox_width_px * self.plot_width_prop)
        if num_x_panels > 1:
            print('>1 x panel not implemented')
            return(False)

        plottop_y = (self.viewbox_height_px - (self.viewbox_height_px * self.plot_height_prop)) / 2
        plotbottom_y = plottop_y + (self.viewbox_height_px * self.plot_height_prop)

        # to prevent x-axis label going off the bottom
        # probably a better way of achieving this
        shift_up_offset = self.viewbox_height_px * self.plot_height_prop * 0.1
        plottop_y -= shift_up_offset
        plotbottom_y -= shift_up_offset

        # this area is just the data i.e., depth line plot
        if num_y_panels > 1:
            # update existing plot area in y direction
            # this uses num_y_panels for overall shape and this_y_panel for target
            y_per_panel = (plotbottom_y - plottop_y) / num_y_panels
            plottop_y = plotbottom_y - y_per_panel * this_y_panel
            plotbottom_y -= y_per_panel * (this_y_panel - 1)

        # lane height . . for plotting multiple data sets over same region.
        upper_y = plotbottom_y - (plotbottom_y - plottop_y) * values_upper_prop
        lower_y = plotbottom_y - (plotbottom_y - plottop_y) * values_lower_prop
        # Single lane for comparing different regions in one plot.
        y_per_lane = (lower_y - upper_y) / 1

        self.plotstart_x = plotstart_x
        self.plotend_x = plotend_x
        self.upper_y = upper_y
        self.lower_y = lower_y
        self.y_per_lane = y_per_lane
        self.plottop_y = plottop_y
        self.plotbottom_y = plotbottom_y

    def plot_values(self, values, 
                          start, 
                          end, 
                          label, 
                          offset = 0,
                          resolution = 10,
                          panel = ((1,1),(1,1)),
                          colour = ('60', '60', '60', '%'), 
                          fill = True,
                          # 'Left' or 'Right'
                          plot_axis = False,
                          max_y_scale = False,
                          add_sample_label = True,
                          y_axis_label = 'Aligned Reads',
                          font_size = '20pt', 
                          use_fontfamily = 'Nimbus Sans L'):
        
        '''
        given the real position ordinate on chromosome with e.g., read depth data, 
        plot line. Also plot y-axis and scale
        Values must be either:
            a dictionary: chromosome position => value
        or:
            an ordered iterable like a list or array. If the list len is less than 
            the chromosome, the resolution must account for the difference e.g., 
            resolution = 10 if the list lenth is 1/10 of the chromosome length
        
        use offset as half moving window width if required
        '''
        
        ## could check for type tuple here for plotting ratios

        if isinstance(values, dict):
            #region_values = dict([(p,d) for p,d in use_depths.items() if start <= p <= end])
            region_values = {}
            # pileup doesn't return zero depths so add them here
            for pos1 in xrange(start - offset, end - offset):
                if pos1 % resolution == 0:
                    try:
                        region_values[pos1 + offset] = values[pos1]
                    except KeyError:
                        region_values[pos1 + offset] = 0
            
            plotpositions_chrom = sorted(region_values)
            
        elif isinstance(values, list) or isinstance(values, _array):
            region_values = {}
            plotpositions_chrom = []
            for pos1 in xrange(start - offset, end - offset):
                if pos1 % resolution == 0:
                    try:
                        region_values[pos1 + offset] = values[int(pos1/resolution)]
                        plotpositions_chrom += [pos1 + offset]
                    except IndexError:
                        # should be off the end of the chromosome
                        pass

        # get positions on x-axis to plot
        plotpositions_x = [self.chrom2plot_x(pos_chrom, start, end) for pos_chrom in plotpositions_chrom]

        #### plot values ####

        # get corresponding depths over this chromosome region
        # normalise
        if not max_y_scale:
            # unless provided . . .
            max_plot_depth = max(region_values.values()) * 1
            # round up to the nearest 10 reads
            max_y_scale = round((max(region_values.values()) + 10) / 10.0) * 10
            
        plotdepths = []
        for pos0 in plotpositions_chrom:
            d = region_values[pos0]
            if d >= max_y_scale:
                use_d = 1.0
            else:
                use_d = d / float(max_y_scale)
            
            plotdepths += [use_d]

        # make paths for each isolate plot lane 
        lane_num = 0

        # establish lane plot area (always single for percent ID at duplicate regions)
        plot_spacing = 3
        lane_upper_y = self.lower_y - (lane_num + 1) * self.y_per_lane + plot_spacing
        lane_lower_y = self.lower_y - lane_num * self.y_per_lane - plot_spacing
        lane_plot_height = lane_lower_y - lane_upper_y
        #print(i, lane_lower_y, lane_lower_y)


        if fill:
            # start at lower left then move up to first position
            commands = ['M %s %s' % (
                                        plotpositions_x[0] + self.plotstart_x, 
                                        lane_lower_y
                                        )]
        else:
            commands = ['M %s %s' % (
                                        plotpositions_x[0] + self.plotstart_x, 
                                        lane_lower_y - lane_plot_height * plotdepths[0]
                                        )]

        for n,d in enumerate(plotdepths):
            # because everything is upside down in SVG minus goes up on page
            commands += ['L %s %s' % (
                                        plotpositions_x[n] + self.plotstart_x,
                                        lane_lower_y - lane_plot_height * d
                                        )]

        if fill:
            use_stroke_width = '0'
            use_fill_colour = self.rgb(*colour)
            # finish at lower right then close
            commands += ['L %s %s z' % (
                                        plotpositions_x[-1] + self.plotstart_x, 
                                        lane_lower_y
                                        )]
        else:
            use_stroke_width = '3'
            use_fill_colour = 'none'
            # plot ended at last point without closing

        plot_path = self.dwg.path(
                            d=' '.join(commands), stroke = self.rgb(*colour), 
                            stroke_linecap='round', stroke_width= use_stroke_width, 
                            fill = use_fill_colour, fill_rule='evenodd'
                            )

        self.dwg.add(plot_path)


        #### plot axis ####

        label_horizontal_offset = 0
        if plot_axis:
            if plot_axis == 'Right':
                use_x_offset = self.plotend_x
                direction = 1
                label_rot = 90
                textanc = 'start'
            else:
                # defaults to left side
                use_x_offset = self.plotstart_x
                direction = -1
                label_rot = 270
                textanc = 'end'
            
            tick_offset = 20 * direction
            # set 2% space between axis and plotting area
            use_x_offset += (self.plotend_x - self.plotstart_x) * 0.02 * direction
            
            commands = ['M %s %s' % (use_x_offset, self.upper_y), 'L %s %s' % (use_x_offset, self.lower_y)]
            plot_path = self.dwg.path(
                                d=' '.join(commands), stroke = 'black', 
                                stroke_linecap='round', stroke_width= '3',
                                fill = 'none', fill_rule='evenodd'
                                )
            
            self.dwg.add(plot_path)
            
            # ticks <== vary by divisibility of max_y_scale
            num_ticks = 5.0
            
            if max_y_scale > num_ticks**2:
                make_integers = True
            else:
                make_integers = False
            
            # make tick font size slightly smaller than labels
            font_size_int = int(font_size.replace('pt',''))
            tick_font_size = '{}pt'.format(font_size_int * 0.85)
            
            for n in range(int(num_ticks) + 1):
                commands = ['M %s %s' % (
                                            use_x_offset, 
                                            self.lower_y - (self.lower_y - self.upper_y) * (n/5.0)
                                        ), 
                                        'L %s %s' % (
                                            use_x_offset + tick_offset, 
                                            self.lower_y - (self.lower_y - self.upper_y) * (n/5.0)
                                        )]
                
                plot_path = self.dwg.path(
                                    d=' '.join(commands), 
                                    stroke = 'black', 
                                    stroke_linecap='round', 
                                    stroke_width= '3',
                                    fill = 'none', 
                                    fill_rule='evenodd'
                                    )
                
                self.dwg.add(plot_path)
                
                value = round(n*(max_y_scale/num_ticks), 2)
                if make_integers:
                    value = int(value)
                
                ticklabel = self.dwg.text('%s' % value, 
                                                  insert = (
                                                    use_x_offset + tick_offset * 1.5, 
                                                    self.lower_y - (self.lower_y - self.upper_y) * (n/5.0)
                                                  ),
                                                  fill='black', 
                                                  font_family=use_fontfamily, 
                                                  text_anchor=textanc, 
                                                  font_size = tick_font_size, 
                                                  baseline_shift='-50%'
                            )
                
                self.dwg.add(ticklabel)
            
            # label y-axis
            label_horizontal_offset = 0
            textanc = 'middle'
            x, y = use_x_offset - label_horizontal_offset, lane_lower_y - (self.lower_y - self.upper_y) * 0.5
            x_pos = x + 90 * direction
            yaxislabel = self.dwg.text(
                                    '', 
                                    insert = (x_pos, y), 
                                    fill='black', font_family=use_fontfamily, 
                                    text_anchor=textanc, font_size = font_size)  # , baseline_shift='-50%'
            
            if isinstance(y_axis_label, str):
                yaxislabel.add(self.dwg.tspan(y_axis_label))
            else:
                # must be tuple
                # InkScape seems not to support em (font height) unites . . . .
                # but knowning font size in pt seems just as good
                font_size_int = int(font_size.replace('pt',''))
                
                # offset away from axis if additional lines in label
                # should probably do a proper pt to px translation?
                extra_dist = len(y_axis_label) * font_size_int * 0
                
                x_pos += extra_dist * direction
                yaxislabel.add(self.dwg.tspan(y_axis_label[0], x = [x_pos], dy = ['-{}pt'.format(font_size_int * 1.2)]))
                yaxislabel.add(self.dwg.tspan(y_axis_label[1], x = [x_pos], dy = ['{}pt'.format(font_size_int * 1.2)]))
            
            added_yaxislabel = self.dwg.add(yaxislabel)
            added_yaxislabel.rotate(label_rot, center = (x_pos, y))

        if add_sample_label:
            # label duplicate (A, B, etc)
            textanc = 'middle'
            isolatelabel = self.dwg.text(label, 
                                        insert = (
                                            self.plotstart_x - label_horizontal_offset * 4, 
                                            lane_lower_y - (self.lower_y - self.upper_y) * 1.3
                                        ), 
                                        fill='black', font_family=use_fontfamily, 
                                        text_anchor=textanc, 
                                        font_size = font_size, baseline_shift='-50%')
            
            self.dwg.add(isolatelabel)

        return(max_y_scale)

    def plot_suspect_regions(self, 
                              start, end, 
                              suspect_regions, 
                              panel = ((1,1),(1,1)),
                              colour = ('80', '10', '10', '%')):

        '''given the real position ordinate on chromosome with suspicious regions, plot them.'''
        
        # establish lane plot area (always single for percent ID at duplicate regions)
        lane_num = 0
        plot_spacing = 3
        lane_upper_y = self.lower_y - (lane_num + 1) * self.y_per_lane + plot_spacing
        lane_lower_y = self.lower_y - lane_num * self.y_per_lane - plot_spacing
        lane_plot_height = lane_lower_y - lane_upper_y

        # draw ambiguous (translocated) regions
        for chrm_s,chrm_e in suspect_regions:
            if chrm_e > start and chrm_s < end:
                
                s = self.chrom2plot_x(max(chrm_s, start), start, end)
                e = self.chrom2plot_x(min(chrm_e, end), start, end)
                
                commands = ['M %s %s' % (
                                        s + self.plotstart_x, 
                                        lane_lower_y
                                        )]
                commands += ['L %s %s' % (
                                        s + self.plotstart_x, 
                                        lane_upper_y
                                        )]
                commands += ['L %s %s' % (
                                        e + self.plotstart_x, 
                                        lane_upper_y
                                        )]
                commands += ['L %s %s z' % (
                                        e + self.plotstart_x, 
                                        lane_lower_y
                                        )]
                plot_path = self.dwg.path(
                                    d=' '.join(commands), stroke = self.rgb(*colour), 
                                    stroke_linecap='round', stroke_width= '3', 
                                    fill = self.rgb(*colour), fill_rule='evenodd',
                                    fill_opacity = 0.2
                                    )
                
                self.dwg.add(plot_path)

    def plot_threshold(self, 
                              threshold,
                              max_y_scale,
                              panel = ((1,1),(1,1)),
                              colour = ('0', '100', '0', '%')):

        '''given the real position ordinate on chromosome with suspiscious regions, plot them.'''
        
        # establish lane plot area (always single for percent ID at duplicate regions)
        lane_num = 0
        plot_spacing = 3
        lane_upper_y = self.lower_y - (lane_num + 1) * self.y_per_lane + plot_spacing
        lane_lower_y = self.lower_y - lane_num * self.y_per_lane - plot_spacing
        lane_plot_height = lane_lower_y - lane_upper_y

        # a "y =" line
        # blue for now
        colour = ('80', '10', '10', '%')
        commands = ['M %s %s' % (
                                self.plotstart_x, 
                                self.lower_y - (self.lower_y - self.upper_y) * threshold * (1.0 / max_y_scale)
                                #lane_lower_y - (lane_lower_y - lane_upper_y) * threshold  <== this required if >1 lane?
                                )]
        commands += ['L %s %s' % (
                                self.plotend_x, 
                                self.lower_y - (self.lower_y - self.upper_y) * threshold * (1.0 / max_y_scale)
                                )]
        plot_path = self.dwg.path(
                            d=' '.join(commands), stroke = self.rgb(*colour), 
                            stroke_linecap='round', stroke_width= '3', stroke_opacity = 0.5, 
                            fill = self.rgb(*colour), fill_rule='evenodd'
                            )

        self.dwg.add(plot_path)

    def doPlot(self,    plot_chrom_start, 
                        plot_chrom_end, 
                        panel = ((1,1),(1,1)), 
                        label = False, 
                        ratio_max = 0.6):
        
        # reuse max_y_scale for multiple plot on same axis
        self.calc_plot_region(              plot_chrom_start, 
                                            plot_chrom_end, 
                                            panel = panel)

        # plot scale
        self.plot_scale(plot_chrom_start, plot_chrom_end, panel)
        # plot ORFs
        self.plot_ORFs(plot_chrom_start, plot_chrom_end, panel)
        # plot large features
        self.plot_LargeFeatures(plot_chrom_start, plot_chrom_end, panel)


        max_y_scale = self.plot_values(     self.checker_info['depth_total'], 
                                            plot_chrom_start, 
                                            plot_chrom_end, 
                                            label, 
                                            panel = panel, 
                                            colour = ('55', '55', '55', '%'), 
                                            plot_axis = 'Left',
                                            y_axis_label = 'Aligned Reads',
                                            add_sample_label = True)

        self.plot_values(                   self.checker_info['depth_proper'], 
                                            plot_chrom_start, 
                                            plot_chrom_end, 
                                            label, 
                                            panel = panel, 
                                            colour = ('75', '75', '75', '%'), 
                                            plot_axis = False, 
                                            max_y_scale = max_y_scale,
                                            add_sample_label = False)

        self.plot_values(  [r if r != -1 else 0 for r in self.checker_info['ratios']], 
                              plot_chrom_start, 
                              plot_chrom_end, 
                              label, 
                              panel = panel,
                              colour = ('0', '100', '0', '%'), 
                              fill = False,
                              plot_axis = 'Right',
                              y_axis_label = ('Non-Proper Pair :','Proper Pair Reads'),
                              # set max to 1
                              max_y_scale = ratio_max,
                              add_sample_label = False)

        self.plot_values(     self.checker_info['smoothed_ratios'], 
                              plot_chrom_start, 
                              plot_chrom_end, 
                              label, 
                              offset = int(round(self.checker_info['mean_insert_size'] / 2)),
                              panel = panel,
                              colour = ('0', '80', '70', '%'), 
                              fill = False,
                              plot_axis = False,
                              # set max to 1
                              max_y_scale = ratio_max,
                              add_sample_label = False)

        # plot threshold
        self.plot_threshold(self.checker_info['threshold'], ratio_max)

        # high proportion of non-proper pairs
        self.plot_suspect_regions(       plot_chrom_start, 
                                         plot_chrom_end,
                                         self.checker_info['suspect_regions']['rearrangements'],
                                         panel = panel,
                                         colour = ('80', '10', '0', '%'))

        # orange in inkscape
        #[float(int('e6aa00'[s:s+2], base=16))/255 for s in (0,2,4)]
        # extension near high proportion of non-proper pairs
        self.plot_suspect_regions(       plot_chrom_start, 
                                         plot_chrom_end,
                                         self.checker_info['suspect_regions']['rearrangements_extended'],
                                         panel = panel,
                                         colour = ('90', '60', '00', '%'))


        self.dwg.save()


if __name__ == '__main__':
    main()