from src.myapp.data.sources.todo_store import TodoStore


class TodoRepository:
    def list(self):
        return TodoStore().list()
