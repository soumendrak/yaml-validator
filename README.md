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
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0    | All files valid |
| 1    | One or more files invalid or errors encountered |

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
	port: 8080	# ← tab used for indentation
```

```bash
$ yaml-validator check bad.yaml
[ERROR] line 3: Tab character used for indentation (YAML requires spaces)

✗ bad.yaml
   1 error(s)
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
│   └── validator.py    # Core validation engine
├── pyproject.toml      # Project metadata & build config
├── requirements.txt    # Dependency pins
├── LICENSE             # MIT License
└── README.md           # This file
```

## License

Licensed under the [MIT License](LICENSE).

---

<p align="center"><sub>Built with ❤️ and zero unnecessary complexity</sub></p>
