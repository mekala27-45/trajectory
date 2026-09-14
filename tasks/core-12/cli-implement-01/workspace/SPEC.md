# logstat specification

`logstat` reads log records, filters them by severity, and prints one of three summaries.

This document is the whole interface. Anything it does not describe is not part of the
tool, and anything it does describe is checked.

## Invocation

```
python3 logstat.py <subcommand> [options] [FILE]
```

`logstat.py` stays at the top of this directory and stays runnable by a plain
`python3 logstat.py`. `<subcommand>` is one of `count`, `top`, `span`, spelled in lower
case.

`FILE` is optional. When it is given the tool reads that file. When it is absent the tool
reads standard input. `-` has no special meaning: it names a file called `-`.

Options may appear before or after `FILE`.

## Options

**`--min-level LEVEL`**

Accepted by every subcommand. Discards every record whose level is below `LEVEL` in the
severity order below. `LEVEL` is one of the five level names, upper case. Defaults to
`DEBUG`, which discards nothing.

**`--number N`**

Accepted by `top` only. The most lines `top` will print. `N` is a non empty string of
decimal digits whose value is 1 or more, so `0`, `-1`, `3.0` and `two` are all rejected.
Defaults to `3`. Passing `--number` to `count` or `span` is a usage error.

Both `--min-level VALUE` and `--min-level=VALUE` are accepted, and likewise for
`--number`.

The tool recognises no other options. `-h` and `--help` are outside this specification:
their behaviour is not defined here and nothing depends on it. Any other argument that
begins with `-`, except the single character `-`, is a usage error.

## Severity order

```
DEBUG < INFO < WARN < ERROR < FATAL
```

That order, least severe first, is the canonical order, and it is the order `count` prints
in.

## Record format

Every input line is exactly one of three things: blank, a record, or malformed.

A **blank** line is empty or contains only spaces and tabs. Blank lines are ignored
completely. They are neither records nor malformed.

A **record** is a line of the form

```
TIMESTAMP|LEVEL|SERVICE|MESSAGE
```

split on `|` at the first three occurrences only. `MESSAGE` is everything after the third
`|`, so a message may itself contain `|`, and a message may be empty. To be a record, all
of these must hold:

- the line contains at least three `|` characters
- `TIMESTAMP` is exactly 20 characters in the form `YYYY-MM-DDTHH:MM:SSZ`, zero padded,
  and names a real instant, so `2026-2-01T09:00:00Z` and `2026-02-30T00:00:00Z` are both
  not records
- `LEVEL` is exactly one of `DEBUG`, `INFO`, `WARN`, `ERROR`, `FATAL`
- `SERVICE` is not empty

Nothing is stripped before those checks are applied, so a line that begins with a space is
malformed: the space lands inside the timestamp. A trailing space, on the other hand, is
just the last character of the message. Any line that is neither blank nor a record is
**malformed**.

Lines are separated by `\n`. The last line does not need a trailing newline. No other line
ending has to be handled.

## Malformed input

Malformed input is fatal, and it is detected before anything is printed. When the input
holds at least one malformed line the tool writes nothing at all to standard output,
writes exactly

```
logstat: malformed record on line N
```

followed by a newline to standard error, and exits 4. `N` is the 1 based number of the
lowest numbered malformed line. Every line of the input counts towards `N`, blank lines
included. `--min-level` does not excuse a malformed line: a malformed line has no level.

The input is read in full before any summary is produced, so exit 4 takes precedence over
exit 5 and over any output at all.

## Exit codes

| code | meaning |
| ---- | ------- |
| 0 | success |
| 2 | usage error: no subcommand, unknown subcommand, unknown option, missing or invalid option value, `--number` on a subcommand other than `top`, or more than one `FILE` |
| 3 | input error: `FILE` cannot be opened or read |
| 4 | the input holds a malformed line |
| 5 | `span` was asked to summarise zero records |

On every non zero exit the tool writes nothing at all to standard output and writes one
diagnostic line to standard error. Two of those diagnostics are fixed and are checked
verbatim, the exit 4 line above and, for exit 5:

```
logstat: no records matched
```

