#!/bin/sh
# Docker demo entrypoint: loads the generated local secrets, then runs the command.
set -e
if [ -f /config/demo.env ]; then
  set -a
  . /config/demo.env
  set +a
fi
exec "$@"
