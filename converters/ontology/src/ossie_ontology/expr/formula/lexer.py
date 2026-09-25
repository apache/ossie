# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import ply.lex as lex

class FormulaLexer:

    def __init__(self):
        self.lexer = lex.lex(module=self)
        self.lexer.begin('INITIAL')

    reserved = {
        'AND': 'AND',
        'AVG': 'AVG',
        'BY' : 'BY',
        'COUNT': 'COUNT',
        'EXISTS': 'EXISTS',
        'FALSE': 'FALSE',
        'GROUP': 'GROUP',
        'MAX' : 'MAX',
        'MIN' : 'MIN',
        'NOT': 'NOT',
        'OR': 'OR',
        'SUM': 'SUM',
        'TRUE': 'TRUE',
        'WHERE': 'WHERE'
    }

    tokens = [
        'ID',
        'COMMA',
        'DIVIDE',
        'DOT',
        'EQUALS',
        'GREATER',
        'GREATEREQ',
        'LBRACKET',
        'LESS',
        'LESSEQ',
        'LPAREN',
        'MINUS',
        'INTEGER',
        'FLOAT',
        'NOTEQUALS',
        'PLUS',
        'RBRACKET',
        'RPAREN',
        'STRING_LITERAL',
        'TIMES'
    ] + list(reserved.values())

    # Ignored characters
    t_ignore = ' \t'

    # Token regular expressions
    t_COMMA = r','
    t_DIVIDE = r'/'
    t_DOT = r'\.'
    t_EQUALS = r'=='
    t_GREATER = r'>'
    t_LBRACKET = r'\['
    t_LESS = r'<'
    t_LPAREN = r'\('
    t_MINUS = r'\-'
    t_PLUS = r'\+'
    t_RBRACKET = r'\]'
    t_RPAREN = r'\)'
    t_TIMES = r'\*'

    def t_GREATEREQ(self, t):
        r'>='
        t.type = 'GREATEREQ'
        return t

    def t_LESSEQ(self, t):
        r'<='
        t.type = 'LESSEQ'
        return t

    def t_NEWLINE(self, t):
        r'\n+'
        t.lexer.lineno += t.value.count('\n')

    def t_NOTEQUALS(self, t):
        r'!='
        t.type = 'NOTEQUALS'
        return t

    def t_INTEGER(self, t):
        r'\d+(?!\.\d)'
        t.value = int(t.value)
        return t

    def t_FLOAT(self, t):
        r'\d+\.\d+'
        t.value = float(t.value)
        return t

    def t_STRING_LITERAL(self, t):
        r'\'[^\']*\''
        t.value = t.value[1:-1]
        return t

    def t_ID(self, t):
        r'[a-zA-Z_0-9]+'
        t.type = self.reserved.get(t.value, 'ID')
        return t

    # every string that doesn't match one of the previous tokens is considered an error
    def t_error(self, t):
        r'.'
        raise SyntaxError(f"Illegal character {t.value!r} at position {t.lexpos}")
