# Development Guide

## Setting Up Your Development Environment

### 1. Install Development Dependencies

```bash
# Install dev tools (ruff, black, mypy, pytest, etc.)
pip install -e ".[dev]"
```

### 2. Set Up Pre-commit Hooks (Optional but Recommended)

Pre-commit hooks automatically lint and format your code before each commit:

```bash
# Install pre-commit
pip install pre-commit

# Set up the git hooks
pre-commit install

# Run against all files (useful after first setup)
pre-commit run --all-files
```

Now whenever you run `git commit`, the hooks will automatically:
- Check for linting issues (ruff)
- Format code (black)
- Run type checks (mypy)
- Fix trailing whitespace, line endings, etc.

---

## Code Quality Tools

### Ruff - Fast Linter

Ruff checks for common Python errors and style issues.

```bash
# Check for linting issues
ruff check model2data/

# Show detailed fixes
ruff check model2data/ --show-fixes

# Automatically fix issues
ruff check model2data/ --fix
```

**What it checks**:
- Syntax errors
- Undefined variables
- Unused imports
- Code style violations
- Complexity issues

---

### Black - Code Formatter

Black automatically formats Python code to a consistent style.

```bash
# Check if code matches Black's style
black --check model2data/

# Automatically format code
black model2data/
```

**Why use it**:
- Consistent style across the codebase
- Removes style debates in code reviews
- All code looks the same
- No need to discuss spacing, quotes, etc.

---

### mypy - Type Checker

mypy checks for type errors without running the code.

```bash
# Type check the codebase
mypy model2data/

# Detailed output
mypy model2data/ --show-error-codes --show-error-context
```

**Why use it**:
- Catch bugs before runtime
- Better IDE support and autocomplete
- Self-documenting code (types are inline docs)
- Refactoring safety

---

### pytest - Testing

Run unit tests and coverage reports.

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=model2data --cov-report=html

# Run specific test file
pytest tests/test_generation.py

# Run specific test
pytest tests/test_generation.py::test_some_function

# Watch mode (re-run on file changes)
pytest-watch
```

---

## CI/CD Checks

The GitHub Actions workflow runs three jobs:

1. **Lint Job** (runs once on Python 3.12)
   - `ruff check` — Catches style and error issues
   - `black --check` — Ensures consistent formatting
   - `mypy` — Checks for type errors

2. **Test Job** (runs on Python 3.9-3.12)
   - `pytest` — Runs unit tests
   - Collects coverage reports
   - Uploads to Codecov

**Pull requests must pass both jobs before merging.**

---

## Workflow for Contributing

### 1. Create a Branch

```bash
git checkout -b feature/my-feature
```

### 2. Make Changes

```bash
# Edit your code
```

### 3. Format and Lint (if pre-commit not set up)

```bash
# Format code
black model2data/

# Fix linting issues
ruff check model2data/ --fix

# Type check
mypy model2data/

# Run tests
pytest --cov=model2data
```

### 4. Commit and Push

```bash
git add .
git commit -m "Add my feature"
# Pre-commit hooks run automatically if installed
git push origin feature/my-feature
```

### 5. Create Pull Request

- GitHub Actions will automatically run lint and test jobs
- All checks must pass before merging
- Address any issues that CI reports

---

## Common Issues and Fixes

### Issue: Black and ruff disagree on formatting

**Solution**: They're configured to work together. Just run:
```bash
ruff check model2data/ --fix
black model2data/
```

### Issue: mypy says "error: Skipping analyzing 'X': module not found"

**Solution**: Some type stubs are not installed. Check `.pre-commit-config.yaml` and `pyproject.toml` for missing type packages. Common ones:
- `types-pyyaml` — for YAML type hints
- `types-requests` — for requests type hints

### Issue: "Pre-commit hooks not running"

**Solution**: Make sure you ran:
```bash
pre-commit install
```

### Issue: "Lint passes locally but fails in CI"

**Solution**: Make sure you're using the same Python version:
```bash
# Check your Python version
python --version

# Or run in a specific version (if using pyenv/conda)
pyenv shell 3.9  # Use same as CI
```

---

## IDE Integration

### VS Code

Install these extensions for a better development experience:

1. **Pylance** (Python language server)
   - Real-time type checking
   - Autocomplete
   - Go to definition

2. **Black Formatter**
   - `ms-python.black-formatter`
   - Auto-format on save

3. **Ruff**
   - `charliermarsh.ruff`
   - Real-time linting with quick fixes

Settings (`.vscode/settings.json`):
```json
{
  "[python]": {
    "editor.defaultFormatter": "ms-python.black-formatter",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": "explicit"
    }
  },
  "pylance.typeCheckingMode": "basic"
}
```

### PyCharm

1. **Settings → Tools → Python Integrated Tools**
   - Default test runner: pytest
   - Default linter: Ruff

2. **Settings → Editor → Code Style → Python**
   - Enable "Black" formatter

3. **Settings → Tools → Black**
   - Enable Black integration

---

## Documentation

For more information:

- **Ruff**: https://docs.astral.sh/ruff/
- **Black**: https://black.readthedocs.io/
- **mypy**: https://mypy.readthedocs.io/
- **pytest**: https://docs.pytest.org/
- **Pre-commit**: https://pre-commit.com/

---

## Questions?

If you have questions about development setup or code quality tools:
1. Check the tool's documentation (linked above)
2. Open an issue on GitHub
3. Ask in a discussion thread
