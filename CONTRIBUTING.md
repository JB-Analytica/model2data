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
3. Install [uv](https://docs.astral.sh/uv/), then run `uv sync --extra dev`.
4. Run tests: `uv run pytest`

See [DEVELOPMENT.md](DEVELOPMENT.md) for the full local dev setup, linting/type-checking, and the release process.

## Pull Request Process

1. Run `uv run poe check` (formats, lints, type-checks, and runs the test suite).
2. Add tests for new functionality.
3. Update documentation if needed.
4. Submit a PR with a clear description of the changes — CI must pass before it can be merged.

## Code Style

- Formatting and linting are enforced by [ruff](https://docs.astral.sh/ruff/) (`uv run ruff format`, `uv run ruff check`).
- Type-checked with [ty](https://docs.astral.sh/ty/) (`uv run ty check model2data/`).
- Write descriptive commit messages.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
