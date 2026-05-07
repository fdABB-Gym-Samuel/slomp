{
  description = "Songless multiplayer backend";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    treefmt-nix.url = "github:numtide/treefmt-nix";
    treefmt-nix.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      treefmt-nix,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        python = pkgs.python312.withPackages (
          ps: with ps; [
            fastapi
            uvicorn
            websockets
            httpx
            pydantic
            pydantic-settings
            python-dotenv
            redis
            mutagen
            pydub
            argon2-cffi
            asyncpg
          ]
        );

        treefmtEval = treefmt-nix.lib.evalModule pkgs ./treefmt.nix;
      in
      {
        formatter = treefmtEval.config.build.wrapper;

        checks.formatting = treefmtEval.config.build.check self;

        devShells.default = pkgs.mkShell {
          packages = [
            python
            pkgs.ruff
            pkgs.postgresql_18
            pkgs.dbmate
            pkgs.valkey
            pkgs.ffmpeg-headless
            pkgs.bun
            pkgs.gnumake
            treefmtEval.config.build.wrapper
          ];

          shellHook = ''
            echo "songless dev shell — python $(python --version | cut -d' ' -f2)"
            export PGDATA="$PWD/.pg"
            export PGHOST=/tmp
            export PGPORT=5432
            export PGDATABASE=slomp
            export VALKEY_URL="redis://127.0.0.1:6379/0"
          '';
        };
      }
    );
}