The wording of the usage and input error diagnostics is yours to choose, as long as
standard error is not empty.

## Subcommand: count

Prints one line per level, from `--min-level` up to `FATAL` in canonical order, then one
`TOTAL` line. The default therefore prints six lines, and `--min-level ERROR` prints
three.

`TOTAL` is the number of records that survived the filter. Each line is

- the label, which is a level name or `TOTAL`, left justified in a field 5 characters wide
- one space
- the count, right justified in a field as wide as the number of digits in `TOTAL`

`TOTAL` is never smaller than any single count, so that one width fits every line and the
right hand edges line up.

Empty input is not an error. With no records at all the default output is six lines whose
counts are all `0` and whose count column is one character wide.

## Subcommand: top

Prints the services with the most surviving records, at most `--number` of them, one per
line. Services are ordered by surviving record count descending. Services with equal
counts are ordered by service name ascending, compared by Unicode code point, which for
ASCII names puts upper case before lower case.

Column widths come from the lines that are actually printed, not from the whole input.
Each line is

- the service name, left justified in a field as wide as the longest service name printed
- one space
- the count, right justified in a field as wide as the largest count printed

When no records survive the filter, `top` prints nothing at all and exits 0. Empty input
is one way to get there.

## Subcommand: span

Prints exactly three lines, in this order:

```
first <earliest surviving timestamp>
last  <latest surviving timestamp>
secs  <whole seconds from the earliest to the latest>
```

Earliest and latest are by timestamp value, not by position in the input. Records do not
have to arrive in order. Timestamps are echoed in the same 20 character form they were
read in. `secs` is the difference in seconds, so it is `0` when one record survives and
when every surviving record carries the same timestamp.

Each line is the label (`first`, `last`, `secs`) left justified in a field 5 characters
wide, then one space, then the value.

When no records survive the filter, `span` prints nothing to standard output, writes
`logstat: no records matched` and a newline to standard error, and exits 5. Empty input is
one way to get there.

## Output rules that hold everywhere

Every line written to standard output ends in exactly one newline, the last line included.
There is no header, no trailing blank line, and nothing else on standard output.

## Worked examples

The four examples below all use this input, saved as `sample.log`:

```
2026-03-01T09:15:04Z|INFO|gateway|GET /health 200
2026-03-01T09:15:09Z|WARN|inventory|pool 3/4 in use
2026-03-01T09:14:58Z|ERROR|inventory|pool exhausted
2026-03-01T09:16:00Z|INFO|checkout|order 1841 placed
2026-03-01T09:16:02Z|ERROR|checkout|timeout calling inventory
2026-03-01T09:16:02Z|ERROR|gateway|503 for /cart | upstream inventory
2026-03-01T09:17:30Z|DEBUG|gateway|retry budget 2 left
2026-03-01T09:17:31Z|FATAL|inventory|giving up
```

Eight records, and two details worth noticing before you write any code. Line 6 has a `|`
inside its message, and it is a record. Line 3 carries the earliest timestamp in the file.

### Example 1

```
$ python3 logstat.py count sample.log
DEBUG 1
INFO  2
WARN  1
ERROR 3
FATAL 1
TOTAL 8
```

Exit status 0. `TOTAL` is 8, one digit, so the count column is one character wide.

### Example 2

```
$ python3 logstat.py top --min-level ERROR sample.log
inventory 2
checkout  1
gateway   1
```

Exit status 0. Three services have records at `ERROR` or above. `checkout` and `gateway`
have one each, so they are ordered by name. The name column is 9 wide because `inventory`
is the longest name printed.

### Example 3

```
$ python3 logstat.py span sample.log
first 2026-03-01T09:14:58Z
last  2026-03-01T09:17:31Z
secs  153
```

Exit status 0. The earliest record is the one on line 3.

### Example 4

```
$ cat sample.log | python3 logstat.py top --number 2
gateway   3
inventory 3
```

Exit status 0. No `FILE`, so the input came from standard input. Across all levels
`gateway` and `inventory` have three records each and `gateway` sorts first, and
`--number 2` cuts the list before `checkout`.
