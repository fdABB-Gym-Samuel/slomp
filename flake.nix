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
    let
      # Hash for the bun-installed node_modules FOD. Whenever bun.lock
      # changes, rebuild .#slomp-frontend; bun is run --frozen-lockfile so
      # the FOD is reproducible. The first build after a lockfile change
      # will fail with "got: sha256-..." — copy that value here.
      nodeModulesHash = "sha256-fm9MHZim1oXUoQdaoz35A/ftff7Z+K6bC2iBdToil5c=";

      perSystem = flake-utils.lib.eachDefaultSystem (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};

          slomp-backend = pkgs.callPackage ./nix/backend.nix { };
          slomp-frontend = pkgs.callPackage ./nix/frontend.nix { inherit nodeModulesHash; };
          slomp-migrations = pkgs.callPackage ./nix/migrations.nix { };

          treefmtEval = treefmt-nix.lib.evalModule pkgs ./treefmt.nix;
        in
        {
          formatter = treefmtEval.config.build.wrapper;

          checks.formatting = treefmtEval.config.build.check self;

          packages = {
            inherit slomp-backend slomp-frontend slomp-migrations;
            default = slomp-backend;
          };

          devShells.default = pkgs.mkShell {
            packages = [
              slomp-backend.passthru.pythonEnv
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
    in
    perSystem
    // {
      nixosModules.slomp = import ./nix/module.nix { inherit self; };
      nixosModules.default = self.nixosModules.slomp;
    };
}
