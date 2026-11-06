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
regenerated script is identical to the original; if not, the
regenerated script would pass if copied over the original (unless a
command had a fatal error; see *Errors*). You must still check that
the new output is correct.

Matching is lenient: row order, column widths and padding may
differ, and when the output matches, the regenerated script keeps
the expected output as written. When in doubt, `ost` reports a
mismatch; a false mismatch costs a regenerated line, but a false
match would hide a bug.

Each *file* argument is a script, a directory, or `-`. For a
directory, `ost` runs every file under it whose name ends in `.ost`,
skipping the output directory. `-` reads a script from standard
input, writes the regenerated script to standard output, and
reports fatal errors on standard error.

This document is also the specification of the format. An
implementation of `ost` in another language must accept the same
scripts and generate the same output.

## OPTIONS

`--help`
: Print a usage message and exit.

`--update`
: Overwrite each script that fails with its regenerated script.

`-d` *dir*
: Write each regenerated script at the script's path relative to
  *dir* (default `.`), under the output directory; so `ost a/b/c.ost`
  in a directory and `ost -d .. b/c.ost` in its subdirectory `a`
  write the same file. A script outside *dir* is regenerated in a
  temporary directory (see *FILES*). A script may access files only
  under *dir* (see *FILE ACCESS*).

`--output-dir` *dir*
: Write each regenerated script under *dir* (default `target/ost`),
  which, if relative, is relative to the `-d` directory.

`--echo`
: Write the regenerated script to standard output as each command
  completes. Useful for finding a command that hangs.

`--timeout` *seconds*
: Interrupt any command that runs for longer than *seconds* (default
  60). A timeout is a fatal error (see *Errors*).

If any script fails, `ost` prints `ost: there were failures`, then,
for each script that fails, its fatal errors (see *Errors*), and a
`diff -u` command that compares the script with the regenerated
script, followed by that command's output; with `--update`, it
also prints `ost: updated` and the script's path.

## EXIT STATUS

* 0 - every script passed.
* 1 - at least one script failed.
* 2 - a usage error, or at least one script is malformed, and was
  not run.

For a malformed script, `ost` prints one line per problem, of the
form *path*`:`*line*`:`*column*`: `*message*:

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

# A function call. A value that is not a set of rows is printed
# with a '> ' prefix, followed by its type.
plus(1, 2);
> 3 : int

# A function call that fails prints the first line of its error.
plus(1, "x");
> error: ost: plus: expected an integer

# End example.ost
```

### Lines and whitespace

A script is UTF-8 text with LF line endings. It must not contain a
tab, a carriage return, or a space at the end of a line, which are
invisible and easily lost or added by an editor; `ost` reports each,
with its line and column, and does not run the script.

A script is a sequence of blank lines, *comments* (lines that start
with `#`), and *commands*, each followed by its *expected output*,
which may be empty. Blank lines and comments are copied to the
regenerated script unchanged. A `#` line inside a command is part of
the command.

### Commands

A command starts at the first line that is not blank, a comment, or
output. It ends at a `;` that is followed on its line only by
spaces, and that is outside all of the following:

| Construct            | Notes                                         |
| -------------------- | --------------------------------------------- |
| `(` ... `)`          | Nested to any depth.                          |
| `'` ... `'`          | Within, `''` is a quote.                      |
| `"` ... `"`          | Within, `""` is a quote.                      |
| `/*` ... `*/`        | Not nested.                                   |
| `--` ... end of line |                                               |
| `{\|` ... `\|}`      | A raw string; also `{id\|` ... `\|id}`.      |

So neither a `;` in the middle of a line nor one followed by a
comment, as in `select 1; -- one`, ends a command. A `)` that has no
matching `(`, or a construct that is open at the end of the script,
makes the script malformed.

A command is a *function call* if it starts with the name of a
function (see *FUNCTIONS*), optionally followed by spaces and line
breaks, and then `(`. Function names are case-sensitive. The
arguments run to the matching `)`, which must be followed only by
the terminating `;`.

Any other command is a *SQL command*, equivalent to a call to
`query`: its text, up to the terminating `;`, is executed by the
database. If it contains several statements, the output is that of
the last.

### Arguments

The arguments of a function call are separated by commas. An
argument is one of:

