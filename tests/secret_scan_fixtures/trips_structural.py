"""Fixtures that MUST trip the structural rules in `scripts/check_no_secrets_in_diff.py`.

EVERY VALUE IN THIS FILE IS CONSTRUCTED AND FAKE. Each one matches the SHAPE of a real credential -- that
is the whole point, a fixture that did not would test nothing -- and none of them is or ever was a valid
credential for anything. No value from this project's history appears here: the 2026-09-11 Telegram token
is revoked but permanently published, and copying it into a file to test a secret scanner would be the same
mistake in a new place. The guard's own tests assert that none of these values is ever printed.

This directory is exempt from the guard's repo scan by name (`FIXTURE_DIR_NAME`), so nothing here can fail
the real build. The consequence is stated in the guard's docstring: a real secret hidden in this directory
would not be seen. It is five short files and they are read on every change to the guard.
"""

PLANTED_GITHUB_PAT = "ghp_R7qN4mWc2XvT9bLpZk1Ae6Yd5Hs3Fg8Ju2Kx"
PLANTED_GITHUB_FINE_GRAINED = "github_pat_11QW3ER4TY5UI6OP7AS8DF_9Gh0Jk1Lz2Xc3Vb4Nm5Qw6Er7Ty8Ui9Op0As1Df2Gh3Jk4L"
PLANTED_TELEGRAM = "8419273650:AAF7qN4mWc2XvT9bLpZk1Ae6Yd5Hs3Fg8Ju"
PLANTED_AWS_KEY_ID = "AKIA7QZL3WX9KMVT2BR4"
# No Slack fixture: GitHub push protection on AxisMeru/pravrudhi rejects any commit containing a
# Slack-token-shaped literal, so that rule could not be given a fixture and was removed from the guard.
# See the RULES comment in scripts/check_no_secrets_in_diff.py.
PLANTED_PEM_HEADER = "-----BEGIN RSA PRIVATE KEY-----"
PLANTED_BEARER_HEADER = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9ZQ.eyJzdWIiOiI0MnpxSW4wIn0.Rf2GsHj7Kq9ZmVt3LwXn8BdYc"}
