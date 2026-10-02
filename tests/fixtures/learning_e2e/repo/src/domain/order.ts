export interface Order {
  id: string;
  total: number;
}

export const isPaid = (order: Order): boolean => order.total <= 0;
