import { createClient, type CreateOrderRequest, type Order, type Transport } from "./generated/client.js";

/** Place an order. The idempotency key defaults to the rule in the README. */
export async function placeOrder(
  transport: Transport,
  customerId: string,
  total: number,
  idempotencyKey?: string,
): Promise<Order> {
  const body: CreateOrderRequest = { customerId, total };
  if (idempotencyKey !== undefined) {
    body.idempotencyKey = idempotencyKey;
  }
  return createClient(transport).createOrder(body);
}

/** One line for the order list in the admin console. */
export function orderLine(order: Order): string {
  return `${order.id} ${order.status} ${order.placedAt}`;
}

/** Fetch an order and render its line. */
export async function fetchOrderLine(transport: Transport, orderId: string): Promise<string> {
  return orderLine(await createClient(transport).getOrder(orderId));
}
