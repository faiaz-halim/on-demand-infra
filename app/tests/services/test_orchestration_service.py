import unittest
from unittest.mock import patch, AsyncMock, MagicMock, call
import subprocess
import os
import tempfile
import shutil
import pathlib
from pydantic import SecretStr
import uuid
import json
import time

from app.core.schemas import AWSCredentials, ChatCompletionRequest, ChatMessage, DockerImage
from app.services.orchestration_service import (
    handle_local_deployment,
    handle_cloud_local_deployment,
    handle_cloud_hosted_deployment,
    handle_cloud_local_decommission,
    handle_cloud_hosted_decommission, # Added
    handle_cloud_local_redeploy,
    handle_cloud_local_scale
)
from app.core.config import settings as app_settings
import docker # For type hinting


class TestOrchestrationService(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.repo_url = "https://github.com/test/repo.git"
        self.namespace = "test-ns"
        self.aws_creds = AWSCredentials(
            aws_access_key_id=SecretStr("test_access_key"),
            aws_secret_access_key=SecretStr("test_secret_key"),
            aws_region="us-east-1"
        )
        self.original_ec2_key_name = app_settings.EC2_DEFAULT_KEY_NAME
        self.original_private_key_base_path = app_settings.EC2_PRIVATE_KEY_BASE_PATH
        self.original_persistent_workspace_base_dir = app_settings.PERSISTENT_WORKSPACE_BASE_DIR

        self.test_persistent_workspaces = tempfile.mkdtemp(prefix="app_test_persistent_")
        app_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces

    def tearDown(self):
        app_settings.EC2_DEFAULT_KEY_NAME = self.original_ec2_key_name
        app_settings.EC2_PRIVATE_KEY_BASE_PATH = self.original_private_key_base_path
        app_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.original_persistent_workspace_base_dir
        shutil.rmtree(self.test_persistent_workspaces)

    async def test_handle_local_deployment_no_creds_involved(self):
        mock_chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="Deploy local please")])
        with patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/tmp/repo"}), \
             patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True, "image_id": "img123", "image_tags": ["tag1"]}), \
             patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value="dep_yaml"), \
             patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="svc_yaml"), \
             patch('app.services.orchestration_service.k8s_service.apply_manifests', return_value=True), \
             patch('app.services.orchestration_service.k8s_service.load_image_into_kind_cluster', return_value=True), \
             patch('app.services.orchestration_service.settings') as mock_settings, \
             patch('app.services.orchestration_service.pathlib.Path.mkdir'), \
             patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open), \
             patch('app.services.orchestration_service.shutil.rmtree'):

            mock_settings.KIND_CLUSTER_NAME = "test-kind-cluster"
            mock_settings.EC2_DEFAULT_APP_PORTS_JSON = json.dumps([{"port":8080, "targetPort": 80, "protocol":"TCP"}])

            response_dict = await handle_local_deployment(self.repo_url, self.namespace, mock_chat_request)
            self.assertTrue(len(mock_chat_request.messages) > 1)
            self.assertEqual(response_dict["status"], "success")
            self.assertIn("successfully deployed locally", response_dict["message"])

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script')
    @patch('app.services.orchestration_service.ssh_service.execute_remote_command')
    @patch('app.services.orchestration_service.ssh_service.upload_file_sftp', return_value=True)
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value="kind: Deployment...")
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="kind: Service...")
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_success(
        self, mock_orch_settings, mock_pathlib_Path,
        mock_gen_svc_manifest, mock_gen_dep_manifest, mock_upload_sftp, mock_ssh_exec,
        mock_gen_bootstrap, mock_gen_tf_config,
        mock_tf_init, mock_tf_apply, mock_tf_destroy,
        mock_mkdtemp, mock_rmtree):

        mock_orch_settings.EC2_DEFAULT_KEY_NAME = "default_key.pem"
        mock_orch_settings.DEFAULT_KIND_VERSION = "0.20.0"; mock_orch_settings.DEFAULT_KUBECTL_VERSION = "1.27.0"
        mock_orch_settings.EC2_DEFAULT_AMI_ID = "ami-settings"; mock_orch_settings.EC2_DEFAULT_INSTANCE_TYPE = "t2.small"
        mock_orch_settings.EC2_DEFAULT_APP_PORTS_JSON = json.dumps([{"port": 80, "protocol": "tcp", "targetPort": 8080}])
        mock_orch_settings.EC2_PRIVATE_KEY_BASE_PATH = "/test/keys"
        mock_orch_settings.EC2_SSH_USERNAME = "test-user"
        mock_orch_settings.EC2_DEFAULT_REPO_PATH = "/home/test-user/app"
        mock_orch_settings.KIND_CLUSTER_NAME = "appkind-cluster"
        mock_orch_settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH = "/tmp/app_manifests_remote"
        mock_orch_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces

        mock_path_instance = MagicMock(spec=pathlib.Path)
        mock_path_instance.exists.return_value = True
        mock_path_instance.__str__.return_value = "/test/keys/user_provided_key.pem"

        mock_persistent_workspace_path_obj = MagicMock(spec=pathlib.Path)
        mock_persistent_workspace_path_obj.__str__.return_value = f"{self.test_persistent_workspaces}/cloud-local/appcl-repo-testuuid"
        mock_persistent_workspace_path_obj.mkdir = MagicMock()
        mock_persistent_workspace_path_obj.__truediv__.side_effect = lambda p: pathlib.Path(str(mock_persistent_workspace_path_obj), p)

        def path_side_effect(arg):
            if arg == mock_orch_settings.EC2_PRIVATE_KEY_BASE_PATH: return mock_path_instance
            elif "appcl-repo-testuuid" in str(arg) : return mock_persistent_workspace_path_obj
            else: mp = MagicMock(spec=pathlib.Path); mp.__str__.return_value = str(arg); return mp
        mock_pathlib_Path.side_effect = path_side_effect
        mock_mkdtemp.return_value = "/mocked/local_manifest_temp"
        mock_gen_bootstrap.return_value = "#!/bin/bash\necho 'Mocked Bootstrap'"
        mock_gen_tf_config.return_value = str(mock_persistent_workspace_path_obj / "main.tf")
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_apply.return_value = (True, {"public_ip": {"value":"1.2.3.4"}, "instance_id": {"value":"i-123"}}, "Apply success", "")
        mock_ssh_exec.side_effect = [("Cloned successfully", "", 0), ("Image built successfully", "", 0), ("Image loaded into Kind", "", 0), ("Remote manifest dir created", "", 0), ("Manifests applied to K8s", "", 0), ("Remote manifests cleaned up", "", 0)]

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], ec2_key_name="user_provided_key.pem", target_namespace=self.namespace)
        with patch('app.services.orchestration_service.uuid.uuid4') as mock_uuid:
            mock_uuid.return_value.hex = "testuuid"
            response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success")
        self.assertIn("EC2 instance appcl-repo-testuuid is provisioning", response["message"])
        self.assertEqual(response["instance_id"], "appcl-repo-testuuid")
        mock_gen_tf_config.assert_called_with(unittest.mock.ANY, str(mock_persistent_workspace_path_obj))
        mock_rmtree.assert_called_once_with("/mocked/local_manifest_temp")
        mock_tf_destroy.assert_not_called()

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_decommission_success(self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree):
        instance_id = "appcl-testapp-123456"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_workspace_path_instance = MagicMock(); mock_workspace_path_instance.exists.return_value = True; mock_workspace_path_instance.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_instance
        mock_tf_init.return_value = (True, "Init success", ""); mock_tf_destroy.return_value = (True, "Destroy success", "")
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission")])
        response = await handle_cloud_local_decommission(instance_id, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "success")
        self.assertIn(f"Instance {instance_id} decommissioned and workspace cleaned.", response["message"])
        expected_workspace_path = str(pathlib.Path(self.test_persistent_workspaces) / "cloud-local" / instance_id)
        mock_tf_init.assert_called_once_with(expected_workspace_path, unittest.mock.ANY)
        mock_tf_destroy.assert_called_once_with(expected_workspace_path, unittest.mock.ANY)
        mock_rmtree.assert_called_once_with(mock_workspace_path_instance)

    async def test_handle_cloud_local_redeploy_stub_callable(self):
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="redeploy")])
        response = await handle_cloud_local_redeploy(instance_id="test-instance", public_ip="1.2.3.4", ec2_key_name="key.pem", repo_url=self.repo_url, namespace=self.namespace, aws_creds=self.aws_creds, chat_request=chat_request)
        self.assertEqual(response["status"], "pending_implementation")

    async def test_handle_cloud_local_scale_stub_callable(self):
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="scale")])
        response = await handle_cloud_local_scale(instance_id="test-instance", public_ip="1.2.3.4", ec2_key_name="key.pem", namespace=self.namespace, replicas=3, aws_creds=self.aws_creds, chat_request=chat_request)
        self.assertEqual(response["status"], "pending_implementation")

    # --- START: Helper for common cloud-hosted mock setup ---
    def _setup_common_cloud_hosted_mocks(self, mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp,
                                         mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
                                         mock_run_terraform_init, mock_run_terraform_apply,
                                         mock_clone_repository, mock_build_docker_image_locally,
                                         mock_get_ecr_login_details, mock_docker_from_env,
                                         mock_login_to_ecr, mock_push_image_to_ecr,
                                         mock_generate_deployment_manifest, mock_generate_service_manifest,
                                         mock_builtin_open,
                                         mock_generate_eks_kubeconfig_file,
                                         mock_install_nginx_ingress_helm
                                         ):

        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX = "appeks-test"; mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX = "appapp-test"
        mock_settings.EKS_DEFAULT_VPC_CIDR = "10.1.0.0/16"; mock_settings.EKS_DEFAULT_NUM_PUBLIC_SUBNETS = 1
        mock_settings.EKS_DEFAULT_NUM_PRIVATE_SUBNETS = 1; mock_settings.EKS_DEFAULT_VERSION = "1.27"
        mock_settings.EKS_DEFAULT_NODE_GROUP_NAME_SUFFIX = "ng-custom"; mock_settings.EKS_DEFAULT_NODE_INSTANCE_TYPE = "t3.small"
        mock_settings.EKS_DEFAULT_NODE_DESIRED_SIZE = 1; mock_settings.EKS_DEFAULT_NODE_MIN_SIZE = 1; mock_settings.EKS_DEFAULT_NODE_MAX_SIZE = 1
        mock_settings.ECR_DEFAULT_IMAGE_TAG_MUTABILITY = "IMMUTABLE"; mock_settings.ECR_DEFAULT_SCAN_ON_PUSH = False
        mock_settings.EC2_DEFAULT_APP_PORTS_JSON = json.dumps([{"port": 8080, "protocol": "tcp"}])
        mock_settings.DEFAULT_DOMAIN_NAME_FOR_APPS = "apptest.com"; mock_settings.NGINX_INGRESS_SERVICE_NAME = "ingress-nginx-controller-svc"
        mock_settings.NGINX_INGRESS_NAMESPACE = "ingress-nginx"; mock_settings.LOAD_BALANCER_DETAILS_TIMEOUT_SECONDS = 20
        mock_settings.ROUTE53_ACM_TF_FILENAME = "dns_and_cert_setup.tf"; mock_settings.EKS_DEFAULT_USER_ARN = "arn:aws:iam::123456789012:user/kubeconfig-user"
        mock_settings.NGINX_HELM_CHART_VERSION = "4.9.0"

        mock_uuid4.return_value.hex = "testuuid"
        _expected_repo_name_part_for_mock = self.repo_url.split('/')[-1].replace('.git', '')
        expected_cluster_name = f"{mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX}-{_expected_repo_name_part_for_mock}-{mock_uuid4.return_value.hex}"

        mock_persistent_workspace_base_path_obj = MagicMock(spec=pathlib.Path)
        mock_ph_cloud_hosted_obj = MagicMock(spec=pathlib.Path)
        mock_ph_cluster_dir_obj = MagicMock(spec=pathlib.Path)
        mock_ph_cluster_dir_obj.__str__.return_value = f"{self.test_persistent_workspaces}/cloud-hosted/{expected_cluster_name}"
        mock_ph_cluster_dir_obj.mkdir = MagicMock()
        mock_ph_cluster_dir_obj.__truediv__.side_effect = lambda p: pathlib.Path(str(mock_ph_cluster_dir_obj), p)

        mock_temp_clone_dir_str = "/tmp/app_clone_ch_helper"
        mock_mkdtemp.return_value = mock_temp_clone_dir_str
        mock_cloned_repo_subdir_path_obj = MagicMock(spec=pathlib.Path)
        mock_cloned_repo_subdir_path_obj.__str__.return_value = f"{mock_temp_clone_dir_str}/{_expected_repo_name_part_for_mock}"
        mock_cloned_repo_subdir_path_obj.exists.return_value = True; mock_cloned_repo_subdir_path_obj.is_dir.return_value = True
        mock_temp_clone_path_obj = MagicMock(spec=pathlib.Path)
        mock_temp_clone_path_obj.__str__.return_value = mock_temp_clone_dir_str
        mock_temp_clone_path_obj.__truediv__.return_value = mock_cloned_repo_subdir_path_obj
        mock_temp_clone_path_obj.exists.return_value = True

        def path_side_effect_func(arg_path):
            if str(arg_path) == mock_settings.PERSISTENT_WORKSPACE_BASE_DIR:
                mock_persistent_workspace_base_path_obj.__truediv__.return_value = mock_ph_cloud_hosted_obj
                mock_ph_cloud_hosted_obj.__truediv__.return_value = mock_ph_cluster_dir_obj
                return mock_persistent_workspace_base_path_obj
            elif str(arg_path) == mock_temp_clone_dir_str: return mock_temp_clone_path_obj
            new_mock_path = MagicMock(spec=pathlib.Path); new_mock_path.__str__.return_value = str(arg_path)
            new_mock_path.__truediv__.side_effect = lambda p: pathlib.Path(str(new_mock_path), p)
            return new_mock_path
        mock_pathlib_Path.side_effect = path_side_effect_func

        mock_generate_ecr_tf_config.return_value = str(mock_ph_cluster_dir_obj / "test_ecr.tf")
        mock_generate_eks_tf_config.return_value = str(mock_ph_cluster_dir_obj / "test_eks.tf")
        mock_run_terraform_init.return_value = (True, "init_success_stdout", "")

        mock_tf_outputs_eks_ecr = {"ecr_repository_url": {"value": f"12345.dkr.ecr.{self.aws_creds.aws_region}.amazonaws.com/{mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{_expected_repo_name_part_for_mock}-testuuid"},
                                 "ecr_repository_name": {"value": f"{mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{_expected_repo_name_part_for_mock}-testuuid"},
                                 "eks_cluster_endpoint": {"value": "test_eks_ep_output"}, "eks_cluster_ca_data": {"value": "test_ca_data"}, "vpc_id": {"value": "vpc-12345"}}
        mock_run_terraform_apply.return_value = (True, mock_tf_outputs_eks_ecr, "apply_stdout_eks_ecr", "")

        mock_clone_repository.return_value = {"success": True, "cloned_path": str(mock_cloned_repo_subdir_path_obj)}
        expected_local_tag_for_test = f"{_expected_repo_name_part_for_mock}-app:{mock_uuid4.return_value.hex}"
        mock_build_docker_image_locally.return_value = {"success": True, "image_id": "img123", "image_tags": [expected_local_tag_for_test]}
        mock_ecr_registry_from_token = f"https://12345.dkr.ecr.{self.aws_creds.aws_region}.amazonaws.com"
        mock_get_ecr_login_details.return_value = ("AWS", "ecr_pass_secret", mock_ecr_registry_from_token)
        mock_docker_client_instance = MagicMock(spec=docker.DockerClient); mock_docker_from_env.return_value = mock_docker_client_instance
        mock_login_to_ecr.return_value = True
        pushed_ecr_uri = f"{mock_ecr_registry_from_token.replace('https://', '')}/{mock_tf_outputs_eks_ecr['ecr_repository_name']['value']}:latest"
        mock_push_image_to_ecr.return_value = pushed_ecr_uri
        mock_generate_deployment_manifest.return_value = "kind: Deployment YAML content"; mock_generate_service_manifest.return_value = "kind: Service YAML content"
        mock_kubeconfig_path_str_expected = str(mock_ph_cluster_dir_obj / f"kubeconfig_{expected_cluster_name}.yaml")
        mock_generate_eks_kubeconfig_file.return_value = mock_kubeconfig_path_str_expected
        mock_install_nginx_ingress_helm.return_value = True
        return {"mock_ph_cluster_dir_obj": mock_ph_cluster_dir_obj, "mock_temp_clone_path_obj": mock_temp_clone_path_obj, "expected_cluster_name": expected_cluster_name,
                "_expected_repo_name_part_for_mock": _expected_repo_name_part_for_mock, "pushed_ecr_uri": pushed_ecr_uri, "mock_tf_outputs_eks_ecr": mock_tf_outputs_eks_ecr,
                "mock_kubeconfig_path_str_expected": mock_kubeconfig_path_str_expected, "expected_local_tag_for_test": expected_local_tag_for_test,
                "aws_env_vars_expected": {"AWS_ACCESS_KEY_ID": self.aws_creds.aws_access_key_id.get_secret_value(), "AWS_SECRET_ACCESS_KEY": self.aws_creds.aws_secret_access_key.get_secret_value(), "AWS_DEFAULT_REGION": self.aws_creds.aws_region}}
    # --- END: Helper for common cloud-hosted mock setup ---

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_success(
        self, mock_builtin_open,
        mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest,
        mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config,
        mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(
            mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp,
            mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
            mock_run_terraform_init, mock_run_terraform_apply,
            mock_clone_repository, mock_build_docker_image_locally,
            mock_get_ecr_login_details, mock_docker_from_env,
            mock_login_to_ecr, mock_push_image_to_ecr,
            mock_generate_deployment_manifest, mock_generate_service_manifest,
            mock_builtin_open,
            mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm
        )
        mock_ph_cluster_dir_obj = common_mocks["mock_ph_cluster_dir_obj"]; expected_cluster_name = common_mocks["expected_cluster_name"]
        _expected_repo_name_part_for_mock = common_mocks["_expected_repo_name_part_for_mock"]; pushed_ecr_uri = common_mocks["pushed_ecr_uri"]
        mock_tf_outputs_eks_ecr = common_mocks["mock_tf_outputs_eks_ecr"]; mock_kubeconfig_path_str_expected = common_mocks["mock_kubeconfig_path_str_expected"]
        aws_env_vars_expected = common_mocks["aws_env_vars_expected"]

        nlb_dns_name_mocked = "test-nlb-abcdefg.elb.us-east-1.amazonaws.com"; nlb_hosted_zone_id_mocked = "Z00NLBHOSTEDZONEIDTEST"
        mock_get_load_balancer_details.return_value = (nlb_dns_name_mocked, nlb_hosted_zone_id_mocked)
        mock_route53_acm_tf_file_path_expected = str(mock_ph_cluster_dir_obj / mock_settings.ROUTE53_ACM_TF_FILENAME)
        mock_generate_route53_acm_tf_config.return_value = mock_route53_acm_tf_file_path_expected
        mock_tf_outputs_domain_expected = {"acm_certificate_arn": {"value": "arn:aws:acm:us-east-1:123456789012:certificate/final-mock-cert-arn"}, "app_url_https": {"value": f"https://testapp.{mock_settings.DEFAULT_DOMAIN_NAME_FOR_APPS}"}}
        mock_run_terraform_apply.side_effect = [(True, mock_tf_outputs_eks_ecr, "apply_stdout_eks_ecr", ""), (True, mock_tf_outputs_domain_expected, "apply_stdout_domain", "")]
        mock_generate_ingress_manifest.return_value = "kind: Ingress YAML content for test"; mock_k8s_apply_manifests.return_value = True

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy cloud hosted with domain")], base_hosted_zone_id="Z00TESTHOSTEDIDFORAPP123", app_subdomain_label="testapp", github_repo_url=self.repo_url, deployment_mode="cloud-hosted", aws_credentials=self.aws_creds, target_namespace=self.namespace)
        response = await handle_cloud_hosted_deployment(repo_url=chat_request.github_repo_url, namespace=chat_request.target_namespace, aws_creds=chat_request.aws_credentials, chat_request=chat_request, instance_id_override=chat_request.instance_id_override)

        self.assertEqual(mock_run_terraform_apply.call_count, 2)
        self.assertEqual(mock_run_terraform_apply.call_args_list[1], call(str(mock_ph_cluster_dir_obj), aws_env_vars_expected, mock_route53_acm_tf_file_path_expected))
        app_name_expected = _expected_repo_name_part_for_mock.lower()
        expected_ingress_file_path = str(mock_ph_cluster_dir_obj / f"{app_name_expected}_ingress.yaml")
        mock_builtin_open.assert_any_call(expected_ingress_file_path, "w")
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["app_url_https"], mock_tf_outputs_domain_expected["app_url_https"]["value"])
        self.assertIn(f"Deployment successful. App URL: {mock_tf_outputs_domain_expected['app_url_https']['value']}", response["message"])

    # --- START: New Failure Tests for handle_cloud_hosted_deployment ---
    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_get_lb_details_fails(
        self, mock_builtin_open, mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, mock_builtin_open, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)
        mock_get_load_balancer_details.return_value = None
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to get Nginx Load Balancer details", chat_request.messages[-1].content)
        mock_generate_route53_acm_tf_config.assert_not_called(); mock_generate_ingress_manifest.assert_not_called(); mock_k8s_apply_manifests.assert_not_called()
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_route53_acm_gen_fails(
        self, mock_builtin_open, mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, mock_builtin_open, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)
        mock_get_load_balancer_details.return_value = ("my-nlb.example.com", "ZNLBHZID")
        mock_generate_route53_acm_tf_config.return_value = None
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error"); self.assertIn("Failed to generate Route53/ACM Terraform config", chat_request.messages[-1].content)
        self.assertEqual(mock_run_terraform_apply.call_count, 1); mock_generate_ingress_manifest.assert_not_called(); mock_k8s_apply_manifests.assert_not_called()
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_route53_acm_apply_fails(
        self, mock_builtin_open, mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, mock_builtin_open, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)
        mock_get_load_balancer_details.return_value = ("my-nlb.example.com", "ZNLBHZID")
        mock_generate_route53_acm_tf_config.return_value = str(common_mocks["mock_ph_cluster_dir_obj"] / "domain_infra.tf")
        mock_run_terraform_apply.side_effect = [(True, common_mocks["mock_tf_outputs_eks_ecr"], "apply_stdout_eks_ecr", ""), (False, {}, "", "Route53/ACM apply failed miserably")]
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error"); self.assertIn("Terraform apply failed for Route53/ACM setup: Route53/ACM apply failed miserably", chat_request.messages[-1].content)
        self.assertEqual(mock_run_terraform_apply.call_count, 2); mock_generate_ingress_manifest.assert_not_called(); mock_k8s_apply_manifests.assert_not_called()
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_ingress_gen_fails(
        self, mock_builtin_open, mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, mock_builtin_open, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)
        mock_get_load_balancer_details.return_value = ("my-nlb.example.com", "ZNLBHZID")
        mock_generate_route53_acm_tf_config.return_value = str(common_mocks["mock_ph_cluster_dir_obj"] / "domain_infra.tf")
        mock_tf_outputs_domain_expected = {"acm_certificate_arn": {"value": "arn:test_cert"}, "app_url_https": {"value": "https://test.com"}}
        mock_run_terraform_apply.side_effect = [(True, common_mocks["mock_tf_outputs_eks_ecr"], "apply_stdout_eks_ecr", ""), (True, mock_tf_outputs_domain_expected, "apply_stdout_domain", "")]
        mock_generate_ingress_manifest.return_value = None
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error"); self.assertIn("Failed to generate Ingress manifest", chat_request.messages[-1].content)
        mock_k8s_apply_manifests.assert_not_called()
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_ingress_save_fails(
        self, mock_open_for_ingress_save,
        mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        with patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open) as m_open_for_app_manifests:
            common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, m_open_for_app_manifests, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)

        mock_get_load_balancer_details.return_value = ("my-nlb.example.com", "ZNLBHZID")
        mock_generate_route53_acm_tf_config.return_value = str(common_mocks["mock_ph_cluster_dir_obj"] / "domain_infra.tf")
        mock_tf_outputs_domain_expected = {"acm_certificate_arn": {"value": "arn:test_cert"}, "app_url_https": {"value": "https://test.com"}}
        mock_run_terraform_apply.side_effect = [(True, common_mocks["mock_tf_outputs_eks_ecr"], "apply_stdout_eks_ecr", ""), (True, mock_tf_outputs_domain_expected, "apply_stdout_domain", "")]
        mock_generate_ingress_manifest.return_value = "kind: Ingress YAML content"
        def open_side_effect(file_path, mode):
            if "ingress" in str(file_path).lower(): raise IOError("Disk is full, cannot save ingress")
            return unittest.mock.DEFAULT
        mock_open_for_ingress_save.side_effect = open_side_effect
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error"); self.assertIn("Failed to save K8s Ingress manifest: Disk is full, cannot save ingress", chat_request.messages[-1].content)
        mock_k8s_apply_manifests.assert_not_called()
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_final_k8s_apply_fails(
        self, mock_builtin_open, mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, mock_builtin_open, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)
        mock_get_load_balancer_details.return_value = ("my-nlb.example.com", "ZNLBHZID")
        mock_generate_route53_acm_tf_config.return_value = str(common_mocks["mock_ph_cluster_dir_obj"] / "domain_infra.tf")
        mock_tf_outputs_domain_expected = {"acm_certificate_arn": {"value": "arn:test_cert"}, "app_url_https": {"value": "https://test.com"}}
        mock_run_terraform_apply.side_effect = [(True, common_mocks["mock_tf_outputs_eks_ecr"], "apply_stdout_eks_ecr", ""), (True, mock_tf_outputs_domain_expected, "apply_stdout_domain", "")]
        mock_generate_ingress_manifest.return_value = "kind: Ingress YAML content"
        mock_k8s_apply_manifests.return_value = False # Simulate failure
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error"); self.assertIn("Failed to apply Kubernetes application manifests to EKS", chat_request.messages[-1].content)
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))
    # --- End of new failure tests ---

    # --- START: Tests for handle_cloud_hosted_decommission ---
    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_decommission_success(self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree):
        cluster_name = "my-eks-cluster-decom"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces

        mock_workspace_path_obj = MagicMock(spec=pathlib.Path)
        mock_workspace_path_obj.exists.return_value = True
        mock_workspace_path_obj.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_obj # For the main workspace path

        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_destroy.return_value = (True, "Destroy success", "")
        mock_rmtree.return_value = None # Simulate successful removal

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission cloud-hosted")])
        response = await handle_cloud_hosted_decommission(cluster_name, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success")
        self.assertIn(f"EKS cluster {cluster_name} decommissioned and workspace cleaned.", response["message"])
        self.assertIn(f"EKS cluster {cluster_name} decommissioned and workspace cleaned.", chat_request.messages[-1].content)

        expected_workspace_path_str = str(pathlib.Path(self.test_persistent_workspaces) / "cloud-hosted" / cluster_name)
        mock_tf_init.assert_called_once_with(expected_workspace_path_str, unittest.mock.ANY)
        mock_tf_destroy.assert_called_once_with(expected_workspace_path_str, unittest.mock.ANY)
        mock_rmtree.assert_called_once_with(mock_workspace_path_obj)

    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_decommission_workspace_not_found(self, mock_settings, mock_pathlib_Path):
        cluster_name = "nonexistent-eks-cluster"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces

        mock_workspace_path_obj = MagicMock(spec=pathlib.Path)
        mock_workspace_path_obj.exists.return_value = False # Simulate workspace not found
        mock_pathlib_Path.return_value = mock_workspace_path_obj

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission cloud-hosted")])
        response = await handle_cloud_hosted_decommission(cluster_name, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Workspace for EKS cluster 'nonexistent-eks-cluster' not found", response["message"])
        self.assertIn("Error: Workspace for EKS cluster 'nonexistent-eks-cluster' not found", chat_request.messages[-1].content)

    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_decommission_tf_init_fails(self, mock_settings, mock_pathlib_Path, mock_tf_init):
        cluster_name = "eks-tf-init-fails"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_workspace_path_obj = MagicMock(spec=pathlib.Path); mock_workspace_path_obj.exists.return_value = True; mock_workspace_path_obj.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_obj
        mock_tf_init.return_value = (False, "", "TF init failed error message")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission cloud-hosted")])
        response = await handle_cloud_hosted_decommission(cluster_name, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform init failed: TF init failed error message", response["message"])
        self.assertIn("Error during Terraform init for EKS workspace: TF init failed error message", chat_request.messages[-1].content)

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_decommission_tf_destroy_fails(self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree):
        cluster_name = "eks-tf-destroy-fails"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_workspace_path_obj = MagicMock(spec=pathlib.Path); mock_workspace_path_obj.exists.return_value = True; mock_workspace_path_obj.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_obj
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_destroy.return_value = (False, "", "TF destroy failed error message")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission cloud-hosted")])
        response = await handle_cloud_hosted_decommission(cluster_name, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform destroy failed: TF destroy failed error message", response["message"])
        self.assertIn("Error: Terraform destroy failed for EKS cluster eks-tf-destroy-fails. Details: TF destroy failed error message", chat_request.messages[-1].content)
        mock_rmtree.assert_not_called()

    @patch('app.services.orchestration_service.shutil.rmtree', side_effect=OSError("Test cleanup error"))
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_decommission_destroy_succeeds_cleanup_fails(self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree_cleanup):
        cluster_name = "eks-cleanup-fails"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_workspace_path_obj = MagicMock(spec=pathlib.Path); mock_workspace_path_obj.exists.return_value = True; mock_workspace_path_obj.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_obj
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_destroy.return_value = (True, "Destroy success", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission cloud-hosted")])
        response = await handle_cloud_hosted_decommission(cluster_name, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success_with_cleanup_error")
        self.assertIn(f"EKS cluster {cluster_name} decommissioned, workspace cleanup failed: Test cleanup error", response["message"])
        self.assertIn(f"EKS cluster {cluster_name} decommissioned, but failed to clean up persistent workspace: Test cleanup error", chat_request.messages[-1].content)
    # --- END: Tests for handle_cloud_hosted_decommission ---

    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_ecr_gen_fails(
        self, mock_settings, mock_generate_ecr_tf_config, mock_generate_eks_tf_config
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_generate_ecr_tf_config.return_value = None # Simulate ECR config generation failure
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate ECR Terraform configuration", response["message"])
        self.assertIn("Failed to generate ECR Terraform configuration", chat_request.messages[-1].content)
        mock_generate_eks_tf_config.assert_not_called()

    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_eks_gen_fails(
        self, mock_settings, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_generate_ecr_tf_config.return_value = "path/to/ecr.tf"
        mock_generate_eks_tf_config.return_value = None # Simulate EKS config generation failure
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate EKS Terraform configuration", response["message"])
        mock_run_terraform_init.assert_not_called()

    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_tf_init_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_run_init, mock_run_apply
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_run_init.return_value = (False, "", "Terraform init failed spectacularly")
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform init failed: Terraform init failed spectacularly", response["message"])
        mock_run_apply.assert_not_called()

    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "init_success", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_tf_apply_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_run_init, mock_run_apply
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_run_apply.return_value = (False, {}, "", "Terraform apply exploded")
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform apply failed: Terraform apply exploded", response["message"])
        self.assertIn("Manual cleanup of AWS resources", chat_request.messages[-1].content) # Check for cleanup advice

    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply', new_callable=AsyncMock) # Ensure it's an async mock
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', new_callable=AsyncMock)
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', new_callable=MagicMock)
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', new_callable=MagicMock)
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_with_instance_id_override(
        self, mock_settings, mock_uuid4,
        mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX = "appeks"
        mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX = "appapp"

        # Make terraform_service functions awaitable mocks that return successful values
        mock_generate_ecr_tf_config.return_value = "path/to/ecr.tf"
        mock_generate_eks_tf_config.return_value = "path/to/eks.tf"
        mock_run_terraform_init.return_value = (True, "init_success_stdout", "")
        mock_tf_outputs = {"ecr_repository_url": {"value": "override_ecr_url"}, "eks_cluster_endpoint": {"value": "override_eks_ep"}}
        mock_run_terraform_apply.return_value = (True, mock_tf_outputs, "apply_stdout", "")

        instance_id_override = "my-custom-id"
        chat_request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="deploy cloud hosted with override")],
            instance_id_override=instance_id_override # Key for cloud-hosted override
        )

        await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )

        mock_uuid4.assert_not_called() # UUID should not be used if override is present

        expected_repo_name_part = "repo"
        expected_cluster_name_override = f"{mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX}-{expected_repo_name_part}-{instance_id_override}"
        expected_ecr_repo_name_override_raw = f"{mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{expected_repo_name_part}-{instance_id_override}"
        expected_ecr_repo_name_override = "".join(c if c.islower() or c.isdigit() or c in ['-', '_', '.'] else '-' for c in expected_ecr_repo_name_override_raw.lower()).strip('-_.')


        # Check that workspace path uses the override
        called_workspace_path_for_ecr = pathlib.Path(mock_generate_ecr_tf_config.call_args[0][1])
        self.assertIn(expected_cluster_name_override, str(called_workspace_path_for_ecr))

        called_workspace_path_for_eks = pathlib.Path(mock_generate_eks_tf_config.call_args[0][1])
        self.assertIn(expected_cluster_name_override, str(called_workspace_path_for_eks))

        # Check that names passed to TF generation use the override
        ecr_context_arg = mock_generate_ecr_tf_config.call_args[0][0]
        self.assertEqual(ecr_context_arg["ecr_repo_name"], expected_ecr_repo_name_override)

        eks_context_arg = mock_generate_eks_tf_config.call_args[0][0]
        self.assertEqual(eks_context_arg["cluster_name"], expected_cluster_name_override)

    @patch('app.services.orchestration_service.shutil.rmtree') # For cleanup
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value=None) # Simulate failure
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr', return_value="pushed_uri_example") # Assume previous steps succeed
    @patch('app.services.orchestration_service.docker_service.login_to_ecr', return_value=True)
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details', return_value=("user","pass","reg_url"))
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True})
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/mock/clone/repo"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_eks_manifest_gen_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks_tf, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_get_login, mock_docker_env, mock_login_ecr, mock_push,
        mock_gen_deploy, mock_mkdtemp, mock_rmtree):

        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_settings.EC2_DEFAULT_APP_PORTS_JSON = json.dumps([{"port": 8080}]) # Ensure it's parsable

        # Simulate successful Terraform apply with necessary outputs for ECR push
        mock_tf_outputs = {
            "ecr_repository_url": {"value": "test_ecr_url_output"},
            "ecr_repository_name": {"value": "appapp-test-repo-testuuid"},
        }
        mock_tf_apply.return_value = (True, mock_tf_outputs, "apply_stdout", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock();
            mock_clone_path_obj.exists.return_value = True # For cleanup check
            # Simulate Path(tempfile.mkdtemp()) behavior for the cleanup logic
            def path_side_effect_for_cleanup(arg_path):
                if str(arg_path) == "/mock/temp_clone_dir":
                    return mock_clone_path_obj
                return MagicMock() # Default for other Path calls
            mock_path_finally.side_effect = path_side_effect_for_cleanup

            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate Kubernetes manifests for EKS", response["message"])
        self.assertIn("Failed to generate Kubernetes manifests for EKS", chat_request.messages[-1].content)
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open) # Mock open
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="kind: Service...") # Success
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value="kind: Deployment...") # Success
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr', return_value="pushed_uri_example")
    @patch('app.services.orchestration_service.docker_service.login_to_ecr', return_value=True)
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details', return_value=("user","pass","reg_url"))
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True})
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/mock/clone/repo"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_eks_manifest_save_fails(
        self, mock_settings, mock_gen_ecr_tf, mock_gen_eks_tf, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_get_login, mock_docker_env, mock_login_ecr, mock_push,
        mock_gen_deploy, mock_gen_service, mock_builtin_open, mock_mkdtemp, mock_rmtree):

        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_settings.EC2_DEFAULT_APP_PORTS_JSON = json.dumps([{"port": 8080}])

        mock_tf_outputs = {
            "ecr_repository_url": {"value": "test_ecr_url_output"},
            "ecr_repository_name": {"value": "appapp-test-repo-testuuid"},
        }
        mock_tf_apply.return_value = (True, mock_tf_outputs, "apply_stdout", "")

        # Simulate IOError during file write
        mock_builtin_open.side_effect = IOError("Disk full simulation")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock();
            mock_clone_path_obj.exists.return_value = True
            def path_side_effect_save_fail(arg_path):
                if str(arg_path) == "/mock/temp_clone_dir": return mock_clone_path_obj
                # For persistent workspace path, make it return a mock that can be used in `open`
                mock_ws_path = MagicMock(spec=pathlib.Path)
                mock_ws_path.__str__.return_value = str(arg_path)
                mock_ws_path.__truediv__.side_effect = lambda p: pathlib.Path(str(mock_ws_path), p)
                return mock_ws_path
            mock_path_finally.side_effect = path_side_effect_save_fail

            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to save K8s manifests for EKS: Disk full simulation", response["message"])
        self.assertIn("Failed to save K8s manifests for EKS: Disk full simulation", chat_request.messages[-1].content)
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": False, "error": "Clone failed miserably"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply') # Assume TF part succeeds
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_clone_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_mkdtemp, mock_rmtree):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_tf_apply.return_value = (True, {"ecr_repository_url": {"value": "some_url"}, "ecr_repository_name": {"value":"some_repo"}}, "", "") # TF is fine

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        # Need to mock Path for the finally block's clone_workspace_path.exists() check
        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock()
            mock_clone_path_obj.exists.return_value = True # Assume it was created before failing
            mock_path_finally.return_value = mock_clone_path_obj

            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to clone repository", response["message"])
        self.assertIn("Clone failed miserably", chat_request.messages[-1].content)
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": False, "error": "Build exploded", "logs": "log log kaboom"})
    @patch('app.services.orchestration_service.git_service.clone_repository') # Success
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply') # TF Success
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_build_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_mkdtemp, mock_rmtree):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_tf_apply.return_value = (True, {"ecr_repository_url": {"value": "some_url"}, "ecr_repository_name": {"value":"some_repo"}}, "", "")

        # Mock for clone path and its subdirectories for build context
        mock_cloned_repo_subdir_path_obj = MagicMock(spec=pathlib.Path)
        mock_cloned_repo_subdir_path_obj.exists.return_value = True
        mock_cloned_repo_subdir_path_obj.is_dir.return_value = True
        mock_temp_clone_path_obj = MagicMock(spec=pathlib.Path)
        mock_temp_clone_path_obj.__truediv__.return_value = mock_cloned_repo_subdir_path_obj
        mock_temp_clone_path_obj.exists.return_value = True

        # This setup is a bit complex due to Path being used multiple times for different things.
        # We need clone_repository to use a Path object that gives the build context.
        mock_clone_repo.return_value = {"success": True, "cloned_path": str(mock_cloned_repo_subdir_path_obj)}

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            # Mock Path specifically for the clone directory operations and the finally block
            def path_side_effect_build_fail(arg_path):
                if str(arg_path) == "/mock/temp_clone_dir": # Path(tempfile.mkdtemp(...))
                    return mock_temp_clone_path_obj
                # Fallback for persistent workspace paths (not the focus of this specific failure test part)
                return MagicMock(spec=pathlib.Path)
            mock_path_finally.side_effect = path_side_effect_build_fail

            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to build Docker image", response["message"])
        self.assertIn("Build exploded", chat_request.messages[-1].content)
        self.assertIn("log log kaboom", chat_request.messages[-1].content)
        mock_rmtree.assert_called_once_with(str(mock_temp_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details', return_value=None)
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True})
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/mock/clone/repo"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_get_ecr_login_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_get_login_details, mock_mkdtemp, mock_rmtree):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_tf_apply.return_value = (True, {"ecr_repository_url": {"value": "some_url"}, "ecr_repository_name": {"value":"some_repo"}}, "", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock(); mock_clone_path_obj.exists.return_value = True
            mock_path_finally.return_value = mock_clone_path_obj
            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to get ECR login credentials", response["message"])
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.docker_service.login_to_ecr', return_value=False)
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details', return_value=("user","pass","reg_url"))
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True})
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/mock/clone/repo"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_ecr_login_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_get_login_details, mock_docker_from_env,
        mock_login_to_ecr, mock_mkdtemp, mock_rmtree):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_tf_apply.return_value = (True, {"ecr_repository_url": {"value": "some_url"}, "ecr_repository_name": {"value":"some_repo"}}, "", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock(); mock_clone_path_obj.exists.return_value = True
            mock_path_finally.return_value = mock_clone_path_obj
            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("ECR login failed", response["message"])
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr', return_value=None)
    @patch('app.services.orchestration_service.docker_service.login_to_ecr', return_value=True)
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details', return_value=("user","pass","reg_url"))
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True})
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/mock/clone/repo"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_ecr_push_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_get_login_details, mock_docker_from_env,
        mock_login_to_ecr, mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_tf_apply.return_value = (True, {"ecr_repository_url": {"value": "some_url"}, "ecr_repository_name": {"value":"some_repo"}}, "", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock(); mock_clone_path_obj.exists.return_value = True
            mock_path_finally.return_value = mock_clone_path_obj
            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to push image to ECR", response["message"])
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


if __name__ == '__main__':
    unittest.main()

    async def test_handle_local_deployment_no_creds_involved(self):
        mock_chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="Deploy local please")])
        with patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/tmp/repo"}) as mock_clone, \
             patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True, "image_id": "img123", "image_tags": ["tag1"]}) as mock_build, \
             patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value="dep_yaml") as mock_gen_dep, \
             patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="svc_yaml") as mock_gen_svc, \
             patch('app.services.orchestration_service.k8s_service.apply_manifests', return_value=True) as mock_apply, \
             patch('app.services.orchestration_service.k8s_service.load_image_into_kind_cluster', return_value=True) as mock_load_kind, \
             patch('app.services.orchestration_service.settings') as mock_settings, \
             patch('app.services.orchestration_service.pathlib.Path.mkdir'), \
             patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open) as mock_open_file, \
             patch('app.services.orchestration_service.shutil.rmtree'):

            mock_settings.KIND_CLUSTER_NAME = "test-kind-cluster"
            mock_settings.EC2_DEFAULT_APP_PORTS_JSON = json.dumps([{"port":8080, "targetPort": 80, "protocol":"TCP"}])

            response_dict = await handle_local_deployment(self.repo_url, self.namespace, mock_chat_request)
            self.assertTrue(len(mock_chat_request.messages) > 1)
            self.assertEqual(response_dict["status"], "success")
            self.assertIn("successfully deployed locally", response_dict["message"])

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ec2_bootstrap_script')
    @patch('app.services.orchestration_service.ssh_service.execute_remote_command')
    @patch('app.services.orchestration_service.ssh_service.upload_file_sftp', return_value=True)
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value="kind: Deployment...")
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="kind: Service...")
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_deployment_success(
        self, mock_orch_settings, mock_pathlib_Path,
        mock_gen_svc_manifest, mock_gen_dep_manifest, mock_upload_sftp, mock_ssh_exec,
        mock_gen_bootstrap, mock_gen_tf_config,
        mock_tf_init, mock_tf_apply, mock_tf_destroy,
        mock_mkdtemp, mock_rmtree):

        mock_orch_settings.EC2_DEFAULT_KEY_NAME = "default_key.pem"
        mock_orch_settings.DEFAULT_KIND_VERSION = "0.20.0"; mock_orch_settings.DEFAULT_KUBECTL_VERSION = "1.27.0"
        mock_orch_settings.EC2_DEFAULT_AMI_ID = "ami-settings"; mock_orch_settings.EC2_DEFAULT_INSTANCE_TYPE = "t2.small"
        mock_orch_settings.EC2_DEFAULT_APP_PORTS_JSON = json.dumps([{"port": 80, "protocol": "tcp", "targetPort": 8080}])
        mock_orch_settings.EC2_PRIVATE_KEY_BASE_PATH = "/test/keys"
        mock_orch_settings.EC2_SSH_USERNAME = "test-user"
        mock_orch_settings.EC2_DEFAULT_REPO_PATH = "/home/test-user/app"
        mock_orch_settings.KIND_CLUSTER_NAME = "appkind-cluster"
        mock_orch_settings.EC2_DEFAULT_REMOTE_MANIFEST_PATH = "/tmp/app_manifests_remote"
        mock_orch_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces

        mock_path_instance = MagicMock(spec=pathlib.Path)
        mock_path_instance.exists.return_value = True
        mock_path_instance.__str__.return_value = "/test/keys/user_provided_key.pem"

        mock_persistent_workspace_path_obj = MagicMock(spec=pathlib.Path)
        mock_persistent_workspace_path_obj.__str__.return_value = f"{self.test_persistent_workspaces}/cloud-local/appcl-repo-testuuid"
        mock_persistent_workspace_path_obj.mkdir = MagicMock()
        mock_persistent_workspace_path_obj.__truediv__.side_effect = lambda p: pathlib.Path(str(mock_persistent_workspace_path_obj), p)

        def path_side_effect(arg):
            if arg == mock_orch_settings.EC2_PRIVATE_KEY_BASE_PATH: return mock_path_instance
            elif "appcl-repo-testuuid" in str(arg) : return mock_persistent_workspace_path_obj
            else: mp = MagicMock(spec=pathlib.Path); mp.__str__.return_value = str(arg); return mp
        mock_pathlib_Path.side_effect = path_side_effect
        mock_mkdtemp.return_value = "/mocked/local_manifest_temp"
        mock_gen_bootstrap.return_value = "#!/bin/bash\necho 'Mocked Bootstrap'"
        mock_gen_tf_config.return_value = str(mock_persistent_workspace_path_obj / "main.tf")
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_apply.return_value = (True, {"public_ip": {"value":"1.2.3.4"}, "instance_id": {"value":"i-123"}}, "Apply success", "")
        mock_ssh_exec.side_effect = [("Cloned successfully", "", 0), ("Image built successfully", "", 0), ("Image loaded into Kind", "", 0), ("Remote manifest dir created", "", 0), ("Manifests applied to K8s", "", 0), ("Remote manifests cleaned up", "", 0)]

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], ec2_key_name="user_provided_key.pem", target_namespace=self.namespace)
        with patch('app.services.orchestration_service.uuid.uuid4') as mock_uuid:
            mock_uuid.return_value.hex = "testuuid"
            response = await handle_cloud_local_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success")
        self.assertIn("EC2 instance appcl-repo-testuuid is provisioning", response["message"])
        self.assertEqual(response["instance_id"], "appcl-repo-testuuid")
        mock_gen_tf_config.assert_called_with(unittest.mock.ANY, str(mock_persistent_workspace_path_obj))
        mock_rmtree.assert_called_once_with("/mocked/local_manifest_temp")
        mock_tf_destroy.assert_not_called()

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_local_decommission_success(self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree):
        instance_id = "appcl-testapp-123456"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_workspace_path_instance = MagicMock(); mock_workspace_path_instance.exists.return_value = True; mock_workspace_path_instance.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_instance
        mock_tf_init.return_value = (True, "Init success", ""); mock_tf_destroy.return_value = (True, "Destroy success", "")
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission")])
        response = await handle_cloud_local_decommission(instance_id, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "success")
        self.assertIn(f"Instance {instance_id} decommissioned and workspace cleaned.", response["message"])
        expected_workspace_path = str(pathlib.Path(self.test_persistent_workspaces) / "cloud-local" / instance_id)
        mock_tf_init.assert_called_once_with(expected_workspace_path, unittest.mock.ANY)
        mock_tf_destroy.assert_called_once_with(expected_workspace_path, unittest.mock.ANY)
        mock_rmtree.assert_called_once_with(mock_workspace_path_instance)

    async def test_handle_cloud_local_redeploy_stub_callable(self):
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="redeploy")])
        response = await handle_cloud_local_redeploy(instance_id="test-instance", public_ip="1.2.3.4", ec2_key_name="key.pem", repo_url=self.repo_url, namespace=self.namespace, aws_creds=self.aws_creds, chat_request=chat_request)
        self.assertEqual(response["status"], "pending_implementation")

    async def test_handle_cloud_local_scale_stub_callable(self):
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="scale")])
        response = await handle_cloud_local_scale(instance_id="test-instance", public_ip="1.2.3.4", ec2_key_name="key.pem", namespace=self.namespace, replicas=3, aws_creds=self.aws_creds, chat_request=chat_request)
        self.assertEqual(response["status"], "pending_implementation")

    # --- START: Helper for common cloud-hosted mock setup ---
    def _setup_common_cloud_hosted_mocks(self, mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp,
                                         mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
                                         mock_run_terraform_init, mock_run_terraform_apply,
                                         mock_clone_repository, mock_build_docker_image_locally,
                                         mock_get_ecr_login_details, mock_docker_from_env,
                                         mock_login_to_ecr, mock_push_image_to_ecr,
                                         mock_generate_deployment_manifest, mock_generate_service_manifest,
                                         mock_builtin_open,
                                         mock_generate_eks_kubeconfig_file,
                                         mock_install_nginx_ingress_helm
                                         ):

        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX = "appeks-test"; mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX = "appapp-test"
        mock_settings.EKS_DEFAULT_VPC_CIDR = "10.1.0.0/16"; mock_settings.EKS_DEFAULT_NUM_PUBLIC_SUBNETS = 1
        mock_settings.EKS_DEFAULT_NUM_PRIVATE_SUBNETS = 1; mock_settings.EKS_DEFAULT_VERSION = "1.27"
        mock_settings.EKS_DEFAULT_NODE_GROUP_NAME_SUFFIX = "ng-custom"; mock_settings.EKS_DEFAULT_NODE_INSTANCE_TYPE = "t3.small"
        mock_settings.EKS_DEFAULT_NODE_DESIRED_SIZE = 1; mock_settings.EKS_DEFAULT_NODE_MIN_SIZE = 1; mock_settings.EKS_DEFAULT_NODE_MAX_SIZE = 1
        mock_settings.ECR_DEFAULT_IMAGE_TAG_MUTABILITY = "IMMUTABLE"; mock_settings.ECR_DEFAULT_SCAN_ON_PUSH = False
        mock_settings.EC2_DEFAULT_APP_PORTS_JSON = json.dumps([{"port": 8080, "protocol": "tcp"}])
        mock_settings.DEFAULT_DOMAIN_NAME_FOR_APPS = "apptest.com"; mock_settings.NGINX_INGRESS_SERVICE_NAME = "ingress-nginx-controller-svc"
        mock_settings.NGINX_INGRESS_NAMESPACE = "ingress-nginx"; mock_settings.LOAD_BALANCER_DETAILS_TIMEOUT_SECONDS = 20
        mock_settings.ROUTE53_ACM_TF_FILENAME = "dns_and_cert_setup.tf"; mock_settings.EKS_DEFAULT_USER_ARN = "arn:aws:iam::123456789012:user/kubeconfig-user"
        mock_settings.NGINX_HELM_CHART_VERSION = "4.9.0"

        mock_uuid4.return_value.hex = "testuuid"
        _expected_repo_name_part_for_mock = self.repo_url.split('/')[-1].replace('.git', '')
        expected_cluster_name = f"{mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX}-{_expected_repo_name_part_for_mock}-{mock_uuid4.return_value.hex}"

        mock_persistent_workspace_base_path_obj = MagicMock(spec=pathlib.Path)
        mock_ph_cloud_hosted_obj = MagicMock(spec=pathlib.Path)
        mock_ph_cluster_dir_obj = MagicMock(spec=pathlib.Path)
        mock_ph_cluster_dir_obj.__str__.return_value = f"{self.test_persistent_workspaces}/cloud-hosted/{expected_cluster_name}"
        mock_ph_cluster_dir_obj.mkdir = MagicMock()
        mock_ph_cluster_dir_obj.__truediv__.side_effect = lambda p: pathlib.Path(str(mock_ph_cluster_dir_obj), p)

        mock_temp_clone_dir_str = "/tmp/app_clone_ch_helper"
        mock_mkdtemp.return_value = mock_temp_clone_dir_str
        mock_cloned_repo_subdir_path_obj = MagicMock(spec=pathlib.Path)
        mock_cloned_repo_subdir_path_obj.__str__.return_value = f"{mock_temp_clone_dir_str}/{_expected_repo_name_part_for_mock}"
        mock_cloned_repo_subdir_path_obj.exists.return_value = True; mock_cloned_repo_subdir_path_obj.is_dir.return_value = True
        mock_temp_clone_path_obj = MagicMock(spec=pathlib.Path)
        mock_temp_clone_path_obj.__str__.return_value = mock_temp_clone_dir_str
        mock_temp_clone_path_obj.__truediv__.return_value = mock_cloned_repo_subdir_path_obj
        mock_temp_clone_path_obj.exists.return_value = True

        def path_side_effect_func(arg_path):
            if str(arg_path) == mock_settings.PERSISTENT_WORKSPACE_BASE_DIR:
                mock_persistent_workspace_base_path_obj.__truediv__.return_value = mock_ph_cloud_hosted_obj
                mock_ph_cloud_hosted_obj.__truediv__.return_value = mock_ph_cluster_dir_obj
                return mock_persistent_workspace_base_path_obj
            elif str(arg_path) == mock_temp_clone_dir_str: return mock_temp_clone_path_obj
            new_mock_path = MagicMock(spec=pathlib.Path); new_mock_path.__str__.return_value = str(arg_path)
            new_mock_path.__truediv__.side_effect = lambda p: pathlib.Path(str(new_mock_path), p)
            return new_mock_path
        mock_pathlib_Path.side_effect = path_side_effect_func

        mock_generate_ecr_tf_config.return_value = str(mock_ph_cluster_dir_obj / "test_ecr.tf")
        mock_generate_eks_tf_config.return_value = str(mock_ph_cluster_dir_obj / "test_eks.tf")
        mock_run_terraform_init.return_value = (True, "init_success_stdout", "")

        mock_tf_outputs_eks_ecr = {"ecr_repository_url": {"value": f"12345.dkr.ecr.{self.aws_creds.aws_region}.amazonaws.com/{mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{_expected_repo_name_part_for_mock}-testuuid"},
                                 "ecr_repository_name": {"value": f"{mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{_expected_repo_name_part_for_mock}-testuuid"},
                                 "eks_cluster_endpoint": {"value": "test_eks_ep_output"}, "eks_cluster_ca_data": {"value": "test_ca_data"}, "vpc_id": {"value": "vpc-12345"}}
        mock_run_terraform_apply.return_value = (True, mock_tf_outputs_eks_ecr, "apply_stdout_eks_ecr", "")

        mock_clone_repository.return_value = {"success": True, "cloned_path": str(mock_cloned_repo_subdir_path_obj)}
        expected_local_tag_for_test = f"{_expected_repo_name_part_for_mock}-app:{mock_uuid4.return_value.hex}"
        mock_build_docker_image_locally.return_value = {"success": True, "image_id": "img123", "image_tags": [expected_local_tag_for_test]}
        mock_ecr_registry_from_token = f"https://12345.dkr.ecr.{self.aws_creds.aws_region}.amazonaws.com"
        mock_get_ecr_login_details.return_value = ("AWS", "ecr_pass_secret", mock_ecr_registry_from_token)
        mock_docker_client_instance = MagicMock(spec=docker.DockerClient); mock_docker_from_env.return_value = mock_docker_client_instance
        mock_login_to_ecr.return_value = True
        pushed_ecr_uri = f"{mock_ecr_registry_from_token.replace('https://', '')}/{mock_tf_outputs_eks_ecr['ecr_repository_name']['value']}:latest"
        mock_push_image_to_ecr.return_value = pushed_ecr_uri
        mock_generate_deployment_manifest.return_value = "kind: Deployment YAML content"; mock_generate_service_manifest.return_value = "kind: Service YAML content"
        mock_kubeconfig_path_str_expected = str(mock_ph_cluster_dir_obj / f"kubeconfig_{expected_cluster_name}.yaml")
        mock_generate_eks_kubeconfig_file.return_value = mock_kubeconfig_path_str_expected
        mock_install_nginx_ingress_helm.return_value = True
        return {"mock_ph_cluster_dir_obj": mock_ph_cluster_dir_obj, "mock_temp_clone_path_obj": mock_temp_clone_path_obj, "expected_cluster_name": expected_cluster_name,
                "_expected_repo_name_part_for_mock": _expected_repo_name_part_for_mock, "pushed_ecr_uri": pushed_ecr_uri, "mock_tf_outputs_eks_ecr": mock_tf_outputs_eks_ecr,
                "mock_kubeconfig_path_str_expected": mock_kubeconfig_path_str_expected, "expected_local_tag_for_test": expected_local_tag_for_test,
                "aws_env_vars_expected": {"AWS_ACCESS_KEY_ID": self.aws_creds.aws_access_key_id.get_secret_value(), "AWS_SECRET_ACCESS_KEY": self.aws_creds.aws_secret_access_key.get_secret_value(), "AWS_DEFAULT_REGION": self.aws_creds.aws_region}}
    # --- END: Helper for common cloud-hosted mock setup ---

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_success(
        self, mock_builtin_open,
        mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest,
        mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config,
        mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(
            mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp,
            mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
            mock_run_terraform_init, mock_run_terraform_apply,
            mock_clone_repository, mock_build_docker_image_locally,
            mock_get_ecr_login_details, mock_docker_from_env,
            mock_login_to_ecr, mock_push_image_to_ecr,
            mock_generate_deployment_manifest, mock_generate_service_manifest,
            mock_builtin_open,
            mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm
        )
        mock_ph_cluster_dir_obj = common_mocks["mock_ph_cluster_dir_obj"]; expected_cluster_name = common_mocks["expected_cluster_name"]
        _expected_repo_name_part_for_mock = common_mocks["_expected_repo_name_part_for_mock"]; pushed_ecr_uri = common_mocks["pushed_ecr_uri"]
        mock_tf_outputs_eks_ecr = common_mocks["mock_tf_outputs_eks_ecr"]; mock_kubeconfig_path_str_expected = common_mocks["mock_kubeconfig_path_str_expected"]
        aws_env_vars_expected = common_mocks["aws_env_vars_expected"]

        nlb_dns_name_mocked = "test-nlb-abcdefg.elb.us-east-1.amazonaws.com"; nlb_hosted_zone_id_mocked = "Z00NLBHOSTEDZONEIDTEST"
        mock_get_load_balancer_details.return_value = (nlb_dns_name_mocked, nlb_hosted_zone_id_mocked)
        mock_route53_acm_tf_file_path_expected = str(mock_ph_cluster_dir_obj / mock_settings.ROUTE53_ACM_TF_FILENAME)
        mock_generate_route53_acm_tf_config.return_value = mock_route53_acm_tf_file_path_expected
        mock_tf_outputs_domain_expected = {"acm_certificate_arn": {"value": "arn:aws:acm:us-east-1:123456789012:certificate/final-mock-cert-arn"}, "app_url_https": {"value": f"https://testapp.{mock_settings.DEFAULT_DOMAIN_NAME_FOR_APPS}"}}
        mock_run_terraform_apply.side_effect = [(True, mock_tf_outputs_eks_ecr, "apply_stdout_eks_ecr", ""), (True, mock_tf_outputs_domain_expected, "apply_stdout_domain", "")]
        mock_generate_ingress_manifest.return_value = "kind: Ingress YAML content for test"; mock_k8s_apply_manifests.return_value = True

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy cloud hosted with domain")], base_hosted_zone_id="Z00TESTHOSTEDIDFORAPP123", app_subdomain_label="testapp", github_repo_url=self.repo_url, deployment_mode="cloud-hosted", aws_credentials=self.aws_creds, target_namespace=self.namespace)
        response = await handle_cloud_hosted_deployment(repo_url=chat_request.github_repo_url, namespace=chat_request.target_namespace, aws_creds=chat_request.aws_credentials, chat_request=chat_request, instance_id_override=chat_request.instance_id_override)

        self.assertEqual(mock_run_terraform_apply.call_count, 2)
        self.assertEqual(mock_run_terraform_apply.call_args_list[1], call(str(mock_ph_cluster_dir_obj), aws_env_vars_expected, mock_route53_acm_tf_file_path_expected))
        app_name_expected = _expected_repo_name_part_for_mock.lower()
        expected_ingress_file_path = str(mock_ph_cluster_dir_obj / f"{app_name_expected}_ingress.yaml")
        mock_builtin_open.assert_any_call(expected_ingress_file_path, "w")
        self.assertEqual(response["status"], "success")
        self.assertEqual(response["app_url_https"], mock_tf_outputs_domain_expected["app_url_https"]["value"])
        self.assertIn(f"Deployment successful. App URL: {mock_tf_outputs_domain_expected['app_url_https']['value']}", response["message"])

    # --- START: New Failure Tests for handle_cloud_hosted_deployment ---
    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_get_lb_details_fails(
        self, mock_builtin_open, mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, mock_builtin_open, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)
        mock_get_load_balancer_details.return_value = None
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to get Nginx Load Balancer details", chat_request.messages[-1].content)
        mock_generate_route53_acm_tf_config.assert_not_called(); mock_generate_ingress_manifest.assert_not_called(); mock_k8s_apply_manifests.assert_not_called()
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_route53_acm_gen_fails(
        self, mock_builtin_open, mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, mock_builtin_open, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)
        mock_get_load_balancer_details.return_value = ("my-nlb.example.com", "ZNLBHZID")
        mock_generate_route53_acm_tf_config.return_value = None
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error"); self.assertIn("Failed to generate Route53/ACM Terraform config", chat_request.messages[-1].content)
        self.assertEqual(mock_run_terraform_apply.call_count, 1); mock_generate_ingress_manifest.assert_not_called(); mock_k8s_apply_manifests.assert_not_called()
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_route53_acm_apply_fails(
        self, mock_builtin_open, mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, mock_builtin_open, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)
        mock_get_load_balancer_details.return_value = ("my-nlb.example.com", "ZNLBHZID")
        mock_generate_route53_acm_tf_config.return_value = str(common_mocks["mock_ph_cluster_dir_obj"] / "domain_infra.tf")
        mock_run_terraform_apply.side_effect = [(True, common_mocks["mock_tf_outputs_eks_ecr"], "apply_stdout_eks_ecr", ""), (False, {}, "", "Route53/ACM apply failed miserably")]
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error"); self.assertIn("Terraform apply failed for Route53/ACM setup: Route53/ACM apply failed miserably", chat_request.messages[-1].content)
        self.assertEqual(mock_run_terraform_apply.call_count, 2); mock_generate_ingress_manifest.assert_not_called(); mock_k8s_apply_manifests.assert_not_called()
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_ingress_gen_fails(
        self, mock_builtin_open, mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, mock_builtin_open, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)
        mock_get_load_balancer_details.return_value = ("my-nlb.example.com", "ZNLBHZID")
        mock_generate_route53_acm_tf_config.return_value = str(common_mocks["mock_ph_cluster_dir_obj"] / "domain_infra.tf")
        mock_tf_outputs_domain_expected = {"acm_certificate_arn": {"value": "arn:test_cert"}, "app_url_https": {"value": "https://test.com"}}
        mock_run_terraform_apply.side_effect = [(True, common_mocks["mock_tf_outputs_eks_ecr"], "apply_stdout_eks_ecr", ""), (True, mock_tf_outputs_domain_expected, "apply_stdout_domain", "")]
        mock_generate_ingress_manifest.return_value = None
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error"); self.assertIn("Failed to generate Ingress manifest", chat_request.messages[-1].content)
        mock_k8s_apply_manifests.assert_not_called()
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_ingress_save_fails(
        self, mock_open_for_ingress_save,
        mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        with patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open) as m_open_for_app_manifests:
            common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, m_open_for_app_manifests, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)

        mock_get_load_balancer_details.return_value = ("my-nlb.example.com", "ZNLBHZID")
        mock_generate_route53_acm_tf_config.return_value = str(common_mocks["mock_ph_cluster_dir_obj"] / "domain_infra.tf")
        mock_tf_outputs_domain_expected = {"acm_certificate_arn": {"value": "arn:test_cert"}, "app_url_https": {"value": "https://test.com"}}
        mock_run_terraform_apply.side_effect = [(True, common_mocks["mock_tf_outputs_eks_ecr"], "apply_stdout_eks_ecr", ""), (True, mock_tf_outputs_domain_expected, "apply_stdout_domain", "")]
        mock_generate_ingress_manifest.return_value = "kind: Ingress YAML content"
        def open_side_effect(file_path, mode):
            if "ingress" in str(file_path).lower(): raise IOError("Disk is full, cannot save ingress")
            return unittest.mock.DEFAULT
        mock_open_for_ingress_save.side_effect = open_side_effect
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error"); self.assertIn("Failed to save K8s Ingress manifest: Disk is full, cannot save ingress", chat_request.messages[-1].content)
        mock_k8s_apply_manifests.assert_not_called()
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp')
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr')
    @patch('app.services.orchestration_service.docker_service.login_to_ecr')
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details')
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally')
    @patch('app.services.orchestration_service.git_service.clone_repository')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_route53_acm_tf_config')
    @patch('app.services.orchestration_service.k8s_service.generate_eks_kubeconfig_file')
    @patch('app.services.orchestration_service.k8s_service.install_nginx_ingress_helm')
    @patch('app.services.orchestration_service.k8s_service.get_load_balancer_details')
    @patch('app.services.orchestration_service.k8s_service.apply_manifests')
    @patch('app.services.orchestration_service.manifest_service.generate_ingress_manifest')
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest')
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest')
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open)
    async def test_handle_cloud_hosted_deployment_final_k8s_apply_fails(
        self, mock_builtin_open, mock_generate_service_manifest, mock_generate_deployment_manifest,
        mock_settings, mock_pathlib_Path, mock_uuid4,
        mock_generate_ingress_manifest, mock_k8s_apply_manifests, mock_get_load_balancer_details,
        mock_install_nginx_ingress_helm, mock_generate_eks_kubeconfig_file,
        mock_generate_route53_acm_tf_config, mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply,
        mock_clone_repository, mock_build_docker_image_locally,
        mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr,
        mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree
    ):
        common_mocks = self._setup_common_cloud_hosted_mocks(mock_settings, mock_uuid4, mock_pathlib_Path, mock_mkdtemp, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init, mock_run_terraform_apply, mock_clone_repository, mock_build_docker_image_locally, mock_get_ecr_login_details, mock_docker_from_env, mock_login_to_ecr, mock_push_image_to_ecr, mock_generate_deployment_manifest, mock_generate_service_manifest, mock_builtin_open, mock_generate_eks_kubeconfig_file, mock_install_nginx_ingress_helm)
        mock_get_load_balancer_details.return_value = ("my-nlb.example.com", "ZNLBHZID")
        mock_generate_route53_acm_tf_config.return_value = str(common_mocks["mock_ph_cluster_dir_obj"] / "domain_infra.tf")
        mock_tf_outputs_domain_expected = {"acm_certificate_arn": {"value": "arn:test_cert"}, "app_url_https": {"value": "https://test.com"}}
        mock_run_terraform_apply.side_effect = [(True, common_mocks["mock_tf_outputs_eks_ecr"], "apply_stdout_eks_ecr", ""), (True, mock_tf_outputs_domain_expected, "apply_stdout_domain", "")]
        mock_generate_ingress_manifest.return_value = "kind: Ingress YAML content"
        mock_k8s_apply_manifests.return_value = False # Simulate failure
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")], base_hosted_zone_id="Z123", app_subdomain_label="app")
        response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)
        self.assertEqual(response["status"], "error"); self.assertIn("Failed to apply Kubernetes application manifests to EKS", chat_request.messages[-1].content)
        mock_rmtree.assert_called_once_with(str(common_mocks["mock_temp_clone_path_obj"]))
    # --- End of new failure tests ---

    # --- START: Tests for handle_cloud_hosted_decommission ---
    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_decommission_success(self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree):
        cluster_name = "my-eks-cluster-decom"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces

        mock_workspace_path_obj = MagicMock(spec=pathlib.Path)
        mock_workspace_path_obj.exists.return_value = True
        mock_workspace_path_obj.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_obj # For the main workspace path

        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_destroy.return_value = (True, "Destroy success", "")
        mock_rmtree.return_value = None # Simulate successful removal

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission cloud-hosted")])
        response = await handle_cloud_hosted_decommission(cluster_name, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success")
        self.assertIn(f"EKS cluster {cluster_name} decommissioned and workspace cleaned.", response["message"])
        self.assertIn(f"EKS cluster {cluster_name} decommissioned and workspace cleaned.", chat_request.messages[-1].content)

        expected_workspace_path_str = str(pathlib.Path(self.test_persistent_workspaces) / "cloud-hosted" / cluster_name)
        mock_tf_init.assert_called_once_with(expected_workspace_path_str, unittest.mock.ANY)
        mock_tf_destroy.assert_called_once_with(expected_workspace_path_str, unittest.mock.ANY)
        mock_rmtree.assert_called_once_with(mock_workspace_path_obj)

    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_decommission_workspace_not_found(self, mock_settings, mock_pathlib_Path):
        cluster_name = "nonexistent-eks-cluster"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces

        mock_workspace_path_obj = MagicMock(spec=pathlib.Path)
        mock_workspace_path_obj.exists.return_value = False # Simulate workspace not found
        mock_pathlib_Path.return_value = mock_workspace_path_obj

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission cloud-hosted")])
        response = await handle_cloud_hosted_decommission(cluster_name, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Workspace for EKS cluster 'nonexistent-eks-cluster' not found", response["message"])
        self.assertIn("Error: Workspace for EKS cluster 'nonexistent-eks-cluster' not found", chat_request.messages[-1].content)

    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_decommission_tf_init_fails(self, mock_settings, mock_pathlib_Path, mock_tf_init):
        cluster_name = "eks-tf-init-fails"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_workspace_path_obj = MagicMock(spec=pathlib.Path); mock_workspace_path_obj.exists.return_value = True; mock_workspace_path_obj.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_obj
        mock_tf_init.return_value = (False, "", "TF init failed error message")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission cloud-hosted")])
        response = await handle_cloud_hosted_decommission(cluster_name, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform init failed: TF init failed error message", response["message"])
        self.assertIn("Error during Terraform init for EKS workspace: TF init failed error message", chat_request.messages[-1].content)

    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_decommission_tf_destroy_fails(self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree):
        cluster_name = "eks-tf-destroy-fails"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_workspace_path_obj = MagicMock(spec=pathlib.Path); mock_workspace_path_obj.exists.return_value = True; mock_workspace_path_obj.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_obj
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_destroy.return_value = (False, "", "TF destroy failed error message")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission cloud-hosted")])
        response = await handle_cloud_hosted_decommission(cluster_name, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform destroy failed: TF destroy failed error message", response["message"])
        self.assertIn("Error: Terraform destroy failed for EKS cluster eks-tf-destroy-fails. Details: TF destroy failed error message", chat_request.messages[-1].content)
        mock_rmtree.assert_not_called()

    @patch('app.services.orchestration_service.shutil.rmtree', side_effect=OSError("Test cleanup error"))
    @patch('app.services.orchestration_service.terraform_service.run_terraform_destroy')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.pathlib.Path')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_decommission_destroy_succeeds_cleanup_fails(self, mock_settings, mock_pathlib_Path, mock_tf_init, mock_tf_destroy, mock_rmtree_cleanup):
        cluster_name = "eks-cleanup-fails"
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_workspace_path_obj = MagicMock(spec=pathlib.Path); mock_workspace_path_obj.exists.return_value = True; mock_workspace_path_obj.is_dir.return_value = True
        mock_pathlib_Path.return_value = mock_workspace_path_obj
        mock_tf_init.return_value = (True, "Init success", "")
        mock_tf_destroy.return_value = (True, "Destroy success", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="decommission cloud-hosted")])
        response = await handle_cloud_hosted_decommission(cluster_name, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "success_with_cleanup_error")
        self.assertIn(f"EKS cluster {cluster_name} decommissioned, workspace cleanup failed: Test cleanup error", response["message"])
        self.assertIn(f"EKS cluster {cluster_name} decommissioned, but failed to clean up persistent workspace: Test cleanup error", chat_request.messages[-1].content)
    # --- END: Tests for handle_cloud_hosted_decommission ---

    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_ecr_gen_fails(
        self, mock_settings, mock_generate_ecr_tf_config, mock_generate_eks_tf_config
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_generate_ecr_tf_config.return_value = None # Simulate ECR config generation failure
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate ECR Terraform configuration", response["message"])
        self.assertIn("Failed to generate ECR Terraform configuration", chat_request.messages[-1].content)
        mock_generate_eks_tf_config.assert_not_called()

    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config')
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_eks_gen_fails(
        self, mock_settings, mock_generate_ecr_tf_config, mock_generate_eks_tf_config, mock_run_terraform_init
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_generate_ecr_tf_config.return_value = "path/to/ecr.tf"
        mock_generate_eks_tf_config.return_value = None # Simulate EKS config generation failure
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate EKS Terraform configuration", response["message"])
        mock_run_terraform_init.assert_not_called()

    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init')
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_tf_init_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_run_init, mock_run_apply
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_run_init.return_value = (False, "", "Terraform init failed spectacularly")
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform init failed: Terraform init failed spectacularly", response["message"])
        mock_run_apply.assert_not_called()

    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "init_success", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_tf_apply_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_run_init, mock_run_apply
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_run_apply.return_value = (False, {}, "", "Terraform apply exploded")
        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        response = await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )
        self.assertEqual(response["status"], "error")
        self.assertIn("Terraform apply failed: Terraform apply exploded", response["message"])
        self.assertIn("Manual cleanup of AWS resources", chat_request.messages[-1].content) # Check for cleanup advice

    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply', new_callable=AsyncMock) # Ensure it's an async mock
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', new_callable=AsyncMock)
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', new_callable=MagicMock)
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', new_callable=MagicMock)
    @patch('app.services.orchestration_service.uuid.uuid4')
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_with_instance_id_override(
        self, mock_settings, mock_uuid4,
        mock_generate_ecr_tf_config, mock_generate_eks_tf_config,
        mock_run_terraform_init, mock_run_terraform_apply
    ):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX = "appeks"
        mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX = "appapp"

        # Make terraform_service functions awaitable mocks that return successful values
        mock_generate_ecr_tf_config.return_value = "path/to/ecr.tf"
        mock_generate_eks_tf_config.return_value = "path/to/eks.tf"
        mock_run_terraform_init.return_value = (True, "init_success_stdout", "")
        mock_tf_outputs = {"ecr_repository_url": {"value": "override_ecr_url"}, "eks_cluster_endpoint": {"value": "override_eks_ep"}}
        mock_run_terraform_apply.return_value = (True, mock_tf_outputs, "apply_stdout", "")

        instance_id_override = "my-custom-id"
        chat_request = ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="deploy cloud hosted with override")],
            instance_id_override=instance_id_override # Key for cloud-hosted override
        )

        await handle_cloud_hosted_deployment(
            self.repo_url, self.namespace, self.aws_creds, chat_request
        )

        mock_uuid4.assert_not_called() # UUID should not be used if override is present

        expected_repo_name_part = "repo"
        expected_cluster_name_override = f"{mock_settings.EKS_DEFAULT_CLUSTER_NAME_PREFIX}-{expected_repo_name_part}-{instance_id_override}"
        expected_ecr_repo_name_override_raw = f"{mock_settings.ECR_DEFAULT_REPO_NAME_PREFIX}-{expected_repo_name_part}-{instance_id_override}"
        expected_ecr_repo_name_override = "".join(c if c.islower() or c.isdigit() or c in ['-', '_', '.'] else '-' for c in expected_ecr_repo_name_override_raw.lower()).strip('-_.')


        # Check that workspace path uses the override
        called_workspace_path_for_ecr = pathlib.Path(mock_generate_ecr_tf_config.call_args[0][1])
        self.assertIn(expected_cluster_name_override, str(called_workspace_path_for_ecr))

        called_workspace_path_for_eks = pathlib.Path(mock_generate_eks_tf_config.call_args[0][1])
        self.assertIn(expected_cluster_name_override, str(called_workspace_path_for_eks))

        # Check that names passed to TF generation use the override
        ecr_context_arg = mock_generate_ecr_tf_config.call_args[0][0]
        self.assertEqual(ecr_context_arg["ecr_repo_name"], expected_ecr_repo_name_override)

        eks_context_arg = mock_generate_eks_tf_config.call_args[0][0]
        self.assertEqual(eks_context_arg["cluster_name"], expected_cluster_name_override)

    @patch('app.services.orchestration_service.shutil.rmtree') # For cleanup
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value=None) # Simulate failure
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr', return_value="pushed_uri_example") # Assume previous steps succeed
    @patch('app.services.orchestration_service.docker_service.login_to_ecr', return_value=True)
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details', return_value=("user","pass","reg_url"))
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True})
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/mock/clone/repo"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_eks_manifest_gen_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks_tf, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_get_login, mock_docker_env, mock_login_ecr, mock_push,
        mock_gen_deploy, mock_mkdtemp, mock_rmtree):

        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_settings.EC2_DEFAULT_APP_PORTS_JSON = json.dumps([{"port": 8080}]) # Ensure it's parsable

        # Simulate successful Terraform apply with necessary outputs for ECR push
        mock_tf_outputs = {
            "ecr_repository_url": {"value": "test_ecr_url_output"},
            "ecr_repository_name": {"value": "appapp-test-repo-testuuid"},
        }
        mock_tf_apply.return_value = (True, mock_tf_outputs, "apply_stdout", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock();
            mock_clone_path_obj.exists.return_value = True # For cleanup check
            # Simulate Path(tempfile.mkdtemp()) behavior for the cleanup logic
            def path_side_effect_for_cleanup(arg_path):
                if str(arg_path) == "/mock/temp_clone_dir":
                    return mock_clone_path_obj
                return MagicMock() # Default for other Path calls
            mock_path_finally.side_effect = path_side_effect_for_cleanup

            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to generate Kubernetes manifests for EKS", response["message"])
        self.assertIn("Failed to generate Kubernetes manifests for EKS", chat_request.messages[-1].content)
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.open', new_callable=unittest.mock.mock_open) # Mock open
    @patch('app.services.orchestration_service.manifest_service.generate_service_manifest', return_value="kind: Service...") # Success
    @patch('app.services.orchestration_service.manifest_service.generate_deployment_manifest', return_value="kind: Deployment...") # Success
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr', return_value="pushed_uri_example")
    @patch('app.services.orchestration_service.docker_service.login_to_ecr', return_value=True)
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details', return_value=("user","pass","reg_url"))
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True})
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/mock/clone/repo"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_eks_manifest_save_fails(
        self, mock_settings, mock_gen_ecr_tf, mock_gen_eks_tf, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_get_login, mock_docker_env, mock_login_ecr, mock_push,
        mock_gen_deploy, mock_gen_service, mock_builtin_open, mock_mkdtemp, mock_rmtree):

        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_settings.EC2_DEFAULT_APP_PORTS_JSON = json.dumps([{"port": 8080}])

        mock_tf_outputs = {
            "ecr_repository_url": {"value": "test_ecr_url_output"},
            "ecr_repository_name": {"value": "appapp-test-repo-testuuid"},
        }
        mock_tf_apply.return_value = (True, mock_tf_outputs, "apply_stdout", "")

        # Simulate IOError during file write
        mock_builtin_open.side_effect = IOError("Disk full simulation")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])

        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock();
            mock_clone_path_obj.exists.return_value = True
            def path_side_effect_save_fail(arg_path):
                if str(arg_path) == "/mock/temp_clone_dir": return mock_clone_path_obj
                # For persistent workspace path, make it return a mock that can be used in `open`
                mock_ws_path = MagicMock(spec=pathlib.Path)
                mock_ws_path.__str__.return_value = str(arg_path)
                mock_ws_path.__truediv__.side_effect = lambda p: pathlib.Path(str(mock_ws_path), p)
                return mock_ws_path
            mock_path_finally.side_effect = path_side_effect_save_fail

            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to save K8s manifests for EKS: Disk full simulation", response["message"])
        self.assertIn("Failed to save K8s manifests for EKS: Disk full simulation", chat_request.messages[-1].content)
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": False, "error": "Clone failed miserably"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply') # Assume TF part succeeds
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_clone_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_mkdtemp, mock_rmtree):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_tf_apply.return_value = (True, {"ecr_repository_url": {"value": "some_url"}, "ecr_repository_name": {"value":"some_repo"}}, "", "") # TF is fine

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        # Need to mock Path for the finally block's clone_workspace_path.exists() check
        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock()
            mock_clone_path_obj.exists.return_value = True # Assume it was created before failing
            mock_path_finally.return_value = mock_clone_path_obj

            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to clone repository", response["message"])
        self.assertIn("Clone failed miserably", chat_request.messages[-1].content)
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": False, "error": "Build exploded", "logs": "log log kaboom"})
    @patch('app.services.orchestration_service.git_service.clone_repository') # Success
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply') # TF Success
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_build_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_mkdtemp, mock_rmtree):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_tf_apply.return_value = (True, {"ecr_repository_url": {"value": "some_url"}, "ecr_repository_name": {"value":"some_repo"}}, "", "")

        # Mock for clone path and its subdirectories for build context
        mock_cloned_repo_subdir_path_obj = MagicMock(spec=pathlib.Path)
        mock_cloned_repo_subdir_path_obj.exists.return_value = True
        mock_cloned_repo_subdir_path_obj.is_dir.return_value = True
        mock_temp_clone_path_obj = MagicMock(spec=pathlib.Path)
        mock_temp_clone_path_obj.__truediv__.return_value = mock_cloned_repo_subdir_path_obj
        mock_temp_clone_path_obj.exists.return_value = True

        # This setup is a bit complex due to Path being used multiple times for different things.
        # We need clone_repository to use a Path object that gives the build context.
        mock_clone_repo.return_value = {"success": True, "cloned_path": str(mock_cloned_repo_subdir_path_obj)}

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            # Mock Path specifically for the clone directory operations and the finally block
            def path_side_effect_build_fail(arg_path):
                if str(arg_path) == "/mock/temp_clone_dir": # Path(tempfile.mkdtemp(...))
                    return mock_temp_clone_path_obj
                # Fallback for persistent workspace paths (not the focus of this specific failure test part)
                return MagicMock(spec=pathlib.Path)
            mock_path_finally.side_effect = path_side_effect_build_fail

            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to build Docker image", response["message"])
        self.assertIn("Build exploded", chat_request.messages[-1].content)
        self.assertIn("log log kaboom", chat_request.messages[-1].content)
        mock_rmtree.assert_called_once_with(str(mock_temp_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details', return_value=None)
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True})
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/mock/clone/repo"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_get_ecr_login_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_get_login_details, mock_mkdtemp, mock_rmtree):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_tf_apply.return_value = (True, {"ecr_repository_url": {"value": "some_url"}, "ecr_repository_name": {"value":"some_repo"}}, "", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock(); mock_clone_path_obj.exists.return_value = True
            mock_path_finally.return_value = mock_clone_path_obj
            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to get ECR login credentials", response["message"])
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.docker_service.login_to_ecr', return_value=False)
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details', return_value=("user","pass","reg_url"))
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True})
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/mock/clone/repo"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_ecr_login_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_get_login_details, mock_docker_from_env,
        mock_login_to_ecr, mock_mkdtemp, mock_rmtree):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_tf_apply.return_value = (True, {"ecr_repository_url": {"value": "some_url"}, "ecr_repository_name": {"value":"some_repo"}}, "", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock(); mock_clone_path_obj.exists.return_value = True
            mock_path_finally.return_value = mock_clone_path_obj
            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("ECR login failed", response["message"])
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


    @patch('app.services.orchestration_service.shutil.rmtree')
    @patch('app.services.orchestration_service.tempfile.mkdtemp', return_value="/mock/temp_clone_dir")
    @patch('app.services.orchestration_service.docker_service.push_image_to_ecr', return_value=None)
    @patch('app.services.orchestration_service.docker_service.login_to_ecr', return_value=True)
    @patch('app.services.orchestration_service.docker.from_env')
    @patch('app.services.orchestration_service.docker_service.get_ecr_login_details', return_value=("user","pass","reg_url"))
    @patch('app.services.orchestration_service.docker_service.build_docker_image_locally', return_value={"success": True})
    @patch('app.services.orchestration_service.git_service.clone_repository', return_value={"success": True, "cloned_path": "/mock/clone/repo"})
    @patch('app.services.orchestration_service.terraform_service.run_terraform_apply')
    @patch('app.services.orchestration_service.terraform_service.run_terraform_init', return_value=(True, "", ""))
    @patch('app.services.orchestration_service.terraform_service.generate_eks_tf_config', return_value="path/to/eks.tf")
    @patch('app.services.orchestration_service.terraform_service.generate_ecr_tf_config', return_value="path/to/ecr.tf")
    @patch('app.services.orchestration_service.settings')
    async def test_handle_cloud_hosted_deployment_ecr_push_fails(
        self, mock_settings, mock_gen_ecr, mock_gen_eks, mock_tf_init, mock_tf_apply,
        mock_clone_repo, mock_build_image, mock_get_login_details, mock_docker_from_env,
        mock_login_to_ecr, mock_push_image_to_ecr, mock_mkdtemp, mock_rmtree):
        mock_settings.PERSISTENT_WORKSPACE_BASE_DIR = self.test_persistent_workspaces
        mock_tf_apply.return_value = (True, {"ecr_repository_url": {"value": "some_url"}, "ecr_repository_name": {"value":"some_repo"}}, "", "")

        chat_request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="deploy")])
        with patch('app.services.orchestration_service.pathlib.Path') as mock_path_finally:
            mock_clone_path_obj = MagicMock(); mock_clone_path_obj.exists.return_value = True
            mock_path_finally.return_value = mock_clone_path_obj
            response = await handle_cloud_hosted_deployment(self.repo_url, self.namespace, self.aws_creds, chat_request)

        self.assertEqual(response["status"], "error")
        self.assertIn("Failed to push image to ECR", response["message"])
        mock_rmtree.assert_called_once_with(str(mock_clone_path_obj))


if __name__ == '__main__':
    unittest.main()
