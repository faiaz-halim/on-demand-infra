import jinja2
import pathlib
import logging
import json
import time
import subprocess # Added
import os # Added
import shutil # Added
from typing import Dict, Any, Optional, List, Tuple # Added Tuple

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
    bootstrap_script_content = generate_ec2_bootstrap_script(context, output_dir=None)
    if bootstrap_script_content is None:
        logger.error("Failed to generate EC2 bootstrap script, cannot proceed with EC2 TF config generation.")
        return None
    context['user_data_content'] = bootstrap_script_content
    template_name = "terraform/aws/ec2_instance.tf.j2"
    required_keys = ['aws_region', 'ami_id', 'instance_type', 'key_name',
                     'instance_name_tag', 'sg_name']
    for key in required_keys:
        if key not in context:
            logger.error(f"Missing required key '{key}' in context for generating EC2 Terraform config.")
            return None
    if 'app_ports' in context and 'app_ports_sg' not in context:
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
        output_file_path = output_path / f"{instance_name_tag}_main.tf"
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

# --- Terraform CLI Execution Functions ---

def _run_terraform_command(command_args: List[str], tf_dir: str, env_vars: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """
    Helper function to run a Terraform CLI command.
    """
    terraform_exe = shutil.which('terraform')
    if not terraform_exe:
        logger.error("Terraform CLI not found. Please ensure it is installed and in PATH.")
        return subprocess.CompletedProcess(args=command_args, returncode=-1, stdout="", stderr="Terraform CLI not found.")

    current_env = os.environ.copy()
    if env_vars:
        current_env.update(env_vars)
        # Sensitive data might be in env_vars, so log keys only or a generic message
        logger.debug(f"Terraform command will run with custom environment variables for keys: {list(env_vars.keys())}")

    full_command = [terraform_exe] + command_args
    logger.info(f"Running Terraform command: {' '.join(full_command)} in directory: {tf_dir}")

    try:
        process = subprocess.run(
            full_command,
            cwd=tf_dir,
            env=current_env,
            capture_output=True,
            text=True,
            check=False # Handle non-zero exit codes manually
        )
        # Log stdout/stderr carefully, especially for JSON outputs or sensitive info
        logger.debug(f"Terraform command '{' '.join(full_command)}' stdout:\n{process.stdout.strip()[:1000]}...") # Log snippet
        if process.stderr.strip():
            logger.info(f"Terraform command '{' '.join(full_command)}' stderr:\n{process.stderr.strip()}")
        return process
    except Exception as e:
        logger.error(f"Exception running Terraform command '{' '.join(full_command)}': {str(e)}", exc_info=True)
        return subprocess.CompletedProcess(args=full_command, returncode=-1, stdout="", stderr=str(e))


def run_terraform_init(tf_dir: str, env_vars: Optional[Dict[str, str]] = None) -> Tuple[bool, str, str]:
    """
    Runs 'terraform init'.
    Returns: (success_bool, stdout_str, stderr_str)
    """
    logger.info(f"Running terraform init in {tf_dir}")
    result = _run_terraform_command(['init', '-no-color', '-input=false'], tf_dir, env_vars)
    if result.returncode == 0:
        logger.info("Terraform init successful.")
    else:
        logger.error(f"Terraform init failed. Return code: {result.returncode}")
    return result.returncode == 0, result.stdout, result.stderr


def run_terraform_apply(tf_dir: str, env_vars: Optional[Dict[str, str]] = None) -> Tuple[bool, Dict[str, Any], str, str]:
    """
    Runs 'terraform apply' and then 'terraform output -json' if apply is successful.
    Returns: (success_bool, outputs_dict, apply_stdout_str, apply_stderr_str)
    """
    logger.info(f"Running terraform apply in {tf_dir}")
    apply_result = _run_terraform_command(['apply', '-auto-approve', '-no-color', '-json', '-input=false'], tf_dir, env_vars)

    outputs: Dict[str, Any] = {}
    if apply_result.returncode == 0:
        logger.info("Terraform apply successful. Attempting to fetch outputs.")
        output_result = _run_terraform_command(['output', '-no-color', '-json'], tf_dir, env_vars)
        if output_result.returncode == 0:
            try:
                parsed_outputs = json.loads(output_result.stdout)
                outputs = {k: v.get('value') for k, v in parsed_outputs.items()}
                logger.info(f"Terraform outputs fetched successfully: {list(outputs.keys())}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse terraform output JSON: {e}. Raw output: {output_result.stdout[:1000]}...")
                # Apply was successful, but output failed. Return success True but empty outputs.
                return True, {}, apply_result.stdout, f"{apply_result.stderr}\nError parsing outputs: {str(e)}"
        else:
            logger.warning(f"Terraform apply succeeded, but 'terraform output -json' failed. Stderr: {output_result.stderr}")
            # Apply was successful, but output failed. Return success True but empty outputs and include output command's stderr.
            return True, {}, apply_result.stdout, f"{apply_result.stderr}\nTerraform output command stderr: {output_result.stderr}"

        return True, outputs, apply_result.stdout, apply_result.stderr
    else:
        logger.error(f"Terraform apply failed. Return code: {apply_result.returncode}")
        return False, {}, apply_result.stdout, apply_result.stderr


def run_terraform_destroy(tf_dir: str, env_vars: Optional[Dict[str, str]] = None) -> Tuple[bool, str, str]:
    """
    Runs 'terraform destroy'.
    Returns: (success_bool, stdout_str, stderr_str)
    """
    logger.info(f"Running terraform destroy in {tf_dir}")
    result = _run_terraform_command(['destroy', '-auto-approve', '-no-color', '-input=false'], tf_dir, env_vars)
    if result.returncode == 0:
        logger.info("Terraform destroy successful.")
    else:
        logger.error(f"Terraform destroy failed. Return code: {result.returncode}")
    return result.returncode == 0, result.stdout, result.stderr
