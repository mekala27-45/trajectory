# orders-client

A typed client for the order service, generated from `openapi.json`, and the three call
sites that use it.

## Layout

```
openapi.json               the contract. The service team owns it, we only consume it.
tools/generate-client.mjs  the generator. Committed, offline, deterministic.
src/generated/client.ts    generated output. Never hand edited.
src/orders.ts              placing an order and rendering its line
src/status.ts              customer facing status labels
src/shipments.ts           the tracking line shown under an order
```

## Workflow

```
node tools/generate-client.mjs    # rewrite src/generated/client.ts from openapi.json
sh build.sh                       # type check src/ and emit dist/
```

`build.sh` deliberately does not regenerate. A build that silently rewrites generated code
hides the moment a contract changed. The intended loop is the other way round: the schema
moves, you regenerate, the compiler names every call site the change broke, and you fix
them one by one.

`src/generated/client.ts` is checked in so that the build needs no network and no
node_modules, and CI asserts it is byte identical to what the generator produces from the
current `openapi.json`. A hand edit to that file fails CI even when it compiles, which is
the whole reason the check exists.

`tsc` is installed globally in the image. There is no node_modules and no network.

## Decisions the call sites have to honour

**Idempotency keys.** `placeOrder` takes an optional key. Every create request carries one:
when the caller does not supply a key, `placeOrder` sends `` `${customerId}:${total}` ``.

**Status labels.** `describeStatus` covers every member of `OrderStatus`:

| status | label |
| ------ | ----- |
| `pending` | `Awaiting payment` |
| `shipped` | `On the way` |
| `cancelled` | `Cancelled` |
| `refunded` | `Refunded` |

The `default` arm of that switch assigns the status to a `never`. It is there so a status
added to the schema breaks the build rather than reaching a customer as a blank label.
Leave it in place.

**Tracking line.** `trackingSummary` returns the order id, then `: `, then the tracking
numbers of that order's shipments joined by `, ` in the order the service returned them.
With no shipments it returns the order id followed by `: none`.
