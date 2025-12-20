# Contributing to model2data

Thank you for your interest in contributing to model2data! We welcome contributions from the community.

## Ways to Contribute

- **Bug Reports**: Open an issue on GitHub with a clear description of the problem.
- **Feature Requests**: Suggest new features or improvements via GitHub issues.
- **Code Contributions**: Submit pull requests with fixes or enhancements.
- **Documentation**: Improve README, add examples, or create tutorials.
- **Testing**: Add tests or help with testing on different platforms.

## Development Setup

1. Fork the repository on GitHub.
2. Clone your fork: `git clone https://github.com/JB-Analytica/model2data.git`
3. Install [UV](https://github.com/astral-sh/uv) (recommended) or use Python 3.10+
4. Set up the development environment:
   ```bash
   # With UV (recommended)
   uv venv                    # Create virtual environment
   source .venv/bin/activate  # Activate on macOS/Linux
   # or `.venv\Scripts\activate` on Windows
   uv pip install -e ".[dev]"

   # Or with pip
   python -m venv .venv
   source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows
   pip install -e ".[dev]"
   ```
5. Install pre-commit hooks: `pre-commit install`
6. Run tests: `pytest` or `poe test`

## Pull Request Process

1. Ensure your code follows the project's style (we use Ruff for linting and formatting).
2. Add tests for new functionality.
3. Update documentation if needed.
4. Ensure all tests pass: `poe all` or `poe check && poe test`
5. Submit a PR with a clear description of the changes.

## Code Style

- We use **Ruff** for linting and formatting (not Black).
- We use **ty** for type checking (fast Rust-based type checker from Astral).
- Follow PEP 8 guidelines.
- Type hints are encouraged (using modern `X | Y` syntax for Python 3.10+).
- Write descriptive commit messages.
- Run `poe fix` to auto-fix formatting and linting issues.
- Run `poe typecheck` to check types, or `poe check` to run linting, type checking, and typos.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
