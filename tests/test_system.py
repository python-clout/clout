import os
import subprocess
import sys

import importlib_resources
import tests.examples


def test_appconfig(tmp_path):
    """Run the appconfig example.

    Get data from CLI, envvars, and config file.

    """
    with importlib_resources.path(tests.examples, "appconfig.py") as path:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            """\
        [dance]
        logging = true
        priority = 3
        """
        )

        env = os.environ.copy()
        env.update({"MYAPP_APP_DANCE_DRY_RUN": "1", "TEST_CONFIG_PATH": config_path})

        args = [
            "myapp",
            "dance",
            "--debug",
            "db",
            "--host",
            "example.com",
            "--port",
            "9999",
            "user",
            "--name",
            "Alice",
            "user",
            "--name",
            "Bob",
        ]

        proc = subprocess.run(
            [sys.executable, path] + args, capture_output=True, check=False, env=env
        )
        assert proc.returncode == 0, proc.stderr.decode()

        output = proc.stdout.decode()
        expected = "Dancing with config:\n Config(db=DB(host='example.com', port=9999, user=User(name='Alice')), debug=True, user=User(name='Bob'), priority=3.0, logging=True, dry_run=True)\n"
        assert output == expected