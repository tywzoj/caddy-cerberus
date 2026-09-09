# caddy-cerberus

A standalone Nix flake for Caddy with the [SJTUG Cerberus](https://github.com/SJTUG/cerberus)
proof-of-work protection plugin. It consumes **`pkgs.caddy.withPlugins`** from
nixpkgs: no nixpkgs fork, custom Caddy derivation, or runtime `caddy add-package`.

| Component | Pin |
| --- | --- |
| Supported system | `x86_64-linux` |
| Caddy | `2.11.4` |
| Cerberus | `v0.4.8` |
| nixpkgs | `e7e7984e947e6f41ceae21727cd74aa5fa269648` |

The Go module uses the canonical lowercase path
`github.com/sjtug/cerberus@v0.4.8`, even though the organization is styled SJTUG.
This release includes its generated web assets; no frontend build is needed.
Both `packages.x86_64-linux.caddy-cerberus` and the default package provide the
custom `caddy` executable.

## Local build and verification

Install Nix and enable flakes in `~/.config/nix/nix.conf` (or `/etc/nix/nix.conf`):

```ini
experimental-features = nix-command flakes
```

From this repository:

```bash
nix flake check
nix build
./result/bin/caddy version
./result/bin/caddy list-modules | grep -i cerberus
```

The named package can also be built with `nix build .#caddy-cerberus`.
The version must report Caddy **2.11.4**. nixpkgs supplies a custom version
prefix, so the output can start with `2.11.4 v2.11.4`.
Module output must include:

```text
cerberus
http.handlers.cerberus
http.handlers.cerberus_endpoint
```

`nix flake check` runs the binary and checks both HTTP handlers and the exact
Caddy/Cerberus module versions in `caddy build-info`, not just the displayed
version prefix. nixpkgs' own plugin installation checks remain enabled.
A missing module or unexpected version fails the check and CI.

## Production installation

On an `x86_64-linux` server with Nix and flakes enabled:

```bash
nix profile install github:<OWNER>/caddy-cerberus
caddy version
caddy list-modules | grep -i cerberus
```

Replace `<OWNER>` with `tywzoj`, or the owner of your fork. On newer Nix
versions, `nix profile add` is the preferred spelling of `nix profile install`.
Ensure the installing user's Nix profile `bin` directory is on `PATH`; use
`command -v caddy` to confirm that an older system installation is not selected.
Remove any conflicting plain Caddy package from that profile first.

For reproducible deployments, select a reviewed release tag or full commit
rather than implicitly following the default branch:

```bash
nix profile install github:tywzoj/caddy-cerberus/v1.0.0
```

`v1.0.0` is an example: use a tag that has actually been published.
Profile installation does not create or restart a system service. Point your
existing service at the chosen profile's `bin/caddy`, preserve its state/data
directories, and restart it deliberately after validating your Caddyfile.
Cerberus is compiled in but not enabled automatically; configure its endpoint
and middleware following the [pinned upstream example](https://github.com/SJTUG/cerberus/blob/v0.4.8/Caddyfile).

The server does not need Go, Node.js, or xcaddy installed, and must not use
`caddy add-package` or `caddy upgrade` to modify the immutable Nix binary.
Without a configured cache containing this package, Nix may build it locally
and fetch build tools into the Nix store automatically. Use a populated binary
cache if the server must only download prebuilt binaries.

## Binary cache

The default NixOS binary cache supplies available nixpkgs dependencies.
No additional cache or credentials are required to build.

To enable an optional **public Cachix cache**:

1. Create a cache at Cachix.
2. Set the GitHub repository Actions variable `CACHIX_CACHE` to its name.
3. Set the Actions secret `CACHIX_AUTH_TOKEN` to a cache-scoped write token.

CI can pull from the configured public cache without a token. Only non-PR
builds with a token push, and only **after validation succeeds**. Fork PRs never
receive cache credentials and never publish. Missing credentials skip
publication rather than failing the build; an attempted push failure fails
the job. Hosted Cachix signing is used; do not commit signing keys or tokens.
If you later use a self-managed signing key, keep it in Actions secrets too.

Configure production Nix with the cache's URL and **public** signing key from
the Cachix dashboard, retaining the default substituter/key:

```ini
extra-substituters = https://<CACHE>.cachix.org
extra-trusted-public-keys = <CACHE>.cachix.org-1:<PUBLIC-KEY>
```

For multi-user Nix, an administrator should put this in `/etc/nix/nix.conf`
and restart the Nix daemon. `cachix use <CACHE>` is an alternative if Cachix
is installed. Never install a cache signing private key or write token on the
production server. Private caches additionally need read-only authentication
and are not covered by this public-cache setup.

```text
Git push/tag -> GitHub Actions -> nix build + checks -> Cachix
                                                         |
                                      server nix profile installation
```

The workflows do not upload raw `/nix/store` contents or Nix closures as
GitHub Actions artifacts or release assets.

## GitHub Actions and releases

- **CI** (`.github/workflows/ci.yml`) runs on branch pushes and pull requests,
  on `ubuntu-latest`. It installs Nix, enables `nix-command flakes`, optionally
  configures Cachix, runs `nix flake check` and `nix build`, and verifies the
  resulting binary. `--no-update-lock-file` prevents CI from silently changing
  the dependency lock. Nix build logs and a failure annotation identify errors.
- **Release** (`.github/workflows/release.yml`) runs for tags matching
  `v*.*.*`, such as `v1.0.0`. It reuses the CI job, including validation and
  optional cache publishing, then creates a GitHub Release containing the
  package release version, Caddy version, supported system, and installation
  command. A failed build/check/cache push prevents release creation.
  No tags need to be created until releases are wanted.
- Actions are pinned to commit SHAs. Build jobs have read-only repository
  permissions; only the release publishing job has `contents: write`.

## Reviewed upgrades

Do not update dependencies automatically on production. Propose updates in a
pull request, review the pins and generated hashes, and merge only after CI
passes.

### Caddy / nixpkgs and `flake.lock`

1. Choose and inspect a nixpkgs revision with the desired Caddy version and
   `caddy.withPlugins` support.
2. Change the nixpkgs revision in `flake.nix`. If intentionally changing Caddy,
   update `expectedCaddyVersion`, the flake description, and this README.
   The version assertion prevents accidental Caddy upgrades.
3. Regenerate the lock with `nix flake update` (or `nix flake update nixpkgs`).
   The input URL itself is revision-pinned, so **`nix flake update` alone does
   not advance nixpkgs**.
4. Recompute the plugin source hash as described below, run all verification
   commands, and commit both `flake.nix` and the Nix-generated `flake.lock`.

### Cerberus and the plugin source hash

1. Choose a released tag compatible with the intended Caddy version. Check its
   `go.mod` and ensure generated templates/web assets are included.
2. Update `cerberusVersion` in `flake.nix` and the version table above.
3. Temporarily set the `withPlugins` `hash` to `pkgs.lib.fakeHash`, then run
   `nix build --print-build-logs`.
4. Nix should report a fixed-output hash mismatch for
   `caddy-src-with-plugins-…`. Replace the placeholder with the reported
   `got: sha256-…` value and rebuild. An unrelated network/compiler failure is
   not a hash result; resolve it first. Never merge the placeholder hash.
5. Run `nix flake check`, `nix build`, and the binary verification commands.
   Commit the real hash with the version change.

This hash covers the entire generated vendored Caddy/plugin source tree, not
just the Cerberus archive. Recalculate it when either dependency or its build
toolchain changes. Cerberus is not a flake input, so a Cerberus-only update need
not change `flake.lock`; all nixpkgs changes require a regenerated lock.

### Deploying an approved update

After reviewing and merging an update, let CI populate the cache before
updating the server. Use `nix profile list` to find the installed entry.
For an entry following the repository's default branch, explicitly run
`nix profile upgrade <ENTRY>` when ready. A tag/commit-pinned entry does not
track newer releases: deliberately replace it with the next reviewed tag or
commit using `nix profile remove <ENTRY>` and the installation command above.
Verify the binary and configuration, then restart the service. Keep the
previous profile generation available for `nix profile rollback`.

## License

This repository is MIT licensed; see [LICENSE](LICENSE). Caddy, Cerberus, and
their dependencies retain their respective upstream licenses.
