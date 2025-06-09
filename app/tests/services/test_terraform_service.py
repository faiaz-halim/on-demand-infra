import unittest
from unittest.mock import patch, MagicMock, call
import pathlib
import yaml # For Kind config tests
import hcl2.parser # For Terraform HCL config tests
import json # For comparing user_data_content
import tempfile
import logging
import os
import jinja2 # For TemplateNotFound exception
import subprocess # For CompletedProcess

# Adjust import path according to your project structure
from app.services.terraform_service import (
    generate_kind_config_yaml,
    generate_ec2_tf_config,
    generate_ec2_bootstrap_script,
    run_terraform_init,       # Added
    run_terraform_apply,      # Added
    run_terraform_destroy,    # Added
    _run_terraform_command    # For testing CLI not found via this helper
)
# Access the configured Jinja2 environment from the service to mock its methods if needed
from app.services import terraform_service as ts # For mocking ts.jinja_env

# Configure basic logging for test visibility
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TestTerraformService(unittest.TestCase):

    def test_generate_kind_config_yaml_success(self):
        logger.info("Testing generate_kind_config_yaml_success...")
        cluster_name = "test-cluster"
        template_name = "kind/kind-config.yaml.j2"
        context = {
            "cluster_name": cluster_name,
            "num_workers": 2,
            "feature_gates": {"MyFeatureGate": True, "AnotherGate": False}
        }

        with tempfile.TemporaryDirectory() as temp_dir_path:
            returned_path_str = generate_kind_config_yaml(
                cluster_name, template_name, temp_dir_path, context
            )

            self.assertIsNotNone(returned_path_str, "Should return a path string on success.")
            output_file = pathlib.Path(returned_path_str)
            self.assertTrue(output_file.exists(), f"Output file {output_file} should exist.")

            with open(output_file, 'r') as f:
                generated_content = f.read()
                parsed_config = yaml.safe_load(generated_content)

            self.assertEqual(parsed_config['kind'], 'Cluster')
            self.assertEqual(parsed_config['apiVersion'], 'kind.x-k8s.io/v1alpha4')
            self.assertEqual(parsed_config['name'], cluster_name)
            self.assertEqual(len(parsed_config['nodes']), 3)
            self.assertEqual(parsed_config['nodes'][0]['role'], 'control-plane')
            self.assertEqual(parsed_config['nodes'][1]['role'], 'worker')
            self.assertEqual(parsed_config['nodes'][2]['role'], 'worker')
            self.assertIn('featureGates', parsed_config)
            self.assertIn('MyFeatureGate', parsed_config['featureGates'])
            self.assertEqual(parsed_config['featureGates']['MyFeatureGate'], True)
            self.assertEqual(parsed_config['featureGates']['AnotherGate'], False)
        logger.info("test_generate_kind_config_yaml_success passed.")

    def test_generate_kind_config_yaml_no_workers_no_feature_gates(self):
        logger.info("Testing generate_kind_config_yaml_no_workers_no_feature_gates...")
        cluster_name = "simple-cluster"
        template_name = "kind/kind-config.yaml.j2"
        context = {"cluster_name": cluster_name}

        with tempfile.TemporaryDirectory() as temp_dir_path:
            returned_path_str = generate_kind_config_yaml(
                cluster_name, template_name, temp_dir_path, context
            )
            self.assertIsNotNone(returned_path_str)
            output_file = pathlib.Path(returned_path_str)
            self.assertTrue(output_file.exists())

            with open(output_file, 'r') as f:
                parsed_config = yaml.safe_load(f)

            self.assertEqual(parsed_config['name'], cluster_name)
            self.assertEqual(len(parsed_config['nodes']), 1)
            self.assertNotIn('featureGates', parsed_config)
        logger.info("test_generate_kind_config_yaml_no_workers_no_feature_gates passed.")

    @patch.object(ts, 'jinja_env')
    def test_generate_kind_config_yaml_template_not_found(self, mock_jinja_env):
        logger.info("Testing generate_kind_config_yaml_template_not_found...")
        mock_jinja_env.get_template.side_effect = jinja2.TemplateNotFound("nonexistent/template.j2")

        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_kind_config_yaml(
                    "test-cluster", "nonexistent/template.j2", temp_dir_path, {}
                )
        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Template 'nonexistent/template.j2' not found" in msg for msg in log_watcher.output))
        logger.info("test_generate_kind_config_yaml_template_not_found passed.")

    @patch('builtins.open')
    def test_generate_kind_config_yaml_write_failure(self, mock_open):
        logger.info("Testing generate_kind_config_yaml_write_failure...")
        mock_open.side_effect = IOError("Simulated write error")

        with patch.object(ts.jinja_env, 'get_template') as mock_get_template:
            mock_template = MagicMock()
            mock_template.render.return_value = "dummy rendered content"
            mock_get_template.return_value = mock_template

            with tempfile.TemporaryDirectory() as temp_dir_path:
                with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                    returned_path_str = generate_kind_config_yaml(
                        "test-cluster", "kind/kind-config.yaml.j2", temp_dir_path, {"cluster_name": "test-cluster"}
                    )
            self.assertIsNone(returned_path_str)
            self.assertTrue(any("IOError writing Kind config" in msg for msg in log_watcher.output) or \
                            any("Simulated write error" in msg for msg in log_watcher.output) )
        logger.info("test_generate_kind_config_yaml_write_failure passed.")

    def test_generate_kind_config_yaml_jinja_env_none(self):
        logger.info("Testing generate_kind_config_yaml_jinja_env_none...")
        original_jinja_env = ts.jinja_env
        ts.jinja_env = None
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_kind_config_yaml(
                    "test-cluster", "kind/kind-config.yaml.j2", temp_dir_path, {}
                )
        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Jinja2 environment not available" in msg for msg in log_watcher.output))
        ts.jinja_env = original_jinja_env
        logger.info("test_generate_kind_config_yaml_jinja_env_none passed.")

    # Tests for generate_ec2_bootstrap_script
    def test_generate_ec2_bootstrap_script_content_success(self):
        logger.info("Testing generate_ec2_bootstrap_script_content_success...")
        context = {'kind_version': '0.20.0', 'kubectl_version': '1.28.0'}
        script_content = generate_ec2_bootstrap_script(context, output_dir=None)
        self.assertIsNotNone(script_content)
        self.assertIn("Installing Kind v0.20.0", script_content)
        self.assertIn("curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64", script_content)
        self.assertIn("Installing kubectl v1.28.0", script_content)
        self.assertIn("curl -Lo kubectl \"https://dl.k8s.io/release/v1.28.0/bin/linux/amd64/kubectl\"", script_content)
        self.assertIn("sudo amazon-linux-extras install docker -y", script_content)
        logger.info("test_generate_ec2_bootstrap_script_content_success passed.")

    def test_generate_ec2_bootstrap_script_content_default_versions(self):
        logger.info("Testing generate_ec2_bootstrap_script_content_default_versions...")
        script_content = generate_ec2_bootstrap_script({}, output_dir=None)
        self.assertIsNotNone(script_content)
        self.assertIn("Installing Kind v0.23.0", script_content)
        self.assertIn("Installing kubectl v1.30.0", script_content)
        logger.info("test_generate_ec2_bootstrap_script_content_default_versions passed.")

    def test_generate_ec2_bootstrap_script_save_to_file_success(self):
        logger.info("Testing generate_ec2_bootstrap_script_save_to_file_success...")
        context = {}
        with tempfile.TemporaryDirectory() as temp_dir_path:
            script_path_str = generate_ec2_bootstrap_script(context, output_dir=temp_dir_path)
            self.assertIsNotNone(script_path_str)
            script_file = pathlib.Path(script_path_str)
            self.assertTrue(script_file.exists())
            self.assertTrue(script_file.name.startswith("ec2_bootstrap_"))
            self.assertTrue(script_file.name.endswith(".sh"))
            with open(script_file, 'r') as f:
                content = f.read()
            self.assertIn("#!/bin/bash", content)
        logger.info("test_generate_ec2_bootstrap_script_save_to_file_success passed.")

    @patch.object(ts, 'jinja_env')
    def test_generate_ec2_bootstrap_script_template_not_found(self, mock_jinja_env):
        logger.info("Testing generate_ec2_bootstrap_script_template_not_found...")
        mock_jinja_env.get_template.side_effect = jinja2.TemplateNotFound("scripts/nonexistent.sh.j2")
        with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
            returned_script = generate_ec2_bootstrap_script({}, output_dir=None)
        self.assertIsNone(returned_script)
        self.assertTrue(any("Bootstrap script template 'scripts/ec2_bootstrap.sh.j2' not found" in msg for msg in log_watcher.output) or \
                        any("Template 'scripts/nonexistent.sh.j2' not found" in msg for msg in log_watcher.output))
        logger.info("test_generate_ec2_bootstrap_script_template_not_found passed.")

    # Refactored Tests for generate_ec2_tf_config
    @patch('app.services.terraform_service.generate_ec2_bootstrap_script', return_value="#!/bin/bash\necho 'Mocked bootstrap'")
    def test_generate_ec2_tf_config_success_basic(self, mock_generate_bootstrap):
        logger.info("Testing generate_ec2_tf_config_success_basic (refactored)...")
        context = {
            "aws_region": "us-east-1", "ami_id": "ami-12345", "instance_type": "t2.micro",
            "key_name": "test-key", "instance_name_tag": "basic-ec2", "sg_name": "basic-sg",
        }
        with tempfile.TemporaryDirectory() as temp_dir_path:
            returned_path_str = generate_ec2_tf_config(context.copy(), temp_dir_path)
            self.assertIsNotNone(returned_path_str)
            output_file = pathlib.Path(returned_path_str)
            self.assertTrue(output_file.exists())
            with open(output_file, 'r') as f:
                file_content = f.read()
                hcl_data = hcl2.parser.parse(file_content)

            mock_generate_bootstrap.assert_called_once_with(context, output_dir=None)
            self.assertEqual(hcl_data['resource'][0]['aws_instance']['ec2_for_kind']['user_data'], json.dumps("#!/bin/bash\necho 'Mocked bootstrap'"))
            self.assertEqual(hcl_data['resource'][0]['aws_instance']['ec2_for_kind']['ami'], context['ami_id'])
        logger.info("test_generate_ec2_tf_config_success_basic (refactored) passed.")

    @patch('app.services.terraform_service.generate_ec2_bootstrap_script')
    def test_generate_ec2_tf_config_success_with_options(self, mock_generate_bootstrap):
        logger.info("Testing generate_ec2_tf_config_success_with_options (refactored)...")
        mocked_bootstrap_content = "#!/bin/bash\necho 'Mocked bootstrap for options test'"
        mock_generate_bootstrap.return_value = mocked_bootstrap_content

        context = {
            "aws_region": "eu-west-1", "ami_id": "ami-67890", "instance_type": "m5.large",
            "key_name": "prod-key", "instance_name_tag": "optioned-ec2", "sg_name": "optioned-sg",
            "ssh_cidr": "10.0.0.0/16",
            "app_ports": [{'port': 80, 'protocol': 'tcp'}, {'port': 443, 'protocol': 'tcp'}],
            "kind_version": "0.20.0"
        }
        with tempfile.TemporaryDirectory() as temp_dir_path:
            returned_path_str = generate_ec2_tf_config(context.copy(), temp_dir_path)
            self.assertIsNotNone(returned_path_str)
            with open(pathlib.Path(returned_path_str), 'r') as f:
                file_content = f.read()
                hcl_data = hcl2.parser.parse(file_content)

            mock_generate_bootstrap.assert_called_once_with(context, output_dir=None)
            self.assertEqual(hcl_data['resource'][0]['aws_instance']['ec2_for_kind']['user_data'], json.dumps(mocked_bootstrap_content))
            sg_resource = hcl_data['resource'][1]['aws_security_group']['ec2_sg']
            self.assertEqual(len(sg_resource['ingress']), len(context['app_ports']) + 1)
            ssh_rule = next(rule for rule in sg_resource['ingress'] if rule.get('from_port') == 22)
            self.assertEqual(ssh_rule['cidr_blocks'], [context['ssh_cidr']])
        logger.info("test_generate_ec2_tf_config_success_with_options (refactored) passed.")

    @patch('app.services.terraform_service.generate_ec2_bootstrap_script', return_value=None)
    def test_generate_ec2_tf_config_bootstrap_fails(self, mock_generate_bootstrap):
        logger.info("Testing generate_ec2_tf_config_bootstrap_fails...")
        context = {
            "aws_region": "us-east-1", "ami_id": "ami-12345", "instance_type": "t2.micro",
            "key_name": "test-key", "instance_name_tag": "basic-ec2", "sg_name": "basic-sg",
        }
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_ec2_tf_config(context.copy(), temp_dir_path)
        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Failed to generate EC2 bootstrap script" in msg for msg in log_watcher.output))
        mock_generate_bootstrap.assert_called_once_with(context, output_dir=None)
        logger.info("test_generate_ec2_tf_config_bootstrap_fails passed.")

    def test_generate_ec2_tf_config_missing_required_context(self):
        logger.info("Testing generate_ec2_tf_config_missing_required_context...")
        context = {"aws_region": "us-east-1"}
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                # generate_ec2_bootstrap_script will be called first. If it needs context not present, it might log.
                # Then generate_ec2_tf_config checks its own required keys.
                # The mock for bootstrap is not active here, so it runs.
                returned_path_str = generate_ec2_tf_config(context, temp_dir_path)
        self.assertIsNone(returned_path_str)
        # Check for the specific error from generate_ec2_tf_config about its direct missing keys
        self.assertTrue(any("Missing required key 'ami_id' in context" in msg for msg in log_watcher.output))
        logger.info("test_generate_ec2_tf_config_missing_required_context passed.")

    @patch.object(ts, 'jinja_env')
    @patch('app.services.terraform_service.generate_ec2_bootstrap_script', return_value="#!/bin/bash\necho 'Mocked bootstrap'")
    def test_generate_ec2_tf_config_tf_template_not_found(self, mock_generate_bootstrap, mock_jinja_env):
        logger.info("Testing generate_ec2_tf_config_tf_template_not_found...")
        # This mock will affect the get_template call for 'terraform/aws/ec2_instance.tf.j2'
        mock_jinja_env.get_template.side_effect = jinja2.TemplateNotFound("terraform/aws/ec2_instance.tf.j2")
        context = {
            "aws_region": "us-east-1", "ami_id": "ami-123", "instance_type": "t2.micro",
            "key_name": "key", "instance_name_tag": "tag", "sg_name": "sg"
        }
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_ec2_tf_config(context, temp_dir_path)
        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Template 'terraform/aws/ec2_instance.tf.j2' not found" in msg for msg in log_watcher.output))
        logger.info("test_generate_ec2_tf_config_tf_template_not_found passed.")

    # Tests for Terraform CLI functions
    @patch('shutil.which', return_value='/fake/path/to/terraform')
    @patch('subprocess.run')
    def test_run_terraform_init_success(self, mock_subprocess_run, mock_shutil_which):
        mock_subprocess_run.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Init success", stderr="")
        success, stdout, stderr = run_terraform_init("/test/tf_dir")
        self.assertTrue(success)
        self.assertEqual(stdout, "Init success")
        self.assertEqual(stderr, "")
        mock_subprocess_run.assert_called_once()
        args, kwargs = mock_subprocess_run.call_args
        self.assertEqual(args[0], ['/fake/path/to/terraform', 'init', '-no-color', '-input=false'])
        self.assertEqual(kwargs['cwd'], '/test/tf_dir')

    @patch('shutil.which', return_value='/fake/path/to/terraform')
    @patch('subprocess.run')
    def test_run_terraform_init_failure(self, mock_subprocess_run, mock_shutil_which):
        mock_subprocess_run.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="Init failed")
        success, stdout, stderr = run_terraform_init("/test/tf_dir")
        self.assertFalse(success)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "Init failed")

    @patch('shutil.which', return_value='/fake/path/to/terraform')
    @patch('subprocess.run')
    def test_run_terraform_apply_success(self, mock_subprocess_run, mock_shutil_which):
        # First call for 'apply', second for 'output'
        mock_apply_result = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Apply success JSON", stderr="")
        mock_output_json = {"output_var": {"value": "output_value"}}
        mock_output_result = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout=json.dumps(mock_output_json), stderr="")
        mock_subprocess_run.side_effect = [mock_apply_result, mock_output_result]

        success, outputs, apply_stdout, apply_stderr = run_terraform_apply("/test/tf_dir")

        self.assertTrue(success)
        self.assertEqual(outputs, {"output_var": "output_value"})
        self.assertEqual(apply_stdout, "Apply success JSON")
        self.assertEqual(apply_stderr, "")

        expected_calls = [
            call(['/fake/path/to/terraform', 'apply', '-auto-approve', '-no-color', '-json', '-input=false'], cwd='/test/tf_dir', env=unittest.mock.ANY, capture_output=True, text=True, check=False),
            call(['/fake/path/to/terraform', 'output', '-no-color', '-json'], cwd='/test/tf_dir', env=unittest.mock.ANY, capture_output=True, text=True, check=False)
        ]
        mock_subprocess_run.assert_has_calls(expected_calls)

    @patch('shutil.which', return_value='/fake/path/to/terraform')
    @patch('subprocess.run')
    def test_run_terraform_apply_apply_fails(self, mock_subprocess_run, mock_shutil_which):
        mock_subprocess_run.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="Apply failed error")
        success, outputs, stdout, stderr = run_terraform_apply("/test/tf_dir")
        self.assertFalse(success)
        self.assertEqual(outputs, {})
        self.assertEqual(stderr, "Apply failed error")

    @patch('shutil.which', return_value='/fake/path/to/terraform')
    @patch('subprocess.run')
    def test_run_terraform_apply_output_fails(self, mock_subprocess_run, mock_shutil_which):
        mock_apply_result = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Apply success JSON", stderr="")
        mock_output_result = MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="Output failed error")
        mock_subprocess_run.side_effect = [mock_apply_result, mock_output_result]

        success, outputs, apply_stdout, apply_stderr = run_terraform_apply("/test/tf_dir")
        self.assertTrue(success) # Apply itself succeeded
        self.assertEqual(outputs, {}) # But outputs are empty
        self.assertIn("Output failed error", apply_stderr) # Error from output command should be in stderr

    @patch('shutil.which', return_value='/fake/path/to/terraform')
    @patch('subprocess.run')
    def test_run_terraform_destroy_success(self, mock_subprocess_run, mock_shutil_which):
        mock_subprocess_run.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Destroy success", stderr="")
        success, stdout, stderr = run_terraform_destroy("/test/tf_dir")
        self.assertTrue(success)
        self.assertEqual(stdout, "Destroy success")

    @patch('shutil.which', return_value=None) # Terraform CLI not found
    @patch('subprocess.run')
    def test_terraform_cli_not_found(self, mock_subprocess_run, mock_shutil_which):
        # Test this via one of the functions, e.g., run_terraform_init
        success, stdout, stderr = run_terraform_init("/test/tf_dir")
        self.assertFalse(success)
        self.assertEqual(stderr, "Terraform CLI not found.")
        mock_subprocess_run.assert_not_called() # subprocess.run in _run_terraform_command should not be called


if __name__ == '__main__':
    expected_kind_template = ts.TEMPLATE_BASE_DIR / "kind/kind-config.yaml.j2"
    expected_ec2_template = ts.TEMPLATE_BASE_DIR / "terraform/aws/ec2_instance.tf.j2"
    expected_script_template = ts.TEMPLATE_BASE_DIR / "scripts/ec2_bootstrap.sh.j2"
    if not expected_kind_template.exists():
        logger.warning(f"Kind template {expected_kind_template} not found.")
    if not expected_ec2_template.exists():
        logger.warning(f"EC2 TF template {expected_ec2_template} not found.")
    if not expected_script_template.exists():
        logger.warning(f"EC2 Bootstrap Script template {expected_script_template} not found.")
    unittest.main()