* An integer, such as `3` or `-12`.
* A floating-point number, such as `1.5` or `-2.5e3`.
* `true` or `false`.
* A string, `"..."`, with the escapes of JSON (RFC 8259).
* A raw string (see *Raw strings*).
* A query: the text of a SQL command. A query must be the last
  argument, because it may contain commas; it runs to the closing
  `)`, without surrounding spaces and line breaks.

Each function declares the kind of each parameter. An argument of
the wrong kind, or the wrong number of arguments, is an error (see
*Errors*).

### Output

A command's expected output immediately follows the line on which
the command ends, and consists of the lines that start with `+`, `|`
or `>`, and the lines of any raw string they contain. It is one of:

* Nothing, for a command that produces nothing, such as `connect`
  or `create table`.
* A table, if the command returns rows.
* A value, if a function returns a value.
* An error, if the command fails.

Expected output that contains more than one table, value or error
does not match. An output line that does not follow a command or
another output line makes the script malformed.

A command with no expected output is still run, and its output is
written to the regenerated script. To write a test, write the
commands without output, run `ost --update`, and check the output.

### Tables

A table is a border line, a header line, a border line, a line for
each row, and a border line:

```
+-------+--------+
| empno | ename  |
+-------+--------+
|  7934 | MILLER |
|  7369 | SMITH  |
+-------+--------+
```

`ost` makes each column as wide as its widest cell or header, with a
space of padding at each side. A header is written as the database
returns it, left-aligned. A cell is right-aligned if its column is in
the integer, decimal, real or double family (see *Types*), and
left-aligned otherwise.

### Types

`ost` works over the common core of SQL types: those that DuckDB and
Postgres share. It formats each value itself, by the rules below,
which depend only on the *family* of a column's type, because
databases differ in which type of a family an expression returns.
(DuckDB's `sum` of an `INTEGER` column is a `HUGEINT`; Postgres's is
a `bigint`.)

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

A value of any other type (such as a list, struct, `BLOB` or `UUID`)
cannot be printed, and is an error, such as
`> error: ost: unsupported type STRUCT(a INTEGER)`. Tests of features
outside the common core do not belong in `.ost` scripts.

A cell is written as follows:

* `NULL` is an empty cell.
* A boolean is `true` or `false`.
* An integer is written in decimal, with `-` if it is negative.
* A decimal of scale *s* has exactly *s* digits after the point, and
  at least one before it: `800.00`, `0.50`, `-0.50`. If *s* is 0
  there is no point.
