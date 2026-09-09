{
  description = "Caddy 2.11.4 with the Cerberus plugin for x86_64-linux";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/e7e7984e947e6f41ceae21727cd74aa5fa269648";

  outputs =
    { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      expectedCaddyVersion = "2.11.4";
      cerberusVersion = "v0.4.8";
      caddy-cerberus =
        assert pkgs.lib.assertMsg (pkgs.caddy.version == expectedCaddyVersion)
          "Review the Caddy version before updating nixpkgs.";
        pkgs.caddy.withPlugins {
          plugins = [ "github.com/sjtug/cerberus@${cerberusVersion}" ];
          hash = pkgs.lib.fakeHash;
        };
    in
    {
      packages.${system} = {
        inherit caddy-cerberus;
        default = caddy-cerberus;
      };

      checks.${system}.caddy-cerberus = pkgs.runCommand "caddy-cerberus-check" {
        nativeBuildInputs = [ caddy-cerberus ];
      } ''
        version="$(caddy version)"
        echo "$version"
        case "$version" in
          v${expectedCaddyVersion}|v${expectedCaddyVersion}\ *) ;;
          *) echo "Expected Caddy v${expectedCaddyVersion}, got: $version" >&2; exit 1 ;;
        esac

        caddy build-info > build-info
        cat build-info
        awk '$1 == "dep" && $2 == "github.com/caddyserver/caddy/v2" && $3 == "v${expectedCaddyVersion}" { found = 1 }
          END { exit !found }' build-info
        awk '$1 == "dep" && $2 == "github.com/sjtug/cerberus" && $3 == "${cerberusVersion}" { found = 1 }
          END { exit !found }' build-info

        caddy list-modules > modules
        grep -i cerberus modules
        grep -Fx 'http.handlers.cerberus' modules
        grep -Fx 'http.handlers.cerberus_endpoint' modules
        touch "$out"
      '';
    };
}
