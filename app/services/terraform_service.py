import jinja2
import pathlib
import logging
import json
import time
import subprocess
import os
import shutil
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

TEMPLATE_BASE_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"

try:
    jinja_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_BASE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False
    )
    jinja_env.filters['tojson'] = json.dumps
    logger.info(f"Jinja2 environment initialized with template base directory: {TEMPLATE_BASE_DIR} and 'tojson' filter.")
except Exception as e:
    logger.error(f"Failed to initialize Jinja2 environment for terraform_service: {e}", exc_info=True)
    jinja_env = None


def generate_kind_config_yaml(
    cluster_name: str,
    template_name: str,
    output_dir: str,
    context: Dict[str, Any]
) -> Optional[str]:
    """Generates a Kind cluster configuration YAML file from a Jinja2 template."""
    if not jinja_env:
        logger.error("Jinja2 environment not available for generate_kind_config_yaml.")
        return None
    if 'cluster_name' not in context:
        context['cluster_name'] = cluster_name
    output_file_path = None
    try:
        template = jinja_env.get_template(template_name)
        rendered_config = template.render(context)
        output_path = pathlib.Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        output_file_path = output_path / f"{cluster_name}-config.yaml"
        with open(output_file_path, 'w') as f:
            f.write(rendered_config)
        logger.info(f"Successfully generated Kind config YAML: {output_file_path}")
        return str(output_file_path.resolve())
    except jinja2.TemplateNotFound:
        logger.error(f"Template '{template_name}' not found in {TEMPLATE_BASE_DIR}", exc_info=True)
        return None
    except IOError as e:
        log_path = output_file_path if output_file_path else output_dir
        logger.error(f"IOError writing Kind config to {log_path}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred generating Kind config YAML: {e}", exc_info=True)
        return None


def generate_ec2_bootstrap_script(context: Dict[str, Any], output_dir: Optional[str] = None) -> Optional[str]:
    """
    Generates an EC2 bootstrap script from a Jinja2 template.
    """
    if not jinja_env:
        logger.error("Jinja2 environment not available for generate_ec2_bootstrap_script.")
        return None
    template_name = "scripts/ec2_bootstrap.sh.j2"
    output_file_path = None
    try:
        template = jinja_env.get_template(template_name)
        rendered_script = template.render(context)
        if output_dir:
            output_path = pathlib.Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            output_file_path = output_path / f"ec2_bootstrap_{timestamp}.sh"
            with open(output_file_path, 'w') as f:
                f.write(rendered_script)
            logger.info(f"Successfully generated EC2 bootstrap script: {output_file_path}")
            return str(output_file_path.resolve())
        else:
            logger.info("Successfully generated EC2 bootstrap script content (not saved to file).")
            return rendered_script
    except jinja2.TemplateNotFound:
        logger.error(f"Bootstrap script template '{template_name}' not found in {TEMPLATE_BASE_DIR}", exc_info=True)
        return None
    except IOError as e:
        log_path = output_file_path if output_file_path else output_dir or "memory"
        logger.error(f"IOError during EC2 bootstrap script generation/saving to {log_path}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred generating EC2 bootstrap script: {e}", exc_info=True)
        return None


