#!/usr/bin/env python3
"""Prepare a validated upstream update; the workflow owns commits and publication."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys


VERSION = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
FAKE_HASH = "sha256-" + "A" * 43 + "="


def version(value):
    match = VERSION.fullmatch(value)
    if not match:
        raise ValueError(f"Not a stable SemVer version: {value!r}")
    return tuple(map(int, match.groups()))


def latest_tag(tags):
    stable = [tag for tag in tags if VERSION.fullmatch(tag)]
    if not stable:
        raise ValueError("No stable version tags found")
    return max(stable, key=version)


def bump_level(old, new):
    before, after = version(old), version(new)
    if after < before:
        raise ValueError(f"Refusing version downgrade: {old} -> {new}")
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right:
            return 3 - index
    return 0


def next_version(current, changes):
    level = max(bump_level(old, new) for old, new in changes)
    if not level:
        raise ValueError("Cannot create a release without a version change")
    parts = list(version(current))
    index = 3 - level
    parts[index] += 1
    parts[index + 1:] = [0] * (2 - index)
    return "v" + ".".join(map(str, parts))


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def api_items(endpoint):
    pages = json.loads(run("gh", "api", "--paginate", "--slurp", endpoint))
    return [item for page in pages for item in page]


def upstream_version(repository):
    return latest_tag(
        item["name"] for item in api_items(f"repos/{repository}/tags?per_page=100")
    )


def read_pin(text, name):
    matches = re.findall(rf'^\s*{name} = "([^"]+)";$', text, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {name} pin")
    return matches[0]


def replace_pin(text, name, value):
    old = read_pin(text, name)
    return text.replace(f'{name} = "{old}";', f'{name} = "{value}";', 1)


def pins(text):
    return read_pin(text, "expectedCaddyVersion"), read_pin(text, "cerberusVersion")


def update_readme(text, repository, tag, caddy, cerberus, revision):
    blocks = {
        "PINS": (
            "| Component | Pin |\n"
            "| --- | --- |\n"
            f"| Package release | `{tag}` |\n"
            "| Supported system | `x86_64-linux` |\n"
            f"| Caddy | `{caddy}` |\n"
            f"| Cerberus | `{cerberus}` |\n"
            f"| nixpkgs | `{revision}` |\n\n"
            f"[cerberus-example]: https://github.com/SJTUG/cerberus/blob/{cerberus}/Caddyfile"
        ),
        "INSTALL": f"```bash\nnix profile install github:{repository}/{tag}\n```",
    }
    for name, content in blocks.items():
        start = f"<!-- BEGIN GENERATED {name} -->"
        end = f"<!-- END GENERATED {name} -->"
        if text.count(start) != 1 or text.count(end) != 1:
            raise ValueError(f"Expected exactly one README {name} block")
        pattern = rf"^{re.escape(start)}\n.*?^{re.escape(end)}$"
        text, count = re.subn(
            pattern, lambda _: f"{start}\n{content}\n{end}", text,
            flags=re.MULTILINE | re.DOTALL,
        )
        if count != 1:
            raise ValueError(f"Malformed README {name} block")
    return text


def plugin_hash(log):
    matches = re.findall(
        r"hash mismatch in fixed-output derivation "
        r"'[^'\n]*/[^/'\n]*caddy-src-with-plugins-[^/'\n]+\.drv':\s*"
        r"specified:\s*" + re.escape(FAKE_HASH) + r"\s*"
        r"got:\s*(sha256-[A-Za-z0-9+/]{43}=)",
        log,
    )
    if len(matches) != 1:
        raise ValueError("Expected one plugin source hash mismatch; inspect the Nix log")
    return matches[0]


def regenerate_hash(path):
    path.write_text(replace_pin(path.read_text(), "hash", FAKE_HASH))
    result = subprocess.run(
        [
            "nix", "build", ".#packages.x86_64-linux.caddy-cerberus.src",
            "--no-link", "--no-update-lock-file", "--print-build-logs",
            "--log-format", "raw",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(result.stdout, flush=True)
    if result.returncode == 0:
        raise ValueError("Nix unexpectedly accepted the placeholder source hash")
    actual = plugin_hash(result.stdout)
    path.write_text(replace_pin(path.read_text(), "hash", actual))


def output(tag="", changed=False):
    print(f"Release tag: {tag or '(none)'}; dependency changes: {changed}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write(f"tag={tag}\nchanged={str(changed).lower()}\n")


def prepare():
    repository = os.environ["GITHUB_REPOSITORY"]
    tags = run("git", "tag", "--merged", "HEAD").splitlines()
    current_tag = latest_tag(tag for tag in tags if tag.startswith("v"))
    releases = {
        release["tag_name"]
        for release in api_items(f"repos/{repository}/releases?per_page=100")
        if not release["draft"] and not release["prerelease"]
    }
    # A previous run may have pushed its tag but failed before publishing.
    if current_tag not in releases:
        output(current_tag)
        return

    path = Path("flake.nix")
    original = path.read_text()
    old_caddy, old_cerberus = pins(original)
    caddy = max(
        (old_caddy, upstream_version("caddyserver/caddy")), key=version
    ).removeprefix("v")
    cerberus = "v" + max(
        (old_cerberus, upstream_version("sjtug/cerberus")), key=version
    ).removeprefix("v")
    if (caddy, cerberus) == (old_caddy, old_cerberus):
        output()
        return

    released_caddy, released_cerberus = pins(run("git", "show", f"{current_tag}:flake.nix"))
    tag = next_version(
        current_tag, [(released_caddy, caddy), (released_cerberus, cerberus)]
    )
    if tag in run("git", "tag", "--list").splitlines():
        raise ValueError(f"Release tag already exists outside this history: {tag}")

    updated = replace_pin(original, "expectedCaddyVersion", caddy)
    updated = replace_pin(updated, "cerberusVersion", cerberus)
    updated = updated.replace(f"Caddy {old_caddy} with", f"Caddy {caddy} with")
    old_url = read_pin(original, r"inputs\.nixpkgs\.url")
    revision = old_url.rsplit("/", 1)[1]

    if caddy != old_caddy:
        revision = json.loads(run("gh", "api", "repos/NixOS/nixpkgs/commits/master"))["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Invalid nixpkgs commit")
        url = f"github:NixOS/nixpkgs/{revision}"
        packaged = run(
            "nix", "eval", "--raw",
            f"{url}#legacyPackages.x86_64-linux.caddy.version",
        )
        if packaged != caddy:
            raise ValueError(
                f"Upstream Caddy is {caddy}, but nixpkgs has {packaged}; "
                "retry after nixpkgs packages the release"
            )
        updated = updated.replace(old_url, url)

    readme = update_readme(
        Path("README.md").read_text(), repository, tag, caddy, cerberus, revision
    )
    path.write_text(updated)
    Path("README.md").write_text(readme)
    if caddy != old_caddy:
        subprocess.run(["nix", "flake", "update", "nixpkgs"], check=True)
    regenerate_hash(path)
    subprocess.run(
        ["nix", "flake", "check", "--no-update-lock-file", "--print-build-logs"],
        check=True,
    )
    subprocess.run(
        ["nix", "build", "--no-update-lock-file", "--print-build-logs"], check=True
    )
    output(tag, changed=True)


if __name__ == "__main__":
    try:
        prepare()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"Upstream update failed: {error}", file=sys.stderr)
        sys.exit(1)
