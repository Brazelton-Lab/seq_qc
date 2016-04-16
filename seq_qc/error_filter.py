#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quality filter paired-end sequencing reads using a maximum errors threshold.

For single-end and interleaved reads:
    error_filter [options] [-o output] input
 
For split paired-end reads:
    error_filter [option] -o out.forward -v out.reverse -s out.singles "\"
        in.forward in.reverse

The number of errors in a sequence is determined by calculating the exact 
sum of bernoulli random variables. The Poisson binomial filtering algorithm 
used here was developed by Fernando Puente-Sanchez and others. If you use this 
script for processing your sequencing data, please cite their paper:

    Puente-Sánchez, F., Aguirre, J., & Parro, V. (2015). A novel conceptual 
    approach to read-filtering in high-throughput amplicon sequencing studies.
    Nucleic acids research, gkv1113.

Input files must be in FASTQ format. Output can be in either FASTQ or FASTA 
format. Compression using gzip and bzip2 algorithms is automatically detected 
for input files. To compress output, add the appropriate file extension to the 
file names (.gz, .bz2). For single-end or interleaved reads, use '-' to 
indicate that input should be taken from standard input (stdin). Similarly, 
leaving out the -o argument will cause output to be sent to standard output 
(stdout).
"""

from __future__ import print_function
from __future__ import division

__author__ = "Christopher Thornton"
__date__ = "2016-04-07"
__version__ = "0.1.0"

import argparse
import math
import seq_io
import sys
import bernoulli
from numpy.random import random
from numpy import percentile
from numpy import isnan

class LengthMismatchError(Exception):
    def __init__(self, fheader = None, ffilename = None, qfilename = None):
        self.fheader = fheader
        self.ffilename = ffilename
        self.qfilename = qfilename
        self.__str__()
    def __str__(self):
        if None not in (self.fheader, self.ffilename, self.qfilename):
            return 'Error reading sequence {} in files {} and {}. Sequence \
                length and quality length do not match'.format(
                repr(self.fheader), repr(self.ffilename), repr(self.qfilename))
        elif not self.qfilename:
            return 'Error reading sequence {} in file {}. Sequence length and \
                "quality length do not match'.format(repr(self.fheader), 
                repr(self.ffilename))
        else:
            return 'Sequence and qualities are of different lengths.'

def get_list(argument):
    try:
        argument = [abs(int(i.lstrip())) for i in argument.split(",")]
    except ValueError:
        print("error: input to -c/--crop and -d/--headcrop must be in the "
            "form \"INT, INT\"")
        sys.exit(1)
    return argument

def crop_string(sequence, trunc=None, headcrop=None):
    seqlen = len(sequence)
    if not headcrop:
        start = 0
    elif headcrop > seqlen:
        start = 0
    else:
        start = headcrop

    if not trunc:
        end = seqlen
    elif trunc and trunc > seqlen:
        end = seqlen
    else:
        end = trunc
    return sequence[start: end]

def calculate_errors_poisson(sequence, quals, alpha):
    """
    Calculate the errors in a sequence approximating the sum of bernouilli 
    random variables to a poisson distribution. Expects quals to be a list of 
    integers containing Phred quality scores.
    """
    ###Check parameters:
    sequence = str(sequence)
    quals = map(int, list(quals))
    alpha = float(alpha)

    if len(sequence) != len(quals):
        raise LengthMismatchError()
    if alpha <= 0 or alpha > 1:
        raise ValueError('Alpha must be between 0 (not included) and 1.')
    ###

    Lambda = 0
    Ns = 0
    for base, qscore in zip(sequence, quals):
        if qscore < 0:
            raise ValueError('Qualities must have positive values.')
        if base == 'N':
            Ns += 1
        else:
            Lambda += 10**(qscore / -10.0)

    accumulated_probs = [0]
    expected_errors = 0

    while 1:
        try:
            probability = ((math.exp(-Lambda) * (Lambda ** expected_errors)) / 
                (math.factorial(expected_errors)))
        except OverflowError:
            probability = 1
        accumulated_probs.append(accumulated_probs[-1] + probability)

        if accumulated_probs[-1] > (1 - alpha):
            break
        else:
            expected_errors += 1

    expected_errors = interpolate(expected_errors - 1, accumulated_probs[-2], expected_errors, accumulated_probs[-1], alpha)

    return expected_errors, Ns

def interpolate(errors1, prob1, errors2, prob2, alpha):
    """
    Perform a linear interpolation in the errors distribution to return the 
    number of errors that has an accumulated probability of 1 - alpha.
    """

    result = (errors1 + ((errors2 - errors1) * ((1 - alpha) - prob1) / 
        (prob2 - prob1)))
    if result < 0:
        # Happens only for very-short high qual sequences in which the 
        # probability of having 0 errors is higher than 1 - alpha.
        result = 0
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('f_file', metavar='in1.fastq',
        help="input reads file in fastq format [required]. Can be a file "
        "containing either forward of interleaved reads")
    input_arg = parser.add_mutually_exclusive_group(required=True)
    input_arg.add_argument('--interleaved',
        action='store_true',
        help="input is interleaved paired-end reads")
    input_arg.add_argument('r_file', metavar='in2.fastq', nargs='?',
        help="input reverse reads file in fastq format")
    parser.add_argument('-o', '--out', metavar='FILE', dest='out_f',
        type=seq_io.open_output, default=sys.stdout,
        help="output file for filtered reads [required]")
    parser.add_argument('-v', '--out-reverse', metavar='FILE', dest='out_r',
        type=seq_io.open_output,
        help="output file for filtered reverse reads")
    parser.add_argument('-s', '--singles', metavar='FILE', dest='out_s',
        type=seq_io.open_output,
        help="output file for filtered orphan reads")
    parser.add_argument('-f', '--out-format', metavar='FORMAT', 
        dest='out_format', default='fastq',
        choices=['fasta', 'fastq'],
        help="output files format [default: fastq]. Options are fastq or fasta")
    parser.add_argument('-l', '--log',
        type=seq_io.open_output,
        help="output log file")
    parser.add_argument('-q', '--qual-type', metavar='TYPE', dest='qual_type',
        type=int, default=33,
        choices=[33, 64],
        help="ASCII base quality score encoding [default: 33]. Options are "
            "33 (for phred33) or 64 (for phred64)")
    parser.add_argument('-a', '--alpha', 
        type=float, default=0.005,
        help="probability of underestimating the actual number of errors in a "
            "sequence [default: 0.005]")
    parser.add_argument('-c', '--crop', metavar='"LEN, LEN"',
        type=get_list, default="0, 0",
        help="trim read to size specified by removing bases from the end of "
            "the read")
    parser.add_argument('-d', '--headcrop', metavar='"LEN, LEN"',
        type=get_list, default="0, 0",
        help="trim of bases from the start of the read")
    parser.add_argument('--ambig',
        action='store_true',
        help="Remove sequences with ambiguous bases. Default is to treat "
            "ambiguous bases as errors")
    parser.add_argument('-e', '--error-calc',
        choices = ('poisson_binomial', 'poisson'),
        help="")
    filter_mode = parser.add_mutually_exclusive_group()
    filter_mode.add_argument('-m', '--max-error', dest='maxerror',
        type=float,
        help="maximum number of errors allowed in a sequence [default: 1]")
    filter_mode.add_argument('-u', '--uncert', 
        type=float, default=0.01,
        help="maximum divergence of the observed sequence from the template "
            "due to sequencing error [default: 0.01]")

    args = parser.parse_args()
    all_args = sys.argv[1:]

    f_file = sys.stdin if args.f_file == '-' else args.f_file
    iterator = seq_io.get_iterator(f_file, args.r_file, args.interleaved)

    if args.r_file and not args.out_r:
        parser.error("argument -v/--out-reverse is required when a reverse "
            "file is provided")

    if args.out_format == 'fasta':
        writer = seq_io.fasta_writer
    else:
        writer = seq_io.fastq_writer

    if args.interleaved:
        args.out_r = args.out_f
    
    error_func = {'poisson_binomial': bernoulli.calculate_errors_PB,
                  'poisson': calculate_errors_poisson}

    pairs_passed = filtered_pairs = fsingles = rsingles = 0
    for i, (forward, reverse) in enumerate(iterator):
        fheader, fsequence = ("{} {}".format(forward['identifier'], 
            forward['description']), crop_string(forward['sequence'], 
            args.crop[0], args.headcrop[0]))
        fquals = [ord(j) - args.qual_type for j in crop_string(
            forward['quality'], args.crop[0], args.headcrop[0])]
        flen = len(fsequence)
        fee, fNs = error_func[args.error_calc](fsequence, fquals, args.alpha)

        rheader, rsequence = ("{} {}".format(reverse['identifier'], 
            reverse['description']), crop_string(reverse['sequence'], 
            args.crop[1], args.headcrop[1]))
        rlen = len(rsequence)
        rquals = [ord(j) - args.qual_type for j in crop_string(
            reverse['quality'], args.crop[1], args.headcrop[1])]
        ree, rNs = error_func[args.error_calc](rsequence, rquals, args.alpha)

        if args.maxerror:
            fthreshold = rthreshold = args.maxerror
        else:
            fthreshold = flen * args.uncert
            rthreshold = rlen * args.uncert

        # both good
        if fee <= fthreshold and ree <= rthreshold:
            pairs_passed += 1
            writer(args.out_f, forward)
            writer(args.out_r, reverse)
        # forward orphaned, reverse filtered
        elif fee <= fthreshold and ree > rthreshold:
            fsingles += 1
            writer(args.out_s, forward)
            seq_io.logger(args.log, "{}\terrors={!s}".format(rheader, ree))
        # reverse orphaned, forward filtered
        elif fee > fthreshold and ree <=rthreshold:
            rsingles += 1
            writer(args.out_s, reverse)
            seq_io.logger(args.log, "{}\terrors={!s}".format(fheader, fee))
        # both discarded
        else:
            filtered_pairs += 1
            seq_io.logger(args.log, "{}\terrors={!s}\n{}\terrors={!s}".format(
                fheader, fee, rheader, ree))

    i += 1
    total = i * 2
    passed = pairs_passed * 2 + fsingles + rsingles
    print("\nRecords processed:\t{!s} ({!s} pairs)\nPassed filtering:\t"
        "{!s} ({:.2%})\n  Paired reads kept:\t{!s} ({:.2%})\n  Forward "
        "only kept:\t{!s} ({:.2%})\n  Reverse only kept:\t{!s} ({:.2%})"
        "\nRead pairs discarded:\t{!s} ({:.2%})\n".format(total, i,
        passed, passed / total, pairs_passed, pairs_passed / i,
        fsingles, fsingles / total, rsingles, rsingles / total,
        filtered_pairs, filtered_pairs / i), file=sys.stderr)

if __name__ == "__main__":
    main()
    sys.exit(0)