/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

grammar OssieExpression;

options { caseInsensitive = true; }

// Ossie expression language (OSSIE_SQL_2026), see core-spec/expression_language.md.
//
// Only expressions are covered -- no SELECT/FROM/WHERE/GROUP BY, no subqueries. The grammar is
// deliberately lenient about function names and arities: every function is parsed as
// `name(args)`, and which functions exist (SUM, DATE_TRUNC, COALESCE, ...) is a matter for
// semantic checks, not syntax. Only constructs with their own syntax get their own rule: CASE,
// CAST, EXTRACT, POSITION, COUNT(*), DISTINCT, FILTER, WITHIN GROUP, OVER, typed literals.
//
// Operands of comparisons and predicates are arithmetic expressions: `a = b = c` needs parentheses.
//
// Deviations from the precedence list in the spec, which puts NOT above * and /: NOT sits between
// the predicates and AND, as in ANSI SQL -- otherwise `NOT a = b` would mean `(NOT a) = b`.
// `||` is parsed at the level of + and -.

expression
 : expr EOF
;

// Boolean level: predicates over value expressions, combined with NOT, AND and OR. The operands of
// a predicate are value expressions, so the AND of BETWEEN cannot be taken for the logical one.
expr
 : valueExpr op=(EQ | NEQ | LT | LTE | GT | GTE) valueExpr                       # comparisonExpr
 | valueExpr NOT? BETWEEN valueExpr AND valueExpr                                # betweenExpr
 | valueExpr NOT? IN LPAREN valueExpr (COMMA valueExpr)* RPAREN                  # inListExpr
 | valueExpr NOT? op=(LIKE | ILIKE) valueExpr                                    # likeExpr
 | valueExpr IS NOT? NULL                                                        # isNullExpr
 | valueExpr IS NOT? DISTINCT FROM valueExpr                                     # isDistinctFromExpr
 | valueExpr                                                                     # valueOnlyExpr
 | NOT expr                                                                      # notExpr
 | expr AND expr                                                                 # andExpr
 | expr OR expr                                                                  # orExpr
;

// Arithmetic and concatenation, tightest binding first.
valueExpr
 : primary                                                                       # primaryValue
 | op=(PLUS | MINUS) valueExpr                                                   # unaryValue
 | valueExpr op=(STAR | SLASH | PERCENT) valueExpr                               # multiplicativeValue
 | valueExpr op=(PLUS | MINUS | CONCAT) valueExpr                                # additiveValue
;

primary
 : LPAREN expr RPAREN                                                            # parenthesized
 | literal                                                                       # literalPrimary
 | caseExpr                                                                      # casePrimary
 | castExpr                                                                      # castPrimary
 | extractExpr                                                                   # extractPrimary
 | positionExpr                                                                  # positionPrimary
 | currentDatetime                                                               # currentDatetimePrimary
 | functionCall                                                                  # functionPrimary
 | qualifiedName                                                                 # referencePrimary
;

// Field | Field '.' Field, and deeper: dataset.field, schema.dataset.field.
qualifiedName
 : identifier (DOT identifier)*
;

// A regular identifier is case-insensitive, a quoted one is exact ("" is a quote inside).
identifier
 : IDENTIFIER
 | QUOTED_IDENTIFIER
 | nonReservedWord
;

// Words with a meaning only in one place, so they stay usable as names elsewhere: a column called
// `date`, `first`, `range`. Reserved are only the words that would make an expression ambiguous.
nonReservedWord
 : DATE | TIME | TIMESTAMP | TIMESTAMP_NTZ
 | FIRST | LAST | NULLS | ROW | ROWS | RANGE | UNBOUNDED | PRECEDING | FOLLOWING | CURRENT
 | FILTER | OVER | WITHIN | GROUP | PARTITION | ASC | DESC | POSITION
;

literal
 : NUMBER                                                                        # numberLiteral
 | STRING                                                                        # stringLiteral
 | (TRUE | FALSE)                                                                # booleanLiteral
 | NULL                                                                          # nullLiteral
 | typeName=(DATE | TIME | TIMESTAMP | TIMESTAMP_NTZ) STRING                     # typedLiteral
;

// CURRENT_DATE and CURRENT_DATE() are both valid.
currentDatetime
 : (CURRENT_DATE | CURRENT_TIME | CURRENT_TIMESTAMP) (LPAREN RPAREN)?
;

functionCall
 : name=identifier LPAREN (STAR | DISTINCT? (expr (COMMA expr)*)?) RPAREN
   withinGroup? filterClause? overClause?
;

// PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY expr)
withinGroup
 : WITHIN GROUP LPAREN ORDER BY sortItem (COMMA sortItem)* RPAREN
;

// <aggregate>(...) FILTER (WHERE <predicate>)
filterClause
 : FILTER LPAREN WHERE expr RPAREN
;