def generate_ec2_tf_config(context: Dict[str, Any], output_dir: str) -> Optional[str]:
    """
    Generates a Terraform HCL configuration file for an EC2 instance,
    including a user data bootstrap script.
    """
    if not jinja_env:
        logger.error("Jinja2 environment not available for generate_ec2_tf_config.")
        return None

    # Ensure user_data_content is in context, even if it's an empty string or None
    if 'user_data_content' not in context:
        context['user_data_content'] = None # Template handles default(null)

    # Ensure settings is available, typically imported at module level
    # from ..core.config import settings (this line is for context, not part of the diff)


    template_name = "terraform/aws/ec2_instance.tf.j2"
    required_keys = ['aws_region', 'ami_id', 'instance_type', 'key_name',
                     'instance_name_tag', 'sg_name']
    for key in required_keys:
        if key not in context:
            logger.error(f"Missing required key '{key}' in context for generating EC2 Terraform config.")
            return None
    if 'app_ports' in context and 'app_ports_sg' not in context: # Convert to the format expected by template's variable
        context['app_ports_sg'] = context['app_ports']
    elif 'app_ports_sg' not in context:
        context['app_ports_sg'] = []

    output_file_path = None
    try:
        template = jinja_env.get_template(template_name)
        rendered_config = template.render(context)
        output_path = pathlib.Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        instance_name_tag = context.get('instance_name_tag', 'ec2-instance')
        output_file_path = output_path / f"{instance_name_tag}_main.tf" # Standardize to _main.tf
        with open(output_file_path, 'w') as f:
            f.write(rendered_config)
        logger.info(f"Successfully generated EC2 Terraform config: {output_file_path}")
        return str(output_file_path.resolve())
    except jinja2.TemplateNotFound:
        logger.error(f"Template '{template_name}' not found in {TEMPLATE_BASE_DIR}", exc_info=True)
        return None
    except IOError as e:
        log_path = output_file_path if output_file_path else output_dir
        logger.error(f"IOError writing EC2 Terraform config to {log_path}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred generating EC2 Terraform config: {e}", exc_info=True)
        return None

def generate_eks_tf_config(context: Dict[str, Any], output_dir: str) -> Optional[str]:
    """
    Generates a Terraform HCL configuration file for an EKS cluster.

    Args:
        context: A dictionary of context variables to pass to the template.
                 Expected keys include: aws_region, cluster_name, eks_version, etc.
        output_dir: The directory where the generated HCL file will be saved.

    Returns:
        The absolute path to the generated .tf file as a string on success,
        None on failure.
    """
    if not jinja_env:
        logger.error("Jinja2 environment not available for generate_eks_tf_config.")
        return None

    # Ensure settings is imported, typically at the top of the file:
    # from ..core.config import settings
    # This is a placeholder for where `settings` would come from.
    # The actual import should be at the top of the file.
    from ..core.config import settings


    template_name = "terraform/aws/eks_cluster.tf.j2"

    required_keys = ["aws_region", "cluster_name"]
    for key in required_keys:
        if key not in context:
            logger.error(f"Missing one or more required keys in context for EKS TF config: {key}")
            return None

    # Populate with defaults from settings if not provided in context
    context.setdefault("vpc_cidr", settings.EKS_DEFAULT_VPC_CIDR)
    context.setdefault("num_public_subnets", settings.EKS_DEFAULT_NUM_PUBLIC_SUBNETS)
    context.setdefault("num_private_subnets", settings.EKS_DEFAULT_NUM_PRIVATE_SUBNETS)
    context.setdefault("eks_version", settings.EKS_DEFAULT_VERSION)
    # Default node_group_name includes cluster_name, so construct it carefully
    context.setdefault("node_group_name", f"{context['cluster_name']}-{settings.EKS_DEFAULT_NODE_GROUP_NAME_SUFFIX}")
    context.setdefault("node_instance_type", settings.EKS_DEFAULT_NODE_INSTANCE_TYPE)
    context.setdefault("node_desired_size", settings.EKS_DEFAULT_NODE_DESIRED_SIZE)
    context.setdefault("node_min_size", settings.EKS_DEFAULT_NODE_MIN_SIZE)
    context.setdefault("node_max_size", settings.EKS_DEFAULT_NODE_MAX_SIZE)

    output_file_path = None
    try:
        template = jinja_env.get_template(template_name)
        rendered_config = template.render(context)

        output_path = pathlib.Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        cluster_name = context['cluster_name'] # Already checked for presence
        output_file_path = output_path / f"{cluster_name}_eks_main.tf"

        with open(output_file_path, 'w') as f:
            f.write(rendered_config)

        logger.info(f"Successfully generated EKS Terraform config: {output_file_path}")
        return str(output_file_path.resolve())

    except jinja2.TemplateNotFound:
        logger.error(f"Template '{template_name}' not found in {TEMPLATE_BASE_DIR}", exc_info=True)
        return None
    except IOError as e:
        log_path = output_file_path if output_file_path else output_dir
        logger.error(f"IOError writing EKS Terraform config to {log_path}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred generating EKS Terraform config: {e}", exc_info=True)
        return None

