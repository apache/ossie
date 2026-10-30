<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->

# ost(1)

## NAME

`ost` - run Ossie SQL Test (`.ost`) scripts

## SYNOPSIS

```
ost [option...] [file...]
```

## DESCRIPTION

An `.ost` script is a sequence of commands, each followed by the
output it is expected to produce. `ost` runs each command, compares
its output with the expected output, and writes a *regenerated*
script: the same commands and comments, with each command's output
replaced by what the command actually produced.

Scripts are *idempotent*: if every command's output matches, the
regenerated script is identical to the original. If not, the
regenerated script is one that would pass if copied over the
original. (You must still check that the new output is correct.)

Matching is lenient. Row order, column widths and padding may differ
from the expected output, and the output still matches; when it
matches, the regenerated script keeps the expected output exactly as
it was written. When in doubt, `ost` reports a mismatch: a false
mismatch costs a regenerated line, but a false match would hide a
bug.

Each *file* argument is a script, a directory, or `-`. For a
directory, `ost` runs every file under it whose name ends in `.ost`,
skipping the output directory (see `--output-dir`). `-` reads a
script from standard input and writes the regenerated script to
standard output.

This document is also the specification of the format. An
implementation of `ost` in another language must accept the same
scripts and generate the same output.

## OPTIONS

`--help`
: Print a usage message and exit.

`--update`
: For each script that fails, overwrite the script with the
  regenerated script.

`--output-dir` *dir*
: Write each regenerated script to *dir* (default `target/ost`), at
  the script's path relative to the current directory. A script
  outside the current directory is written at its file name.

`--echo`
: Write the regenerated script to standard output as each command
  completes. Useful for finding the command that hangs.

`--timeout` *seconds*
: Interrupt any command that runs for longer than *seconds* (default
  60). The command's output is an error (see *Errors*).

For each script that fails, `ost` prints a unified diff between the
script and the regenerated script.

## EXIT STATUS

* 0 - every script passed.
* 1 - at least one script failed.
* 2 - a usage error, or at least one script is malformed (see
  *Lines and whitespace* and *Commands*), and was not run.

For a malformed script, `ost` prints one line per problem, of the
form *path*`:`*line*`:`*column*`: `*message*, for example

```
tests/basic.ost:12:31: trailing space
tests/basic.ost:38:1: unterminated command (open '(' at 38:6)
```

A script read from standard input has path `-`.

## FILE FORMAT

### Example

```
# An example .ost (Ossie SQL Test) file.

# Connect to the SCOTT schema.
connect("scott");

# A SQL query. Because there is no ORDER BY clause, the rows may be
# in any order. Column widths and padding may also differ.
select *
from emp
where job = 'CLERK';
+-------+--------+-------+------+------------+---------+------+--------+
| empno | ename  | job   | mgr  | hiredate   | sal     | comm | deptno |
+-------+--------+-------+------+------------+---------+------+--------+
|  7934 | MILLER | CLERK | 7782 | 1982-01-23 | 1300.00 |      |     10 |
|  7369 | SMITH  | CLERK | 7902 | 1980-12-17 |  800.00 |      |     20 |
|  7876 | ADAMS  | CLERK | 7788 | 1987-05-23 | 1100.00 |      |     20 |
|  7900 | JAMES  | CLERK | 7698 | 1981-12-03 |  950.00 |      |     30 |
+-------+--------+-------+------+------------+---------+------+--------+
(4 rows)

# A function call. A value that is not a set of rows is printed
# with a '> ' prefix, followed by its type.
plus(1, 2);
> 3 : int

# A command that fails prints the first line of its error.
select * from nonexistent;
> error: Catalog Error: Table with name nonexistent does not exist!

# End example.ost
```

### Lines and whitespace

A script is UTF-8 text with LF line endings. It must not contain a
tab, a carriage return, or a space at the end of a line; `ost`
reports each one, with its line and column, and does not run the
script. (A tab or trailing space is invisible, and is easily lost or
added by an editor.)

