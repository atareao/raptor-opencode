#!/usr/bin/env bash
#
# install.sh - thin installer wrapper for raptor-opencode.
#
# Installs the adapter (pipx when available, a local .venv otherwise) and then
# delegates every argument verbatim to `raptor-opencode install`.
#
# Usage: ./install.sh [--ref TAG] [--scope global|project] [--project-dir DIR]
#                      [--merge-config] [--skip-deps] [--dry-run] [--yes]
#                      [--raptor-home DIR]
set -euo pipefail

readonly PROG=${0##*/}
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)
readonly ORIG_CWD=$PWD
PYTHON_BIN=${PYTHON:-python3}
readonly MIN_MINOR=11

usage() {
	cat <<'EOF'
Install raptor-opencode and run `raptor-opencode install`.

Usage: ./install.sh [OPTIONS]

Options:
  --scope SCOPE        where to install: global (default) or project
  --project-dir DIR    project directory for --scope project (default: the
                       current working directory, never this repository)
  --ref TAG            RAPTOR git ref to install (default: v3.1.0)
  --merge-config       merge the fragment into the scope's config file
                       (global: opencode.jsonc; project: ./opencode.json)
  --skip-deps          let the CLI skip RAPTOR's virtualenv and requirements
  --dry-run            print the install plan only, change nothing
  --yes                do not prompt
  --raptor-home DIR    RAPTOR checkout (default: $RAPTOR_HOME or ~/.local/share/raptor)
  -h, --help           show this help and exit

Unknown options are forwarded untouched to `raptor-opencode install`.
Set PYTHON=/path/to/python3 to pick a specific interpreter (>= 3.11).

`--scope project` keeps agents, commands, plugins, skills and the manifest
inside the project directory; only the shared virtualenv lives under
$XDG_DATA_HOME. The project config file (`./opencode.json`) is merged only
with --merge-config.

The wrapper never installs RAPTOR's Python requirements itself: the CLI does
that in its own venv, unless --skip-deps is given.
EOF
}

die() {
	printf '%s: error: %s\n' "$PROG" "$*" >&2
	exit 1
}

note() {
	printf '%s: %s\n' "$PROG" "$*" >&2
}

has_flag() {
	local needle=$1
	shift
	local arg
	for arg in "$@"; do
		[[ $arg == "$needle" ]] && return 0
	done
	return 1
}

# --- argument pre-flight -----------------------------------------------------
# --scope/--project-dir are validated here because --scope project changes how
# the CLI is invoked (the project directory must survive the `cd` below).
SCOPE=global
PROJECT_DIR=""
project_dir_given=0
expect=""
for arg in "$@"; do
	case $expect in
	scope)
		SCOPE=$arg
		expect=""
		continue
		;;
	project-dir)
		PROJECT_DIR=$arg
		project_dir_given=1
		expect=""
		continue
		;;
	esac
	case $arg in
	-h | --help)
		usage
		exit 0
		;;
	--scope=*) SCOPE=${arg#*=} ;;
	--scope) expect=scope ;;
	--project-dir=*)
		PROJECT_DIR=${arg#*=}
		project_dir_given=1
		;;
	--project-dir)
		expect=project-dir
		project_dir_given=1
		;;
	esac
done

case $SCOPE in
global | project) ;;
*) die "invalid --scope '$SCOPE'; expected global or project" ;;
esac

if [[ $SCOPE == project && -z $PROJECT_DIR ]]; then
	PROJECT_DIR=$ORIG_CWD
fi

extra=()
if [[ $SCOPE == project ]]; then
	[[ -d $PROJECT_DIR ]] || die "--project-dir $PROJECT_DIR is not a directory"
	if [[ $project_dir_given -eq 0 ]]; then
		extra+=(--project-dir "$PROJECT_DIR")
	fi
fi

# --- python >= 3.11 ----------------------------------------------------------
command -v "$PYTHON_BIN" >/dev/null 2>&1 ||
	die "python3 not found; install Python 3.${MIN_MINOR}+ and retry (or set PYTHON=...)"

python_version=$("$PYTHON_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') ||
	die "cannot run $PYTHON_BIN"
if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
	die "Python 3.11+ required, found $python_version ($PYTHON_BIN)"
fi

[[ -f $ROOT/pyproject.toml ]] ||
	die "pyproject.toml not found in $ROOT; run this script from the repository checkout"

# --- dry-run: nothing to install, the sources are right here -----------------
if has_flag --dry-run "$@"; then
	note "dry run: skipping adapter installation, printing the plan"
	(cd "$ROOT" && "$PYTHON_BIN" -m raptor_opencode install "$@" "${extra[@]+"${extra[@]}"}")
	exit $?
fi

# --- install the adapter -----------------------------------------------------
if command -v pipx >/dev/null 2>&1; then
	note "installing raptor-opencode with pipx"
	(cd "$ROOT" && pipx install --force .)
	if BIN=$(command -v raptor-opencode); then
		note "raptor-opencode available at $BIN"
	elif [[ -x ${HOME:-/nonexistent}/.local/bin/raptor-opencode ]]; then
		BIN="$HOME/.local/bin/raptor-opencode"
		note "raptor-opencode installed at $BIN (add ~/.local/bin to PATH: pipx ensurepath)"
	else
		die "pipx finished but raptor-opencode is not on PATH; run 'pipx ensurepath' and retry"
	fi
else
	note "pipx not found, using a local virtualenv at $ROOT/.venv"
	VENV="$ROOT/.venv"
	if [[ ! -x $VENV/bin/python ]]; then
		"$PYTHON_BIN" -m venv "$VENV" ||
			die "cannot create $VENV; install python3-venv (Debian/Ubuntu) and retry"
	else
		note "reusing existing virtualenv"
	fi
	(cd "$ROOT" && "$VENV/bin/python" -m pip install --quiet -e .) ||
		die "pip install -e . failed in $VENV; check your network or index settings"
	BIN="$VENV/bin/raptor-opencode"
	[[ -x $BIN ]] || die "entry point $BIN is missing; re-run $PROG"
fi

# --- delegate to the CLI -----------------------------------------------------
note "running: $BIN install $* (scope $SCOPE${PROJECT_DIR:+, project $PROJECT_DIR})"
status=0
"$BIN" install "$@" "${extra[@]+"${extra[@]}"}" || status=$?

if [[ $status -eq 0 ]]; then
	note "done. Restart OpenCode, then run: raptor-opencode doctor --scope $SCOPE"
else
	note "install failed with exit code $status; re-run with --dry-run to inspect the plan"
fi
exit "$status"