overClause
 : OVER LPAREN
   (PARTITION BY expr (COMMA expr)*)?
   (ORDER BY sortItem (COMMA sortItem)*)?
   frameClause?
   RPAREN
;

sortItem
 : expr direction=(ASC | DESC)? (NULLS nulls=(FIRST | LAST))?
;

frameClause
 : unit=(ROWS | RANGE) (BETWEEN frameBound AND frameBound | frameBound)
;

frameBound
 : UNBOUNDED (PRECEDING | FOLLOWING)
 | CURRENT ROW
 | expr (PRECEDING | FOLLOWING)
;

// Searched and simple CASE.
caseExpr
 : CASE operand=expr? whenClause+ (ELSE elseResult=expr)? END
;

whenClause
 : WHEN condition=expr THEN result=expr
;

castExpr
 : (CAST | TRY_CAST) LPAREN expr AS dataType RPAREN
;

// VARCHAR, INT, DECIMAL(10, 2), TIMESTAMP_NTZ, ...: the target types are plain words, which one is
// supported is not a syntax question.
dataType
 : identifier (LPAREN NUMBER (COMMA NUMBER)? RPAREN)?
;

// EXTRACT(YEAR FROM date_expr)
extractExpr
 : EXTRACT LPAREN field=identifier FROM expr RPAREN
;

// POSITION(substr IN str)
positionExpr
 : POSITION LPAREN expr IN expr RPAREN
;

// Keywords. Matching is case-insensitive (options above).
AND            : 'AND' ;
AS             : 'AS' ;
ASC            : 'ASC' ;
BETWEEN        : 'BETWEEN' ;
BY             : 'BY' ;
CASE           : 'CASE' ;
CAST           : 'CAST' ;
CURRENT        : 'CURRENT' ;
CURRENT_DATE   : 'CURRENT_DATE' ;
CURRENT_TIME   : 'CURRENT_TIME' ;
CURRENT_TIMESTAMP : 'CURRENT_TIMESTAMP' ;
DATE           : 'DATE' ;
DESC           : 'DESC' ;
DISTINCT       : 'DISTINCT' ;
ELSE           : 'ELSE' ;
END            : 'END' ;
EXTRACT        : 'EXTRACT' ;
FALSE          : 'FALSE' ;
FILTER         : 'FILTER' ;
FIRST          : 'FIRST' ;
FOLLOWING      : 'FOLLOWING' ;
FROM           : 'FROM' ;
GROUP          : 'GROUP' ;
ILIKE          : 'ILIKE' ;
IN             : 'IN' ;
IS             : 'IS' ;
LAST           : 'LAST' ;
LIKE           : 'LIKE' ;
NOT            : 'NOT' ;
NULL           : 'NULL' ;
NULLS          : 'NULLS' ;
OR             : 'OR' ;
ORDER          : 'ORDER' ;
OVER           : 'OVER' ;
PARTITION      : 'PARTITION' ;
POSITION       : 'POSITION' ;
PRECEDING      : 'PRECEDING' ;
RANGE          : 'RANGE' ;
ROW            : 'ROW' ;
ROWS           : 'ROWS' ;
THEN           : 'THEN' ;
TIME           : 'TIME' ;
TIMESTAMP      : 'TIMESTAMP' ;
TIMESTAMP_NTZ  : 'TIMESTAMP_NTZ' ;
TRUE           : 'TRUE' ;
TRY_CAST       : 'TRY_CAST' ;
UNBOUNDED      : 'UNBOUNDED' ;
WHEN           : 'WHEN' ;
WHERE          : 'WHERE' ;
WITHIN         : 'WITHIN' ;

EQ      : '=' ;
NEQ     : '<>' | '!=' ;
LTE     : '<=' ;
GTE     : '>=' ;
LT      : '<' ;
GT      : '>' ;
PLUS    : '+' ;
MINUS   : '-' ;
STAR    : '*' ;
SLASH   : '/' ;
PERCENT : '%' ;
CONCAT  : '||' ;
LPAREN  : '(' ;
RPAREN  : ')' ;
COMMA   : ',' ;
DOT     : '.' ;

// 42, 3.14, .5, 1e10, 1.5E-3. The sign is an operator.
NUMBER : DIGIT+ ('.' DIGIT*)? EXPONENT?
       | '.' DIGIT+ EXPONENT?
       ;

// 'it''s'
STRING : '\'' ( ~'\'' | '\'\'' )* '\'' ;

IDENTIFIER        : [A-Z_] [A-Z_0-9$]* ;
QUOTED_IDENTIFIER : '"' ( ~'"' | '""' )* '"' ;

LINE_COMMENT  : '--' ~[\r\n]* -> channel(HIDDEN) ;
BLOCK_COMMENT : '/*' .*? '*/' -> channel(HIDDEN) ;
WS            : [ \t\r\n]+ -> channel(HIDDEN) ;

fragment DIGIT    : [0-9] ;
fragment EXPONENT : 'E' [+-]? DIGIT+ ;