* A double is the shortest decimal that converts back to the same
  double, as written by Python's
  [`repr`](https://docs.python.org/3/library/functions.html#repr):
  `0.1`, `0.30000000000000004`, `1e+16`, `1e-07`, `-0.0`, `inf`,
  `nan`.
* A real (32-bit) is the shortest decimal that converts back to the
  same real, written the same way; so `0.1::REAL` is `0.1`.
* A string is written verbatim, unless it is empty, starts or ends
  with a space, starts with `"`, contains `|`, or contains a control
  character or whitespace other than a space. Then it is written in
  quotes, with the escapes of JSON: `""`, `" a"`, `"a|b"`, `"a\nb"`.
  Control characters, U+2028 and U+2029 are written as `\uXXXX`.
* A date is `YYYY-MM-DD`, as in `2024-03-10`.
* A time is `HH:MM:SS`, with a fraction of a second only if it is not
  zero, without trailing zeros: `12:34:56`, `12:34:56.5`.
* A timestamp is a date, a space, and a time: `2024-03-10 12:34:56.5`.
* A timestamptz is written in UTC, as a timestamp followed by `+00`.
* An interval is an ISO 8601 duration: `P`, the days followed by
  `D`, then `T` and the hours, minutes and seconds followed by `H`,
  `M` and `S`, omitting each part that is zero; seconds may have a
  fraction. A day is 24 hours. So `INTERVAL '3 days 04:05:06.5'` is
  `P3DT4H5M6.5S`, `INTERVAL '25 hours'` is `P1DT1H`, a negative
  interval is `-` followed by its absolute value, as in `-PT1H`, and
  a zero interval is `PT0S`.

Numbers have no thousands separator, and their decimal point is `.`.
Dates and timestamps before 1 AD or after 9999 AD, infinite dates and
timestamps, and intervals with a month or year component are outside
the common core, and how they are printed is not specified.

### Values

A value is a line `> `, the value, ` : `, and its type:

```
> 3 : int
> 2.5 : double
> false : boolean
> "abc" : string
```

A number or boolean is written as in a table cell. A string is
written in quotes, with the escapes of JSON, and non-ASCII
characters verbatim except for control characters, U+2028 and
U+2029, which are written as `\uXXXX`. A function that returns
nothing has no output.

### Raw strings

A string that contains a line break is written as a *raw string*,
whose content is verbatim, without escapes. The content starts
immediately after the opening fence `{|`, and ends immediately
before the closing fence `|}`; lines after the first have no `> `
prefix:

```
> {|first line
second line|} : string
```

A fence tag that starts with an underscore means that the line break
after the opening fence is not content. `ost` uses one if the
content's second line starts with a space, so that the content's
lines line up:

```
> {_|
select deptno,
  avg(sal) as avg_sal
from scott.emp
|_} : string
```

If the content contains the closing fence, the fences carry a tag:
`{a|` ... `|a}`, or `{_a|` ... `|_a}`. A tag consists of the letters
`a` to `z` and underscores; `ost` uses the first tag in the sequence
`a`, `b`, ..., `z`, `aa`, `ab`, ... whose closing fence does not
occur in the content.

`ost` writes a string as a raw string only if it is a function's
value, contains a line break, has no space before a line break, and
consists of printable ASCII characters and line breaks. A raw string
in the expected output matches the quoted string with the same
content. Raw strings may also be function arguments, such as the
script that the `ost` function runs.

### Errors

If a command fails, its output is `> error: ` followed by the first
line of the error message, such as
`> error: ost: plus: expected an integer`. A command fails if its
function raises an error, including for arguments of the wrong
number or kind. `ost` continues with the next command.

A script may expect an error that `ost` raises, which is the same on
every database. Two kinds of error are *fatal*:

* A *database error*. Database errors are not portable: for a table
  that does not exist, DuckDB reports
  `Catalog Error: Table with name nonexistent does not exist!`, and
  PostgreSQL reports `ERROR:  relation "nonexistent" does not exist`.
  A function that accesses the database fails if the database raises
  an error. All of the built-in functions do so, and any function
  added to the harness should (see `README.md`).
* A *timeout* (see `--timeout`), which depends on the machine and its
  load.

A fatal error is written as the command's output, so that the author
can see it, but it never matches, even if the expected output is the
same error, and it fails the script. `ost` reports it with the
script's path and the line on which the command starts, with the
whole message:

```
tests/a.ost:1: database error: Parser Error: syntax error at or near "emp"

LINE 1: select * fro emp
                     ^^^
```

## MATCHING

Unless `matchStrict` is set (see `set`), the actual output matches
the expected output if they are equivalent:

* Two tables are equivalent if their headers are the same, ignoring
  case, and their rows are the same. Cells are compared without
  their padding, and without the quotes and escapes of quoted cells;
  the comparison is exact, so `x y` does not match `x  y`, nor
  `800.00` `800.0`. Border lines may differ in width.
* Rows are compared as a multiset - the same rows, in any order -
  unless the command is ordered (see `query`).
* Two values are equivalent if their types and values are the same.
  Spaces around ` : ` are ignored. A raw string is equivalent to the
  quoted string with the same content.
* Two errors are equivalent if their messages are the same, unless
  one is fatal (see *Errors*).
* Outputs of different kinds are not equivalent, nor is expected
  output that `ost` cannot parse equivalent to anything.

If the output matches, the regenerated script contains the expected
output exactly as it was written; if not, the actual output, written
as described in *FILE FORMAT*. In an unordered table, the rows that
match an expected row come first, in the order of the expected
output, followed by the other rows in the order the database
returned them, so that the diff shows only the rows that changed.

### Deterministic tests

A test must give the same output on every run, and on every
database. `ost` does not check this, because many queries that look
nondeterministic always give the same output: a query that returns
at most one row, or whose sort key has no ties in the data, has only
one order. The author of a test is responsible for writing queries
that are deterministic:

* Make each sort key unique, for example `order by deptno, empno`,
  not `order by deptno`; likewise for `LIMIT` and for `ORDER BY` in
  a window function. Or remove `ORDER BY`, so that the rows are
  compared in any order.
* Do not use functions whose result changes from run to run, such as
  `now()`, `current_date` or `random()`.
* Do not depend on the session time zone, for example by casting a
  timestamptz to a date or timestamp, or by writing a timestamptz
  literal without an offset.

## FUNCTIONS

`connect(name)`
: Closes the current database, and opens a new in-memory database,
  loaded with the fixture *name*:
  * `"empty"` - no tables.
  * `"scott"` - the SCOTT schema: tables `dept`, `emp`, `bonus` and
    `salgrade` in schema `scott`, which is on the search path, so
    both `emp` and `scott.emp` work.

  Each script starts as if it began with `connect("empty")`.

`query(sql)`
: Executes *sql*, as a SQL command does. The rows are ordered
  (compared in order) if and only if *sql* contains `order by`, in
  any case, with any spaces or line breaks between the words. The
  rule is a search of the text, not a parse, so `order by` in a
  window, a subquery, a comment or a string literal makes the rows
  ordered. When the rule is wrong, use `queryOrdered` or
  `queryUnordered`.

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
  database (as if it began with `connect("empty")`) and properties,
  but the same file access (see *FILE ACCESS*), and returns the
  regenerated script. A fatal error in *script* fails the script
  that calls `ost`. If *script* is malformed, the result is an error
  that describes the first problem, with path `<string>`, such as
  `> error: <string>:2:1: unterminated command (open '(' at 1:6)`.

  `ost` is how a script tests `ost`. Lenient matching keeps expected
  output as written, so a script cannot test the output that `ost`
  generates directly; but the result of `ost` is a string, which
  must match exactly. For example, this shows that `x  y` does not
  match `x y`, because the regenerated script differs from the
  original:

  ```
  ost({|select 'x y' as c;
  +------+
  | c    |
  +------+
  | x  y |
  +------+
  |});
  > {|select 'x y' as c;
  +-----+
  | c   |
  +-----+
  | x y |
  +-----+
  |} : string
  ```

## FILE ACCESS

A script may read and write files, for example with DuckDB's
`read_csv` or `COPY`, only under the `-d` directory. A relative
path, such as `read_csv('emp.csv')`, is looked up in the current
directory, then in the script's directory, so a script can read a
file beside it (if the current directory is under the `-d`
directory). A path that contains `..`, or a file outside the `-d`
directory, even through a symbolic link, is refused with a database
error (see *Errors*). A script cannot lift the restriction, which
also prevents it from attaching a database or installing an
extension.

## PYTHON API

The Python implementation can run a script held in a string, which
is useful in unit tests and in programs that generate scripts:

```python
import ossie_ost

result = ossie_ost.run(text, path="<string>")
result.passed   # True if output == text, with no fatal errors
result.output   # the regenerated script
```

`run(text, path="<string>", *, timeout=60, functions=None,
root=None, script_dir=None)` runs *text* as a script, as the command
line would, and returns a `Result` with fields `path`, `input`,
`output`, `fatal_errors` (a line and message for each fatal error)
and `passed`. It writes no files. *path* is used only in messages,
and need not exist. *functions* adds functions to the built-in ones
(see `README.md`). *root* is the directory under which the script
may access files (default: the current directory), and *script_dir*
the script's directory, if any (see *FILE ACCESS*).

If *text* is malformed, `run` raises `ossie_ost.ScriptError`, whose
`problems` field is a list of `Problem(line, column, message)`, and
whose string form is the lines that the command line would print.

## FILES

`target/ost/`
: The default output directory, under the `-d` directory.

A temporary directory, such as `/tmp/ost-k2j3h4ab/`
: Regenerated scripts that are outside the `-d` directory, at their
  absolute paths. A run creates the directory when it first needs
  it, in the system's temporary directory, so that only the user can
  write to it (for example, as Python's `tempfile.mkdtemp` does in
  `tempfile.gettempdir()`), and does not delete it; the `diff`
  command names each file in it. A fixed, shared directory would let
  another user plant a symbolic link that makes `ost` overwrite one
  of the user's files, and would let two runs overwrite each other's
  files.

## BUGS

A column header cannot contain `|`, or start with `"`; a table with
such a header never matches.

A script must be valid UTF-8, without a byte-order mark; `ost` does
not report one that is not as malformed.

## SEE ALSO

* [apache/ossie#501](https://github.com/apache/ossie/issues/501),
  the proposal for this format.
* [Sql Logic Test](https://sqlite.org/sqllogictest/doc/trunk/about.wiki)
  (`.slt`), [Quidem](https://github.com/hydromatic/quidem) (`.iq`),
  and Morel's
  [`.smli`](https://github.com/hydromatic/morel/blob/main/docs/script-format.md),
  earlier idempotent test formats.
