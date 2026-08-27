{
  description = "Canon CCAPI Home Assistant integration — dev shell";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
  let
    systems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ];
    forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
  in {
    devShells = forAllSystems (pkgs: {
      default = pkgs.mkShell {
        packages = with pkgs; [
          python313
          uv
          ruff

          # The justfile is this repo's entry point, and its deploy recipes
          # shell out to rsync over ssh. Pinning them here means a clone plus
          # `direnv allow` is enough - no assumptions about the host system.
          just
          rsync
          openssh

          # CCAPI speaks JSON; jq is what makes poking at a camera by hand
          # bearable next to tools/dump_capabilities.py.
          jq
        ];

        shellHook = ''
          # Let uv use the interpreter from nixpkgs instead of downloading one.
          export UV_PYTHON="${pkgs.python313}/bin/python"
          export UV_PYTHON_DOWNLOADS=never
          export UV_PROJECT_ENVIRONMENT="$PWD/.venv"

          # Home Assistant imports the integration as custom_components.canon_ccapi
          export PYTHONPATH="$PWD''${PYTHONPATH:+:$PYTHONPATH}"

          if ! uv sync --quiet; then
            echo "canon-ccapi: 'uv sync' failed (offline?). Run it manually once you have network."
          fi

          if [ -f .venv/bin/activate ]; then
            source .venv/bin/activate
          fi

          echo "canon-ccapi dev shell — tests: pytest | lint: just lint | deploy: just sync"
        '';
      };
    });

    # `nix flake check` runs the linter the same way `just lint` does, against
    # the committed tree rather than the working copy. Lint only: `ruff format`
    # is deliberately not gated here, so reformatting stays a deliberate act
    # (`just fmt`) instead of something CI demands file by file.
    checks = forAllSystems (pkgs: {
      lint = pkgs.runCommand "canon-ccapi-ruff" { nativeBuildInputs = [ pkgs.ruff ]; } ''
        cd ${self}
        ruff check --no-cache .
        touch $out
      '';
    });

    formatter = forAllSystems (pkgs: pkgs.nixpkgs-fmt);
  };
}
