# NixOS module: the OpenTapoVac daemon (`opentapovac serve`).
#
#   imports = [ "${opentapovac-src}/nix/module.nix" ];
#   services.opentapovac = {
#     enable = true;
#     credentialsFile = "/etc/opentapovac/credentials.yaml";  # user: / pass:, outside the store
#     settings = { robot.host = "10.47.128.10"; timezone = "Europe/Oslo"; rooms = { ... }; };
#   };
#
# The daemon binds to localhost; put a reverse proxy with authentication in
# front of it (docs/design.md §6).  Everything in `settings` ends up in the
# world-readable store: keep the Tapo password in `credentialsFile`.
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.opentapovac;
  yaml = pkgs.formats.yaml { };
  credentialsPath = "/run/credentials/opentapovac.service/credentials.yaml";
  configFile = yaml.generate "opentapovac.yaml" (
    lib.recursiveUpdate cfg.settings {
      robot.credentials = credentialsPath;
      listen = cfg.listen;
      state_dir = "/var/lib/opentapovac";
      cache_dir = "/var/cache/opentapovac";
    }
  );
in
{
  options.services.opentapovac = {
    enable = lib.mkEnableOption "the OpenTapoVac daemon";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ./package.nix { }";
    };

    listen = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1:8765";
      description = "HOST:PORT for the web page and the HTTP API.";
    };

    credentialsFile = lib.mkOption {
      # a string, not types.path: a path literal would copy the password into the store
      type = lib.types.str;
      example = "/etc/opentapovac/credentials.yaml";
      description = ''
        Absolute path, outside the Nix store, of a YAML file with the Tapo
        account, `user:` and `pass:`.  Read by systemd (LoadCredential), so it
        can stay root-only.
      '';
    };

    settings = lib.mkOption {
      type = yaml.type;
      default = { };
      description = ''
        The config file (docs/design.md §7, README): robot.host, timezone,
        defaults, rooms, order, presets, ...  `robot.credentials`, `listen`,
        `state_dir` and `cache_dir` are set by this module.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.hasPrefix "/" cfg.credentialsFile && !lib.hasPrefix builtins.storeDir cfg.credentialsFile;
        message = "services.opentapovac.credentialsFile must be an absolute path outside the Nix store";
      }
    ];
    environment.systemPackages = [ cfg.package ];
    # the CLI on the same host talks to the daemon
    environment.variables.OPENTAPOVAC_CONFIG = "${configFile}";

    systemd.services.opentapovac = {
      description = "OpenTapoVac daemon";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      environment.OPENTAPOVAC_CONFIG = "${configFile}";
      serviceConfig = {
        ExecStart = "${lib.getExe cfg.package} serve";
        DynamicUser = true;
        StateDirectory = "opentapovac";
        CacheDirectory = "opentapovac";
        LoadCredential = "credentials.yaml:${cfg.credentialsFile}";
        Restart = "on-failure";
        RestartSec = 10;
        # hardening; the daemon needs the network and its two directories only
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        NoNewPrivileges = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
      };
    };
  };
}
