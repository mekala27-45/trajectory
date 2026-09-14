import { createClient, type Transport } from "./generated/client.js";

/** The tracking line shown under an order. See the README for the exact format. */
export async function trackingSummary(transport: Transport, orderId: string): Promise<string> {
  const shipments = await createClient(transport).listOrderShipments(orderId);
  return `${orderId}: ${shipments.trackingNumber}`;
}
