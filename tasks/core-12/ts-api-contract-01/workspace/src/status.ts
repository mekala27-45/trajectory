import type { OrderStatus } from "./generated/client.js";

/**
 * The customer facing label for a status.
 *
 * The default arm assigns to `never` on purpose. When the schema gains a status this stops
 * compiling, which is the only reason anyone finds out in time. Leave it in place.
 */
export function describeStatus(status: OrderStatus): string {
  switch (status) {
    case "pending":
      return "Awaiting payment";
    case "shipped":
      return "On the way";
    case "cancelled":
      return "Cancelled";
    default: {
      const unhandled: never = status;
      throw new Error(`unhandled order status: ${String(unhandled)}`);
    }
  }
}
