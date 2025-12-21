# Development Guide

## Setting Up Your Development Environment

### 1. Install uv

If you haven't already, install uv:
```bash
# On macOS and Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# On Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Install Development Dependencies
```bash
# Install the project with dev dependencies
uv sync --extra dev
```

### 3. Set Up Pre-commit Hooks (Optional but Recommended)

Pre-commit hooks automatically lint and format your code before each commit:
```bash
# Install pre-commit
uv tool install pre-commit

# Set up the git hooks
pre-commit install

# Run against all files (useful after first setup)
pre-commit run --all-files
```

Now whenever you run `git commit`, the hooks will automatically:
- Check for linting issues (ruff)
- Format code (ruff format)
- Fix trailing whitespace, line endings, etc.

---

## Code Quality Tools

### Ruff - Fast Linter and Formatter

Ruff checks for common Python errors, style issues, and formats your code.
```bash
# Check for linting issues
uv run ruff check

# Show detailed fixes
uv run ruff check --show-fixes

# Automatically fix issues
uv run ruff check --fix

# Check formatting
uv run ruff format --check

# Format code
uv run ruff format
```

**What it checks**:
- Syntax errors
- Undefined variables
- Unused imports
- Code style violations
- Complexity issues
- Consistent code formatting

---

### ty - Type Checker

ty is Astral's fast type checker that analyzes Python code for type errors.
```bash
# Type check the codebase
uv run ty check model2data/

# More detailed output
uv run ty check model2data/ --verbose
```

**Why use it**:
- Catch bugs before runtime
- Better IDE support and autocomplete
- Self-documenting code (types are inline docs)
- Refactoring safety
- Faster than traditional type checkers

---

### pytest - Testing

Run unit tests and coverage reports.
```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=model2data --cov-report=html

# Run specific test file
uv run pytest tests/test_generation.py

# Run specific test
uv run pytest tests/test_generation.py::test_some_function
```

---

## CI/CD Checks

The GitHub Actions workflow runs three jobs:

1. **Lint Job** (runs once on Python 3.12)
   - `ruff check` — Catches style and error issues
   - `ruff format --check` — Ensures consistent formatting
   - `ty check` — Checks for type errors

2. **Test Job** (runs on Python 3.10-3.14)
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
uv run ruff format

# Fix linting issues
uv run ruff check --fix

# Type check
uv run ty check model2data/

# Run tests
uv run pytest --cov=model2data
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

### Issue: Ruff formatting issues

**Solution**: Just run the formatter:
```bash
uv run ruff format
```

### Issue: ty reports type errors

**Solution**: Fix the type annotations in your code. ty will give you specific line numbers and error messages.

### Issue: "Pre-commit hooks not running"

**Solution**: Make sure you ran:
```bash
pre-commit install
```

### Issue: "Lint passes locally but fails in CI"

**Solution**: Make sure you're running all the checks:
```bash
uv run ruff check --show-fixes
uv run ruff format --check
uv run ty check model2data/
```

---

## IDE Integration

### VS Code

Install these extensions for a better development experience:

1. **Pylance** (Python language server)
   - Real-time type checking
   - Autocomplete
   - Go to definition

2. **Ruff**
   - `charliermarsh.ruff`
   - Real-time linting with quick fixes
   - Auto-format on save

Settings (`.vscode/settings.json`):
```json
{
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": "explicit",
      "source.fixAll": "explicit"
    }
  }
}
```

### PyCharm

1. **Settings → Tools → Python Integrated Tools**
   - Default test runner: pytest

2. **Settings → Tools → Ruff**
   - Enable Ruff integration for linting and formatting

---

## Documentation

For more information:

- **uv**: https://docs.astral.sh/uv/
- **Ruff**: https://docs.astral.sh/ruff/
- **ty**: https://docs.astral.sh/ty/
- **pytest**: https://docs.pytest.org/
- **Pre-commit**: https://pre-commit.com/

---

## Questions?

If you have questions about development setup or code quality tools:
1. Check the tool's documentation (linked above)
2. Open an issue on GitHub
3. Ask in a discussion thread
