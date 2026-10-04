#!/usr/bin/env bash
set -e
source src/app.sh
[ "$(greet)" = "hello" ]
