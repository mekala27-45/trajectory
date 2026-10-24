# trajectory-eval

Evaluation harness for coding agents. Runs an agent against containerized software engineering
tasks with hidden verification tests, records the complete trajectory, and scores the trajectory
rather than only the outcome.

Not published to PyPI yet, so run it from a clone of the repository:

```
git clone https://github.com/mekala27-45/trajectory && cd trajectory
uv sync --all-packages
uv run trajectory tasks list
uv run trajectory run --task py-failing-suite-01 --model anthropic/claude-sonnet-4-5
uv run trajectory replay <run-id>
```

Full documentation: https://github.com/mekala27-45/trajectory
