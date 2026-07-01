_default:
    @just --list

# Build qudi-iqo-modules
build:
    @nix build

# Run qudi with GUI
qudi:
    @nix run

# Run qudi without GUI
qudi-headless:
    @nix run . -- -g

# Run a Jupyter notebook server for the given directory
notebook DIR='.':
    @nix develop -c jupyter notebook --notebook-dir={{DIR}}

# Check the flake
check:
    @nix flake check

# Format the project
fmt:
    @nix fmt

# Lint the project
lint:
    @nix run .#lint-project

# Run local CI
ci: check fmt lint build
