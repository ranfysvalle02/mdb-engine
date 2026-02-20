#!/bin/bash
# ==============================================================================
# Download MongoDB sample_mflix dataset (one-time)
# ==============================================================================
#
# Downloads the official MongoDB sample dataset archive to the local directory.
# The Member app's on_startup (or demo seed) handles restoring sample_mflix
# into MongoDB automatically -- this script just gets the file locally.
#
# The archive is ~350 MB and contains sample_mflix plus other sample databases.
#
# Usage:
#   ./download-sample-dataset.sh
# ==============================================================================

set -euo pipefail

ARCHIVE_URL="https://atlas-education.s3.amazonaws.com/sampledata.archive"
ARCHIVE_FILE="sampledata.archive"

echo "=== Download MongoDB Sample Dataset ==="
echo ""

# ---- Check for curl ----
if ! command -v curl &>/dev/null; then
    echo "ERROR: curl is not installed."
    exit 1
fi

# ---- Download if not already present ----
if [ -f "$ARCHIVE_FILE" ] && [ -s "$ARCHIVE_FILE" ]; then
    echo "Already downloaded: $ARCHIVE_FILE ($(du -h "$ARCHIVE_FILE" | cut -f1))"
    echo "Delete it and re-run to re-download."
else
    rm -f "$ARCHIVE_FILE"
    echo "Downloading sample dataset (~350 MB)..."
    echo ""
    curl -fSL --progress-bar -o "$ARCHIVE_FILE" "$ARCHIVE_URL"
    echo ""
    echo "Download complete: $(du -h "$ARCHIVE_FILE" | cut -f1)"
fi

echo ""
echo "Archive saved to: $(pwd)/$ARCHIVE_FILE"
echo ""
echo "To restore into MongoDB manually:"
echo "  mongorestore --uri=\"mongodb://admin:password@localhost:27017/?authSource=admin&directConnection=true\" \\"
echo "    --archive=$ARCHIVE_FILE --drop --nsInclude=\"sample_mflix.*\""
echo ""
echo "Or just use the Member app's 'Seed Demo Data' button -- it creates"
echo "sample movies without needing the full dataset."
