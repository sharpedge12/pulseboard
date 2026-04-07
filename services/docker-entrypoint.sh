#!/bin/sh
# Ensure the uploads directory tree is writable by appuser.
# Named Docker volumes are created as root — this script runs as root
# at container start to fix ownership, then drops to appuser.

set -e

# Fix ownership of the uploads directory (created by named volume as root)
if [ -d /app/uploads ]; then
    chown -R appuser:appgroup /app/uploads
fi

# Create subdirectories if they don't exist and set ownership
mkdir -p /app/uploads/avatars /app/uploads/draft /app/uploads/thread /app/uploads/post /app/uploads/message
chown -R appuser:appgroup /app/uploads

# Drop privileges and exec the real command.
# Use 'su-exec' if available, otherwise fall back to 'su'.
# We install gosu in the Dockerfiles for reliable privilege dropping.
exec gosu appuser "$@"
