#!/usr/bin/env python
# -*- coding: utf-8 -*-

# This file is part of FTE.
#
# FTE is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# FTE is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with FTE.  If not, see <http://www.gnu.org/licenses/>.

try:
    import gmpy2 as gmpy
except ImportError:
    import gmpy

import math

import fte.cDFA


class LanguageIsEmptySetException(Exception):

    """Raised when the input language results in a set that is not rankable.
    """
    pass


class IntegerOutOfRangeException(Exception):
    pass


class DFA(object):

    def __init__(self, dfa, max_len):
        self._dfa = dfa
        self.max_len = max_len

        self._words_in_language = self._dfa.getNumWordsInLanguage(
            0, self.max_len)
        self._words_in_slice = self._dfa.getNumWordsInLanguage(
            self.max_len, self.max_len)

        self._offset = self._words_in_language - self._words_in_slice
        self._offset = gmpy.mpz(self._offset)

        if self._words_in_slice == 0:
            raise LanguageIsEmptySetException()

        self._capacity = int(math.floor(math.log(self._words_in_slice, 2)))

    def rank(self, X):
        """Given a string ``X`` return ``c``, where ``c`` is the lexicographical
        rank of ``X`` in the language of all strings of length ``max_len``
        generated by ``regex``.
        """

        c = gmpy.mpz(0)
        self._dfa.rank(X, c)
        c -= self._offset
        return c

    def unrank(self, c):
        """The inverse of ``rank``.
        """

        if c > (self._words_in_slice - 1):
            raise IntegerOutOfRangeException()

        c = gmpy.mpz(c)
        c += self._offset
        X = self._dfa.unrank(c)

        return str(X)

    def getCapacity(self):
        """Returns the size, in bits, of the language of our input ``regex``.
        Calculated as the floor of log (base 2) of the cardinality of the set of
        strings up to length ``max_len`` in the language generated by the input
        ``regex``.
        """

        return self._capacity


def from_regex(regex, max_len):
    """Given an input ``regex`` and integer ``max_len`` constructs an
    ``fte.dfa.DFA()`` object that can be used to ``(un)rank`` into the language
    generated by ``regex`` with strings of length ``max_len``.
    """

    fst_path = fte.conf.getValue('general.bin_dir')

    regex = str(regex)
    max_len = int(max_len)

    att_fst = fte.cDFA.attFstFromRegex(str(regex))
    att_fst = fte.cDFA.attFstMinimize(str(fst_path), str(att_fst))
    att_fst = att_fst.strip()

    dfa = fte.cDFA.DFA(att_fst, max_len)
    retval = DFA(dfa, max_len)

    return retval
