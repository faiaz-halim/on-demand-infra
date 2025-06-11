import unittest
from unittest.mock import patch, MagicMock, call
import subprocess
import os
import tempfile # Not strictly needed if _get_kind_kubeconfig is mocked directly
import shutil   # Not strictly needed if _get_kind_kubeconfig is mocked directly

# Adjust path as per your project structure
from app.services.k8s_service import (
    create_namespace_if_not_exists,
    apply_manifests,
    delete_namespace_k8s,
    scale_deployment
    # _get_kind_kubeconfig and _run_kubectl_command are implicitly tested via public methods,
    # but we will mock them.
    generate_eks_kubeconfig_file, # Added
    install_nginx_ingress_helm    # Added
)
import yaml     # Added
import pathlib  # Added

class TestK8sService(unittest.TestCase):

    def setUp(self):
        # Common dummy path for mocked kubeconfig
        self.dummy_kubeconfig_path = "/dummy/kubeconfig.yaml"

    # Test create_namespace_if_not_exists
    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    def test_create_namespace_if_not_exists_creates_when_not_found(self, mock_get_kubeconfig, mock_run_kubectl):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path

        # Simulate namespace not found, then successful creation
        mock_run_kubectl.side_effect = [
            MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="Error: namespaces \"test-ns\" not found"),
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="namespace/test-ns created", stderr="")
        ]

        result = create_namespace_if_not_exists(namespace="test-ns", cluster_name="test-cluster")
        self.assertTrue(result)

        expected_calls = [
            call(['get', 'namespace', 'test-ns', '-o', 'name'], self.dummy_kubeconfig_path),
            call(['create', 'namespace', 'test-ns'], self.dummy_kubeconfig_path)
        ]
        mock_run_kubectl.assert_has_calls(expected_calls)
        mock_get_kubeconfig.assert_called_once_with("test-cluster")

    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    def test_create_namespace_if_not_exists_already_exists(self, mock_get_kubeconfig, mock_run_kubectl):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="namespace/test-ns", stderr="")

        result = create_namespace_if_not_exists(namespace="test-ns", cluster_name="test-cluster")
        self.assertTrue(result)
        mock_run_kubectl.assert_called_once_with(['get', 'namespace', 'test-ns', '-o', 'name'], self.dummy_kubeconfig_path)

    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    def test_create_namespace_if_not_exists_creation_fails(self, mock_get_kubeconfig, mock_run_kubectl):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path
        mock_run_kubectl.side_effect = [
            MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="Error: namespaces \"test-ns\" not found"),
            MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="creation failed")
        ]

        result = create_namespace_if_not_exists(namespace="test-ns", cluster_name="test-cluster")
        self.assertFalse(result)

    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig', return_value=None) # Simulate failure to get kubeconfig
    def test_create_namespace_if_not_exists_kubeconfig_fails(self, mock_get_kubeconfig, mock_run_kubectl):
        result = create_namespace_if_not_exists(namespace="test-ns", cluster_name="test-cluster")
        self.assertFalse(result)
        mock_run_kubectl.assert_not_called()

    # Test apply_manifests
    @patch('app.services.k8s_service.create_namespace_if_not_exists', return_value=True)
    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    @patch('pathlib.Path.exists', return_value=True) # Mock that manifest file/dir exists
    def test_apply_manifests_success(self, mock_path_exists, mock_get_kubeconfig, mock_run_kubectl, mock_create_ns):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="applied", stderr="")

        result = apply_manifests(manifest_dir_or_file="/path/to/manifests", cluster_name="test-cluster", namespace="test-ns")
        self.assertTrue(result)
        mock_create_ns.assert_called_once_with("test-ns", "test-cluster")
        mock_run_kubectl.assert_called_once_with(['apply', '-f', '/path/to/manifests', '--namespace', 'test-ns'], self.dummy_kubeconfig_path)

    @patch('app.services.k8s_service.create_namespace_if_not_exists', return_value=False) # Simulate namespace creation failure
    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    @patch('pathlib.Path.exists', return_value=True)
    def test_apply_manifests_namespace_creation_fails(self, mock_path_exists, mock_get_kubeconfig, mock_run_kubectl, mock_create_ns):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path # Though it might not be reached

        result = apply_manifests(manifest_dir_or_file="/path/to/manifests", cluster_name="test-cluster", namespace="test-ns")
        self.assertFalse(result)
        mock_create_ns.assert_called_once_with("test-ns", "test-cluster")
        mock_run_kubectl.assert_not_called() # Apply should not be called

    @patch('app.services.k8s_service.create_namespace_if_not_exists', return_value=True)
    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    @patch('pathlib.Path.exists', return_value=True)
    def test_apply_manifests_kubectl_apply_fails(self, mock_path_exists, mock_get_kubeconfig, mock_run_kubectl, mock_create_ns):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="apply failed")

        result = apply_manifests(manifest_dir_or_file="/path/to/manifests", cluster_name="test-cluster", namespace="test-ns")
        self.assertFalse(result)

    @patch('pathlib.Path.exists', return_value=False) # Manifest file/dir does not exist
    @patch('app.services.k8s_service.create_namespace_if_not_exists', return_value=True)
    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    def test_apply_manifests_path_not_exist(self, mock_get_kubeconfig, mock_run_kubectl, mock_create_ns, mock_path_exists):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path

        result = apply_manifests(manifest_dir_or_file="/invalid/path", cluster_name="test-cluster", namespace="test-ns")
        self.assertFalse(result)
        mock_create_ns.assert_called_once_with("test-ns", "test-cluster") # Namespace check happens before path check in current code
        # _run_kubectl_command for apply should not be called if path doesn't exist
        # Check that it's called 0 times for apply (mock_run_kubectl might be called by create_namespace)
        # This requires more specific mocking if _run_kubectl_command is shared.
        # For simplicity, we assume create_namespace_if_not_exists is correctly mocked and isolated.

        # Filter calls to ensure the 'apply' command was not made
        apply_cmd_called = any(
            call_args[0][0][0] == 'apply' for call_args in mock_run_kubectl.call_args_list
        )
        self.assertFalse(apply_cmd_called, "_run_kubectl_command should not be called for 'apply' if path does not exist")


    # Test delete_namespace_k8s
    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    def test_delete_namespace_k8s_success(self, mock_get_kubeconfig, mock_run_kubectl):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="namespace/test-ns deleted", stderr="")

        result = delete_namespace_k8s(namespace="test-ns", cluster_name="test-cluster")
        self.assertTrue(result)
        mock_run_kubectl.assert_called_once_with(['delete', 'namespace', 'test-ns', '--ignore-not-found=true'], self.dummy_kubeconfig_path)

    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    def test_delete_namespace_k8s_failure(self, mock_get_kubeconfig, mock_run_kubectl):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="delete error")

        result = delete_namespace_k8s(namespace="test-ns", cluster_name="test-cluster")
        self.assertFalse(result)

    # Test scale_deployment
    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    def test_scale_deployment_success(self, mock_get_kubeconfig, mock_run_kubectl):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="deployment.apps/my-app scaled", stderr="")

        result = scale_deployment(deployment_name="my-app", namespace="test-ns", replicas=3, cluster_name="test-cluster")
        self.assertTrue(result)
        mock_run_kubectl.assert_called_once_with(['scale', 'deployment', 'my-app', '--replicas=3', '--namespace', 'test-ns'], self.dummy_kubeconfig_path)

    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('app.services.k8s_service._get_kind_kubeconfig')
    def test_scale_deployment_failure(self, mock_get_kubeconfig, mock_run_kubectl):
        mock_get_kubeconfig.return_value = self.dummy_kubeconfig_path
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="scale failed")

        result = scale_deployment(deployment_name="my-app", namespace="test-ns", replicas=3, cluster_name="test-cluster")
        self.assertFalse(result)

