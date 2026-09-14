# report-builder

Builds a small report from two plugins that share a core library.

Everything installs offline from the vendored `wheelhouse/`. There is no network access.

```
sh check.sh
```

`check.sh` resolves `requirements.txt` into a temporary directory and runs `app.py`
against it.
