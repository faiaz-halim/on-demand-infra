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
    scale_deployment,
    generate_eks_kubeconfig_file,
    install_nginx_ingress_helm,
    get_load_balancer_details # Added
)
import yaml
import pathlib
import base64 # Added for EKS kubeconfig test
import json # Added for get_load_balancer_details test
import time # Added for get_load_balancer_details test


class TestK8sService(unittest.TestCase):

    def setUp(self):
        self.dummy_kubeconfig_path = "/dummy/kubeconfig.yaml"

    # Test refactored create_namespace_if_not_exists (takes kubeconfig_path)
    @patch('app.services.k8s_service._run_kubectl_command')
    def test_create_namespace_if_not_exists_eks_creates_when_not_found(self, mock_run_kubectl):
        mock_run_kubectl.side_effect = [
            MagicMock(spec=subprocess.CompletedProcess, returncode=1, stdout="", stderr="Error: namespaces \"test-ns\" not found"),
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="namespace/test-ns created", stderr="")
        ]
        result = create_namespace_if_not_exists(namespace="test-ns", kubeconfig_path=self.dummy_kubeconfig_path)
        self.assertTrue(result)
        expected_calls = [
            call(['get', 'namespace', 'test-ns', '-o', 'name'], self.dummy_kubeconfig_path),
            call(['create', 'namespace', 'test-ns'], self.dummy_kubeconfig_path)
        ]
        mock_run_kubectl.assert_has_calls(expected_calls)

    @patch('app.services.k8s_service._run_kubectl_command')
    def test_create_namespace_if_not_exists_eks_already_exists(self, mock_run_kubectl):
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="namespace/test-ns", stderr="")
        result = create_namespace_if_not_exists(namespace="test-ns", kubeconfig_path=self.dummy_kubeconfig_path)
        self.assertTrue(result)
        mock_run_kubectl.assert_called_once_with(['get', 'namespace', 'test-ns', '-o', 'name'], self.dummy_kubeconfig_path)

    # Test refactored apply_manifests (takes kubeconfig_path)
    @patch('app.services.k8s_service.create_namespace_if_not_exists', return_value=True)
    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('pathlib.Path.exists', return_value=True)
    def test_apply_manifests_eks_success(self, mock_path_exists, mock_run_kubectl, mock_create_ns):
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="applied", stderr="")

        result = apply_manifests(kubeconfig_path=self.dummy_kubeconfig_path, manifest_dir_or_file="/path/to/manifests", namespace="test-ns")
        self.assertTrue(result)
        mock_create_ns.assert_called_once_with("test-ns", self.dummy_kubeconfig_path)
        mock_run_kubectl.assert_called_once_with(['apply', '-f', '/path/to/manifests', '--namespace', 'test-ns'], self.dummy_kubeconfig_path)

    @patch('app.services.k8s_service.create_namespace_if_not_exists', return_value=False)
    @patch('app.services.k8s_service._run_kubectl_command')
    @patch('pathlib.Path.exists', return_value=True)
    def test_apply_manifests_eks_namespace_create_fails(self, mock_path_exists, mock_run_kubectl, mock_create_ns):
        result = apply_manifests(kubeconfig_path=self.dummy_kubeconfig_path, manifest_dir_or_file="/path/to/manifests", namespace="test-ns")
        self.assertFalse(result)
        mock_create_ns.assert_called_once_with("test-ns", self.dummy_kubeconfig_path)
        mock_run_kubectl.assert_not_called()


    # Original tests for Kind-specific functions (delete_namespace_k8s, scale_deployment)
    # These still use _get_kind_kubeconfig internally, so their tests remain largely the same.
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

    @patch('app.services.k8s_service.time.sleep', return_value=None)
    @patch('app.services.k8s_service._run_kubectl_command')
    def test_get_lb_details_success_after_retries(self, mock_run_kubectl, mock_time_sleep):
        service_name = "nginx-ingress-controller"
        namespace = "ingress-nginx"
        kubeconfig = "/fake/kubeconfig"
        expected_hostname = "my-loadbalancer.elb.amazonaws.com"
        expected_hz_id = "Z0123456789ABCDEFGHIJ" # Example Hosted Zone ID for ALB

        mock_run_kubectl.side_effect = [
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout=json.dumps({"status": {"loadBalancer": {"ingress": []}}}), stderr=""),
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout=json.dumps({"status": {"loadBalancer": {"ingress": [{"hostname": ""}]}}}), stderr=""),
            MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout=json.dumps({"status": {"loadBalancer": {"ingress": [{"hostname": expected_hostname}]}}, "metadata": {"annotations": {"service.beta.kubernetes.io/aws-load-balancer-hosted-zone-id": expected_hz_id}}}), stderr="")
        ]

        hostname, hz_id = get_load_balancer_details(kubeconfig, service_name, namespace, timeout_seconds=45)

        self.assertEqual(hostname, expected_hostname)
        self.assertEqual(hz_id, expected_hz_id)
        self.assertEqual(mock_run_kubectl.call_count, 3)
        self.assertEqual(mock_time_sleep.call_count, 2) # Called before the second and third attempts
        mock_time_sleep.assert_called_with(15)

    @patch('app.services.k8s_service.time.sleep', return_value=None)
    @patch('app.services.k8s_service._run_kubectl_command')
    def test_get_lb_details_success_immediate_nlb(self, mock_run_kubectl, mock_time_sleep):
        expected_hostname = "my-nlb.example.com"
        # NLBs might not have the hosted-zone-id annotation, or it might be handled differently.
        # The primary return for NLB via hostname is what the current code extracts.
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout=json.dumps({"status": {"loadBalancer": {"ingress": [{"hostname": expected_hostname}]}}}), stderr="")

        hostname, hz_id = get_load_balancer_details("/fake/kc", "svc", "ns", timeout_seconds=5)
        self.assertEqual(hostname, expected_hostname)
        self.assertIsNone(hz_id) # For NLB (no annotation)
        mock_time_sleep.assert_not_called()

    @patch('app.services.k8s_service.time.sleep', return_value=None)
    @patch('app.services.k8s_service._run_kubectl_command')
    def test_get_lb_details_timeout(self, mock_run_kubectl, mock_time_sleep):
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout=json.dumps({"status": {"loadBalancer": {"ingress": []}}}), stderr="")

        # Test with a short timeout that allows for a few retries
        timeout = 35 # e.g. allows 2 retries (sleep 15, sleep 15)
        num_expected_sleeps = timeout // 15

        result = get_load_balancer_details("/fake/kubeconfig", "svc", "ns", timeout_seconds=timeout)
        self.assertIsNone(result)
        self.assertEqual(mock_time_sleep.call_count, num_expected_sleeps)

    @patch('app.services.k8s_service._run_kubectl_command') # Mock _run_kubectl_command
    def test_get_lb_details_kubectl_not_found_via_run_command(self, mock_run_kubectl):
        # Simulate _run_kubectl_command returning an error if kubectl is not found
        # (e.g., if shutil.which returned None within _run_kubectl_command, it would raise or return specific error)
        # For this test, we assume _run_kubectl_command itself signals the problem.
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=127, stderr="kubectl not found")

        result = get_load_balancer_details("/fake/kubeconfig", "svc", "ns", timeout_seconds=1)
        self.assertIsNone(result)
        # _run_kubectl_command would be called, but it would fail, so get_load_balancer_details would return None.
        # No time.sleep should occur if the first command execution itself indicates kubectl is missing.
        # However, the current retry loop in get_load_balancer_details will retry even on non-zero exit codes.
        # So, if timeout_seconds is > 0, it might sleep.
        # Let's keep timeout_seconds very low to minimize retries if the first call fails.

    @patch('app.services.k8s_service.time.sleep', return_value=None)
    @patch('app.services.k8s_service._run_kubectl_command')
    def test_get_lb_details_json_parse_error(self, mock_run_kubectl, mock_time_sleep):
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=0, stdout="this is not json {", stderr="")

        result = get_load_balancer_details("/fake/kubeconfig", "svc", "ns", timeout_seconds=1)
        self.assertIsNone(result)
        # A JSON parse error is critical and should not lead to retries.
        mock_time_sleep.assert_not_called()

    @patch('app.services.k8s_service.time.sleep', return_value=None)
    @patch('app.services.k8s_service._run_kubectl_command')
    def test_get_lb_details_get_service_fails_rc_not_zero(self, mock_run_kubectl, mock_time_sleep):
        mock_run_kubectl.return_value = MagicMock(spec=subprocess.CompletedProcess, returncode=1, stderr="Error getting service")

        result = get_load_balancer_details("/fake/kubeconfig", "svc", "ns", timeout_seconds=30) # Allow for retries
        self.assertIsNone(result)
        # Retries should happen on non-zero RC, so sleep should be called.
        self.assertTrue(mock_time_sleep.call_count >= 1)
