// Clean leaf — no upward imports.
import type { Todo } from '../../domain/models/todo';

export class TodoStore {
  private items: Todo[] = [];
}
