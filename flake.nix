{
  description = "Songless multiplayer backend";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        python = pkgs.python312.withPackages (ps: with ps; [
          fastapi
          uvicorn
          websockets
          httpx
          pydantic
          pydantic-settings
          python-dotenv
        ]);
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.ruff
          ];

          shellHook = ''
            echo "songless dev shell — python $(python --version | cut -d' ' -f2)"
          '';
        };
      });
}
