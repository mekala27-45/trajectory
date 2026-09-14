# trajectory-eval

Evaluation harness for coding agents. Runs an agent against containerized software engineering
tasks with hidden verification tests, records the complete trajectory, and scores the trajectory
rather than only the outcome.

```
pip install trajectory-eval
trajectory tasks list
trajectory run --task py-failing-suite-01 --model anthropic/claude-sonnet-4-5
trajectory replay <run-id>
```

Full documentation: https://github.com/mekala27-45/trajectory
