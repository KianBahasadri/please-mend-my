#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
ROOT_DIR="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
cd "$ROOT_DIR"

if ! command -v pdflatex &>/dev/null; then
    echo "Error: pdflatex is not installed or not in PATH." >&2
    exit 1
fi

echo "Compiling process_document.tex..."
pdflatex -interaction=nonstopmode -halt-on-error process_document.tex >/dev/null
# Second pass for hyperref cross-references
pdflatex -interaction=nonstopmode -halt-on-error process_document.tex >/dev/null

# Clean up auxiliary build files
rm -f process_document.aux process_document.log process_document.out

PAGES=$(pdfinfo process_document.pdf | awk '/^Pages:/ {print $2}')
WORDS=$(pdftotext process_document.pdf - | wc -w)

echo "Successfully built process_document.pdf!"
echo "  • Pages: $PAGES"
echo "  • Word Count: $WORDS words (max: 1000)"
