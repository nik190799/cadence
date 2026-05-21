// VIOLATION: features/ must not import from data/sources/.
import { TodoStore } from '../../data/sources/todoStore';

// VIOLATION: features/ must not import from data/repositories/ either.
import { TodoRepository } from '../../data/repositories/todoRepository';

// OK: features importing other features is allowed by these rules.
import { OtherThing } from '../shared/util';

export class TodosController {
  constructor(private store: TodoStore, private repo: TodoRepository) {}
}
