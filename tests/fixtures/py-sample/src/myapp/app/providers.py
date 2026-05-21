from src.myapp.data.repositories.todo_repository import TodoRepository


def todo_list_reader():
    return TodoRepository().list()
