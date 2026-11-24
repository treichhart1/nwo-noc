#!/bin/sh
# Read-only checks; no Redis connection, configuration or persisted-state changes.
set -eu
test "$REDIS_VERSION" = "7.4.12"
case "$(redis-server --version)" in
  "Redis server v=7.4.12 "*) ;;
  *) echo "Unexpected Redis7 server version" >&2; exit 1 ;;
esac
test "$(id -u redis)" = "999"
test "$(id -g redis)" = "1000"
for requirement in libcrypto3:3.5.9-r1 libssl3:3.5.9-r1 zlib:1.4-r0; do
  package=${requirement%%:*}
  minimum=${requirement#*:}
  installed=$(apk info -v | sed -n "s/^$package-//p")
  test -n "$installed"
  case "$(apk version -t "$installed" "$minimum")" in
    '='|'>') ;;
    *) echo "Supported Redis runtime library fix missing" >&2; exit 1 ;;
  esac
  printf '%s=%s\n' "$package" "$installed"
done
redis-server --version
redis-cli --version
