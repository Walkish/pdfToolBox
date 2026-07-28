#!/bin/bash
# Print the uv version pinned in [tool.uv] required-version of a pyproject.toml.

sed -n '/^\[tool\.uv\]/,/^\[/ {
    /required-version/ {
        s/.*[=:][[:space:]]*["'"'"']\([0-9][0-9.]*\)["'"'"'].*/\1/p
        q
    }
}' "$1"
