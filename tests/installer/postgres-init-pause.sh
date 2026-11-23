#!/bin/sh
# Disposable regression only; never mounted by application deployments.
set -eu
touch /tmp/noc119-initializing
for attempt in $(seq 1 60); do
    if test -f /tmp/noc119-initialization-release; then exit 0; fi
    sleep 1
done
exit 1
