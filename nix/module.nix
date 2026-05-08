{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.slomp;
  inherit (lib)
    mkOption
    mkEnableOption
    mkIf
    mkDefault
    types
    ;

  defaultBackend = self.packages.${pkgs.stdenv.hostPlatform.system}.slomp-backend or null;
  defaultFrontend = self.packages.${pkgs.stdenv.hostPlatform.system}.slomp-frontend or null;
  defaultMigrations = self.packages.${pkgs.stdenv.hostPlatform.system}.slomp-migrations or null;
in
{
  options.services.slomp = {
    enable = mkEnableOption "slomp multiplayer Songless game";

    package = mkOption {
      type = types.package;
      default = defaultBackend;
      defaultText = lib.literalExpression "slomp.packages.\${system}.slomp-backend";
      description = "Backend (FastAPI) package.";
    };

    frontendPackage = mkOption {
      type = types.package;
      default = defaultFrontend;
      defaultText = lib.literalExpression "slomp.packages.\${system}.slomp-frontend";
      description = ''
        Frontend package. Must contain a static SPA build at
        $out/share/slomp-frontend (with index.html as the SPA fallback).
      '';
    };

    migrationsPackage = mkOption {
      type = types.package;
      default = defaultMigrations;
      defaultText = lib.literalExpression "slomp.packages.\${system}.slomp-migrations";
      description = "Package containing dbmate migrations under $out/migrations.";
    };

    user = mkOption {
      type = types.str;
      default = "slomp";
      description = "System user the backend runs as.";
    };

    group = mkOption {
      type = types.str;
      default = "slomp";
      description = "System group the backend runs as.";
    };

    stateDir = mkOption {
      type = types.str;
      default = "/var/lib/slomp";
      description = "Working directory for the backend service.";
    };

    backend = {
      host = mkOption {
        type = types.str;
        default = "127.0.0.1";
        description = "Bind address for the FastAPI/uvicorn process.";
      };

      port = mkOption {
        type = types.port;
        default = 8000;
        description = "Bind port for the FastAPI/uvicorn process.";
      };

      sessionCookieSecure = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Whether session cookies should be marked Secure (HTTPS-only).
          Disable only if you are serving over plain HTTP for testing.
        '';
      };

      corsOrigins = mkOption {
        type = types.listOf types.str;
        default = [ ];
        example = [ "https://slomp.example.com" ];
        description = ''
          Extra CORS origins to allow. Empty by default since the bundled
          nginx vhost serves the frontend on the same origin as the API.
        '';
      };

      extraEnvironment = mkOption {
        type = types.attrsOf types.str;
        default = { };
        description = "Extra environment variables for the backend service.";
      };

      environmentFile = mkOption {
        type = types.nullOr types.path;
        default = null;
        description = ''
          Path to an EnvironmentFile (key=value lines) for the backend
          systemd unit. Useful for secrets you do not want in the Nix store.
        '';
      };
    };

    database = {
      createLocally = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Whether to enable a local PostgreSQL service and provision the
          slomp database/user. Set to false to point the backend at an
          existing Postgres instance via `database.url`.
        '';
      };

      name = mkOption {
        type = types.str;
        default = "slomp";
        description = "Postgres database name.";
      };

      user = mkOption {
        type = types.str;
        default = "slomp";
        description = "Postgres role used by the backend.";
      };

      url = mkOption {
        type = types.str;
        description = "DATABASE_URL passed to backend and dbmate.";
      };
    };

    valkey = {
      createLocally = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Whether to enable a local Valkey (Redis-compatible) instance.
          Set to false to point the backend at an existing instance via
          `valkey.url`.
        '';
      };

      url = mkOption {
        type = types.str;
        default = "redis://127.0.0.1:6379/0";
        description = "VALKEY_URL passed to the backend.";
      };
    };

    nginx = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Whether to configure an nginx vhost that serves the frontend
          static files and reverse-proxies the API and WebSocket to the
          backend.
        '';
      };

      hostName = mkOption {
        type = types.str;
        default = "slomp.localhost";
        description = "Server name for the nginx vhost.";
      };

      enableACME = mkOption {
        type = types.bool;
        default = false;
        description = "Enable ACME (Let's Encrypt) for the vhost.";
      };

      forceSSL = mkOption {
        type = types.bool;
        default = false;
        description = "Redirect plain HTTP to HTTPS for the vhost.";
      };
    };
  };

  config = mkIf cfg.enable (
    lib.mkMerge [
      {
        services.slomp.database.url = mkDefault (
          if cfg.database.createLocally then
            "postgres://${cfg.database.user}@/${cfg.database.name}?host=/run/postgresql&sslmode=disable"
          else
            throw "services.slomp.database.url must be set when services.slomp.database.createLocally = false"
        );

        users.users = mkIf (cfg.user == "slomp") {
          slomp = {
            isSystemUser = true;
            group = cfg.group;
            home = cfg.stateDir;
            description = "slomp service user";
          };
        };

        users.groups = mkIf (cfg.group == "slomp") {
          slomp = { };
        };
      }

      (mkIf cfg.database.createLocally {
        services.postgresql = {
          enable = true;
          ensureDatabases = [ cfg.database.name ];
          ensureUsers = [
            {
              name = cfg.database.user;
              ensureDBOwnership = true;
            }
          ];
        };
      })

      (mkIf cfg.valkey.createLocally {
        services.redis = {
          package = pkgs.valkey;
          servers.slomp = {
            enable = true;
            port = 6379;
            bind = "127.0.0.1";
          };
        };
      })

      {
        systemd.services.slomp-migrate = {
          description = "slomp database migrations";
          wantedBy = [ "multi-user.target" ];
          after = lib.optional cfg.database.createLocally "postgresql.service";
          requires = lib.optional cfg.database.createLocally "postgresql.service";

          environment = {
            DATABASE_URL = cfg.database.url;
          };

          serviceConfig = {
            Type = "oneshot";
            User = cfg.user;
            Group = cfg.group;
            ExecStart = "${pkgs.dbmate}/bin/dbmate --migrations-dir ${cfg.migrationsPackage}/migrations --no-dump-schema up";
            RemainAfterExit = true;
          };
        };

        systemd.services.slomp-backend = {
          description = "slomp backend (FastAPI)";
          wantedBy = [ "multi-user.target" ];
          after = [
            "network.target"
            "slomp-migrate.service"
          ]
          ++ lib.optional cfg.database.createLocally "postgresql.service"
          ++ lib.optional cfg.valkey.createLocally "redis-slomp.service";
          requires = [ "slomp-migrate.service" ];

          environment = {
            DATABASE_URL = cfg.database.url;
            VALKEY_URL = cfg.valkey.url;
            SESSION_COOKIE_SECURE = if cfg.backend.sessionCookieSecure then "true" else "false";
          }
          // lib.optionalAttrs (cfg.backend.corsOrigins != [ ]) {
            CORS_ORIGINS = builtins.toJSON cfg.backend.corsOrigins;
          }
          // cfg.backend.extraEnvironment;

          serviceConfig = {
            User = cfg.user;
            Group = cfg.group;
            WorkingDirectory = cfg.stateDir;
            StateDirectory = "slomp";
            ExecStart = "${cfg.package}/bin/slomp-backend --host ${cfg.backend.host} --port ${toString cfg.backend.port}";
            Restart = "on-failure";
            RestartSec = 5;
            EnvironmentFile = lib.optional (cfg.backend.environmentFile != null) cfg.backend.environmentFile;

            NoNewPrivileges = true;
            PrivateTmp = true;
            ProtectSystem = "strict";
            ProtectHome = true;
            ReadWritePaths = [ cfg.stateDir ];
            PrivateDevices = true;
            ProtectKernelTunables = true;
            ProtectKernelModules = true;
            ProtectControlGroups = true;
            RestrictAddressFamilies = [
              "AF_INET"
              "AF_INET6"
              "AF_UNIX"
            ];
            LockPersonality = true;
            MemoryDenyWriteExecute = false;
          };
        };
      }

      (mkIf cfg.nginx.enable {
        services.nginx = {
          enable = true;
          recommendedProxySettings = true;
          recommendedGzipSettings = true;
          recommendedOptimisation = true;

          virtualHosts.${cfg.nginx.hostName} = {
            enableACME = cfg.nginx.enableACME;
            forceSSL = cfg.nginx.forceSSL;
            root = "${cfg.frontendPackage}/share/slomp-frontend";

            locations."/" = {
              tryFiles = "$uri $uri/ /index.html";
            };

            # Reverse-proxy backend HTTP routes and the /rooms/{code}/ws
            # WebSocket. Frontend SPA routes use /room (singular), so there is
            # no collision with the backend's /rooms (plural) prefix.
            locations."~ ^/(auth|me|spotify|health|rooms)(/|$)" = {
              proxyPass = "http://${cfg.backend.host}:${toString cfg.backend.port}";
              proxyWebsockets = true;
              extraConfig = ''
                proxy_read_timeout 3600s;
                proxy_send_timeout 3600s;
              '';
            };
          };
        };
      })
    ]
  );
}
