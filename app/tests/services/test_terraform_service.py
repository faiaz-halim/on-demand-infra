import unittest
from unittest.mock import patch, MagicMock
import pathlib
import yaml
import tempfile
import logging
import os

# Adjust import path according to your project structure
from app.services.terraform_service import generate_kind_config_yaml
# Access the configured Jinja2 environment from the service to mock its methods if needed
from app.services import terraform_service as ts # For mocking ts.jinja_env

# Configure basic logging for test visibility
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TestTerraformService(unittest.TestCase):

    def test_generate_kind_config_yaml_success(self):
        logger.info("Testing generate_kind_config_yaml_success...")
        cluster_name = "test-cluster"
        template_name = "kind/kind-config.yaml.j2" # Relative to TEMPLATE_BASE_DIR in terraform_service
        context = {
            "cluster_name": cluster_name, # Explicitly pass, though function adds it if missing
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
                logger.debug(f"Generated Kind Config YAML:\n{generated_content}")
                parsed_config = yaml.safe_load(generated_content)

            self.assertEqual(parsed_config['kind'], 'Cluster')
            self.assertEqual(parsed_config['apiVersion'], 'kind.x-k8s.io/v1alpha4')
            self.assertEqual(parsed_config['name'], cluster_name)

            # Nodes: 1 control-plane + 2 workers
            self.assertIn('nodes', parsed_config)
            self.assertEqual(len(parsed_config['nodes']), 3)
            self.assertEqual(parsed_config['nodes'][0]['role'], 'control-plane')
            self.assertEqual(parsed_config['nodes'][1]['role'], 'worker')
            self.assertEqual(parsed_config['nodes'][2]['role'], 'worker')

            # Feature Gates
            self.assertIn('featureGates', parsed_config)
            self.assertIn('MyFeatureGate', parsed_config['featureGates'])
            # The template uses `{{ value | lower }}`, so Python True becomes YAML true
            self.assertEqual(parsed_config['featureGates']['MyFeatureGate'], True)
            self.assertIn('AnotherGate', parsed_config['featureGates'])
            self.assertEqual(parsed_config['featureGates']['AnotherGate'], False)
        logger.info("test_generate_kind_config_yaml_success passed.")


    def test_generate_kind_config_yaml_no_workers_no_feature_gates(self):
        logger.info("Testing generate_kind_config_yaml_no_workers_no_feature_gates...")
        cluster_name = "simple-cluster"
        template_name = "kind/kind-config.yaml.j2"
        context = {
            "cluster_name": cluster_name,
            # num_workers is omitted or 0
            # feature_gates is omitted
        }

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
            self.assertIn('nodes', parsed_config)
            self.assertEqual(len(parsed_config['nodes']), 1) # Only control-plane
            self.assertEqual(parsed_config['nodes'][0]['role'], 'control-plane')
            self.assertNotIn('featureGates', parsed_config)
        logger.info("test_generate_kind_config_yaml_no_workers_no_feature_gates passed.")

    @patch.object(ts, 'jinja_env') # Patch the jinja_env instance in terraform_service
    def test_generate_kind_config_yaml_template_not_found(self, mock_jinja_env):
        logger.info("Testing generate_kind_config_yaml_template_not_found...")
        # Configure the mock to raise TemplateNotFound when get_template is called
        mock_jinja_env.get_template.side_effect = jinja2.TemplateNotFound("nonexistent/template.j2")

        cluster_name = "test-cluster"
        template_name = "nonexistent/template.j2" # This template shouldn't exist
        context = {"cluster_name": cluster_name}

        with tempfile.TemporaryDirectory() as temp_dir_path:
            # Expecting an error log message
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_kind_config_yaml(
                    cluster_name, template_name, temp_dir_path, context
                )

        self.assertIsNone(returned_path_str, "Function should return None on TemplateNotFound.")
        # Check for specific log message
        self.assertTrue(any("Template 'nonexistent/template.j2' not found" in msg for msg in log_watcher.output))
        logger.info("test_generate_kind_config_yaml_template_not_found passed.")

    @patch('app.services.terraform_service.pathlib.Path.mkdir') # Mock mkdir to avoid actual creation
    @patch('builtins.open') # Mock the open call for writing the file
    def test_generate_kind_config_yaml_write_failure(self, mock_open, mock_mkdir):
        logger.info("Testing generate_kind_config_yaml_write_failure...")
        # Configure mock_open to raise IOError on write
        mock_open.side_effect = IOError("Simulated write error")

        cluster_name = "test-cluster"
        template_name = "kind/kind-config.yaml.j2" # A valid template for rendering attempt
        context = {"cluster_name": cluster_name}

        # The temp_dir_path for output_dir is not strictly needed since open is mocked,
        # but the function expects it, so provide a dummy.
        dummy_output_dir = "dummy_dir"

        with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
            returned_path_str = generate_kind_config_yaml(
                cluster_name, template_name, dummy_output_dir, context
            )

        self.assertIsNone(returned_path_str, "Function should return None on IOError during write.")
        self.assertTrue(any(f"IOError writing Kind config to {pathlib.Path(dummy_output_dir) / f'{cluster_name}-config.yaml'}" in msg for msg in log_watcher.output) or \
                        any("Simulated write error" in msg for msg in log_watcher.output) ) # Check for specific log message
        logger.info("test_generate_kind_config_yaml_write_failure passed.")


    def test_generate_kind_config_yaml_jinja_env_none(self):
        logger.info("Testing generate_kind_config_yaml_jinja_env_none...")
        # Temporarily set the service's jinja_env to None to simulate initialization failure
        original_jinja_env = ts.jinja_env
        ts.jinja_env = None

        cluster_name = "test-cluster"
        template_name = "kind/kind-config.yaml.j2"
        context = {"cluster_name": cluster_name}
        dummy_output_dir = "dummy_dir"

        with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
            returned_path_str = generate_kind_config_yaml(
                cluster_name, template_name, dummy_output_dir, context
            )

        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Jinja2 environment not available" in msg for msg in log_watcher.output))

        # Restore the original jinja_env
        ts.jinja_env = original_jinja_env
        logger.info("test_generate_kind_config_yaml_jinja_env_none passed.")


if __name__ == '__main__':
    # This is important: The tests rely on the TEMPLATE_BASE_DIR in terraform_service.py
    # being correctly set up relative to that file's location to find the actual
    # 'kind/kind-config.yaml.j2' template for the success cases.
    # Ensure that app/templates/kind/kind-config.yaml.j2 exists.

    # A quick check to ensure the template file used in tests actually exists
    # This is more of a sanity check for the test environment itself.
    expected_template_path = ts.TEMPLATE_BASE_DIR / "kind/kind-config.yaml.j2"
    if not expected_template_path.exists():
        logger.warning(f"CRITICAL TEST SETUP WARNING: The Kind template {expected_template_path} does not exist. Success tests will fail.")
        # Optionally, create a dummy one here for test execution if absolutely necessary,
        # but it's better if the actual file from the previous step is present.
        # expected_template_path.parent.mkdir(parents=True, exist_ok=True)
        # with open(expected_template_path, "w") as f:
        #     f.write("kind: Cluster\napiVersion: kind.x-k8s.io/v1alpha4\nname: {{ cluster_name }}\nnodes:\n- role: control-plane\n")
        # logger.info(f"Created a minimal dummy template at {expected_template_path} for test run.")

    unittest.main()
