// Clean — only imports allowed siblings and app/providers.
import { todoListReader } from '../../app/providers';
import type { Todo } from '../../domain/models/todo';

export function listTodos(): Promise<Todo[]> {
  return todoListReader();
}
