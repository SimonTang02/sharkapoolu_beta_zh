#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "用法：$0 /absolute/or/relative/path/to/resume.tex" >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "$script_dir/.." && pwd)
document=$(realpath -- "$1")
document_dir=$(dirname -- "$document")
private_cv_root="$project_root/private_data/cv"

if [[ "$document_dir" == "$private_cv_root/source" ]]; then
  output_dir="$private_cv_root/build"
else
  output_dir="$document_dir/build"
fi

mkdir -p -- "$output_dir"
tex_bin="$project_root/.TinyTeX/bin/x86_64-linux"
export PATH="$tex_bin:$PATH"
export TEXINPUTS="$project_root/cv/latex:$document_dir:${TEXINPUTS:-}"

engine=-pdf
if rg -q '^% !TEX program = xelatex' "$document"; then
  engine=-xelatex
fi

exec latexmk \
  -cd \
  "$engine" \
  -interaction=nonstopmode \
  -halt-on-error \
  -synctex=1 \
  -file-line-error \
  -outdir="$output_dir" \
  "$document"
