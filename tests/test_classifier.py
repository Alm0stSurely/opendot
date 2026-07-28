"""Tests for the irreversibility classifier — safety-critical.

A false 'reversible' means a silent un-undoable surprise, so these lean on
verifying that dangerous/escaping commands are caught, and that the fail-safe
default (unknown => confirm) holds.
"""

from opendot.reversibility.classifier import classify

WD = "/tmp/ws"


def _rev(cmd):
    return classify(cmd, WD).reversible


# --- should be flagged irreversible (needs confirm) ---

def test_sudo_flagged():
    assert not _rev("sudo rm -rf /var")


def test_network_flagged():
    assert not _rev("curl https://example.com -o out")
    assert not _rev("wget http://x/y")
    assert not _rev("ssh host 'do thing'")


def test_git_remote_flagged():
    assert not _rev("git push origin main")
    assert not _rev("git pull")


def test_pkg_install_flagged():
    assert not _rev("pip install requests")
    assert not _rev("npm install left-pad")
    assert not _rev("brew install wget")


def test_destructive_db_flagged():
    assert not _rev("psql -c 'DROP TABLE users'")
    assert not _rev("mysql -e 'delete from orders'")


def test_rm_outside_workspace_flagged():
    assert not _rev("rm -rf /etc/hosts")
    assert not _rev("rm ../secrets.txt")
    assert not _rev("rm ~/important")


def test_outside_path_flagged():
    assert not _rev("cp secret.txt /etc/")
    assert not _rev("mv data ../../out")


def test_unknown_command_fails_safe():
    assert not _rev("some_weird_binary --do-stuff")


# --- should be allowed (reversible via snapshot / read-only) ---

def test_readonly_allowed():
    assert _rev("ls -la")
    assert _rev("cat file.py")
    assert _rev("grep -r TODO .")
    assert _rev("pwd")


def test_inworkspace_mutations_allowed():
    assert _rev("touch new.txt")
    assert _rev("mkdir subdir")
    assert _rev("echo hi > out.txt")
    assert _rev("rm old.txt")            # in-workspace rm: snapshot covers it
    assert _rev("python script.py")


def test_empty_command_allowed():
    assert _rev("")
