# findings.json

Write your conclusions to `findings.json` in the working directory, as a single JSON
object with exactly these keys.

| Key                   | Type            | Meaning                                                                                 |
| --------------------- | --------------- | --------------------------------------------------------------------------------------- |
| `root_cause_service`  | string          | The service whose own change started the incident.                                        |
| `trigger_event_id`    | string          | The `event_id` of the log line that started it.                                           |
| `trigger_timestamp`   | string          | That line's timestamp, to the second, as `YYYY-MM-DDTHH:MM:SSZ`.                          |
| `resolution_event_id` | string          | The `event_id` of the log line after which the errors stop.                               |
| `error_signature`     | string          | A short lowercase phrase, copied from the causal error lines, that identifies the failure. |
| `impacted_services`   | array of string | Every service that emitted at least one 5xx status, sorted alphabetically.                 |
| `total_5xx`           | integer         | Total number of log lines whose `status` field is 500 or greater.                          |
| `line_count`          | integer         | Total number of lines in the log.                                                          |

Example of the shape, with values that are not the answer:

```json
{
  "root_cause_service": "payments",
  "trigger_event_id": "cfg-000001",
  "trigger_timestamp": "2026-04-12T13:45:00Z",
  "resolution_event_id": "cfg-000002",
  "error_signature": "circuit open",
  "impacted_services": ["api-gateway", "payments"],
  "total_5xx": 12,
  "line_count": 40029
}
```

## Log format

```
<timestamp> <LEVEL> [<service>] <message> key=value key=value ...
```

Not every line has an `event_id`, and not every line has a `status`.
