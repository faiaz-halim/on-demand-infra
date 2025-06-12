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
    generate_eks_tf_config,
    generate_ecr_tf_config,
    generate_route53_acm_tf_config, # Added
    run_terraform_init,
    run_terraform_apply,
    run_terraform_destroy,
    _run_terraform_command    # For testing CLI not found via this helper
)
# Access the configured Jinja2 environment from the service to mock its methods if needed
from app.services import terraform_service as ts # For mocking ts.jinja_env
from app.core.config import settings # Added for EKS tests to access defaults

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

    # --- Tests for generate_eks_tf_config ---

    def test_generate_eks_tf_config_success_basic_defaults(self):
        logger.info("Testing generate_eks_tf_config_success_basic_defaults...")
        context = {
            "aws_region": "us-west-2",
            "cluster_name": "test-eks-cluster"
        }
        with tempfile.TemporaryDirectory() as temp_dir_path:
            tf_file_path = generate_eks_tf_config(context.copy(), temp_dir_path)

            self.assertIsNotNone(tf_file_path, "Should return a path string on success.")
            output_file = pathlib.Path(tf_file_path)
            self.assertTrue(output_file.exists(), f"Output file {output_file} should exist.")

            with open(output_file, 'r') as f:
                file_content = f.read()
                hcl_data = hcl2.parser.parse(file_content)

            # Provider and Region
            self.assertIn('provider "aws"', file_content)
            self.assertIn(f'region = "{context["aws_region"]}"', file_content)

            # VPC CIDR
            vpc_resource = hcl_data['resource'][0]['aws_vpc']['eks_vpc']
            self.assertEqual(vpc_resource['cidr_block'], settings.EKS_DEFAULT_VPC_CIDR)
            self.assertIn(context["cluster_name"], vpc_resource['tags']['Name'])


            # EKS Cluster version
            eks_cluster_resource = hcl_data['resource'][0]['aws_eks_cluster']['eks_cluster']
            self.assertEqual(eks_cluster_resource['name'], context["cluster_name"])
            self.assertEqual(eks_cluster_resource['version'], settings.EKS_DEFAULT_VERSION)

            # Node group instance type and scaling config
            node_group_resource = hcl_data['resource'][0]['aws_eks_node_group']['node_group']
            self.assertEqual(node_group_resource['instance_types'], [settings.EKS_DEFAULT_NODE_INSTANCE_TYPE])
            self.assertEqual(node_group_resource['scaling_config'][0]['desired_size'], settings.EKS_DEFAULT_NODE_DESIRED_SIZE)
            self.assertEqual(node_group_resource['scaling_config'][0]['min_size'], settings.EKS_DEFAULT_NODE_MIN_SIZE)
            self.assertEqual(node_group_resource['scaling_config'][0]['max_size'], settings.EKS_DEFAULT_NODE_MAX_SIZE)
            expected_ng_name = f"{context['cluster_name']}-{settings.EKS_DEFAULT_NODE_GROUP_NAME_SUFFIX}"
            self.assertEqual(node_group_resource['node_group_name'], expected_ng_name)


            # Check number of subnets
            public_subnets = [res for res_type in hcl_data['resource'] for res_name, res_config in res_type.items() if res_name == 'aws_subnet' and 'public_subnet' in res_config[0]]
            private_subnets = [res for res_type in hcl_data['resource'] for res_name, res_config in res_type.items() if res_name == 'aws_subnet' and 'private_subnet' in res_config[0]]
            self.assertEqual(len(public_subnets), settings.EKS_DEFAULT_NUM_PUBLIC_SUBNETS)
            self.assertEqual(len(private_subnets), settings.EKS_DEFAULT_NUM_PRIVATE_SUBNETS)
        logger.info("test_generate_eks_tf_config_success_basic_defaults passed.")

    def test_generate_eks_tf_config_success_with_overrides(self):
        logger.info("Testing generate_eks_tf_config_success_with_overrides...")
        context = {
            "aws_region": "eu-central-1",
            "cluster_name": "custom-eks",
            "vpc_cidr": "192.168.0.0/16",
            "num_public_subnets": 3,
            "num_private_subnets": 3,
            "eks_version": "1.28",
            "node_group_name": "custom-ng",
            "node_instance_type": "m5.large",
            "node_desired_size": 3,
            "node_min_size": 2,
            "node_max_size": 5
        }
        with tempfile.TemporaryDirectory() as temp_dir_path:
            tf_file_path = generate_eks_tf_config(context.copy(), temp_dir_path)
            self.assertIsNotNone(tf_file_path)
            with open(pathlib.Path(tf_file_path), 'r') as f:
                file_content = f.read()
                hcl_data = hcl2.parser.parse(file_content)

            self.assertIn(f'region = "{context["aws_region"]}"', file_content)

            vpc_resource = hcl_data['resource'][0]['aws_vpc']['eks_vpc']
            self.assertEqual(vpc_resource['cidr_block'], context['vpc_cidr'])
            self.assertIn(context["cluster_name"], vpc_resource['tags']['Name'])

            eks_cluster_resource = hcl_data['resource'][0]['aws_eks_cluster']['eks_cluster']
            self.assertEqual(eks_cluster_resource['name'], context["cluster_name"])
            self.assertEqual(eks_cluster_resource['version'], context['eks_version'])

            node_group_resource = hcl_data['resource'][0]['aws_eks_node_group']['node_group']
            self.assertEqual(node_group_resource['instance_types'], [context['node_instance_type']])
            self.assertEqual(node_group_resource['scaling_config'][0]['desired_size'], context['node_desired_size'])
            self.assertEqual(node_group_resource['scaling_config'][0]['min_size'], context['node_min_size'])
            self.assertEqual(node_group_resource['scaling_config'][0]['max_size'], context['node_max_size'])
            self.assertEqual(node_group_resource['node_group_name'], context['node_group_name'])

            public_subnets = [res for res_type in hcl_data['resource'] for res_name, res_config in res_type.items() if res_name == 'aws_subnet' and 'public_subnet' in res_config[0]]
            private_subnets = [res for res_type in hcl_data['resource'] for res_name, res_config in res_type.items() if res_name == 'aws_subnet' and 'private_subnet' in res_config[0]]
            self.assertEqual(len(public_subnets), context['num_public_subnets'])
            self.assertEqual(len(private_subnets), context['num_private_subnets'])
        logger.info("test_generate_eks_tf_config_success_with_overrides passed.")

    def test_generate_eks_tf_config_missing_required_context(self):
        logger.info("Testing generate_eks_tf_config_missing_required_context...")
        # Missing 'cluster_name'
        context_missing_cluster = {"aws_region": "us-east-1"}
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_eks_tf_config(context_missing_cluster, temp_dir_path)
        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Missing one or more required keys in context for EKS TF config: cluster_name" in msg for msg in log_watcher.output))

        # Missing 'aws_region'
        context_missing_region = {"cluster_name": "test-cluster"}
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_eks_tf_config(context_missing_region, temp_dir_path)
        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Missing one or more required keys in context for EKS TF config: aws_region" in msg for msg in log_watcher.output))
        logger.info("test_generate_eks_tf_config_missing_required_context passed.")

    @patch.object(ts, 'jinja_env')
    def test_generate_eks_tf_config_template_not_found(self, mock_jinja_env):
        logger.info("Testing generate_eks_tf_config_template_not_found...")
        mock_jinja_env.get_template.side_effect = jinja2.TemplateNotFound("terraform/aws/eks_cluster.tf.j2")
        context = {"aws_region": "us-east-1", "cluster_name": "test-cluster"}
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_eks_tf_config(context, temp_dir_path)
        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Template 'terraform/aws/eks_cluster.tf.j2' not found" in msg for msg in log_watcher.output))
        logger.info("test_generate_eks_tf_config_template_not_found passed.")

    # --- Tests for generate_ecr_tf_config ---

    def test_generate_ecr_tf_config_success_basic_defaults(self):
        logger.info("Testing generate_ecr_tf_config_success_basic_defaults...")
        context = {
            "aws_region": "us-east-1",
            "ecr_repo_name": "test-my-app"
        }
        with tempfile.TemporaryDirectory() as temp_dir_path:
            tf_file_path = generate_ecr_tf_config(context.copy(), temp_dir_path)

            self.assertIsNotNone(tf_file_path, "Should return a path string on success.")
            output_file = pathlib.Path(tf_file_path)
            self.assertTrue(output_file.exists(), f"Output file {output_file} should exist.")

            with open(output_file, 'r') as f:
                file_content = f.read()
                hcl_data = hcl2.parser.parse(file_content)

            self.assertIn(f'region = "{context["aws_region"]}"', file_content)

            ecr_repo_resource = hcl_data['resource'][0]['aws_ecr_repository']['ecr_repo']
            self.assertEqual(ecr_repo_resource['name'], context["ecr_repo_name"])
            self.assertEqual(ecr_repo_resource['image_tag_mutability'], settings.ECR_DEFAULT_IMAGE_TAG_MUTABILITY)

            # HCL boolean is not quoted, template uses |lower so True becomes true
            expected_scan_on_push_str = str(settings.ECR_DEFAULT_SCAN_ON_PUSH).lower()
            self.assertEqual(ecr_repo_resource['image_scanning_configuration'][0]['scan_on_push'], expected_scan_on_push_str)

            self.assertIn('output "ecr_repository_url"', file_content)
            self.assertIn('output "ecr_repository_name"', file_content)
        logger.info("test_generate_ecr_tf_config_success_basic_defaults passed.")

    def test_generate_ecr_tf_config_success_with_overrides(self):
        logger.info("Testing generate_ecr_tf_config_success_with_overrides...")
        context = {
            "aws_region": "eu-west-1",
            "ecr_repo_name": "custom-repo",
            "image_tag_mutability": "IMMUTABLE",
            "scan_on_push": False  # Note: Python False, template converts to 'false'
        }
        with tempfile.TemporaryDirectory() as temp_dir_path:
            tf_file_path = generate_ecr_tf_config(context.copy(), temp_dir_path)
            self.assertIsNotNone(tf_file_path)
            with open(pathlib.Path(tf_file_path), 'r') as f:
                file_content = f.read()
                hcl_data = hcl2.parser.parse(file_content)

            self.assertIn(f'region = "{context["aws_region"]}"', file_content)
            ecr_repo_resource = hcl_data['resource'][0]['aws_ecr_repository']['ecr_repo']
            self.assertEqual(ecr_repo_resource['name'], context["ecr_repo_name"])
            self.assertEqual(ecr_repo_resource['image_tag_mutability'], context["image_tag_mutability"])
            self.assertEqual(ecr_repo_resource['image_scanning_configuration'][0]['scan_on_push'], str(context["scan_on_push"]).lower())
        logger.info("test_generate_ecr_tf_config_success_with_overrides passed.")

    def test_generate_ecr_tf_config_missing_required_context(self):
        logger.info("Testing generate_ecr_tf_config_missing_required_context...")
        # Missing 'ecr_repo_name'
        context_missing_repo = {"aws_region": "us-east-1"}
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_ecr_tf_config(context_missing_repo, temp_dir_path)
        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Missing required key 'ecr_repo_name' in context for generating ECR Terraform config." in msg for msg in log_watcher.output))

        # Missing 'aws_region'
        context_missing_region = {"ecr_repo_name": "test-repo"}
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_ecr_tf_config(context_missing_region, temp_dir_path)
        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Missing required key 'aws_region' in context for generating ECR Terraform config." in msg for msg in log_watcher.output))
        logger.info("test_generate_ecr_tf_config_missing_required_context passed.")

    @patch.object(ts, 'jinja_env')
    def test_generate_ecr_tf_config_template_not_found(self, mock_jinja_env):
        logger.info("Testing generate_ecr_tf_config_template_not_found...")
        mock_jinja_env.get_template.side_effect = jinja2.TemplateNotFound("terraform/aws/ecr_repository.tf.j2")
        context = {"aws_region": "us-east-1", "ecr_repo_name": "test-repo"}
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                returned_path_str = generate_ecr_tf_config(context, temp_dir_path)
        self.assertIsNone(returned_path_str)
        self.assertTrue(any("Template 'terraform/aws/ecr_repository.tf.j2' not found" in msg for msg in log_watcher.output))
        logger.info("test_generate_ecr_tf_config_template_not_found passed.")

    # --- Tests for generate_route53_acm_tf_config ---

    def test_generate_route53_acm_tf_config_success(self):
        logger.info("Testing generate_route53_acm_tf_config_success...")
        context = {
            "aws_region": "us-east-1",
            "base_hosted_zone_id": "Z123BASEID",
            "app_full_domain_name": "app.example.com",
            "nlb_dns_name": "my-nlb-dns.elb.amazonaws.com",
            "nlb_hosted_zone_id": "ZNLBHOSTEDID"
        }
        with tempfile.TemporaryDirectory() as temp_dir_path:
            tf_file_path = generate_route53_acm_tf_config(context.copy(), temp_dir_path)

            self.assertIsNotNone(tf_file_path, "Should return a path string on success.")
            output_file = pathlib.Path(tf_file_path)
            self.assertTrue(output_file.exists(), f"Output file {output_file} should exist.")

            with open(output_file, 'r') as f:
                file_content = f.read()
                # Using string checks as HCL parsing for complex for_each might be tricky
                # and the template is relatively stable for these parts.

            self.assertIn(f'region = "{context["aws_region"]}"', file_content)
            self.assertIn(f'zone_id = "{context["base_hosted_zone_id"]}"', file_content) # For data source

            # ACM Certificate
            self.assertIn('resource "aws_acm_certificate" "app_cert"', file_content)
            self.assertIn(f'domain_name       = "{context["app_full_domain_name"]}"', file_content)
            self.assertIn('validation_method = "DNS"', file_content)

            # Route53 Record for ACM validation
            self.assertIn('resource "aws_route53_record" "cert_validation_dns"', file_content)
            expected_for_each_str = "for dvo in aws_acm_certificate.app_cert.domain_validation_options : dvo.domain_name => {\n      name   = dvo.resource_record_name\n      record = dvo.resource_record_value\n      type   = dvo.resource_record_type\n    } if dvo.domain_name == "
            # Remove newlines and multiple spaces for robust comparison
            self.assertIn(''.join(expected_for_each_str.split()), ''.join(file_content.split()))
            self.assertIn(f'if dvo.domain_name == "{context["app_full_domain_name"]}"', file_content)


            # ACM Certificate Validation
            self.assertIn('resource "aws_acm_certificate_validation" "cert_validation_wait"', file_content)
            self.assertIn('certificate_arn         = aws_acm_certificate.app_cert.arn', file_content)
            self.assertIn('validation_record_fqdns = [for record in aws_route53_record.cert_validation_dns : record.fqdn]', file_content)

            # Route53 Alias Record for App
            self.assertIn('resource "aws_route53_record" "app_alias_record"', file_content)
            self.assertIn(f'name    = "{context["app_full_domain_name"]}"', file_content)
            self.assertIn('type    = "A"', file_content)
            self.assertIn(f'name                   = "{context["nlb_dns_name"]}"', file_content)
            self.assertIn(f'zone_id                = "{context["nlb_hosted_zone_id"]}"', file_content) # For alias target
            # Check that the app_alias_record uses the base_hosted_zone_id for its own zone_id
            # This requires careful parsing or a more specific string search
            self.assertTrue(file_content.count(f'zone_id = data.aws_route53_zone.base.zone_id') >= 2)


            # Outputs
            self.assertIn('output "acm_certificate_arn"', file_content)
            self.assertIn('value       = aws_acm_certificate_validation.cert_validation_wait.certificate_arn', file_content)
            self.assertIn('output "app_url_https"', file_content)
            self.assertIn(f'value       = "https://{context["app_full_domain_name"]}"', file_content)

        logger.info("test_generate_route53_acm_tf_config_success passed.")

    def test_generate_route53_acm_tf_config_missing_required_context(self):
        logger.info("Testing generate_route53_acm_tf_config_missing_required_context...")
        base_context = {
            "aws_region": "us-east-1",
            "base_hosted_zone_id": "Z123BASEID",
            "app_full_domain_name": "app.example.com",
            "nlb_dns_name": "my-nlb-dns.elb.amazonaws.com",
            "nlb_hosted_zone_id": "ZNLBHOSTEDID"
        }

        required_keys = ['base_hosted_zone_id', 'app_full_domain_name', 'nlb_dns_name', 'nlb_hosted_zone_id']

        for key_to_remove in required_keys:
            context = base_context.copy()
            del context[key_to_remove]
            with tempfile.TemporaryDirectory() as temp_dir_path:
                with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                    tf_file_path = generate_route53_acm_tf_config(context, temp_dir_path)
            self.assertIsNone(tf_file_path, f"Should fail when '{key_to_remove}' is missing.")
            self.assertTrue(
                any(f"Missing required key '{key_to_remove}'" in msg for msg in log_watcher.output),
                f"Missing key log for '{key_to_remove}' not found."
            )
        logger.info("test_generate_route53_acm_tf_config_missing_required_context passed.")

    @patch.object(ts, 'jinja_env')
    def test_generate_route53_acm_tf_config_template_not_found(self, mock_jinja_env):
        logger.info("Testing generate_route53_acm_tf_config_template_not_found...")
        mock_jinja_env.get_template.side_effect = jinja2.TemplateNotFound("terraform/aws/route53_acm.tf.j2")
        context = {
            "aws_region": "us-east-1", "base_hosted_zone_id": "Z123", "app_full_domain_name": "app.example.com",
            "nlb_dns_name": "nlb.dns", "nlb_hosted_zone_id": "ZNLB"
        }
        with tempfile.TemporaryDirectory() as temp_dir_path:
            with self.assertLogs(logger='app.services.terraform_service', level='ERROR') as log_watcher:
                tf_file_path = generate_route53_acm_tf_config(context, temp_dir_path)
        self.assertIsNone(tf_file_path)
        self.assertTrue(any("Template 'terraform/aws/route53_acm.tf.j2' not found" in msg for msg in log_watcher.output))
        logger.info("test_generate_route53_acm_tf_config_template_not_found passed.")