def generate_ecr_tf_config(context: Dict[str, Any], output_dir: str) -> Optional[str]:
    """
    Generates a Terraform HCL configuration file for an AWS ECR repository.

    Args:
        context: A dictionary of context variables to pass to the template.
                 Expected keys: aws_region, ecr_repo_name.
                 Optional keys: image_tag_mutability, scan_on_push.
        output_dir: The directory where the generated HCL file will be saved.

    Returns:
        The absolute path to the generated .tf file as a string on success,
        None on failure.
    """
    if not jinja_env:
        logger.error("Jinja2 environment not available for generate_ecr_tf_config.")
        return None

    from ..core.config import settings # Import settings for defaults

    template_name = "terraform/aws/ecr_repository.tf.j2"

    required_keys = ['aws_region', 'ecr_repo_name']
    for key in required_keys:
        if key not in context:
            logger.error(f"Missing required key '{key}' in context for generating ECR Terraform config.")
            return None

    # Populate with defaults from settings if not provided in context
    context.setdefault("image_tag_mutability", settings.ECR_DEFAULT_IMAGE_TAG_MUTABILITY)
    context.setdefault("scan_on_push", settings.ECR_DEFAULT_SCAN_ON_PUSH)

    output_file_path = None
    try:
        template = jinja_env.get_template(template_name)
        rendered_config = template.render(context)

        output_path = pathlib.Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        ecr_repo_name = context['ecr_repo_name']
        output_file_path = output_path / f"{ecr_repo_name}_ecr.tf" # Filename convention

        with open(output_file_path, 'w') as f:
            f.write(rendered_config)

        logger.info(f"Successfully generated ECR Terraform config: {output_file_path}")
        return str(output_file_path.resolve())

    except jinja2.TemplateNotFound:
        logger.error(f"Template '{template_name}' not found in {TEMPLATE_BASE_DIR}", exc_info=True)
        return None
    except IOError as e:
        log_path = output_file_path if output_file_path else output_dir
        logger.error(f"IOError writing ECR Terraform config to {log_path}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred generating ECR Terraform config: {e}", exc_info=True)
        return None

def generate_route53_acm_tf_config(context: Dict[str, Any], output_dir: str, filename_override: Optional[str] = None) -> Optional[str]:
    """
    Generates Terraform configuration for Route53 records and ACM certificate validation.
    Saves the .tf file to the specified output directory.
    If filename_override is provided, it's used as the filename.
    """
    if not jinja_env:
        logger.error("Jinja2 environment not available for generate_route53_acm_tf_config.")
        return None

    from ..core.config import settings # Import settings if needed for defaults, though not used here

    template_name = "terraform/aws/route53_acm.tf.j2"

    required_keys = [
        'aws_region',
        'base_hosted_zone_id',
        'app_full_domain_name',
        'nlb_dns_name',
        'nlb_hosted_zone_id'
    ]
    for key in required_keys:
        if key not in context:
            logger.error(f"Missing required key '{key}' in context for generating Route53/ACM Terraform config.")
            return None

    output_file_path = None
    try:
        template = jinja_env.get_template(template_name)
        rendered_config = template.render(context)

        output_path = pathlib.Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        if filename_override:
            output_filename = filename_override if filename_override.endswith(".tf") else f"{filename_override}.tf"
        else:
            app_domain_name_for_file = context['app_full_domain_name'].replace('.', '_') # Sanitize for filename
            output_filename = f"{app_domain_name_for_file}_route53_acm.tf"

        output_file_path = output_path / output_filename

        with open(output_file_path, 'w') as f:
            f.write(rendered_config)

        logger.info(f"Successfully generated Route53/ACM Terraform config: {output_file_path}")
        return str(output_file_path.resolve())

    except jinja2.TemplateNotFound:
        logger.error(f"Template '{template_name}' not found in {TEMPLATE_BASE_DIR}", exc_info=True)
        return None
    except IOError as e:
        log_path = output_file_path if output_file_path else output_dir
        logger.error(f"IOError writing Route53/ACM Terraform config to {log_path}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred generating Route53/ACM Terraform config: {e}", exc_info=True)
        return None

