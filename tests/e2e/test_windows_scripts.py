"""Smoke Windows — scripts/df.ps1 e a guarda de SO do install.sh.

Só faz sentido no Windows (usa pwsh e o Git Bash); noutros SO o módulo faz skip.
Offline: não arranca o jogo nem toca em processos — só 'help', 'status' e a guarda.
"""
import os
import shutil
import subprocess
import unittest

PWSH = shutil.which("pwsh")
GIT_BASH = os.path.join("C:\\", "Program Files", "Git", "bin", "bash.exe")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

needs_windows = unittest.skipUnless(os.name == "nt" and PWSH, "só no Windows, com pwsh")


@needs_windows
class DfPs1SmokeTests(unittest.TestCase):
    def run_df(self, *args):
        return subprocess.run(
            [PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", os.path.join(ROOT, "scripts", "df.ps1"), *args],
            capture_output=True, text=True, timeout=120)

    def test_help_sai_zero_e_mostra_os_subcomandos(self):
        rv = self.run_df("help")
        self.assertEqual(rv.returncode, 0, rv.stderr)
        self.assertIn("arranca tudo", rv.stdout)
        for sub in ("stop", "status", "logs", "install-link"):
            self.assertIn(sub, rv.stdout)

    def test_status_responde_offline(self):
        rv = self.run_df("status")
        self.assertEqual(rv.returncode, 0, rv.stderr)
        self.assertIn("backend FastAPI", rv.stdout)
        self.assertIn("Dwarf Fortress", rv.stdout)

    def test_argumento_desconhecido_sai_2(self):
        rv = self.run_df("--nao-existe")
        self.assertEqual(rv.returncode, 2)
        self.assertIn("desconhecido", rv.stderr)


@needs_windows
class InstallGuardTests(unittest.TestCase):
    def test_install_sh_recusa_windows_e_aponta_para_df_ps1(self):
        bash = GIT_BASH if os.path.isfile(GIT_BASH) else shutil.which("bash")
        if not bash:
            self.skipTest("Git Bash não encontrado")
        rv = subprocess.run([bash, "install.sh", "--check"], cwd=ROOT,
                            capture_output=True, text=True, timeout=120)
        self.assertEqual(rv.returncode, 3, rv.stdout + rv.stderr)
        said = rv.stdout + rv.stderr
        self.assertIn("df.ps1", said)
        self.assertIn("exit 3", said)


if __name__ == "__main__":
    unittest.main(verbosity=2)
