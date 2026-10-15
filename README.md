<div align="center">

<img src="./logo.svg" alt="yaml-validator logo" width="140">

# yaml-validator

[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Live-brightgreen?style=flat-square)](https://soumendrak.github.io/yaml-validator/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![MIT License](https://img.shields.io/badge/License-MIT-blue?style=flat)](LICENSE)
[![PyYAML](https://img.shields.io/badge/PyYAML-6.0+-orange?style=flat)](https://pyyaml.org)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat)]()

**Validate YAML files for syntax, style, and schema correctness.**

**Live:** [https://soumendrak.github.io/yaml-validator/](https://soumendrak.github.io/yaml-validator/)

</div>

---

## Features

- **Syntax validation** — catches malformed YAML with precise line/column reporting
- **Style checks** — trailing whitespace, tab indentation, line length, missing EOF newlines
- **Auto-fix** — `--fix` repairs tab indentation, trailing whitespace and missing EOF newlines in place, without touching comments or layout
- **Schema validation** — validate YAML structure against a JSON Schema (or YAML schema) file
- **Multi-document support** — handles YAML files with `---` separators
- **Machine-readable output** — `--json` flag for CI/CD integration
- **Zero runtime deps beyond PyYAML** — schema validation needs `jsonschema` (optional)

## Installation

```bash
# Via pip (from source or GitHub)
pip install git+https://github.com/soumendrak/yaml-validator.git

# Or clone and install locally
git clone https://github.com/soumendrak/yaml-validator.git
cd yaml-validator
pip install .

# With schema validation support
pip install ".[schema]"
```

## Usage

```bash
# Basic syntax check
yaml-validator check config.yaml

# Multiple files
yaml-validator check *.yaml

# With schema validation
yaml-validator check config.yaml --schema schema.json

# Multi-document YAML
yaml-validator check multi-doc.yaml --multi

# Quiet mode (errors + final summary only)
yaml-validator check config.yaml -q

# JSON output (for CI)
yaml-validator check config.yaml --json

# Lint (alias for check)
yaml-validator lint config.yaml

# Auto-fix style issues in place, then validate
yaml-validator check config.yaml --fix

# Keep a copy of the original as config.yaml.bak; indent tabs become 4 spaces
yaml-validator check config.yaml --fix --backup --tab-width 4
```

### Auto-fix (`--fix`)

`--fix` (or `-f`, on `check` and `lint`) rewrites each file in place, then validates it and reports what is left:

| Rule | Fix |
|------|-----|
| `no-tabs` | Tabs in the indentation of tab-indented lines become `--tab-width` spaces (default 2) |
| `trailing-spaces` | Trailing whitespace is removed (skipped with `--no-trailing-spaces`) |
| `end-of-file-newline` | A final newline is added |

- Only whitespace is edited, so comments, quoting, key order, blank lines and CRLF line endings stay as they are.
- A fix is applied only if it is safe. If the file parses, the parsed data must stay identical; for example, trailing spaces inside a `|` block scalar are kept. If the file does not parse, the fixes must make it parse; otherwise the file is left untouched.
- Running `--fix` again changes nothing.
- The file is only rewritten when something was fixed. `--backup` saves the original as `<file>.bak` first, always at mode 0600 and owned by you.
- The fixed file replaces the original atomically and keeps its mode, group, and best-effort extended attributes such as ACLs and SELinux labels (skipped on filesystems without xattr support; and owner, when run with the rights to set it). This breaks hard links: other names for the same file keep the old content.

```bash
$ yaml-validator check bad.yaml --fix
Fixed 1 issue(s) in bad.yaml (1 no-tabs)
✓ bad.yaml
```

Fixes that were skipped are listed with the reason (`Skipped trailing-spaces fix in x.yaml: ...`). With `--json`, the output gains `fixed` and `fix_skipped` keys.

### Exit Codes

| Code | Meaning |
|------|---------|
| 0    | All files valid |
| 1    | One or more files invalid or errors encountered (with `--fix`: a file is still invalid after fixing) |

## Examples

**Valid YAML:**
```yaml
# config.yaml
server:
  host: localhost
  port: 8080
database:
  url: postgres://db:5432/mydb
  pool_size: 10
```

```bash
$ yaml-validator check config.yaml
✓ config.yaml
```

**Invalid YAML:**
```yaml
# bad.yaml
server:
  host: localhost
	port: 8080
```

```bash
$ yaml-validator check bad.yaml
[ERROR] line 3:1: found character '\t' that cannot start any token
[ERROR] line 3: Tab character used for indentation (YAML requires spaces)

✗ bad.yaml
   1 error(s)
   1 warning(s)
```

## Schema Validation

Define a JSON Schema and validate YAML against it:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["server", "database"],
  "properties": {
    "server": {
      "type": "object",
      "required": ["host", "port"],
      "properties": {
        "host": {"type": "string"},
        "port": {"type": "integer"}
      }
    }
  }
}
```

```bash
yaml-validator check config.yaml --schema schema.json
```

## Development

```bash
git clone https://github.com/soumendrak/yaml-validator.git
cd yaml-validator
uv venv
source .venv/bin/activate
uv pip install -e ".[schema]"
```

Run tests:
```bash
uv pip install pytest
pytest
```

## Project Structure

```
yaml-validator/
├── index.html          # GitHub Pages landing page
├── logo.svg            # Project logo
├── yaml_validator/
│   ├── __init__.py     # Package metadata
│   ├── cli.py          # CLI entry point (argparse)
│   └── validator.py    # Core validation engine and --fix logic
├── tests/              # pytest suite
├── pyproject.toml      # Project metadata & build config
├── requirements.txt    # Dependency pins
├── LICENSE             # MIT License
└── README.md           # This file
```

## License

Licensed under the [MIT License](LICENSE).

---

<p align="center"><sub>Built with ❤️ and zero unnecessary complexity</sub></p>