# --- Terraform CLI Execution Functions ---
# ... (These functions remain as previously implemented) ...
def _run_terraform_command(command_args: List[str], tf_dir: str, env_vars: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    terraform_exe = shutil.which('terraform')
    if not terraform_exe:
        logger.error("Terraform CLI not found.")
        return subprocess.CompletedProcess(args=command_args, returncode=-1, stdout="", stderr="Terraform CLI not found.")
    current_env = os.environ.copy()
    if env_vars: current_env.update(env_vars)
    logger.debug(f"Running Terraform command: {[terraform_exe] + command_args} in {tf_dir}")
    try:
        process = subprocess.run([terraform_exe] + command_args, cwd=tf_dir, env=current_env, capture_output=True, text=True, check=False)
        logger.debug(f"TF cmd stdout:\n{process.stdout.strip()[:1000]}")
        if process.stderr.strip(): logger.info(f"TF cmd stderr:\n{process.stderr.strip()}")
        return process
    except Exception as e:
        logger.error(f"Exception running TF cmd: {e}", exc_info=True)
        return subprocess.CompletedProcess(args=[terraform_exe] + command_args, returncode=-1, stdout="", stderr=str(e))

def run_terraform_init(tf_dir: str, env_vars: Optional[Dict[str, str]] = None) -> Tuple[bool, str, str]:
    logger.info(f"Running terraform init in {tf_dir}")
    result = _run_terraform_command(['init', '-no-color', '-input=false'], tf_dir, env_vars)
    if result.returncode == 0: logger.info("Terraform init successful.")
    else: logger.error(f"Terraform init failed. RC: {result.returncode}")
    return result.returncode == 0, result.stdout, result.stderr

def run_terraform_apply(tf_dir: str, env_vars: Optional[Dict[str, str]] = None) -> Tuple[bool, Dict[str, Any], str, str]:
    logger.info(f"Running terraform apply in {tf_dir}")
    apply_result = _run_terraform_command(['apply', '-auto-approve', '-no-color', '-json', '-input=false'], tf_dir, env_vars)
    outputs: Dict[str, Any] = {}
    if apply_result.returncode == 0:
        logger.info("Terraform apply successful. Fetching outputs.")
        output_result = _run_terraform_command(['output', '-no-color', '-json'], tf_dir, env_vars)
        if output_result.returncode == 0:
            try:
                parsed_outputs = json.loads(output_result.stdout)
                outputs = {k: v.get('value') for k, v in parsed_outputs.items()}
                logger.info(f"TF outputs fetched: {list(outputs.keys())}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse TF output JSON: {e}")
                return True, {}, apply_result.stdout, f"{apply_result.stderr}\nError parsing outputs: {str(e)}"
        else:
            logger.warning(f"TF apply OK, but 'output -json' failed. Stderr: {output_result.stderr}")
            return True, {}, apply_result.stdout, f"{apply_result.stderr}\nTF output stderr: {output_result.stderr}"
        return True, outputs, apply_result.stdout, apply_result.stderr
    else:
        logger.error(f"Terraform apply failed. RC: {apply_result.returncode}")
        return False, {}, apply_result.stdout, apply_result.stderr

def run_terraform_destroy(tf_dir: str, env_vars: Optional[Dict[str, str]] = None) -> Tuple[bool, str, str]:
    logger.info(f"Running terraform destroy in {tf_dir}")
    result = _run_terraform_command(['destroy', '-auto-approve', '-no-color', '-input=false'], tf_dir, env_vars)
    if result.returncode == 0: logger.info("Terraform destroy successful.")
    else: logger.error(f"Terraform destroy failed. RC: {result.returncode}")
    return result.returncode == 0, result.stdout, result.stderr
