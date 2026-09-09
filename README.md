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

The additional `checks.x86_64-linux.runtime` check validates and starts
[`examples/Caddyfile`](examples/Caddyfile) on loopback, then checks:

- `/healthz` stays public and returns exactly `ok`.
- An unauthenticated protected request gets the Cerberus HTML challenge and
  `X-Cerberus-Status: CHALLENGE`, never the origin response.
- The challenge page's embedded CSS is available through the Cerberus endpoint.
- An empty verification POST is rejected with HTTP 400 and `FAIL`, without an
  approval cookie; using GET on the verification endpoint returns HTTP 404.

Cerberus v0.4.8 returns **HTTP 200** for a challenge, so checking only the status
code is insufficient. This smoke test does not solve proof-of-work or exercise
browser JavaScript; the successful approval flow is part of the deployment
rehearsal below. It uses temporary state, bounded requests, and process cleanup;
no TLS, external upstream, or runtime network downloads are needed.

Run just this check with `nix build .#checks.x86_64-linux.runtime`.

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

### systemd deployment example

[`examples/caddy-cerberus.service`](examples/caddy-cerberus.service) is for a
non-NixOS `x86_64-linux` systemd host with multi-user Nix. It uses a dedicated,
root-managed **build profile**, separate from users' `nix profile` installations.
Do not manage this dedicated profile with `nix profile install/remove`.

First configure the public cache as described below. Choose an actual reviewed
release tag or full commit, and install without allowing local or remote builds:

```bash
REV='<reviewed-tag-or-full-commit>'
sudo nix build "github:tywzoj/caddy-cerberus/$REV" \
  --profile /nix/var/nix/profiles/caddy-cerberus --no-link \
  --option max-jobs 0 --option builders "" --option fallback false
```

A cache miss fails rather than compiling on the server. Evaluation may still
download the locked nixpkgs source. If intentionally building locally instead,
omit the three `--option` arguments.

From a checkout of that same revision, provision the service and configuration
(reuse an existing `caddy` user only after checking it has a `caddy` group):

```bash
getent passwd caddy || sudo useradd --system --user-group \
  --home-dir /var/lib/caddy-cerberus --shell /usr/sbin/nologin caddy
sudo install -d -o root -g caddy -m 0750 /etc/caddy-cerberus
sudo install -d -o caddy -g caddy -m 0700 /var/lib/caddy-cerberus
sudo install -o root -g caddy -m 0640 examples/Caddyfile /etc/caddy-cerberus/Caddyfile
sudo install -o root -g root -m 0644 examples/caddy-cerberus.service \
  /etc/systemd/system/caddy-cerberus.service
sudo systemd-analyze verify /etc/systemd/system/caddy-cerberus.service
sudo systemctl daemon-reload
sudo systemctl enable --now caddy-cerberus
curl --noproxy '*' -fsS http://127.0.0.1:18080/healthz
```

The example is deliberately loopback-only and returns a placeholder protected
response. Before exposing it, replace `http://127.0.0.1:18080` with your real
hostname, remove `bind 127.0.0.1`, and replace `respond "Protected origin" 200`
with your upstream handler, keeping it **after** `cerberus` inside `route`.
Configure DNS and allow ports 80/443 for Caddy's automatic HTTPS. Keep the
`/.cerberus/*` endpoint outside protection; `/healthz` is intentionally public.
Do not add a browser-only User-Agent matcher, which would let other clients
bypass protection. Behind a proxy, configure Caddy's trusted proxies only for
the actual proxy addresses and prevent direct access around that proxy.

The unit runs as `caddy`, grants only the low-port binding capability, and keeps
configuration and the package profile read-only to the service. Its persistent
HOME/XDG directories are under `/var/lib/caddy-cerberus`; retain and back them
up, especially Caddy's TLS storage. Cerberus state and its default generated
signing key are in memory: restarting can require clients to solve a new
challenge. The `16MiB` Cerberus cache budget is suitable for the example, not a
hard process memory limit; size it for production traffic. If configuring a
signing-key file, provision it separately with restricted service-readable
permissions, never in Git or the Nix store.

Before every deliberate restart, validate as the service user using the same
state paths (the unit also validates on startup):

```bash
sudo -u caddy env HOME=/var/lib/caddy-cerberus \
  XDG_DATA_HOME=/var/lib/caddy-cerberus/data \
  XDG_CONFIG_HOME=/var/lib/caddy-cerberus/config \
  /nix/var/nix/profiles/caddy-cerberus/bin/caddy validate \
  --config /etc/caddy-cerberus/Caddyfile --adapter caddyfile
sudo systemctl restart caddy-cerberus
sudo journalctl -u caddy-cerberus -n 50 --no-pager
```

The admin API is disabled, so use restart, not `caddy reload`. Stop or reconfigure
any existing web service before binding its ports.

For upgrades, back up the Caddyfile, repeat the pinned `nix build --profile`
command with the approved new revision, validate, and restart. Each successful
build updates the profile atomically and retains a previous generation. If
validation fails, do not restart: restore the profile and any changed config.
To roll back this **build profile**:

