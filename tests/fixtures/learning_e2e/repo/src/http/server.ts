import { isPaid } from "../domain/order";

export const handle = (body: { id: string; total: number }): string =>
  isPaid(body) ? "paid" : "open";
