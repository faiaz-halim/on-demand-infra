#!/bin/bash
set -e

# Check if directory argument is provided
if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <terraform_directory>"
    exit 1
fi

TERRAFORM_DIR="$1"

# Run tfsec in a Docker container
docker run --rm -v "$(pwd):/src" aquasec/tfsec:latest --minimum-severity=HIGH --format=json --out=results.json "/src/$TERRAFORM_DIR"

# The exit code of the docker command will be the exit code of the script
exit $?
