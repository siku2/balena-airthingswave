#!/bin/bash

# Optimize shell for safety.
set -o errexit -o noclobber -o nounset -o pipefail

cd /usr/src/app

timeout 600 \
    python -m airthingswave-mqtt config.yaml > /proc/1/fd/1 2>&1