if __name__ == '__main__':
    expected_kind_template = ts.TEMPLATE_BASE_DIR / "kind/kind-config.yaml.j2"
    expected_ec2_template = ts.TEMPLATE_BASE_DIR / "terraform/aws/ec2_instance.tf.j2"
    expected_eks_template = ts.TEMPLATE_BASE_DIR / "terraform/aws/eks_cluster.tf.j2"
    expected_ecr_template = ts.TEMPLATE_BASE_DIR / "terraform/aws/ecr_repository.tf.j2"
    expected_route53_acm_template = ts.TEMPLATE_BASE_DIR / "terraform/aws/route53_acm.tf.j2" # Added
    expected_script_template = ts.TEMPLATE_BASE_DIR / "scripts/ec2_bootstrap.sh.j2"
    if not expected_kind_template.exists():
        logger.warning(f"Kind template {expected_kind_template} not found.")
    if not expected_ec2_template.exists():
        logger.warning(f"EC2 TF template {expected_ec2_template} not found.")
    if not expected_eks_template.exists():
        logger.warning(f"EKS TF template {expected_eks_template} not found.")
    if not expected_ecr_template.exists():
        logger.warning(f"ECR TF template {expected_ecr_template} not found.")
    if not expected_route53_acm_template.exists(): # Added
        logger.warning(f"Route53/ACM TF template {expected_route53_acm_template} not found.")
    if not expected_script_template.exists():
        logger.warning(f"EC2 Bootstrap Script template {expected_script_template} not found.")
    unittest.main()
