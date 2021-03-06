# Developer setup

The following steps highlight the developer install process.


## Setup tools

If you are a developer then we strongly suggest installing into a virtual environment to prevent
overwriting the production version of ksconf and for the installation of the developer tools.  (The
virtualenv name `ksconfdev-pyve` is used below, but this can be whatever suites, just make sure not
to commit it.
.)

    # Setup and activate virtual environment
    virtualenv ksconfdev-pyve
    . ksconfdev-pyve/bin/activate

    # Install developer packages
    pip install -r requirements-dev.txt


## Install ksconf

    git clone https://github.com/Kintyre/ksconf.git
    cd ksconf
    pip install .

## Building the docs

    cd ksconf
    . ksconfdev-pyve/bin/activate

    cd docs
    make html
    open build/html/index.html


If you'd like to build PDF, then you'll need some extra tools.  On Mac, you may also want to install
the following (for building docs, and the like):

    brew install homebrew/cask/mactex-no-gui

(Doh!  Still doesn't work, instructions are incomplete for mac latex, ....)


# Contributing back

Pull requests are greatly welcome!  If you plan on contributing code back to the main `ksconf` repo,
please follow the standard GitHub fork and pull-request work-flow.  We also ask that you enable a
set of git hooks to help safeguard against avoidable issues.

## Pre-commit hook

The ksconf project uses the [pre-commit][pre-commit] hook to enable the following checks:

 * Fixes trailing whitespace, EOF, and EOLs
 * Confirms python code compiles (AST)
 * Blocks the committing of large files and keys
 * Rebuilds the CLI docs.  (Eventually to be replaced with an argparse Sphinx extension)
 * Confirms that all Unit test pass.  (Currently this is the same tests also run by Travis CI, but
   since test complete in under 5 seconds, the run-everywhere approach seems appropriate for now.
   Eventually, the local testing will likely become a subset of the full test suite.)

Note that this repo both uses pre-commit for it's own validation (as discussed here) and provides a
pre-commit hook service to other repos.  This way repositories housing Splunk apps can, for example,
use 'ksconf --check' or `ksconf --sort` against their own `.conf` files for validation purposes.

### Installing the pre-commit hook

To run ensure you changes comply with the ksconf coding standards, please install and activate
[pre-commit][pre-commit].

Install:

    sudo pip install pre-commit

    # Register the pre-commit hooks (one time setup) 
    cd ksconf
    pre-commit install --install-hooks


## Install gitlint

Gitlint will check to ensure that commit messages are in compliance with the standard subject,
empty-line, body format.  You can enable it with:

    gitlint install-hook



[gitlint]: https://jorisroovers.github.io/gitlint/
[pre-commit]: https://pre-commit.com/