if __name__ == '__main__':
    unittest.main()


class TestK8sServiceEKS(unittest.TestCase):
    def setUp(self):
        self.cluster_name = "test-eks-cluster"
        self.endpoint_url = "https://test-eks-endpoint.com"
        self.ca_data = base64.b64encode(b"test_ca_cert_data").decode('utf-8')
        self.aws_region = "us-east-1"
        self.user_arn = "arn:aws:iam::123456789012:user/test-user"

    @patch('app.services.k8s_service.pathlib.Path.mkdir')
    @patch('app.services.k8s_service.yaml.dump')
    @patch('app.services.k8s_service.open', new_callable=unittest.mock.mock_open)
    def test_generate_eks_kubeconfig_file_success_with_user_arn(self, mock_open_file, mock_yaml_dump, mock_mkdir):
        with tempfile.TemporaryDirectory() as temp_dir:
            kubeconfig_path = generate_eks_kubeconfig_file(
                self.cluster_name, self.endpoint_url, self.ca_data, self.aws_region, self.user_arn, temp_dir
            )
            self.assertIsNotNone(kubeconfig_path)
            self.assertEqual(kubeconfig_path, str(pathlib.Path(temp_dir) / f"kubeconfig_{self.cluster_name}.yaml"))

            mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)
            mock_open_file.assert_called_once_with(pathlib.Path(kubeconfig_path), "w")

            # Verify structure of dumped YAML
            args_list = mock_yaml_dump.call_args_list
            self.assertEqual(len(args_list), 1)
            dumped_config = args_list[0][0][0] # First arg of first call

            self.assertEqual(dumped_config['apiVersion'], 'v1')
            self.assertEqual(dumped_config['clusters'][0]['name'], self.cluster_name)
            self.assertEqual(dumped_config['clusters'][0]['cluster']['server'], self.endpoint_url)
            self.assertEqual(dumped_config['clusters'][0]['cluster']['certificate-authority-data'], self.ca_data)
            self.assertEqual(dumped_config['users'][0]['user']['exec']['args'], [
                "eks", "get-token", "--cluster-name", self.cluster_name, "--role-arn", self.user_arn
            ])

    @patch('app.services.k8s_service.pathlib.Path.mkdir')
    @patch('app.services.k8s_service.yaml.dump')
    @patch('app.services.k8s_service.open', new_callable=unittest.mock.mock_open)
    def test_generate_eks_kubeconfig_file_success_no_user_arn(self, mock_open_file, mock_yaml_dump, mock_mkdir):
        with tempfile.TemporaryDirectory() as temp_dir:
            kubeconfig_path = generate_eks_kubeconfig_file(
                self.cluster_name, self.endpoint_url, self.ca_data, self.aws_region, None, temp_dir
            )
            self.assertIsNotNone(kubeconfig_path)
            dumped_config = mock_yaml_dump.call_args[0][0]
            self.assertEqual(dumped_config['users'][0]['user']['exec']['args'], [
                "eks", "get-token", "--cluster-name", self.cluster_name
            ])

    @patch('app.services.k8s_service.open', side_effect=IOError("Failed to write"))
    def test_generate_eks_kubeconfig_file_write_error(self, mock_open_file):
        with tempfile.TemporaryDirectory() as temp_dir:
            kubeconfig_path = generate_eks_kubeconfig_file(
                self.cluster_name, self.endpoint_url, self.ca_data, self.aws_region, None, temp_dir
            )
            self.assertIsNone(kubeconfig_path)

    @patch('app.services.k8s_service.subprocess.run')
    @patch('app.services.k8s_service.shutil.which', return_value="/fake/path/to/helm")
    def test_install_nginx_ingress_helm_success(self, mock_shutil_which, mock_subprocess_run):
        # Simulate successful Helm commands
        mock_subprocess_run.side_effect = [
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Repo added", stderr=""), # helm repo add
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Repo updated", stderr=""), # helm repo update
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Nginx installed", stderr="") # helm install
        ]

        result = install_nginx_ingress_helm("dummy_kubeconfig.yaml", namespace="test-ingress", helm_chart_version="4.0.0")
        self.assertTrue(result)

        env_with_kubeconfig = os.environ.copy()
        env_with_kubeconfig["KUBECONFIG"] = "dummy_kubeconfig.yaml"

        expected_calls = [
            call(['/fake/path/to/helm', 'repo', 'add', 'ingress-nginx', 'https://kubernetes.github.io/ingress-nginx', '--force-update'], capture_output=True, text=True, check=False, env=env_with_kubeconfig),
            call(['/fake/path/to/helm', 'repo', 'update'], capture_output=True, text=True, check=False, env=env_with_kubeconfig),
            call(['/fake/path/to/helm', 'upgrade', '--install', 'ingress-nginx', 'ingress-nginx/ingress-nginx', '--namespace', 'test-ingress', '--create-namespace', '--version', '4.0.0', '--set', 'controller.service.type=LoadBalancer'], capture_output=True, text=True, check=False, env=env_with_kubeconfig)
        ]
        mock_subprocess_run.assert_has_calls(expected_calls)

    @patch('app.services.k8s_service.subprocess.run')
    @patch('app.services.k8s_service.shutil.which', return_value="/fake/path/to/helm")
    def test_install_nginx_ingress_helm_repo_add_fails_strict(self, mock_shutil_which, mock_subprocess_run):
        mock_subprocess_run.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="Failed to add repo strictly")
        result = install_nginx_ingress_helm("dummy_kubeconfig.yaml")
        self.assertFalse(result)

    @patch('app.services.k8s_service.subprocess.run')
    @patch('app.services.k8s_service.shutil.which', return_value="/fake/path/to/helm")
    def test_install_nginx_ingress_helm_repo_update_fails(self, mock_shutil_which, mock_subprocess_run):
        mock_subprocess_run.side_effect = [
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Repo added", stderr=""), # helm repo add
            MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="Update failed")  # helm repo update
        ]
        result = install_nginx_ingress_helm("dummy_kubeconfig.yaml")
        self.assertFalse(result)

    @patch('app.services.k8s_service.subprocess.run')
    @patch('app.services.k8s_service.shutil.which', return_value="/fake/path/to/helm")
    def test_install_nginx_ingress_helm_install_fails_not_already_exists(self, mock_shutil_which, mock_subprocess_run):
        mock_subprocess_run.side_effect = [
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Repo added", stderr=""),
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Repo updated", stderr=""),
            MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="Install totally failed"), # helm install
            MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="Status check failed") # helm status
        ]
        result = install_nginx_ingress_helm("dummy_kubeconfig.yaml")
        self.assertFalse(result)

    @patch('app.services.k8s_service.subprocess.run')
    @patch('app.services.k8s_service.shutil.which', return_value="/fake/path/to/helm")
    def test_install_nginx_ingress_helm_install_fails_but_already_exists_and_healthy(self, mock_shutil_which, mock_subprocess_run):
        mock_subprocess_run.side_effect = [
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Repo added", stderr=""),
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="Repo updated", stderr=""),
            MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="cannot re-use a name that is still in use"), # helm install
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="STATUS: deployed", stderr="") # helm status
        ]
        result = install_nginx_ingress_helm("dummy_kubeconfig.yaml")
        self.assertTrue(result)

    @patch('app.services.k8s_service.shutil.which', return_value=None) # Helm CLI not found
    def test_install_nginx_ingress_helm_cli_not_found(self, mock_shutil_which):
        result = install_nginx_ingress_helm("dummy_kubeconfig.yaml")
        self.assertFalse(result)