A script is a sequence of *chunks*:

* A *blank line*.
* A *comment*, a line that starts with `#`.
* A *command*, followed by its *expected output*, which may be
  empty.

Blank lines and comments are copied to the regenerated script
unchanged. A `#` line inside a command is part of the command, not a
comment.

### Commands

A command starts at the first line that is not a blank line, a
comment, or output. It ends at a `;` that is followed on its line
only by spaces, and that is outside all of the following:

| Construct            | Notes                                         |
| -------------------- | --------------------------------------------- |
| `(` ... `)`          | Nested to any depth.                          |
| `'` ... `'`          | Within, `''` is a quote.                      |
| `"` ... `"`          | Within, `""` is a quote.                      |
| `/*` ... `*/`        | Not nested.                                   |
| `--` ... end of line |                                               |
| `{\|` ... `\|}`      | A raw string; also `{id\|` ... `\|id}`.      |

So a `;` in the middle of a line does not end a command; nor does a
`;` that is followed by a comment, as in `select 1; -- one`. A `)`
that has no matching `(`, or a construct that is still open at the
end of the script, is an error, and `ost` does not run the script.

A command is a *function call* if it starts with the name of a
function (see *FUNCTIONS*), optionally followed by spaces and line
breaks, followed by `(`. Function names are case-sensitive. The
function's arguments run to the matching `)`, which must be followed
only by the terminating `;`.

Any other command is a *SQL command*. Its text, up to but not
including the terminating `;`, is executed by the database. If the
text contains several statements, the output is that of the last
statement.

### Arguments

The arguments of a function call are separated by commas. An
argument is one of:

* An integer, such as `3` or `-12`.
* A floating-point number, such as `1.5` or `-2.5e3`.
* `true` or `false`.
* A string, `"..."`, with the escapes of JSON (RFC 8259).
* A raw string (see *Raw strings*).
* A query: the text of a SQL command. A query must be the last
  argument, and runs to the closing `)` of the call, because a
  query may itself contain commas. Spaces and line breaks around it
  are removed.

Each function declares the kind of each of its parameters. An
argument of the wrong kind, or the wrong number of arguments, is an
error in the command's output (see *Errors*).

### Output

A command's expected output immediately follows the line on which
the command ends, and consists of the lines that start with `+`,
`|` or `>` (and the lines of any raw string they contain, and the
row count that ends a table). It is one of:

* Nothing. A command that produces nothing (such as `connect`, or a
  `create table` statement) has no output.
* A table, if the command returns rows.
* A value, if a function returns a value.
* An error, if the command fails.

If the lines after a command contain more than one table, value or
error, the expected output does not match, and the regenerated
script contains only the actual output. An output line that does
not follow a command or another output line (for example, one after
a blank line) is an error, and `ost` does not run the script.

A command that has no expected output is still run, and its output
is written to the regenerated script. To write a new test, write the
commands without output, run `ost --update`, and check the output.

### Tables

A table starts with a line that begins with `+`, and ends with a
line `(1 row)` or `(N rows)`, where *N* is the number of rows:

```
+-------+--------+
| empno | ename  |
+-------+--------+
|  7934 | MILLER |
|  7369 | SMITH  |
+-------+--------+
(2 rows)
```

When `ost` writes a table, each column is as wide as its widest cell
or header, with one space of padding at each side. A header is
written as the database returns it, and is left-aligned. A cell is
right-aligned if its column is in the integer, decimal, real or
double family (see *Types*), and left-aligned otherwise.
A table with no rows has its header, and two border lines with
nothing between them, followed by `(0 rows)`.

### Types

