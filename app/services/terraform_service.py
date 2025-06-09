import jinja2
import pathlib
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)
if not logger.handlers: # Basic config for standalone use or if app logger isn't set
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Define the base template directory relative to this file's location
# Assumes this file is in app/services/ and templates are in app/templates/
TEMPLATE_BASE_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"

# Initialize Jinja2 environment - specific to this service's needs or could be global
# For now, initialize it here. If multiple services use templates from TEMPLATE_BASE_DIR,
# it could be initialized in a shared core module.
try:
    jinja_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_BASE_DIR)), # Loader for base templates dir
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False # Explicitly false for YAML/JSON/HCL generation
    )
    logger.info(f"Jinja2 environment initialized with template base directory: {TEMPLATE_BASE_DIR}")
except Exception as e:
    logger.error(f"Failed to initialize Jinja2 environment for terraform_service: {e}", exc_info=True)
    jinja_env = None


def generate_kind_config_yaml(
    cluster_name: str,
    template_name: str, # e.g., "kind/kind-config.yaml.j2"
    output_dir: str,
    context: Dict[str, Any]
) -> Optional[str]:
    """
    Generates a Kind cluster configuration YAML file from a Jinja2 template.

    Args:
        cluster_name: The name of the Kind cluster.
        template_name: The path to the template file relative to TEMPLATE_BASE_DIR
                       (e.g., "kind/kind-config.yaml.j2").
        output_dir: The directory where the generated YAML file will be saved.
        context: A dictionary of context variables to pass to the template.
                 This should include 'cluster_name' and any other variables
                 expected by the template (e.g., num_workers, feature_gates).

    Returns:
        The absolute path to the generated YAML file as a string on success,
        None on failure (e.g., template not found, write error).
    """
    if not jinja_env:
        logger.error("Jinja2 environment not available for generate_kind_config_yaml.")
        return None

    if 'cluster_name' not in context: # Ensure cluster_name is in context for the template
        context['cluster_name'] = cluster_name

    try:
        template = jinja_env.get_template(template_name)
        rendered_config = template.render(context)

        output_path = pathlib.Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True) # Ensure output directory exists

        # Use a more specific filename, perhaps related to the template or cluster
        output_file_path = output_path / f"{cluster_name}-config.yaml"

        with open(output_file_path, 'w') as f:
            f.write(rendered_config)

        logger.info(f"Successfully generated Kind config YAML: {output_file_path}")
        return str(output_file_path.resolve()) # Return absolute path

    except jinja2.TemplateNotFound:
        logger.error(f"Template '{template_name}' not found in {TEMPLATE_BASE_DIR}", exc_info=True)
        return None
    except IOError as e:
        logger.error(f"IOError writing Kind config to {output_file_path}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred generating Kind config YAML: {e}", exc_info=True)
        return None

# Example usage (for manual testing of this module)
# if __name__ == '__main__':
#     logger.info("Testing generate_kind_config_yaml...")
#     test_output_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "test_generated_configs"
#
#     # Ensure the template exists for testing
#     kind_template_dir = TEMPLATE_BASE_DIR / "kind"
#     kind_template_dir.mkdir(parents=True, exist_ok=True)
#     kind_template_file = kind_template_dir / "kind-config.yaml.j2"
#     if not kind_template_file.exists():
#         with open(kind_template_file, "w") as f:
#             f.write("# kind-config.yaml.j2\n")
#             f.write("kind: Cluster\n")
#             f.write("apiVersion: kind.x-k8s.io/v1alpha4\n")
#             f.write("name: {{ cluster_name }}\n")
#             f.write("nodes:\n")
#             f.write("- role: control-plane\n")
#             f.write("{% if num_workers and num_workers > 0 %}\n")
#             f.write("{% for i in range(num_workers) %}\n")
#             f.write("- role: worker\n")
#             f.write("{% endfor %}\n")
#             f.write("{% endif %}\n")
#             f.write("{% if feature_gates %}\n")
#             f.write("featureGates:\n")
#             f.write("{% for key, value in feature_gates.items() %}\n")
#             f.write("  \"{{ key }}\": {{ value | lower }}\n")
#             f.write("{% endfor %}\n")
#             f.write("{% endif %}\n")
#         logger.info(f"Created dummy template at {kind_template_file}")
#
#     test_context = {
#         "num_workers": 2,
#         "feature_gates": {"MyFeature": True, "AnotherGate": False}
#     }
#
#     generated_file = generate_kind_config_yaml(
#         cluster_name="test-cluster",
#         template_name="kind/kind-config.yaml.j2",
#         output_dir=str(test_output_dir),
#         context=test_context
#     )
#
#     if generated_file:
#         logger.info(f"Kind config YAML generated successfully at: {generated_file}")
#         with open(generated_file, 'r') as f_read:
#             logger.info(f"Content:\n{f_read.read()}")
#     else:
#         logger.error("Failed to generate Kind config YAML.")
