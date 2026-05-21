# VIOLATION: features/ may not import from data/sources/.
from src.myapp.data.sources.todo_store import TodoStore

# VIOLATION: same rule applies to data/repositories/.
import src.myapp.data.repositories.todo_repository

# OK: sibling import.
from .helpers import normalize


def list_todos():
    return TodoStore().list()