```bash
sudo nix-env --profile /nix/var/nix/profiles/caddy-cerberus --list-generations
sudo nix-env --profile /nix/var/nix/profiles/caddy-cerberus --rollback
```

Restore the matching Caddyfile backup, validate again, and restart. Do not delete
old generations or garbage-collect them until the deployment is accepted.
Package rollback does not roll back configuration or mutable state.

## Binary cache

The default NixOS binary cache supplies available nixpkgs dependencies.
No additional cache or credentials are required to build.

To enable an optional **public Cachix cache**:

1. Create a cache at Cachix.
2. Set the GitHub repository Actions variable `CACHIX_CACHE` to its name.
3. Set the Actions secret `CACHIX_AUTH_TOKEN` to a cache-scoped write token.

CI only builds and validates: it can pull from the configured public cache
without a token, but never receives cache write credentials or publishes,
including on pushes to `main` and when called by Release. Only the tag-triggered
**Release** workflow publishes, and only **after validation succeeds**. Its
dedicated `publish-cache` job realises the same package from the tagged commit
and checks its output path against CI's validated output before pushing. The
write token is provided only to that publishing step, not to build commands.
Missing credentials skip publication rather than failing the release; an
attempted push failure blocks release creation. Hosted Cachix signing is used;
do not commit signing keys or tokens. If you later use a self-managed signing
key, keep it in Actions secrets too.

After a successful cache push, Release's separate `verify-cache` job starts on
a fresh runner without checkout, a write token, or a preinstalled copy of the custom package.
It fetches the exact output path from the build job, with local and remote
builders disabled, verifies the installed store path, and runs the downloaded
binary's version/module checks. A missing closure or download failure blocks
release creation. If publication is skipped, this job is also skipped and the
GitHub Release can still be created: that is **not** proof that the package is
available from a cache.

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
PR / main push -> CI -> build + checks (read-only cache access)
Release tag -> CI validation -> publish-cache -> verify-cache -> GitHub Release
                                    |
                                  Cachix -> server installation
```

The workflows do not upload raw `/nix/store` contents or Nix closures as
GitHub Actions artifacts or release assets.

## GitHub Actions and releases

- **CI** (`.github/workflows/ci.yml`) runs on pushes to `main` and pull requests
  targeting `main` (including subsequent commits), on `ubuntu-latest`.
  Feature-branch pushes do not trigger a separate CI run. `workflow_call`
  remains available for the tag release workflow.
  It installs Nix, enables `nix-command flakes`, optionally
  configures read-only Cachix access, runs `nix flake check` and `nix build`, and
  verifies the resulting binary. It never publishes or accepts cache write
  credentials. `--no-update-lock-file` prevents CI from silently changing
  the dependency lock. Nix build logs and a failure annotation identify errors.
- **Release** (`.github/workflows/release.yml`) runs for tags matching
  `v*.*.*`, such as `v1.0.0`. It calls CI for validation without passing secrets,
  then runs its own optional cache publication and download verification jobs.
  Finally it creates a GitHub Release containing the
  package release version, Caddy version, supported system, and installation
  command. A failed build/check/cache push/download verification prevents release creation.
  No tags need to be created until releases are wanted.
- Actions are pinned to commit SHAs. Build jobs have read-only repository
  permissions; only the release publishing job has `contents: write`.

### Release and deployment rehearsal

Use a test server before production; the workflow cannot verify a server it
cannot access. Do not treat these steps as already completed:

1. Review and merge the intended commit. Confirm its CI checks, including the
   HTTP runtime check, pass. Ordinary CI does not populate the cache.
2. Choose an unused release version and tag that exact reviewed commit. Push
   the tag only when ready to publish; the existing release workflow repeats
   validation and creates the release after success. Merely passing branch CI
   does not publish a release or cache. For download-only deployment, require
   a successful `verify-cache` job in this release run, not a skipped job.
3. On a clean test server configured with the public cache, install that exact
   tag using the download-only systemd instructions above. Record the commit,
   resolved store path, service status, and configuration revision.
4. Verify `/healthz`, then visit a protected route with a fresh browser session.
   Confirm the challenge loads its JS/WASM assets, completes, and reaches the
   intended origin; verify an invalid submission never reaches the origin.
   The automated HTTP smoke test intentionally does not claim this browser
   coverage.
5. After a second reviewed version is available, rehearse a pinned upgrade and
   rollback to the previous profile/configuration. Recheck health and browser
   approval after each restart, and review the service logs before production.

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

After reviewing and merging an update, publish a reviewed release tag and wait
for Release's cache publication and download verification before a download-only
server update. Ordinary CI does not populate the cache.
Use `nix profile list` to find the installed entry.
For an entry following the repository's default branch, explicitly run
`nix profile upgrade <ENTRY>` when ready. A tag/commit-pinned entry does not
track newer releases: deliberately replace it with the next reviewed tag or
commit using `nix profile remove <ENTRY>` and the installation command above.
Verify the binary and configuration, then restart the service. Keep the
previous profile generation available for `nix profile rollback`.

## License

This repository is MIT licensed; see [LICENSE](LICENSE). Caddy, Cerberus, and
their dependencies retain their respective upstream licenses.
