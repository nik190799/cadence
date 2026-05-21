// Provider declarations — repository imports are allowed here.
import { TodoRepository } from '../data/repositories/todoRepository';

export const todoListReader = () => new TodoRepository().list();
