# Learning-loop fixtures

- `repo/`: the base repository for `tests/test_learning_e2e.py`, a tiny
  TypeScript app with `src/domain`, `src/db` and `src/http` and one seed
  boundary rule (`src/domain` must not import `src/http`). The test copies
  it into a temporary git repo (with LF line endings), adds the factory
  tools and schemas, and runs two agent attempts that both import `src/db`
  from `src/domain`.
- `golden/`: one valid example per learning schema, used by
  `tests/test_learning_contract.py`.
