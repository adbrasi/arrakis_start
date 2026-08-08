import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPOSITORY_ROOT / "bootstrap.sh"
FEATURE_REF = "feature/arrakis-ui-redesign"


class BootstrapGitRefTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.remote = self.root / "arrakis.git"
        self._create_remote()

    def tearDown(self):
        self.temp_dir.cleanup()

    def git(self, *args, cwd=None):
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
        )
        if result.returncode:
            self.fail(
                f"git {' '.join(args)} failed ({result.returncode}):\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result.stdout.strip()

    def run_bootstrap(self, command, *, env=None):
        harness = (
            f"source <(sed '$d' {shlex.quote(str(BOOTSTRAP))})\n"
            f"{command}"
        )
        return subprocess.run(
            ["bash", "-c", harness],
            cwd=REPOSITORY_ROOT,
            text=True,
            capture_output=True,
            env={**os.environ, **(env or {})},
        )

    def clone_shallow_main(self, destination):
        self.git(
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            "main",
            self.remote.as_uri(),
            str(destination),
        )

    def _create_remote(self):
        self.seed = self.root / "seed"
        self.git("init", "--quiet", "--initial-branch=main", str(self.seed))
        self.git("config", "user.name", "Bootstrap Test", cwd=self.seed)
        self.git("config", "user.email", "bootstrap@example.test", cwd=self.seed)
        (self.seed / "branch.txt").write_text("main\n", encoding="utf-8")
        self.git("add", "branch.txt", cwd=self.seed)
        self.git("commit", "--quiet", "-m", "Main branch", cwd=self.seed)
        self.git("switch", "--quiet", "-c", FEATURE_REF, cwd=self.seed)
        (self.seed / "branch.txt").write_text("feature\n", encoding="utf-8")
        self.git("commit", "--quiet", "-am", "Feature branch", cwd=self.seed)
        self.git("init", "--bare", "--quiet", str(self.remote))
        self.git("remote", "add", "origin", self.remote.as_uri(), cwd=self.seed)
        self.git("push", "--quiet", "origin", "main", FEATURE_REF, cwd=self.seed)
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.remote)

    def test_default_ref_clones_main(self):
        destination = self.root / "default-main"
        command = "\n".join(
            [
                "unset ARRAKIS_GIT_REF",
                "configure_arrakis_git_ref",
                "install_arrakis_repo "
                f"{shlex.quote(str(destination))} "
                f"{shlex.quote(self.remote.as_uri())} "
                '"$ARRAKIS_GIT_REF"',
            ]
        )

        result = self.run_bootstrap(command)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("branch", "--show-current", cwd=destination), "main")
        self.assertEqual((destination / "branch.txt").read_text(encoding="utf-8"), "main\n")

    def test_fresh_clone_checks_out_requested_feature_branch(self):
        destination = self.root / "fresh-feature"
        command = "\n".join(
            [
                f"ARRAKIS_GIT_REF={shlex.quote(FEATURE_REF)}",
                "configure_arrakis_git_ref",
                "install_arrakis_repo "
                f"{shlex.quote(str(destination))} "
                f"{shlex.quote(self.remote.as_uri())} "
                '"$ARRAKIS_GIT_REF"',
            ]
        )

        result = self.run_bootstrap(command)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.git("branch", "--show-current", cwd=destination), FEATURE_REF
        )
        self.assertEqual(
            self.git("rev-parse", "--is-shallow-repository", cwd=destination), "true"
        )
        self.assertEqual(
            self.git("config", "--get-all", "remote.origin.fetch", cwd=destination),
            f"+refs/heads/{FEATURE_REF}:refs/remotes/origin/{FEATURE_REF}",
        )
        self.assertEqual(
            (destination / "branch.txt").read_text(encoding="utf-8"), "feature\n"
        )

    def test_unsafe_ref_is_rejected_before_any_git_operation(self):
        result = self.run_bootstrap(
            "ARRAKIS_GIT_REF='-c core.sshCommand=echo injected'\n"
            "configure_arrakis_git_ref"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ARRAKIS_GIT_REF inválido", result.stdout + result.stderr)

    def test_safe_ref_is_validated_before_git_is_installed(self):
        result = self.run_bootstrap(
            "PATH=/definitely-no-git\n"
            f"ARRAKIS_GIT_REF={shlex.quote(FEATURE_REF)}\n"
            "configure_arrakis_git_ref"
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_qualified_ref_is_rejected_before_fetch_refspec_is_built(self):
        result = self.run_bootstrap(
            "ARRAKIS_GIT_REF='refs/heads/main'\n"
            "configure_arrakis_git_ref"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ARRAKIS_GIT_REF inválido", result.stdout + result.stderr)

    def test_backslash_ref_is_rejected_before_git_is_installed(self):
        result = self.run_bootstrap(
            "PATH=/definitely-no-git\n"
            "ARRAKIS_GIT_REF='feature\\bad'\n"
            "configure_arrakis_git_ref"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ARRAKIS_GIT_REF inválido", result.stdout + result.stderr)

    def test_control_character_ref_is_rejected_before_git_is_installed(self):
        result = self.run_bootstrap(
            "PATH=/definitely-no-git\n"
            "ARRAKIS_GIT_REF=$'feature\\001bad'\n"
            "configure_arrakis_git_ref"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ARRAKIS_GIT_REF inválido", result.stdout + result.stderr)

    def test_existing_shallow_main_checkout_switches_to_feature_with_tracking(self):
        destination = self.root / "existing-shallow-main"
        self.clone_shallow_main(destination)
        self.assertEqual(self.git("branch", "--show-current", cwd=destination), "main")
        self.assertEqual(
            self.git("config", "--get-all", "remote.origin.fetch", cwd=destination),
            "+refs/heads/main:refs/remotes/origin/main",
        )

        result = self.run_bootstrap(
            "update_arrakis_repo "
            f"{shlex.quote(str(destination))} "
            f"{shlex.quote(self.remote.as_uri())} "
            f"{shlex.quote(FEATURE_REF)}"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.git("branch", "--show-current", cwd=destination), FEATURE_REF
        )
        self.assertEqual(
            self.git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=destination),
            f"origin/{FEATURE_REF}",
        )
        self.assertEqual(
            (destination / "branch.txt").read_text(encoding="utf-8"), "feature\n"
        )

        (self.seed / "branch.txt").write_text("feature update\n", encoding="utf-8")
        self.git("commit", "--quiet", "-am", "Advance feature branch", cwd=self.seed)
        self.git("push", "--quiet", "origin", FEATURE_REF, cwd=self.seed)

        result = self.run_bootstrap(
            "update_arrakis_repo "
            f"{shlex.quote(str(destination))} "
            f"{shlex.quote(self.remote.as_uri())} "
            f"{shlex.quote(FEATURE_REF)}"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (destination / "branch.txt").read_text(encoding="utf-8"),
            "feature update\n",
        )

    def test_dirty_checkout_fails_without_switching_or_losing_local_data(self):
        destination = self.root / "dirty-main"
        self.clone_shallow_main(destination)
        branch_file = destination / "branch.txt"
        branch_file.write_text("main\nlocal change\n", encoding="utf-8")

        result = self.run_bootstrap(
            "if update_arrakis_repo "
            f"{shlex.quote(str(destination))} "
            f"{shlex.quote(self.remote.as_uri())} "
            f"{shlex.quote(FEATURE_REF)}; then\n"
            "  echo 'unexpected update success' >&2\n"
            "  exit 90\n"
            "else\n"
            "  exit $?\n"
            "fi"
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("alterações locais", result.stdout + result.stderr)
        self.assertEqual(self.git("branch", "--show-current", cwd=destination), "main")
        self.assertEqual(branch_file.read_text(encoding="utf-8"), "main\nlocal change\n")
        self.assertNotEqual(
            subprocess.run(
                ["git", "diff", "--quiet"], cwd=destination, check=False
            ).returncode,
            0,
        )


if __name__ == "__main__":
    unittest.main()
