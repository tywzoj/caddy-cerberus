{
  description = "Caddy 2.11.9 with the Cerberus plugin for x86_64-linux";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/4fe25e12eb9f4c744966dd3de5a05246cf098601";

  outputs =
    { nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      expectedCaddyVersion = "2.11.9";
      cerberusVersion = "v0.4.9";
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

      checks.${system} = {
        caddy-cerberus = pkgs.runCommand "caddy-cerberus-check" {
          nativeBuildInputs = [ caddy-cerberus ];
        } ''
          version="$(caddy version)"
          echo "$version"
          reported="''${version%% *}"
          if [ "''${reported#v}" != "${expectedCaddyVersion}" ]; then
            echo "Expected Caddy ${expectedCaddyVersion}, got: $version" >&2
            exit 1
          fi

          caddy build-info > build-info.txt
          cat build-info.txt
          awk '$1 == "dep" && $2 == "github.com/caddyserver/caddy/v2" && $3 == "v${expectedCaddyVersion}" { found = 1 }
            END { exit !found }' build-info.txt
          awk '$1 == "dep" && $2 == "github.com/sjtug/cerberus" && $3 == "${cerberusVersion}" { found = 1 }
            END { exit !found }' build-info.txt

          caddy list-modules > modules
          grep -i cerberus modules
          grep -Fx 'cerberus' modules
          grep -Fx 'http.handlers.cerberus' modules
          grep -Fx 'http.handlers.cerberus_endpoint' modules
          touch "$out"
        '';

        runtime = pkgs.runCommand "caddy-cerberus-runtime-check" {
          nativeBuildInputs = [ caddy-cerberus pkgs.curl ];
        } ''
          timeout 90s bash ${./tests/runtime.sh} ${./examples/Caddyfile}
          touch "$out"
        '';
      };
    };
}
