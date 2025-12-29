# Contributors

Thank you to everyone who has contributed to BlueBubbles Linux!

## Maintainers

- **Nick** - Creator and lead maintainer

## Contributors

<!--
Add your name here when you contribute!
Format: - **Name** (@github-username) - Description of contribution
-->

## How to Contribute

We welcome contributions! Here's how you can help:

1. **Report bugs** - Open an issue describing the problem
2. **Suggest features** - Open an issue with your idea
3. **Submit code** - Fork, make changes, and open a pull request
4. **Improve docs** - Help improve the README or add documentation
5. **Test** - Try the app and report issues

### Development Setup

```bash
git clone https://github.com/yourusername/bluebubbles-linux.git
cd bluebubbles-linux
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Code Style

- We use `ruff` for linting
- We use `mypy` for type checking
- Run `ruff check src/` and `mypy src/` before submitting

### Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run linting and type checking
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## Acknowledgments

Special thanks to:

- The [BlueBubbles](https://bluebubbles.app/) team for creating the amazing server
- The GTK and GNOME teams for the excellent toolkit
- The gtk4-layer-shell developers for Wayland integration
