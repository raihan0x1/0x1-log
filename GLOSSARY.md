# 0x1LOG

Embedded observability for FastAPI apps: the events an app records, and the dashboard that reads them back.

## Language

**Event**:
One stored record of what the app did: a log line, a served request, an error, or a finished span.
_Avoid_: Entry, record, row

**Error group**:
All error events that share one signature (exception type plus innermost file and line), counted together on the Errors view.
_Avoid_: Grouping (alone), error bucket

**Repeat**:
A run of consecutive events with the same type, name, level and status, shown as one row with a count and opened to see each event.
_Avoid_: Group, duplicate, collapse