`ost` works over the common core of SQL types: those that DuckDB and
Postgres share. It formats each value itself, according to the rules
below; it never asks the database to convert a value to a string.
The rules depend only on the *family* of a column's type, not on the
exact type, because databases differ in which type of a family an
expression returns. (DuckDB's `sum` of an `INTEGER` column is a
`HUGEINT`; Postgres's is a `bigint`.)

| Family      | DuckDB types                                 | Postgres types                    |
| ----------- | -------------------------------------------- | --------------------------------- |
| boolean     | `BOOLEAN`                                    | `boolean`                         |
| integer     | `TINYINT` ... `HUGEINT`, `UTINYINT` ... `UHUGEINT` | `smallint`, `integer`, `bigint` |
| decimal     | `DECIMAL(p, s)`                              | `numeric(p, s)`                   |
| real        | `REAL`                                       | `real`                            |
| double      | `DOUBLE`                                     | `double precision`                |
| string      | `VARCHAR`                                    | `varchar`, `text`                 |
| date        | `DATE`                                       | `date`                            |
| time        | `TIME`                                       | `time`                            |
| timestamp   | `TIMESTAMP`                                  | `timestamp`                       |
| timestamptz | `TIMESTAMPTZ`                                | `timestamptz`                     |
| interval    | `INTERVAL`                                   | `interval`                        |

A value of any other type (such as a list, struct, map, `BLOB`,
`UUID` or `JSON`) cannot be printed, and the command's output is an
error, `> error: ost: unsupported type STRUCT(a INTEGER)`. Tests of
features outside the common core do not belong in `.ost` scripts.

A cell is written as follows:

* `NULL` is an empty cell.
* A boolean is `true` or `false`.
* An integer is written in decimal, with `-` if it is negative.
* A decimal of scale *s* is written with exactly *s* digits after the
  point, and at least one digit before it: `800.00`, `0.50`,
  `-0.50`. If *s* is 0 there is no point.
* A double is written as the shortest decimal that converts back to
  the same double, in the format of Python's `repr`: `0.1`,
  `0.30000000000000004`, `1e+16`, `1e-07`, `-0.0`, `inf`, `-inf`,
  `nan`.
* A real (32-bit) is written the same way, but as the shortest
  decimal that converts back to the same real. So `0.1::REAL` is
  `0.1`, not `0.10000000149011612`.
* A string is written verbatim, unless it is empty, starts or ends
  with a space, starts with `"`, contains `|`, or contains a control
  character or a whitespace character other than a space. Then it is
  written in quotes, with the escapes of JSON: `""`, `" a"`,
  `"a|b"`, `"a\nb"`. Control characters, U+2028 (line separator)
  and U+2029 (paragraph separator) are written as `\uXXXX`.
* A date is written `YYYY-MM-DD`, as in `2024-03-10`.
* A time is written `HH:MM:SS`, followed by a fraction of a second
  only if it is not zero, without trailing zeros: `12:34:56`,
  `12:34:56.5`.
* A timestamp is a date, a space, and a time:
  `2024-03-10 12:34:56.5`.
* A timestamptz is written in UTC, as a timestamp followed by `+00`:
  `2024-03-10 12:34:56+00`.
* An interval is written as an ISO 8601 duration: `P`, then the
  number of days followed by `D` if it is not zero, then (if any are
  not zero) `T` and the hours, minutes and seconds, each followed by
  `H`, `M` or `S` if it is not zero. Seconds may have a fraction, as
  for a time. A day is 24 hours, so `INTERVAL '25 hours'` is
  `P1DT1H`. A negative interval is written as `-` followed by its
  absolute value, as in `-PT1H`. A zero interval is `PT0S`. So
  `INTERVAL '3 days 04:05:06.5'` is `P3DT4H5M6.5S`.

The following values are outside the common core, and the result of
printing them is not specified: dates and timestamps before 1 AD or
after 9999 AD, the infinite dates and timestamps (`infinity` and
`-infinity`), and intervals that have a month or year component.

Numbers never have a thousands separator, and their decimal point is
always `.`.

The output of a script depends only on the script and the version
of the database; never on the time zone, locale or environment of
the machine that runs it. A script must therefore not use functions
whose result changes from run to run, such as `now()`,
`current_date` or `random()`; nor operations whose result depends on
the database's session time zone, such as casting a timestamptz to a
date or timestamp, or a timestamptz literal without an offset.

### Values

A value is a line that starts with `> `, followed by the value, ` : `,
and its type:

```
> 3 : int
> 2.5 : double
> false : boolean
> "abc" : string
```

An integer, floating-point number or boolean is written as in a
table cell. A string is written in quotes, with the escapes of JSON,
and with non-ASCII characters verbatim except for control characters,
U+2028 and U+2029, which are written as `\uXXXX`.

A function that returns nothing (`None`) has no output.

### Raw strings

A string that contains a line break is written as a *raw string*,
whose content is verbatim, without escapes. The content starts
immediately after the opening fence `{|`, ends immediately before
the closing fence `|}`, and continues over as many lines as needed.
Lines after the first are not prefixed with `> `, and start at
column 0:

```
> {|first line
second line|} : string
```

If the content's second line starts with a space, `ost` instead
starts the content on the line after the opening fence, so that the
content's lines line up, and the fence tag starts with an underscore.
A tag that starts with an underscore means that the line break after
the opening fence is not content:

```
> {_|
select deptno,
  avg(sal) as avg_sal
from scott.emp
|_} : string
```

(Here, the content ends with a line break, so the closing fence is
alone on the last line.)

If the content contains `|}` (or `|_}`), the fences carry a longer
tag, `{a|` ... `|a}` (or `{_a|` ... `|_a}`). A tag consists of the
letters `a` to `z` and underscores. `ost` uses the shortest tag in
the sequence `a`, `b`, ..., `z`, `aa`, `ab`, ... whose closing fence
does not occur in the content.

`ost` writes a string as a raw string only if it is the value of a
function, contains a line break, and has no space before a line
break, and if all of its characters are printable ASCII or line
breaks. Otherwise it writes the string in quotes. A raw string in
the expected output matches the quoted string with the same content.

Raw strings may also be function arguments. The `ost` function (see
*FUNCTIONS*) takes a whole script as a raw string, and that script's
commands end in `;`, so the scanner treats a raw string as a single
token.

### Errors

If a command fails, its output is a line `> error: ` followed by the
first line of the error message:

```
> error: Catalog Error: Table with name nonexistent does not exist!
```

A command fails if the database raises an error, if a function
raises an error (including for arguments of the wrong number or
kind), or if the command exceeds the `--timeout`. `ost` continues
with the next command.

## MATCHING

Unless `matchStrict` is set (see `set`), the actual output matches
the expected output if they are equivalent:

* Two tables are equivalent if their headers are the same, ignoring
  case, and their rows are the same. Cells are compared after
  removing their padding, and after removing quotes and escapes from
  quoted cells; the comparison is exact, so `x y` does not match
  `x  y`, and `800.00` does not match `800.0`. Border lines may
  differ in width. The row count in the last line must be the number
  of rows.
* Rows are compared as a multiset - the same rows, in any order -
  unless the command is ordered (see `query`).
* Two values are equivalent if their types are the same and their
  values are the same. Spaces around ` : ` are ignored. A raw string
  is equivalent to the quoted string with the same content.
* Two errors are equivalent if their messages are the same.
* Outputs of different kinds (for example a table and an error) are
  not equivalent. If `ost` cannot parse the expected output, it is
  not equivalent to anything.

If the actual output matches, the regenerated script contains the
expected output, exactly as it was written. If not, the regenerated
script contains the actual output, written as described in *FILE
FORMAT*. For an unordered table, the rows that match an expected row
are written first, in the order of the expected output, followed by
the other rows in the order the database returned them; so the diff
shows only the rows that changed.

## FUNCTIONS

`connect(name)`
: Closes the current database, and opens a new in-memory DuckDB
  database, loaded with the fixture *name*. Fixtures are:
  * `"empty"` - no tables.
  * `"scott"` - the SCOTT schema: tables `dept`, `emp`, `bonus` and
    `salgrade` in schema `scott`, which is on the search path, so
    both `emp` and `scott.emp` work.

  Each script starts as if it began with `connect("empty")`.

`query(sql)`
: Executes *sql*; equivalent to writing *sql* as a SQL command. The
  rows are ordered (compared in order) if and only if *sql* contains
  `order by`, in any case, with any spaces or line breaks between
  `order` and `by`. The rule is a search of the text, not a parse,
  so `order by` in a window (`over (order by sal)`), in a subquery,
  or even in a comment or string literal, makes the rows ordered.
  When the rule gives the wrong answer, use `queryOrdered` or
  `queryUnordered`. A SQL command follows the same rule.

`queryOrdered(sql)`
: Executes *sql*; its rows are ordered.

`queryUnordered(sql)`
: Executes *sql*; its rows are unordered, even if *sql* contains
  `order by`.

`plus(a, b)`
: Returns the sum of integers *a* and *b*. (An example of a function
  that returns a value.)

`set(name, value)`
: Sets the property *name* to *value*, for the rest of the script.
  The only property is `"matchStrict"` (default `false`): if `true`,
  the actual output matches the expected output only if they are
  identical, character for character. Use it to test the layout of
  output.

`ost(script)`
: Runs *script*, a string, as a separate script, with its own
  database (as if it began with `connect("empty")`) and its own
  properties, and returns the regenerated script. If *script* is
  malformed, the result is an error describing the first problem,
  with path `<string>` and with lines and columns relative to
  *script*, such as
  `> error: <string>:2:1: unterminated command (open '(' at 1:6)`.
  If the result is the same as
  *script*, then *script* passed.

  `ost` is how a script tests `ost`. A script cannot test the output
  that `ost` generates directly, because lenient matching keeps the
  expected output as written; but the result of `ost` is a string,
  and strings must match exactly. For example, the following shows
  that `x  y` does not match `x y`, because the regenerated script
  differs from the original:

  ```
  ost({|select 'x y' as c;
  +------+
  | c    |
  +------+
  | x  y |
  +------+
  (1 row)
  |});
  > {|select 'x y' as c;
  +-----+
  | c   |
  +-----+
  | x y |
  +-----+
  (1 row)
  |} : string
  ```

## PYTHON API

The Python implementation can run a script held in a string, which
is useful in unit tests and in programs that generate scripts:

```python
import ossie_ost

result = ossie_ost.run(text, path="<string>")
result.passed   # True if result.output == text
result.output   # the regenerated script
```

`run(text, path="<string>", *, timeout=60, functions=None)` runs
*text* as a script, exactly as the command line would, and returns
a `Result` with fields `path`, `input`, `output` and `passed`. It
writes no files. *path* is used only in messages; it need not
exist. *functions* adds functions to the built-in ones (see
`README.md` for how to define one).

If *text* is malformed, `run` raises `ossie_ost.ScriptError`, whose
`problems` field is a list of `Problem(line, column, message)`, and
whose string form is the lines that the command line would print,
each starting with *path*.

## FILES

`target/ost/`
: Default directory for regenerated scripts.

## SEE ALSO

* [apache/ossie#501](https://github.com/apache/ossie/issues/501),
  the proposal for this format.
* [Sql Logic Test](https://sqlite.org/sqllogictest/doc/trunk/about.wiki)
  (`.slt`), [Quidem](https://github.com/hydromatic/quidem) (`.iq`),
  and Morel's
  [`.smli`](https://github.com/hydromatic/morel/blob/main/docs/script-format.md),
  earlier idempotent test formats.
