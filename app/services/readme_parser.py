import re
from app.core.logging_config import get_logger
from typing import Dict, List, Optional

logger = get_logger(__name__)

def extract_commands_from_section(section_content: str) -> List[str]:
    """
    Extracts potential commands from a section of text.
    Looks for lines that might be shell commands, often within triple backticks
    or indented code blocks.
    """
    commands = []
    # Regex for fenced code blocks (```bash, ```sh, ```shell, or just ```)
    fenced_code_blocks = re.findall(r"```(?:bash|sh|shell|)\s*
(.*?)
```", section_content, re.DOTALL)
    for block in fenced_code_blocks:
        lines = block.strip().split('\n')
        for line in lines:
            line = line.strip()
            # Basic heuristic: not a comment, not empty, doesn't start with output prompts like '$' or '>' (can be added if needed)
            if line and not line.startswith('#'):
                commands.append(line)

    # Regex for indented code blocks (4 spaces or a tab)
    # This is a simpler version and might need refinement
    indented_code_blocks = re.findall(r"^(?: {4}|\t)(.*)", section_content, re.MULTILINE)
    for line in indented_code_blocks:
        line = line.strip()
        if line and not line.startswith('#') and line not in commands: # Avoid duplicates from fenced blocks if any overlap
            # Further check to avoid capturing plain text that happens to be indented
            if re.match(r"^[a-zA-Z0-9\-_]+\s+.*", line) or line.startswith("./") or line.startswith("docker"):
                commands.append(line)

    # If no code blocks found, try to extract lines that look like commands directly
    if not commands:
        lines = section_content.strip().split('\n')
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                 # Heuristic: starts with common command words or patterns
                if line.startswith(('make', 'npm', 'yarn', 'docker', 'python', 'java', 'mvn', 'gradle', './', 'sh ')) or \
                   re.match(r"^[a-zA-Z0-9\-_]+\s+(--)?[a-zA-Z0-9\-_]+", line): # e.g. command --option
                    commands.append(line)

    logger.debug(f"Extracted commands: {commands} from section content.")
    return commands

def extract_build_run_instructions(readme_content: str) -> Dict[str, List[str]]:
    """
    Parses README.md content to find build and run instructions.
    Looks for specific headers and then extracts commands from those sections.
    """
    instructions = {
        "build_commands": [],
        "run_commands": [],
        "install_commands": [], # Added for installation/setup steps
        "usage_examples": []    # Added for general usage examples
    }
    if not readme_content:
        logger.warning("README content is empty.")
        return instructions

    # Normalize headers: convert to lowercase and remove leading/trailing non-alphanumeric chars (like #)
    def normalize_header(text):
        text = text.lower().strip()
        text = re.sub(r"^[^a-z0-9]+", "", text)
        text = re.sub(r"[^a-z0-9]+$", "", text)
        return text.strip()

    # Regex to find markdown headers and their content
    # This regex captures the header text and the content until the next header of the same or higher level, or EOF
    # It handles both ATX style (### Header) and Setext style (Header
 ---) for H1/H2
    section_regex = r"(?:^#{1,6}\s*(.+?)\s*#*
|(^.+)
(?:={3,}|-{3,})
)((?:(?!^#{1,6}\s|^.+
(?:={3,}|-{3,})).|
)*)"

    found_sections = False
    for match in re.finditer(section_regex, readme_content, re.MULTILINE):
        found_sections = True
        header_text = match.group(1) or match.group(2) # Group 1 for ATX, Group 2 for Setext
        section_content = match.group(3)

        if not header_text or not section_content:
            continue

        normalized_header = normalize_header(header_text)
        logger.debug(f"Found section: '{normalized_header}'")

        commands_in_section = extract_commands_from_section(section_content)

        if any(keyword in normalized_header for keyword in ["build", "building", "compilation", "compile"]):
            instructions["build_commands"].extend(commands_in_section)
        elif any(keyword in normalized_header for keyword in ["run", "running", "start", "launch", "execute"]):
            instructions["run_commands"].extend(commands_in_section)
        elif any(keyword in normalized_header for keyword in ["install", "installation", "setup", "requirements", "dependencies", "prerequisites"]):
            instructions["install_commands"].extend(commands_in_section)
        elif any(keyword in normalized_header for keyword in ["usage", "example", "demo"]):
             instructions["usage_examples"].extend(commands_in_section)

    # If no specific sections were found by regex, try a simpler global command extraction
    if not found_sections:
        logger.info("No distinct markdown sections found, attempting global command extraction.")
        all_commands = extract_commands_from_section(readme_content)
        if all_commands:
            # Crude assignment: assume first few might be install/build, later ones run. This is very heuristic.
            # This part could be significantly improved, possibly with LLM assistance in a later stage.
            instructions["install_commands"] = all_commands[:len(all_commands)//2] # Example split
            instructions["run_commands"] = all_commands[len(all_commands)//2:]      # Example split

    # Remove duplicates
    for key in instructions:
        instructions[key] = sorted(list(set(instructions[key])))

    if not any(instructions.values()):
        logger.info("No specific build, run, install, or usage commands found in README.")
    else:
        logger.info(f"Extracted instructions: {instructions}")

    return instructions

# Example Usage (for testing this module):
# if __name__ == "__main__":
#     import json # Added for example
#     sample_readme_content = """
#     # My Awesome Project

#     ## Installation
#     Make sure you have Python 3.8+.
#     ```bash
#     pip install -r requirements.txt
#     ```

#     ## Building
#     To build the project:
#        make build
#     Or using docker:
#     ```
#     docker build -t myapp .
#     ```

#     ## Running the Application
#     You can run the app using:
#         python app/main.py

#     ### With Docker
#     ```sh
#     docker run -p 8000:8000 myapp
#     ```

#     ## Usage
#     Send a POST request to /api/do_something.
#     Example:
#     `curl -X POST http://localhost:8000/api/do_something`
#     """
#     parsed_instructions = extract_build_run_instructions(sample_readme_content)
#     print(json.dumps(parsed_instructions, indent=2))

#     sample_readme_no_blocks = """
#     # Simpler Project
#     Install with: pip install simple.
#     Build with: make all
#     Run using: ./run_script.sh
#     """
#     parsed_instructions_no_blocks = extract_build_run_instructions(sample_readme_no_blocks)
#     print(json.dumps(parsed_instructions_no_blocks, indent=2))
#     ```
