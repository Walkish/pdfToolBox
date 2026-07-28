#!/bin/bash

############################################################################################
# Checks that the local machine has everything this project needs: uv and the Python
# toolchain from pyproject.toml, plus the external binaries the toolbox shells out to.
#
# Ghostscript and poppler are not Python dependencies and uv cannot install them, so a
# missing one otherwise shows up as a runtime failure on the first upload rather than here.
############################################################################################

PYTHON_VERSION=$1
UV_VERSION=$2

# Summary of commands to be run by the user if the checks fail
commands_to_run=""

run_check() {
  # Check if the command is successful
  #  $1: Message to print
  #  $2: Command to run (the actual check)
  #  $3: Instruction for the user if the check fails
  echo ">>> $1"
  if ! eval "$2" &> /dev/null; then
      echo "FAILED"
      commands_to_run+="- $3\n"
  else
      echo "PASSED"
  fi
  echo ""
}

# Check uv installation
run_check "Checking uv installation..." \
          "which uv" \
          "uv is not installed. Run 'make update-uv' (or 'curl -LsSf https://astral.sh/uv/$UV_VERSION/install.sh | sh')."

# Check the specific python version installation
run_check "Checking python $PYTHON_VERSION installation..." \
          "uv python find $PYTHON_VERSION" \
          "Python $PYTHON_VERSION is not installed through uv. Run 'uv python install $PYTHON_VERSION'."

# Ghostscript does the compression; without it the app refuses to start.
# Windows names the console executable gswin64c (gswin32c on a 32-bit install)
# and ships no `gs` at all, so all three names are accepted here.
run_check "Checking Ghostscript installation..." \
          "which gs || which gswin64c || which gswin32c" \
          "Ghostscript is not installed. Run 'brew install ghostscript' (macOS) or 'choco install ghostscript' (Windows)."

# poppler supplies pdfimages/pdftoppm/pdftotext/pdfinfo: resolution measurement and previews.
for binary in pdfimages pdftoppm pdftotext pdfinfo; do
  run_check "Checking poppler ($binary)..." \
            "which $binary" \
            "poppler is not installed ($binary missing). Run 'brew install poppler' (macOS) or 'choco install poppler' (Windows)."
done

# Print the summary of commands the user needs to run (if any)
if [ -n "$commands_to_run" ]; then
  echo "Some checks failed. Run the following to fix them:"
  echo -e "$commands_to_run"
  exit 1
fi

echo "All checks passed."
