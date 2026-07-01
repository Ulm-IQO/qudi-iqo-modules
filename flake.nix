{
  description = "Qudi iqo modules development flake";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    qudi-core = {
      url = "github:SparrowQuantum/qudi-core-sparrow";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = {
    nixpkgs,
    flake-utils,
    qudi-core,
    ...
  }:
    flake-utils.lib.eachDefaultSystem (
      system: let
        pkgs = nixpkgs.legacyPackages.${system};
        inherit (pkgs) lib;

        # Used to retrieve the correct zaber-motion wheel for each platform:
        zaberWheels = {
          x86_64-darwin = {
            url = "https://files.pythonhosted.org/packages/23/19/50da32c76e3541557a45a9748c694833f9bee350469c02ab2c9461b23c84/zaber_motion-9.3.0-py3-none-macosx_10_4_universal2.whl";
            sha256 = "sha256-ZgLQdw+rLT17g4H8e9VBq6f9iO+QTUKlZhDZQtyuHn0=";
          };
          aarch64-darwin = {
            url = "https://files.pythonhosted.org/packages/23/19/50da32c76e3541557a45a9748c694833f9bee350469c02ab2c9461b23c84/zaber_motion-9.3.0-py3-none-macosx_10_4_universal2.whl";
            sha256 = "sha256-ZgLQdw+rLT17g4H8e9VBq6f9iO+QTUKlZhDZQtyuHn0=";
          };
          x86_64-linux = {
            url = "https://files.pythonhosted.org/packages/f4/71/53d59a7aa3cbc0f174fff2566317c79541406846dc107bc7735eead6cf4c/zaber_motion-9.3.0-py3-none-manylinux_2_27_x86_64.whl";
            sha256 = "sha256-yauP6djabb1GZkL2M2F1arpInOnKVTEjTfZQKOHA2Ps=";
          };
          aarch64-linux = {
            url = "https://files.pythonhosted.org/packages/67/b0/65a2a03e3c6387a401a05065b9ab7458a06aec35fb156bf5e1888fddf4f6/zaber_motion-9.3.0-py3-none-manylinux_2_27_aarch64.whl";
            sha256 = "sha256-hpVx62ZppvGvC9tfAkpFAERw8lScBcq9jgw1MNuivHs=";
          };
        };

        pythonOverrides = _: super: rec {
          # nitypes dep is not in nixpkgs, but we need it for nidaqmx 1.5.0
          nitypes = super.buildPythonPackage rec {
            pname = "nitypes";
            version = "1.0.1";
            pyproject = true;

            src = pkgs.fetchPypi {
              inherit pname version;
              sha256 = "sha256-3CCpjACWUa358naewVzb6Tk3W60vJUZ2WrvCOYIwGos=";
            };

            build-system = with super; [poetry-core];

            dependencies = with super; [
              numpy
              hightime
              typing-extensions
            ];
          };

          #  nidaqmx version override due to outdated nixpkgs version
          nidaqmx = super.nidaqmx.overridePythonAttrs (old: rec {
            version = "1.5.0";
            src = pkgs.fetchFromGitHub {
              owner = "ni";
              repo = "nidaqmx-python";
              tag = version;
              hash = "sha256-S5lTz6hH8WCS9QNlT18k7UEGvJCNUx57oYbl3vPKD6E=";
            };
            dependencies =
              old.dependencies
              ++ [
                nitypes
                super.typing-extensions
              ];
          });

          # lxml version override due to outdated nixpkgs version
          lxml = super.lxml.overridePythonAttrs (_: rec {
            version = "6.1.1";
            src = pkgs.fetchFromGitHub {
              owner = "lxml";
              repo = "lxml";
              tag = "lxml-${version}";
              hash = "sha256-SRJaegK4PxgK0rdILVp3J92VnjPmExiD2AuMLoGQIbA=";
            };
            postPatch = ''
              substituteInPlace pyproject.toml \
                --replace-fail 'Cython>=3.2.4' 'Cython'
            '';
          });

          # pyvisa version override due to outdated nixpkgs version
          pyvisa = super.pyvisa.overridePythonAttrs (_: rec {
            version = "1.16.2";
            src = pkgs.fetchFromGitHub {
              owner = "pyvisa";
              repo = "pyvisa";
              tag = version;
              hash = "sha256-wxWva02nKkuFjralzVIrVTXfDHEeBYihckUcj8p44/k=";
            };
          });

          # Zaber-motion not in nixpkgs, but we need it for the main module
          zaber-motion = super.buildPythonPackage rec {
            pname = "zaber-motion";
            version = "9.3.0";
            format = "wheel";
            src = pkgs.fetchurl {
              url = zaberWheels.${system}.url;
              sha256 = zaberWheels.${system}.sha256;
            };

            build-system = with super; [
              setuptools
              wheel
            ];

            dependencies = with super; [reactivex];
          };

          # Disable flaky tests
          watchfiles = super.watchfiles.overridePythonAttrs (_: {doCheck = false;});
          jupytext = super.jupytext.overridePythonAttrs (_: {doCheck = false;});
        };

        qudiLib = qudi-core.lib.${system};
        qudiCoreOverrides = qudiLib.pythonOverrides;
        python = pkgs.python313.override {
          packageOverrides = lib.composeExtensions pythonOverrides qudiCoreOverrides;
        };

        qudiCore = qudiLib.mkQudiCore {inherit pkgs python;};

        mkQudiIqoModules = {
          pkgs,
          python,
        }:
          python.pkgs.buildPythonPackage {
            pname = "qudi-iqo-modules";
            version = lib.head (lib.strings.splitString "\n" (builtins.readFile ./VERSION));
            pyproject = true;
            src = ./.;

            build-system = with python.pkgs; [
              setuptools
              setuptools-scm
              wheel
            ];

            dependencies = with python.pkgs; [
              qudiCore
              fysom
              entrypoints
              lmfit
              lxml
              matplotlib
              nidaqmx
              numpy
              pyqtgraph
              pyside6
              pyvisa
              scipy
              zaber-motion
            ];
          };

        qudiIqoModules = mkQudiIqoModules {inherit pkgs python;};
        qudiEnv = python.withPackages (_: [qudiIqoModules]);

        fmtPackage = pkgs.writeShellScriptBin "fmt" ''
          ${pkgs.alejandra}/bin/alejandra . --quiet
        '';

        lintPackage = pkgs.writeShellScriptBin "lint-project" ''
          ${pkgs.deadnix}/bin/deadnix .
        '';
      in {
        packages = {
          default = qudiIqoModules;
          qudi-iqo-modules = qudiIqoModules;
        };

        lib = {
          pythonOverrides = pythonOverrides;
          mkQudiIqoModules = mkQudiIqoModules;
        };

        apps = {
          default = {
            type = "app";
            program = "${qudiEnv}/bin/qudi";
            meta = {
              description = "Launch Qudi";
            };
          };
          lint-project = {
            type = "app";
            program = "${lintPackage}/bin/lint-project";
            meta = {
              description = "Run deadnix on the project";
            };
          };
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pkgs.ruff
            pkgs.alejandra
            pkgs.deadnix
            pkgs.just
            pkgs.uv
            pkgs.which
            pkgs.gh
            pkgs.fd
            qudiEnv
          ];
        };

        formatter = fmtPackage;
      }
    );
}
